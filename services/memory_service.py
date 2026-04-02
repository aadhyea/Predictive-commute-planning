"""
Commute memory service — pattern detection and savings opportunity analysis.

Public functions:
  detect_patterns()             — analyse trip history for behavioural patterns;
                                  used by the agent to personalise recommendations.
  detect_savings_opportunities() — identify trips where a cheaper option (metro)
                                  was available but a more expensive one (cab) was taken;
                                  used by the monthly cost tracker (Session 6).

Trip dict shape (from SupabaseClient.get_trip_history):
  origin       : str
  destination  : str
  route_label  : str   — e.g. "Cab (Ola/Uber)", "Metro (Walk + Metro + Walk)"
  mode         : str   — 'cab' | 'transit' | 'metro_hybrid'
  duration_min : int
  cost_inr     : int
  planned_at   : str   — ISO-8601 UTC timestamp
"""

import logging
import statistics
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Peak-hour bands (local clock hours, inclusive on both ends) ───────────────
_PEAK_AM_START = 8
_PEAK_AM_END   = 11
_PEAK_PM_START = 17
_PEAK_PM_END   = 20

# Minimum trips needed before we surface a pattern as reliable
_MIN_TRIPS_FOR_MODE_PREF  = 3
_MIN_TRIPS_FOR_PEAK_CAB   = 2
_MIN_TRIPS_FOR_ROUTE_FREQ = 2

_IST_OFFSET_HOURS = 5.5   # UTC+5:30


# ══════════════════════════════════════════════════════════════════════════════
# Pattern detection  (Session 3 — personalisation)
# ══════════════════════════════════════════════════════════════════════════════

def detect_patterns(trips: Optional[List[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    """
    Analyse a user's trip history and return detected behavioural patterns.

    Returns None for guests or users with no history — callers must check
    for None explicitly (not just falsy) before using the result.

    Return shape when history exists:
    {
        "trip_count"          : int,
        "preferred_mode"      : str | None,   # 'cab' | 'transit' | 'metro_hybrid'
        "peak_cab_usage"      : bool,          # takes cabs during peak hours
        "usual_duration_min"  : int | None,    # median trip duration
        "avg_cost_by_mode"    : {mode: int},   # mean cost per mode
        "route_frequency"     : {"A → B": n},  # top O→D pairs, count ≥ threshold
        "has_reliable_data"   : bool,          # enough trips for confident patterns
        "usual_departure_hour": float | None,  # median departure hour (IST)
        "most_frequent_route" : {"origin": str, "destination": str} | None,
        "usual_metro_line"    : str | None,
    }
    """
    if not trips:
        return None

    trip_count = len(trips)

    # Parse timestamps once
    parsed: List[Dict[str, Any]] = []
    for t in trips:
        dt = _parse_timestamp(t.get("planned_at"))
        parsed.append({**t, "_dt": dt})

    # Preferred mode
    mode_counts = Counter(t["mode"] for t in parsed if t.get("mode"))
    preferred_mode: Optional[str] = None
    if mode_counts and sum(mode_counts.values()) >= _MIN_TRIPS_FOR_MODE_PREF:
        top_mode, top_count = mode_counts.most_common(1)[0]
        if top_count / trip_count > 0.40:
            preferred_mode = top_mode

    # Peak-hour cab usage
    peak_cab_trips = [
        t for t in parsed
        if t.get("mode") == "cab" and _is_peak_hour(t["_dt"])
    ]
    peak_cab_usage = len(peak_cab_trips) >= _MIN_TRIPS_FOR_PEAK_CAB

    # Usual (median) duration
    durations = [
        t["duration_min"] for t in parsed
        if isinstance(t.get("duration_min"), (int, float)) and t["duration_min"] > 0
    ]
    usual_duration_min: Optional[int] = (
        round(statistics.median(durations)) if durations else None
    )

    # Average cost by mode
    cost_by_mode: Dict[str, List[int]] = {}
    for t in parsed:
        mode = t.get("mode")
        cost = t.get("cost_inr")
        if mode and isinstance(cost, (int, float)) and cost > 0:
            cost_by_mode.setdefault(mode, []).append(int(cost))
    avg_cost_by_mode = {
        mode: round(statistics.mean(costs))
        for mode, costs in cost_by_mode.items()
        if costs
    }

    # Route frequency (origin → destination pairs)
    route_counts: Counter = Counter()
    for t in parsed:
        origin = (t.get("origin") or "").strip()
        dest   = (t.get("destination") or "").strip()
        if origin and dest:
            key = f"{_short(origin)} → {_short(dest)}"
            route_counts[key] += 1
    route_frequency = {
        route: count
        for route, count in route_counts.most_common(5)
        if count >= _MIN_TRIPS_FOR_ROUTE_FREQ
    }

    # Usual departure hour (median, IST float)
    hours = []
    for t in parsed:
        dt = t["_dt"]
        if dt is not None:
            ist_hour = (dt.hour + dt.minute / 60 + _IST_OFFSET_HOURS) % 24
            hours.append(ist_hour)
    usual_departure_hour: Optional[float] = (
        round(statistics.median(hours), 1) if len(hours) >= 5 else None
    )

    # Most frequent O→D pair as structured dict
    most_frequent_route: Optional[Dict[str, str]] = None
    structured_routes: Counter = Counter()
    for t in parsed:
        origin = (t.get("origin") or "").strip()
        dest   = (t.get("destination") or "").strip()
        if origin and dest:
            structured_routes[f"{origin}||{dest}"] += 1
    if structured_routes:
        top_key = structured_routes.most_common(1)[0][0]
        orig, dest = top_key.split("||", 1)
        most_frequent_route = {"origin": orig, "destination": dest}

    # Most used metro line
    _METRO_LINES = ["Blue", "Yellow", "Red", "Green", "Violet", "Pink", "Magenta", "Orange"]
    line_mentions: List[str] = []
    for t in parsed:
        if t.get("mode") == "metro_hybrid" and t.get("route_label"):
            label = t["route_label"]
            for line in _METRO_LINES:
                if line in label:
                    line_mentions.append(line)
                    break
    usual_metro_line: Optional[str] = (
        Counter(line_mentions).most_common(1)[0][0] if line_mentions else None
    )

    return {
        "trip_count":           trip_count,
        "preferred_mode":       preferred_mode,
        "peak_cab_usage":       peak_cab_usage,
        "usual_duration_min":   usual_duration_min,
        "avg_cost_by_mode":     avg_cost_by_mode,
        "route_frequency":      route_frequency,
        "has_reliable_data":    trip_count >= _MIN_TRIPS_FOR_MODE_PREF,
        "usual_departure_hour": usual_departure_hour,
        "most_frequent_route":  most_frequent_route,
        "usual_metro_line":     usual_metro_line,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Savings opportunity detection  (Session 6 — monthly cost tracker)
# ══════════════════════════════════════════════════════════════════════════════

def detect_savings_opportunities(
    spend: Dict[str, Any],
    trips: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Identify trips where metro was available but cab was taken.

    Compares each cab trip against known metro trips on the same O→D pair.
    When no metro history exists for a route, estimates ₹45 if cab cost > ₹100.

    Args:
        spend: Output of get_monthly_spend (not used directly but kept for
               signature symmetry with the agent tool).
        trips: List of trip dicts from get_trip_history.

    Returns:
        List of dicts sorted by saving (descending):
        [{"date", "route", "cab_cost", "metro_cost", "saving"}, ...]
    """
    # Build cheapest known metro cost per (origin_key, dest_key)
    metro_costs: Dict[tuple, int] = {}
    for trip in trips:
        if trip.get("mode") in ("metro", "metro_hybrid", "transit"):
            key  = (_route_key(trip.get("origin", "")), _route_key(trip.get("destination", "")))
            cost = trip.get("cost_inr") or 0
            if cost and (key not in metro_costs or cost < metro_costs[key]):
                metro_costs[key] = cost

    opportunities: List[Dict[str, Any]] = []
    seen: set = set()  # deduplicate by route

    for trip in trips:
        if trip.get("mode") != "cab":
            continue
        cab_cost = trip.get("cost_inr") or 0
        if not cab_cost:
            continue

        key = (_route_key(trip.get("origin", "")), _route_key(trip.get("destination", "")))

        # Use known metro cost; fall back to ₹45 estimate when cab is expensive
        metro_cost = metro_costs.get(key)
        if metro_cost is None:
            if cab_cost > 100:
                metro_cost = 45
            else:
                continue

        saving = cab_cost - metro_cost
        if saving <= 0:
            continue

        route_label = (
            f"{trip.get('origin', '').split(',')[0].strip()} → "
            f"{trip.get('destination', '').split(',')[0].strip()}"
        )

        # Keep only the highest-saving entry per route
        if key in seen:
            continue
        seen.add(key)

        opportunities.append({
            "date":       (trip.get("planned_at") or "")[:10],
            "route":      route_label,
            "cab_cost":   cab_cost,
            "metro_cost": metro_cost,
            "saving":     saving,
        })

    opportunities.sort(key=lambda x: x["saving"], reverse=True)
    return opportunities


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    """Parse ISO-8601 string → aware datetime, or None on failure."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        logger.debug(f"Could not parse timestamp: {value!r}")
        return None


def _is_peak_hour(dt: Optional[datetime]) -> bool:
    """Return True if dt falls in a weekday AM or PM peak band (IST)."""
    if dt is None:
        return False
    ist_hour_float = (dt.hour + dt.minute / 60 + _IST_OFFSET_HOURS) % 24
    hour    = int(ist_hour_float)
    weekday = dt.weekday()   # 0 = Monday, 6 = Sunday
    if weekday >= 5:          # weekend — no peak adjustment
        return False
    am_peak = _PEAK_AM_START <= hour <= _PEAK_AM_END
    pm_peak = _PEAK_PM_START <= hour <= _PEAK_PM_END
    return am_peak or pm_peak


def _short(address: str) -> str:
    """Return the first comma-delimited token for a compact route label."""
    return address.split(",")[0].strip()


def _route_key(location: str) -> str:
    """Normalise a location string to a simple key for route matching."""
    return location.lower().strip().split(",")[0].strip()