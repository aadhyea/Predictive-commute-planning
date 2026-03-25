"""
Delhi Metro Service - Loads and processes DMRC GTFS data.

Reads standard GTFS files from DMRC-GTFS/:
  stops.txt      – stop_id, stop_name, stop_lat, stop_lon
  routes.txt     – route_id, route_short_name, route_long_name, route_color
  trips.txt      – route_id, service_id, trip_id
  stop_times.txt – trip_id, departure_time, stop_id, stop_sequence, shape_dist_traveled
"""

import csv
import logging
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Dict, List, Optional, Tuple

from geopy.distance import geodesic

from config import settings

logger = logging.getLogger(__name__)


# ============================================
# DMRC LINE COLOUR → DISPLAY NAME
# ============================================

LINE_COLOR_TO_NAME: Dict[str, str] = {
    "RED":           "Red Line",
    "YELLOW":        "Yellow Line",
    "BLUE":          "Blue Line",
    "GREEN":         "Green Line",
    "VIOLET":        "Violet Line",
    "ORANGE":        "Orange/Airport Line",
    "ORANGE/AIRPORT":"Orange/Airport Line",
    "MAGENTA":       "Magenta Line",
    "PINK":          "Pink Line",
    "GRAY":          "Gray Line",
    "RAPID":         "Rapid Metro",
    "AQUA":          "Aqua Line",
}

LINE_HEX_COLORS: Dict[str, str] = {
    "Red Line":            "#FF0000",
    "Yellow Line":         "#FFFF00",
    "Blue Line":           "#0066CC",
    "Green Line":          "#00AA00",
    "Violet Line":         "#7B00D4",
    "Orange/Airport Line": "#FF8C00",
    "Magenta Line":        "#CC00CC",
    "Pink Line":           "#FF69B4",
    "Gray Line":           "#808080",
    "Rapid Metro":         "#00BFFF",
    "Aqua Line":           "#00E5E5",
}

# Standard DMRC operational parameters (not present in GTFS, well-known)
DMRC_START_TIME = time(5, 30)
DMRC_END_TIME   = time(23, 30)
DMRC_PEAK_FREQ_MINS    = 4   # average headway during peak
DMRC_OFFPEAK_FREQ_MINS = 8   # average headway off-peak


# ============================================
# DATA CLASSES
# ============================================

@dataclass
class MetroStation:
    """Metro station"""
    station_id: str
    name: str
    line: str               # primary line
    lat: float
    lng: float
    interchange: bool = False
    connecting_lines: List[str] = field(default_factory=list)


@dataclass
class MetroConnection:
    """Direct connection between two consecutive stations on the same line"""
    from_station: str
    to_station: str
    line: str
    duration_minutes: int
    distance_km: float
    fare_rupees: int


@dataclass
class MetroLine:
    """Metro line (colour/corridor)"""
    line_name: str
    color: str
    stations: List[str]                     # stop_ids in order
    frequency_peak_minutes: int
    frequency_offpeak_minutes: int
    operational_hours: Tuple[time, time]    # (first train, last train)


# ============================================
# SERVICE
# ============================================

class DelhiMetroService:
    """
    Loads DMRC GTFS data and exposes query methods used by the agent.

    Public interface is identical to the previous custom-format version
    so no callers need to change.
    """

    def __init__(self):
        self.stations:    Dict[str, MetroStation]              = {}
        self.lines:       Dict[str, MetroLine]                 = {}
        self.connections: List[MetroConnection]                = []
        self.fare_matrix: Dict[Tuple[str, str], int]           = {}

        # Temporary mapping used only during initialisation
        self._route_id_to_line: Dict[str, str] = {}

        self._load_stations()
        self._load_routes()
        self._build_from_gtfs()

        logger.info(
            f"Delhi Metro GTFS loaded – "
            f"{len(self.stations)} stations, "
            f"{len(self.lines)} lines, "
            f"{len(self.connections)} connections"
        )

    # ------------------------------------------------------------------
    # LOADING
    # ------------------------------------------------------------------

    def _load_stations(self):
        """Load station positions from GTFS stops.txt"""
        stops_file = settings.get_metro_data_path(settings.DELHI_METRO_STOPS_FILE)
        if not stops_file.exists():
            logger.warning(f"stops.txt not found at {stops_file}")
            return

        with open(stops_file, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                sid  = row["stop_id"].strip()
                name = row["stop_name"].strip()
                lat  = float(row["stop_lat"].strip())
                lng  = float(row["stop_lon"].strip())

                # line / interchange filled later by _build_from_gtfs
                self.stations[sid] = MetroStation(
                    station_id=sid,
                    name=name,
                    line="Unknown",
                    lat=lat,
                    lng=lng,
                )

    def _load_routes(self):
        """
        Load metro lines from GTFS routes.txt.

        Skips reverse-direction routes (route_short_name ending in '_R').
        When the same colour appears on multiple forward routes (e.g. RED
        has R_RD and R_RS), we store all; _build_from_gtfs picks the
        longest (most stations) to be the canonical representation.
        """
        routes_file = settings.get_metro_data_path(settings.DELHI_METRO_ROUTES_FILE)
        if not routes_file.exists():
            logger.warning(f"routes.txt not found at {routes_file}")
            return

        with open(routes_file, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                short_name = row.get("route_short_name", "").strip()

                # Skip reverse-direction duplicates (e.g. R_RD_R, Y_HQ_R)
                if short_name.endswith("_R"):
                    continue

                route_id  = row["route_id"].strip()
                long_name = row.get("route_long_name", "").strip()
                raw_color = row.get("route_color", "").strip()

                line_name = self._parse_line_name(long_name)
                if not line_name:
                    logger.debug(f"Unrecognised colour in route '{long_name}' – skipped")
                    continue

                hex_color = (
                    f"#{raw_color}" if raw_color
                    else LINE_HEX_COLORS.get(line_name, "#888888")
                )

                # Create MetroLine placeholder (stations filled by _build_from_gtfs)
                if line_name not in self.lines:
                    self.lines[line_name] = MetroLine(
                        line_name=line_name,
                        color=hex_color,
                        stations=[],
                        frequency_peak_minutes=DMRC_PEAK_FREQ_MINS,
                        frequency_offpeak_minutes=DMRC_OFFPEAK_FREQ_MINS,
                        operational_hours=(DMRC_START_TIME, DMRC_END_TIME),
                    )

                # Map route_id → line_name for the GTFS join
                self._route_id_to_line[route_id] = line_name

    # ------------------------------------------------------------------
    # GTFS JOIN: trips + stop_times → station order, timings, connections
    # ------------------------------------------------------------------

    def _build_from_gtfs(self):
        """
        Join trips.txt + stop_times.txt to:
          - Assign each station its primary line
          - Set the ordered station list on each MetroLine
          - Build MetroConnections with real travel durations
          - Detect interchange stations (appear on > 1 line)
        """
        data_dir      = settings.DELHI_METRO_DATA_DIR
        trips_file    = data_dir / "trips.txt"
        stoptimes_file = data_dir / "stop_times.txt"

        if not trips_file.exists() or not stoptimes_file.exists():
            logger.warning("trips.txt / stop_times.txt missing – skipping GTFS join")
            return

        # 1. Pick one representative weekday trip per route_id
        #    (prefer weekday; fall back to whatever comes first)
        route_to_trip: Dict[str, str] = {}
        with open(trips_file, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                rid = row["route_id"].strip()
                if rid not in self._route_id_to_line:
                    continue   # not a forward route we care about
                tid     = row["trip_id"].strip()
                svc     = row.get("service_id", "").strip()
                # Prefer weekday trips; only store first occurrence per route
                if rid not in route_to_trip or svc == "weekday":
                    route_to_trip[rid] = tid

        # 2. Load stop_times only for the chosen trips
        reference_trips = set(route_to_trip.values())
        # trip_id → sorted list of (stop_sequence, stop_id, dep_seconds, dist_meters)
        trip_stops: Dict[str, List[Tuple[int, str, int, float]]] = {
            t: [] for t in reference_trips
        }

        with open(stoptimes_file, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                tid = row["trip_id"].strip()
                if tid not in reference_trips:
                    continue
                seq       = int(row["stop_sequence"].strip())
                sid       = row["stop_id"].strip()
                dep_secs  = self._parse_time(row.get("departure_time", ""))
                dist_m    = float(row.get("shape_dist_traveled", "0") or "0")
                trip_stops[tid].append((seq, sid, dep_secs, dist_m))

        # Sort each trip's stop list by sequence
        for tid in trip_stops:
            trip_stops[tid].sort(key=lambda x: x[0])

        # 3. Process each forward route
        #    station_lines: stop_id → list of line_names it serves
        station_lines: Dict[str, List[str]] = {s: [] for s in self.stations}

        for route_id, line_name in self._route_id_to_line.items():
            trip_id = route_to_trip.get(route_id)
            if not trip_id:
                continue

            stops = trip_stops.get(trip_id, [])
            if not stops:
                continue

            ordered_ids = [sid for _, sid, _, _ in stops]

            # Keep the longest station list as the canonical direction for this line
            if len(ordered_ids) > len(self.lines[line_name].stations):
                self.lines[line_name].stations = ordered_ids

            # Assign line to each station
            for sid in ordered_ids:
                if sid in station_lines and line_name not in station_lines[sid]:
                    station_lines[sid].append(line_name)

            # Build connections between consecutive stops
            for i in range(len(stops) - 1):
                _, from_id, dep_a, dist_a = stops[i]
                _, to_id,   dep_b, dist_b = stops[i + 1]

                if from_id not in self.stations or to_id not in self.stations:
                    continue

                duration = self._seconds_to_minutes(dep_b - dep_a)
                dist_km  = round((dist_b - dist_a) / 1000, 2)  # metres → km
                if dist_km <= 0:
                    # Fallback: geodesic
                    a = self.stations[from_id]
                    b = self.stations[to_id]
                    dist_km = round(geodesic((a.lat, a.lng), (b.lat, b.lng)).km, 2)

                fare = self._estimate_fare(dist_km)

                self.connections.append(MetroConnection(
                    from_station=from_id,
                    to_station=to_id,
                    line=line_name,
                    duration_minutes=duration,
                    distance_km=dist_km,
                    fare_rupees=fare,
                ))
                self.fare_matrix[(from_id, to_id)] = fare
                self.fare_matrix[(to_id, from_id)] = fare   # symmetric

        # 4. Update station objects with line info and interchange flag
        for sid, lines_list in station_lines.items():
            if sid not in self.stations or not lines_list:
                continue
            station = self.stations[sid]
            station.line = lines_list[0]
            if len(lines_list) > 1:
                station.interchange     = True
                station.connecting_lines = lines_list[1:]

    # ------------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_line_name(route_long_name: str) -> Optional[str]:
        """
        Extract display name from GTFS route_long_name.
        Format: "COLOR_Description"  e.g. "YELLOW_Huda City Centre to Qutab Minar"
        """
        if "_" not in route_long_name:
            return None
        color_key = route_long_name.split("_", 1)[0].upper()
        return LINE_COLOR_TO_NAME.get(color_key)

    @staticmethod
    def _parse_time(t: str) -> int:
        """
        Parse GTFS time string 'HH:MM:SS' → total seconds.
        GTFS times can exceed 24:00:00 for trips past midnight.
        """
        if not t:
            return 0
        try:
            h, m, s = t.strip().split(":")
            return int(h) * 3600 + int(m) * 60 + int(s)
        except (ValueError, AttributeError):
            return 0

    @staticmethod
    def _seconds_to_minutes(seconds: int) -> int:
        """Convert seconds to minutes, minimum 1."""
        return max(1, round(seconds / 60))

    def _estimate_fare(self, distance_km: float) -> int:
        """DMRC distance-based fare tiers (₹)"""
        if distance_km <= 2:   return 10
        if distance_km <= 5:   return 20
        if distance_km <= 12:  return 30
        if distance_km <= 21:  return 40
        if distance_km <= 32:  return 50
        return 60

    def _is_peak_hour(self, check_time: time) -> bool:
        morning = time(8, 0) <= check_time <= time(10, 0)
        evening = time(17, 0) <= check_time <= time(20, 0)
        return morning or evening

    # ------------------------------------------------------------------
    # PUBLIC QUERY METHODS  (interface unchanged from previous version)
    # ------------------------------------------------------------------

    def find_station_by_name(self, name: str) -> Optional[MetroStation]:
        """Find station by name (case-insensitive, partial match)."""
        name_lower = name.lower()
        # Exact match first
        for station in self.stations.values():
            if station.name.lower() == name_lower:
                return station
        # Partial match
        for station in self.stations.values():
            if name_lower in station.name.lower():
                return station
        return None

    def find_nearest_station(self, lat: float, lng: float) -> Optional[MetroStation]:
        """Find nearest metro station to given coordinates."""
        if not self.stations:
            return None
        nearest, min_dist = None, float("inf")
        for station in self.stations.values():
            d = geodesic((lat, lng), (station.lat, station.lng)).meters
            if d < min_dist:
                min_dist, nearest = d, station
        return nearest

    def get_connections_from_station(self, station_id: str) -> List[MetroConnection]:
        """Get all outgoing connections from a station."""
        return [c for c in self.connections if c.from_station == station_id]

    def get_line_info(self, line_name: str) -> Optional[MetroLine]:
        return self.lines.get(line_name)

    def is_operational(self, line_name: str, check_time: time) -> bool:
        """Return True if the line runs at check_time."""
        line = self.lines.get(line_name)
        if not line:
            return False
        start, end = line.operational_hours
        if end < start:   # overnight wrap
            return check_time >= start or check_time <= end
        return start <= check_time <= end

    def get_frequency(self, line_name: str, is_peak_hour: bool) -> int:
        """Return headway in minutes for a line."""
        line = self.lines.get(line_name)
        if not line:
            return 10
        return (
            line.frequency_peak_minutes if is_peak_hour
            else line.frequency_offpeak_minutes
        )

    def get_fare(self, from_station: str, to_station: str) -> int:
        """Return fare between two stations (default ₹50 if unknown)."""
        return self.fare_matrix.get((from_station, to_station), 50)

    def get_interchange_stations(self) -> List[MetroStation]:
        """Return all stations where two or more lines meet."""
        return [s for s in self.stations.values() if s.interchange]

    def calculate_route_duration(
        self,
        connections: List[MetroConnection],
        include_waiting_time: bool = True,
        departure_time: Optional[datetime] = None,
    ) -> int:
        """
        Calculate total journey time for an ordered list of connections.

        Adds average waiting time at the first boarding point and at each
        line interchange (interchange walk = 5 mins).
        """
        if not connections:
            return 0

        total = sum(c.duration_minutes for c in connections)

        if include_waiting_time:
            if departure_time is None:
                departure_time = datetime.now()
            is_peak = self._is_peak_hour(departure_time.time())

            # Wait for first train (half the headway on average)
            total += self.get_frequency(connections[0].line, is_peak) / 2

            # Wait + walk at each interchange
            for i in range(len(connections) - 1):
                if connections[i].line != connections[i + 1].line:
                    total += self.get_frequency(connections[i + 1].line, is_peak) / 2
                    total += 5  # interchange walking time

        return int(total)


# Singleton – import this in other modules
delhi_metro = DelhiMetroService()


# ============================================
# CITY-AGNOSTIC METRO FINDER
# ============================================

DELHI_ALIASES = {
    "delhi", "new delhi", "delhi ncr", "delhi / ncr",
    "new delhi municipal council", "central delhi", "south delhi",
    "north delhi", "east delhi", "west delhi", "gurugram", "gurgaon",
    "noida", "faridabad", "ghaziabad",
}


def _is_delhi(city: str) -> bool:
    """Return True if city string maps to Delhi/NCR."""
    normalized = city.lower().strip()
    # Direct match
    if normalized in DELHI_ALIASES:
        return True
    # Starts-with match catches "Delhi Cantonment" etc.
    return normalized.startswith("delhi") or normalized.startswith("new delhi")


async def find_nearest_metro_any_city(city: str, lat: float, lng: float) -> dict | None:
    """
    Delhi/NCR → uses GTFS (fast, precise).
    Any other city → uses Google Maps Places search (city-agnostic).
    Returns: {"name": str, "lat": float, "lng": float, "distance_km": float}
    """
    if _is_delhi(city):
        station = delhi_metro.find_nearest_station(lat, lng)
        if station:
            dist_km = geodesic((lat, lng), (station.lat, station.lng)).km
            return {
                "name":        station.name,
                "lat":         station.lat,
                "lng":         station.lng,
                "distance_km": round(dist_km, 3),
                "line":        station.line,
            }
        return None

    # Non-Delhi: Places Text Search for nearby metro/rapid-transit station.
    # Sort by actual distance (Places relevance order ≠ nearest).
    from maps.google_maps_client import maps_client
    places = await maps_client.search_places(
        "metro station", location=f"{lat},{lng}", radius=10000
    )
    if not places:
        return None

    # Pick the genuinely closest result, not just the first one
    candidates = [
        (geodesic((lat, lng), (p["lat"], p["lng"])).km, p)
        for p in places
    ]
    candidates.sort(key=lambda x: x[0])
    dist_km, nearest = candidates[0]

    return {
        "name":        nearest["name"],
        "lat":         nearest["lat"],
        "lng":         nearest["lng"],
        "distance_km": round(dist_km, 3),
        "line":        None,
    }
