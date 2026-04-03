"""
Hybrid Route Service — the core routing intelligence.

Combines:
  - Delhi Metro GTFS graph (metro_service)
  - Google Maps SDK (directions + traffic)
  - Weather impact (weather_service)

Produces a ranked list of RouteOption objects ready for the agent to reason over.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from geopy.distance import geodesic

from config import settings
from maps.google_maps_client import maps_client
from services.metro_service import delhi_metro, find_nearest_metro_any_city, _is_delhi
from services.weather_service import weather_service, WeatherService
from services.crowding_service import estimate_crowding

logger = logging.getLogger(__name__)

# Cab cost model (Ola/Uber Delhi rough estimate)
CAB_BASE_FARE_RS  = 30
CAB_PER_KM_RS     = 12
CAB_SURGE_PEAK    = 1.5   # multiplier during peak hours

# Auto-rickshaw cost model
AUTO_BASE_FARE_RS = 25
AUTO_PER_KM_RS    = 10

# Speed models (km/h)
WALK_SPEED_KMH    = 4.5
AUTO_SPEED_KMH    = 18    # auto/2-wheeler in city traffic
CAB_SPEED_KMH     = 22    # 4-wheeler in city traffic

# First/last-mile thresholds
WALK_THRESHOLD_KM = 1.0   # < 1 km  → walk
AUTO_THRESHOLD_KM = 3.0   # 1–3 km  → auto / 2-wheeler
                           # > 3 km  → cab  / 4-wheeler
MAX_FIRST_MILE_KM = 8.0   # beyond this, metro hybrid not practical

# Comfort weight for scoring
WEIGHT_DURATION  = 0.40
WEIGHT_COST      = 0.25
WEIGHT_COMFORT   = 0.20
WEIGHT_CERTAINTY = 0.15


# ============================================
# DATA CLASSES
# ============================================

@dataclass
class RouteStep:
    mode: str           # "metro", "walk", "cab", "bus", "transit"
    instruction: str
    duration_minutes: int
    distance_km: float
    cost_rupees: int = 0
    line: Optional[str] = None          # metro line name
    departure_stop: Optional[str] = None
    arrival_stop: Optional[str] = None
    num_stops: Optional[int] = None


@dataclass
class RouteOption:
    route_id: str
    label: str                          # "Metro", "Cab", "Metro + Cab", "Hybrid"
    steps: List[RouteStep]

    total_duration_minutes: int
    total_distance_km: float
    total_cost_rupees: int
    num_transfers: int

    departure_time: datetime
    arrival_time: datetime

    # Scoring
    score: float = 0.0                  # 0-1, higher is better
    on_time_probability: float = 1.0   # 0-1
    comfort_score: float = 1.0         # 0-1

    # Context
    weather_delay_risk: float = 0.0
    disruptions: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    overview_polyline: str = ""     # encoded Google Maps polyline (transit routes only)
    city: Optional[str] = None      # detected city for multi-city badge

    def to_dict(self) -> Dict[str, Any]:
        return {
            "route_id":              self.route_id,
            "label":                 self.label,
            "total_duration_minutes": self.total_duration_minutes,
            "total_distance_km":     round(self.total_distance_km, 2),
            "total_cost_rupees":     self.total_cost_rupees,
            "num_transfers":         self.num_transfers,
            "departure_time":        self.departure_time.isoformat(),
            "arrival_time":          self.arrival_time.isoformat(),
            "score":                 round(self.score, 3),
            "on_time_probability":   round(self.on_time_probability, 3),
            "comfort_score":         round(self.comfort_score, 3),
            "weather_delay_risk":    self.weather_delay_risk,
            "disruptions":           self.disruptions,
            "notes":                 self.notes,
            "overview_polyline":     self.overview_polyline,
            "city":                  self.city,
            "steps": [
                {
                    "mode":            s.mode,
                    "instruction":     s.instruction,
                    "duration_minutes": s.duration_minutes,
                    "distance_km":     round(s.distance_km, 2),
                    "cost_rupees":     s.cost_rupees,
                    "line":            s.line,
                    "departure_stop":  s.departure_stop,
                    "arrival_stop":    s.arrival_stop,
                    "num_stops":       s.num_stops,
                }
                for s in self.steps
            ],
        }


# ============================================
# SERVICE
# ============================================

class HybridRouteService:
    """
    Fetches and ranks route options between an origin and destination.

    Usage:
        options = await hybrid_route_service.get_route_options(
            origin="Rajiv Chowk, Delhi",
            destination="Cyber City, Gurugram",
            departure_time=datetime.now(),
            required_arrival=datetime.now() + timedelta(hours=1),
        )
    """

    def __init__(self):
        # Populated by plan_commute() before the agent loop; cleared after.
        # None = guest or no history — scoring runs with neutral weights.
        self._user_patterns: Optional[Dict[str, Any]] = None

    def set_user_patterns(self, patterns: Optional[Dict[str, Any]]) -> None:
        """
        Set detected user patterns before the agent loop starts so that
        _score_and_rank() can apply personalised weight adjustments.
        Always call set_user_patterns(None) after the loop — use try/finally.
        """
        self._user_patterns = patterns

    async def get_route_options(
        self,
        origin: str,
        destination: str,
        departure_time: Optional[datetime] = None,
        required_arrival: Optional[datetime] = None,
        user_prefs: Optional[Dict[str, Any]] = None,
        city_override: Optional[str] = None,
    ) -> List[RouteOption]:
        """
        Main entry point. Returns up to MAX_ROUTES_TO_COMPARE ranked options.
        """
        departure_time = departure_time or datetime.now()
        user_prefs     = user_prefs or {}

        import asyncio

        # Geocode origin first so we can pass its coordinates to the weather service
        origin_geo = await maps_client.geocode(origin)
        weather_lat = origin_geo["lat"] if origin_geo else None
        weather_lon = origin_geo["lng"] if origin_geo else None

        # Fetch remaining data concurrently (weather now uses origin coords, not Delhi default)
        weather_task         = asyncio.create_task(
            weather_service.get_current_conditions(lat=weather_lat, lon=weather_lon)
        )
        gm_transit_task      = asyncio.create_task(
            maps_client.get_directions(origin, destination, mode="transit",
                                    departure_time=departure_time, alternatives=True)
        )
        gm_drive_task        = asyncio.create_task(
            maps_client.get_traffic_conditions(origin, destination, mode="driving")
        )
        destination_geo_task = asyncio.create_task(maps_client.geocode(destination))

        weather_data, gm_transit, gm_drive, dest_geo = await asyncio.gather(
            weather_task, gm_transit_task, gm_drive_task, destination_geo_task,
        )

        commute_impact  = weather_data.get("commute_impact", {})
        weather_risk    = commute_impact.get("delay_risk", 0.0)
        prefer_metro    = commute_impact.get("prefer_metro", False)

        # Compute heat index and crowding for scoring penalties
        temp_c       = weather_data.get("temperature_c") or 25.0
        humidity_pct = weather_data.get("humidity_pct") or 50
        heat_info    = WeatherService.compute_heat_index(temp_c, humidity_pct)
        heat_category = heat_info["category"]

        options: List[RouteOption] = []

        # --- Option 1: Google Maps transit (most accurate for real world)
        for i, gm_route in enumerate(gm_transit[:2]):   # max 2 transit variants
            opt = self._build_from_gm_transit(
                gm_route, departure_time, weather_risk,
                label=f"Transit Route {i+1}" if i > 0 else "Transit (Google Maps)"
            )
            if opt:
                options.append(opt)

        # --- Option 2: Cab / Ride-share
        if gm_drive:
            cab_opt = self._build_cab_option(
                origin, destination, gm_drive, departure_time, weather_risk
            )
            options.append(cab_opt)

        # --- Option 3: Metro-first hybrid (walk to nearest station + metro + walk)
        if origin_geo and dest_geo:
            hybrid = await self._build_metro_hybrid(
                origin_geo, dest_geo, departure_time, weather_risk, city_override=city_override
            )
            if hybrid:
                options.append(hybrid)

        # Score & rank — or skip and let Gemini reason about the ranking
        if settings.LLM_SCORING_ENABLED:
            # Return options in build order (transit → cab → metro hybrid).
            # Gemini will receive them with raw metrics and decide the ranking.
            pass
        else:
            options = self._score_and_rank(
                options, user_prefs, prefer_metro, required_arrival, departure_time,
                heat_category=heat_category,
                user_patterns=self._user_patterns,
            )

        # Trim to configured max
        return options[:settings.MAX_ROUTES_TO_COMPARE]

    # ============================================
    # BUILDERS
    # ============================================

    def _build_from_gm_transit(
        self,
        gm_route: Dict[str, Any],
        departure_time: datetime,
        weather_risk: float,
        label: str = "Transit (Google Maps)",
    ) -> Optional[RouteOption]:
        try:
            dur_s    = gm_route["duration_seconds"]
            dist_m   = gm_route["distance_meters"]
            gm_steps = gm_route.get("steps", [])

            steps       = []
            total_cost  = 0
            num_xfers   = 0
            prev_line   = None

            for s in gm_steps:
                mode = s["mode"].lower()
                t    = s.get("transit", {})
                cost = 0

                if mode == "transit":
                    line = t.get("line_name", "Transit")
                    if prev_line and prev_line != line:
                        num_xfers += 1
                    # Rough cost: metro ₹30, bus ₹15
                    vtype = t.get("vehicle_type", "").upper()
                    cost  = 30 if "SUBWAY" in vtype or "METRO" in vtype else 15
                    step  = RouteStep(
                        mode=          "metro" if "SUBWAY" in vtype or "METRO" in vtype else "bus",
                        instruction=   f"Take {line} from {t.get('departure_stop','')} to {t.get('arrival_stop','')}",
                        duration_minutes= max(1, round(s["duration_seconds"] / 60)),
                        distance_km=   s["distance_meters"] / 1000,
                        cost_rupees=   cost,
                        line=          line,
                        departure_stop=t.get("departure_stop"),
                        arrival_stop=  t.get("arrival_stop"),
                        num_stops=     t.get("num_stops"),
                    )
                    prev_line = line
                else:
                    step = RouteStep(
                        mode=          mode,
                        instruction=   s.get("instruction", mode.title()),
                        duration_minutes= max(1, round(s["duration_seconds"] / 60)),
                        distance_km=   s["distance_meters"] / 1000,
                        cost_rupees=   0,
                    )

                total_cost += cost
                steps.append(step)

            arrival = departure_time + timedelta(seconds=dur_s)
            on_time_prob = max(0.3, 1.0 - weather_risk * 0.6)

            return RouteOption(
                route_id=           str(uuid.uuid4())[:8],
                label=              label,
                steps=              steps,
                total_duration_minutes= max(1, round(dur_s / 60)),
                total_distance_km=  dist_m / 1000,
                total_cost_rupees=  total_cost or 30,
                num_transfers=      num_xfers,
                departure_time=     departure_time,
                arrival_time=       arrival,
                on_time_probability=on_time_prob,
                weather_delay_risk= weather_risk,
                overview_polyline=  gm_route.get("overview_polyline", ""),
            )
        except Exception as e:
            logger.warning(f"Could not parse GM transit route: {e}")
            return None

    def _build_cab_option(
        self,
        origin: str,
        destination: str,
        traffic: Dict[str, Any],
        departure_time: datetime,
        weather_risk: float,
    ) -> RouteOption:
        dur_s    = traffic.get("duration_in_traffic_seconds",
                               traffic.get("normal_duration_seconds", 3600))
        level    = traffic.get("traffic_level", "moderate")

        # Estimate distance from duration (rough: 30 km/h average in traffic)
        dist_km  = max(1.0, (dur_s / 3600) * CAB_SPEED_KMH)
        is_peak  = self._is_peak(departure_time)
        surge    = CAB_SURGE_PEAK if is_peak else 1.0
        cost     = round((CAB_BASE_FARE_RS + dist_km * CAB_PER_KM_RS) * surge)

        # Cab is more affected by weather than metro
        on_time_prob = max(0.2, 1.0 - weather_risk * 0.8 - self._traffic_penalty(level))

        arrival = departure_time + timedelta(seconds=dur_s)
        step    = RouteStep(
            mode=             "cab",
            instruction=      f"Cab from {origin} to {destination} ({level} traffic)",
            duration_minutes= max(1, round(dur_s / 60)),
            distance_km=      dist_km,
            cost_rupees=      cost,
        )

        notes = []
        if is_peak:
            notes.append(f"Surge pricing active (~{CAB_SURGE_PEAK}x)")
        if level in ("heavy", "severe"):
            notes.append(f"Heavy traffic expected ({level})")

        return RouteOption(
            route_id=           str(uuid.uuid4())[:8],
            label=              "Cab (Ola/Uber)",
            steps=              [step],
            total_duration_minutes= max(1, round(dur_s / 60)),
            total_distance_km=  dist_km,
            total_cost_rupees=  cost,
            num_transfers=      0,
            departure_time=     departure_time,
            arrival_time=       arrival,
            comfort_score=      0.8,    # comfortable but costly
            on_time_probability=on_time_prob,
            weather_delay_risk= weather_risk,
            notes=              notes,
        )

    async def _build_metro_hybrid(
        self,
        origin_geo: Dict[str, Any],
        dest_geo: Dict[str, Any],
        departure_time: datetime,
        weather_risk: float,
        city_override: Optional[str] = None,
    ) -> Optional[RouteOption]:
        """
        First-mile → metro → last-mile.
        First/last mile mode chosen by distance:
          < 1 km  → walk
          1–3 km  → auto-rickshaw / 2-wheeler
          > 3 km  → cab / 4-wheeler
        """
        try:
            olat, olng = origin_geo["lat"], origin_geo["lng"]
            dlat, dlng = dest_geo["lat"],   dest_geo["lng"]

            # Use explicit override, or auto-detect from origin address_components
            if city_override and city_override != "Auto-detect":
                city = city_override
            else:
                city = "unknown"
                for comp in origin_geo.get("address_components", []):
                    if "locality" in comp["types"]:
                        city = comp["long_name"]
                        break
                    if "administrative_area_level_2" in comp["types"]:
                        city = comp["long_name"]

            if city == "unknown":
                logger.warning("Could not detect city from origin geocode; skipping metro hybrid.")
                return None

            nearest_origin = await find_nearest_metro_any_city(city, olat, olng)
            nearest_dest   = await find_nearest_metro_any_city(city, dlat, dlng)

            if not nearest_origin or not nearest_dest:
                return None

            # If both ends resolve to the same station, a metro leg adds no value
            if nearest_origin["name"] == nearest_dest["name"]:
                return None

            first_km = nearest_origin["distance_km"]
            last_km  = nearest_dest["distance_km"]

            # Skip if metro station is too far to be practical
            if first_km > MAX_FIRST_MILE_KM or last_km > MAX_FIRST_MILE_KM:
                return None

            is_peak = self._is_peak(departure_time)
            surge   = CAB_SURGE_PEAK if is_peak else 1.0

            first_step = self._mile_step(
                first_km, weather_risk, is_peak, surge,
                dest_name=nearest_origin["name"], is_first=True,
            )
            last_step = self._mile_step(
                last_km, weather_risk, is_peak, surge,
                dest_name=nearest_dest["name"], is_first=False,
            )

            # Metro leg — use coordinates for precision; no transit_mode filter so
            # Namma Metro (Bangalore), Mumbai Metro etc. are not excluded by Google's
            # "subway" classification.
            origin_coord = f"{nearest_origin['lat']},{nearest_origin['lng']}"
            dest_coord   = f"{nearest_dest['lat']},{nearest_dest['lng']}"
            metro_routes = await maps_client.get_directions(
                origin=        origin_coord,
                destination=   dest_coord,
                mode=          "transit",
                departure_time=departure_time + timedelta(minutes=first_step.duration_minutes),
            )

            if not metro_routes:
                return None

            mr         = metro_routes[0]
            metro_dur  = max(1, round(mr["duration_seconds"] / 60))
            metro_dist = mr["distance_meters"] / 1000
            metro_cost = self._estimate_metro_fare(metro_dist)

            metro_step = RouteStep(
                mode=          "metro",
                instruction=   f"Metro: {nearest_origin['name']} → {nearest_dest['name']}",
                duration_minutes=metro_dur,
                distance_km=   metro_dist,
                cost_rupees=   metro_cost,
                line=          nearest_origin.get("line"),
                departure_stop=nearest_origin["name"],
                arrival_stop=  nearest_dest["name"],
            )

            steps      = [first_step, metro_step, last_step]
            total_dur  = first_step.duration_minutes + metro_dur + last_step.duration_minutes
            total_dist = first_km + metro_dist + last_km
            total_cost = first_step.cost_rupees + metro_cost + last_step.cost_rupees
            arrival    = departure_time + timedelta(minutes=total_dur)

            # Build a human-readable label (append city tag for non-Delhi)
            def _mode_label(step: RouteStep) -> str:
                return {"walk": "Walk", "auto": "Auto", "cab": "Cab"}.get(step.mode, step.mode.title())

            city_tag  = "" if _is_delhi(city) else f" [{city}]"
            label     = f"Metro ({_mode_label(first_step)} + Metro + {_mode_label(last_step)}){city_tag}"

            on_time_prob = max(0.5, 1.0 - weather_risk * 0.3)

            return RouteOption(
                route_id=              str(uuid.uuid4())[:8],
                label=                 label,
                steps=                 steps,
                total_duration_minutes=total_dur,
                total_distance_km=     total_dist,
                total_cost_rupees=     total_cost,
                num_transfers=         0,
                departure_time=        departure_time,
                arrival_time=          arrival,
                comfort_score=         0.7,
                on_time_probability=   on_time_prob,
                weather_delay_risk=    weather_risk,
                city=                  city,
            )

        except Exception as e:
            logger.warning(f"Metro hybrid build failed: {e}")
            return None

    def _mile_step(
        self,
        dist_km: float,
        weather_risk: float,
        is_peak: bool,
        surge: float,
        dest_name: str,
        is_first: bool,
    ) -> RouteStep:
        """
        Return the appropriate first- or last-mile RouteStep:
          < 1 km          → walk  (unless bad weather + > 0.5 km → auto)
          1 – 3 km        → auto-rickshaw
          > 3 km          → cab
        """
        direction = "to" if is_first else "from"
        endpoint  = f"{dest_name} metro station" if is_first else "destination"

        # Bad weather nudge: upgrade walk → auto if rainy/foggy and distance > 0.5 km
        forced_auto = weather_risk >= 0.5 and dist_km > 0.5

        if dist_km < WALK_THRESHOLD_KM and not forced_auto:
            dur_min = max(1, round(dist_km / WALK_SPEED_KMH * 60))
            return RouteStep(
                mode=            "walk",
                instruction=     f"Walk {direction} {endpoint} ({dist_km:.1f} km)",
                duration_minutes=dur_min,
                distance_km=     dist_km,
                cost_rupees=     0,
            )

        if dist_km <= AUTO_THRESHOLD_KM:
            dur_min = max(2, round(dist_km / AUTO_SPEED_KMH * 60))
            cost    = round((AUTO_BASE_FARE_RS + dist_km * AUTO_PER_KM_RS) * (surge if is_peak else 1.0))
            return RouteStep(
                mode=            "auto",
                instruction=     f"Auto-rickshaw {direction} {endpoint} ({dist_km:.1f} km)",
                duration_minutes=dur_min,
                distance_km=     dist_km,
                cost_rupees=     cost,
            )

        # > AUTO_THRESHOLD_KM → cab
        dur_min = max(3, round(dist_km / CAB_SPEED_KMH * 60))
        cost    = round((CAB_BASE_FARE_RS + dist_km * CAB_PER_KM_RS) * surge)
        return RouteStep(
            mode=            "cab",
            instruction=     f"Cab {direction} {endpoint} ({dist_km:.1f} km)",
            duration_minutes=dur_min,
            distance_km=     dist_km,
            cost_rupees=     cost,
        )

    # ============================================
    # SCORING & RANKING
    # ============================================

    def _score_and_rank(
        self,
        options: List[RouteOption],
        user_prefs: Dict[str, Any],
        prefer_metro: bool,
        required_arrival: Optional[datetime],
        departure_time: datetime,
        heat_category: str = "comfortable",
        user_patterns: Optional[Dict[str, Any]] = None,
    ) -> List[RouteOption]:
        if not options:
            return []

        # Unpack pattern signals — explicit None check, not just falsy,
        # so an empty-but-present patterns dict doesn't silently skip.
        if user_patterns is None:
            pattern_preferred_mode = None
            pattern_peak_cab       = False
        else:
            pattern_preferred_mode = user_patterns.get("preferred_mode")
            pattern_peak_cab       = user_patterns.get("peak_cab_usage", False)

        is_peak_now = self._is_peak(departure_time)

        # Normalise durations and costs for relative scoring
        durations = [o.total_duration_minutes for o in options]
        costs     = [o.total_cost_rupees       for o in options]
        min_dur, max_dur = min(durations), max(durations)
        min_cost, max_cost = min(costs), max(costs)

        for opt in options:
            dur_norm  = self._normalise(opt.total_duration_minutes, min_dur, max_dur, invert=True)
            cost_norm = self._normalise(opt.total_cost_rupees,       min_cost, max_cost, invert=True)
            certainty = opt.on_time_probability
            comfort   = opt.comfort_score

            # ── Standard user preference adjustments ─────────────────────────
            if user_prefs.get("prefer_comfort_over_speed"):
                comfort *= 1.2
            if prefer_metro and "metro" in opt.label.lower():
                certainty *= 1.15

            # --- Session 5: Heat + crowding comfort penalties ---
            # Penalise routes with long outdoor walk segments when heat is dangerous
            if heat_category == "dangerous":
                outdoor_walk_km = sum(
                    s.distance_km for s in opt.steps
                    if s.mode == "walk" and s.distance_km > 0.5
                )
                if outdoor_walk_km > 0:
                    comfort -= 0.3   # each dangerous-heat walk leg hurts comfort
                    if "metro" not in opt.label.lower():
                        opt.notes.append(
                            f"Dangerous heat ({heat_category}) — walk segment of "
                            f"{outdoor_walk_km:.1f} km not recommended."
                        )

            # Penalise metro options when crowding is very high (per actual line)
            if "metro" in opt.label.lower():
                metro_step = next((s for s in opt.steps if s.mode == "metro"), None)
                line_name = (metro_step.line or "Generic") if metro_step else "Generic"  # "Generic" → Namma Metro baseline
                route_crowding = estimate_crowding(line_name, departure_time)["occupancy"]
                if route_crowding > 0.85:
                    comfort -= 0.2   # crowded but still viable — not eliminated
                    opt.notes.append(
                        f"Metro is very crowded ({int(route_crowding*100)}% occupancy). "
                        "Consider an earlier departure to avoid the rush."
                    )

            # On-time penalty: if required_arrival given, check buffer
            # ── Pattern-based weight adjustments ─────────────────────────────
            # Boost comfort for the mode the user historically prefers.
            # Cap at +0.1 so we nudge rather than override objective scores.
            if pattern_preferred_mode:
                is_metro_opt = "metro" in opt.label.lower()
                is_cab_opt   = "cab"   in opt.label.lower()

                if pattern_preferred_mode == "metro_hybrid" and is_metro_opt:
                    comfort += 0.1
                elif pattern_preferred_mode == "cab" and is_cab_opt:
                    comfort += 0.1
                elif pattern_preferred_mode == "transit" and not is_metro_opt and not is_cab_opt:
                    comfort += 0.1

            # If user historically hails cabs at peak and it's peak now, give
            # cab a small certainty bump — they've proven it works for them.
            if pattern_peak_cab and is_peak_now and "cab" in opt.label.lower():
                certainty += 0.05

            # ── On-time penalty: if required_arrival given, check buffer ──────
            if required_arrival:
                buffer_min = (required_arrival - opt.arrival_time).total_seconds() / 60
                if buffer_min < 0:
                    certainty *= 0.2   # route arrives too late — heavily penalise
                elif buffer_min < 5:
                    certainty *= 0.6

            score = (
                WEIGHT_DURATION  * dur_norm  +
                WEIGHT_COST      * cost_norm  +
                WEIGHT_COMFORT   * min(max(comfort, 0.0), 1.0) +
                WEIGHT_CERTAINTY * min(certainty, 1.0)
            )

            opt.score               = round(min(score, 1.0), 3)
            opt.on_time_probability = round(min(certainty, 1.0), 3)
            opt.comfort_score       = round(min(comfort, 1.0), 3)

        options.sort(key=lambda o: o.score, reverse=True)
        return options

    # ============================================
    # HELPERS
    # ============================================

    @staticmethod
    def _normalise(value: float, lo: float, hi: float, invert: bool = False) -> float:
        if hi == lo:
            return 1.0
        n = (value - lo) / (hi - lo)
        return round(1.0 - n if invert else n, 4)

    @staticmethod
    def _is_peak(dt: datetime) -> bool:
        t = dt.time()
        from datetime import time as dtime
        return dtime(8, 0) <= t <= dtime(10, 30) or dtime(17, 0) <= t <= dtime(20, 30)

    @staticmethod
    def _traffic_penalty(level: str) -> float:
        return {"light": 0.0, "moderate": 0.1, "heavy": 0.25, "severe": 0.4}.get(level, 0.1)

    @staticmethod
    def _estimate_metro_fare(dist_km: float) -> int:
        if dist_km <= 2:   return 10
        if dist_km <= 5:   return 20
        if dist_km <= 12:  return 30
        if dist_km <= 21:  return 40
        if dist_km <= 32:  return 50
        return 60


# Singleton
hybrid_route_service = HybridRouteService()
