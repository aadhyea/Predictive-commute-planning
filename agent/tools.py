"""
Agent tool definitions and executors.

Two parts:
  1. GEMINI_TOOLS  — google-genai FunctionDeclaration format
  2. execute_tool  — dispatcher that runs the right service call (model-agnostic)
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict

from google.genai import types

from maps.google_maps_client import maps_client
from services.metro_service import delhi_metro
from services.weather_service import weather_service
from services.hybrid_route_service import hybrid_route_service

logger = logging.getLogger(__name__)

S = types.Schema     # shorthand
T = types.Type       # shorthand


# ============================================
# TOOL SCHEMAS  (Gemini FunctionDeclaration format)
# ============================================

GEMINI_TOOLS = [
    types.Tool(function_declarations=[

        types.FunctionDeclaration(
            name="get_weather",
            description=(
                "Get current weather conditions at the user's origin location and their impact on commuting. "
                "Returns delay risk, affected transport modes, and a recommendation. "
                "Always call this first, passing the geocoded lat/lng of the origin address."
            ),
            parameters=S(
                type=T.OBJECT,
                properties={
                    "lat": S(type=T.NUMBER, description="Latitude of the user's origin location"),
                    "lon": S(type=T.NUMBER, description="Longitude of the user's origin location"),
                },
                required=["lat", "lon"],
            ),
        ),

        types.FunctionDeclaration(
            name="get_route_options",
            description=(
                "Fetch ranked route options between an origin and destination. "
                "Returns up to 3 options: transit (Google Maps), cab (estimated), "
                "and metro hybrid (walk + metro + walk). Each includes duration, cost, "
                "on-time probability, and step-by-step breakdown."
            ),
            parameters=S(
                type=T.OBJECT,
                properties={
                    "origin": S(type=T.STRING, description="Starting location, e.g. 'Rajiv Chowk Metro Station, Delhi'"),
                    "destination": S(type=T.STRING, description="Ending location, e.g. 'Cyber City, Gurugram'"),
                    "departure_time_iso": S(type=T.STRING, description="Planned departure time in ISO 8601 format. Defaults to now if omitted."),
                    "required_arrival_iso": S(type=T.STRING, description="Required arrival time in ISO 8601 format. Used to flag routes that arrive too late."),
                    "city": S(type=T.STRING, description="City name auto-populated from geocoding, e.g. 'Delhi', 'Mumbai', 'Bengaluru'. Used to select the correct routing strategy."),
                },
                required=["origin", "destination"],
            ),
        ),

        types.FunctionDeclaration(
            name="get_traffic_conditions",
            description=(
                "Get real-time road traffic between two points. "
                "Returns traffic level (light/moderate/heavy/severe), normal duration, and delay in seconds."
            ),
            parameters=S(
                type=T.OBJECT,
                properties={
                    "origin": S(type=T.STRING, description="Starting location"),
                    "destination": S(type=T.STRING, description="Ending location"),
                },
                required=["origin", "destination"],
            ),
        ),

        types.FunctionDeclaration(
            name="get_metro_status",
            description=(
                "Check if a Delhi Metro line is currently operational. "
                "Returns whether the line is running and its current frequency."
            ),
            parameters=S(
                type=T.OBJECT,
                properties={
                    "line_name": S(type=T.STRING, description="Metro line name, e.g. 'Yellow Line', 'Blue Line', 'Magenta Line'"),
                },
                required=["line_name"],
            ),
        ),

        types.FunctionDeclaration(
            name="find_nearest_metro",
            description="Find the nearest Delhi Metro station to a given address or location name.",
            parameters=S(
                type=T.OBJECT,
                properties={
                    "location": S(type=T.STRING, description="Address or landmark, e.g. 'Connaught Place, Delhi'"),
                },
                required=["location"],
            ),
        ),

        types.FunctionDeclaration(
            name="calculate_leave_time",
            description=(
                "Calculate the latest safe time to leave home given a required arrival time, "
                "route duration, and buffer preference. Returns leave-by time and urgency level."
            ),
            parameters=S(
                type=T.OBJECT,
                properties={
                    "required_arrival_iso": S(type=T.STRING, description="Required arrival time in ISO 8601 format"),
                    "route_duration_minutes": S(type=T.INTEGER, description="Total route duration in minutes"),
                    "buffer_minutes": S(type=T.INTEGER, description="User's preferred safety buffer in minutes (default: 15)"),
                },
                required=["required_arrival_iso", "route_duration_minutes"],
            ),
        ),

        types.FunctionDeclaration(
            name="get_user_history",
            description=(
                "Retrieve the user's recent trip history from the database. "
                "Returns up to 10 past trips with origin, destination, mode, duration, cost, and time. "
                "Use this to personalise recommendations — detect preferred modes, usual routes, "
                "and typical journey times before suggesting a plan."
            ),
            parameters=S(
                type=T.OBJECT,
                properties={
                    "user_id": S(type=T.STRING, description="The authenticated user's UUID"),
                },
                required=["user_id"],
            ),
        ),

    ])
]


# ============================================
# TOOL EXECUTOR
# ============================================

async def execute_tool(tool_name: str, tool_input: Dict[str, Any]) -> str:
    """
    Dispatch tool calls from the agent loop.
    Returns a JSON string to feed back into the conversation.
    """
    try:
        if tool_name == "get_weather":
            result = await _get_weather(tool_input)

        elif tool_name == "get_route_options":
            result = await _get_route_options(tool_input)

        elif tool_name == "get_traffic_conditions":
            result = await _get_traffic_conditions(tool_input)

        elif tool_name == "get_metro_status":
            result = _get_metro_status(tool_input)

        elif tool_name == "find_nearest_metro":
            result = await _find_nearest_metro(tool_input)

        elif tool_name == "calculate_leave_time":
            result = _calculate_leave_time(tool_input)

        elif tool_name == "get_user_history":
            result = await _get_user_history(tool_input)

        else:
            result = {"error": f"Unknown tool: {tool_name}"}

        return json.dumps(result, default=str)

    except Exception as e:
        logger.error(f"Tool '{tool_name}' failed: {e}", exc_info=True)
        return json.dumps({"error": str(e), "tool": tool_name})


# ============================================
# INDIVIDUAL TOOL IMPLEMENTATIONS
# ============================================

async def _get_weather(inp: Dict) -> Dict:
    conditions = await weather_service.get_current_conditions(
        lat=inp.get("lat"),
        lon=inp.get("lon"),
    )
    # Keep it concise for the context window
    return {
        "condition":         conditions.get("condition"),
        "description":       conditions.get("description"),
        "temperature_c":     conditions.get("temperature_c"),
        "rain_1h_mm":        conditions.get("rain_1h_mm"),
        "wind_speed_kmh":    conditions.get("wind_speed_kmh"),
        "visibility_km":     conditions.get("visibility_km"),
        "commute_impact": {
            "delay_risk":       conditions["commute_impact"]["delay_risk"],
            "severity":         conditions["commute_impact"]["severity"],
            "alerts":           conditions["commute_impact"]["alerts"],
            "recommendation":   conditions["commute_impact"]["recommendation"],
            "prefer_metro":     conditions["commute_impact"]["prefer_metro"],
            "affected_modes":   conditions["commute_impact"]["affected_modes"],
        }
    }


async def _get_route_options(inp: Dict) -> Dict:
    departure = None
    required  = None

    if dep_str := inp.get("departure_time_iso"):
        try:
            departure = datetime.fromisoformat(dep_str)
        except ValueError:
            pass

    if arr_str := inp.get("required_arrival_iso"):
        try:
            required = datetime.fromisoformat(arr_str)
        except ValueError:
            pass

    options = await hybrid_route_service.get_route_options(
        origin=           inp["origin"],
        destination=      inp["destination"],
        departure_time=   departure,
        required_arrival= required,
        city_override=    inp.get("city"),
    )

    return {
        "num_options": len(options),
        "options":     [o.to_dict() for o in options],
    }


async def _get_traffic_conditions(inp: Dict) -> Dict:
    traffic = await maps_client.get_traffic_conditions(
        origin=      inp["origin"],
        destination= inp["destination"],
        mode=        "driving",
    )
    return traffic


def _get_metro_status(inp: Dict) -> Dict:
    line_name = inp["line_name"]
    now       = datetime.now().time()
    line      = delhi_metro.get_line_info(line_name)

    if not line:
        # Try partial match
        for name in delhi_metro.lines:
            if line_name.lower() in name.lower():
                line = delhi_metro.lines[name]
                line_name = name
                break

    if not line:
        return {
            "line":        line_name,
            "operational": False,
            "error":       "Line not found. Known lines: " + ", ".join(delhi_metro.lines.keys())
        }

    is_op   = delhi_metro.is_operational(line_name, now)
    is_peak = delhi_metro._is_peak_hour(now)
    freq    = delhi_metro.get_frequency(line_name, is_peak)
    start, end = line.operational_hours

    return {
        "line":             line_name,
        "operational":      is_op,
        "first_train":      start.strftime("%H:%M"),
        "last_train":       end.strftime("%H:%M"),
        "frequency_minutes": freq,
        "is_peak_hour":     is_peak,
        "num_stations":     len(line.stations),
    }


async def _find_nearest_metro(inp: Dict) -> Dict:
    location = inp["location"]
    geo      = await maps_client.geocode(location)

    if not geo:
        return {"error": f"Could not geocode '{location}'"}

    station = delhi_metro.find_nearest_station(geo["lat"], geo["lng"])
    if not station:
        return {"error": "No metro stations found"}

    from geopy.distance import geodesic
    dist_km = geodesic(
        (geo["lat"], geo["lng"]),
        (station.lat, station.lng)
    ).km

    walk_min = max(1, round(dist_km / 4.5 * 60))

    return {
        "location_searched":  location,
        "nearest_station":    station.name,
        "station_id":         station.station_id,
        "line":               station.line,
        "distance_km":        round(dist_km, 3),
        "walking_minutes":    walk_min,
        "is_interchange":     station.interchange,
        "connecting_lines":   station.connecting_lines,
        # Deep link to open Google Maps walking directions
        "walk_directions_url": (
            f"https://www.google.com/maps/dir/?api=1"
            f"&origin={geo['lat']},{geo['lng']}"
            f"&destination={station.lat},{station.lng}"
            f"&travelmode=walking"
        ),
    }


def _calculate_leave_time(inp: Dict) -> Dict:
    arr_str        = inp["required_arrival_iso"]
    route_duration = int(inp["route_duration_minutes"])
    buffer         = int(inp.get("buffer_minutes", 15))

    try:
        required_arrival = datetime.fromisoformat(arr_str)
    except ValueError:
        return {"error": f"Invalid datetime: {arr_str}"}

    latest_leave = required_arrival - timedelta(minutes=route_duration + buffer)
    now          = datetime.now()
    mins_until   = (latest_leave - now).total_seconds() / 60

    if mins_until < 0:
        urgency = "CRITICAL"
        risk    = 1.0
    elif mins_until < 5:
        urgency = "HIGH"
        risk    = 0.85
    elif mins_until < 15:
        urgency = "MEDIUM"
        risk    = 0.55
    else:
        urgency = "LOW"
        risk    = max(0.1, 1.0 - mins_until / 60)

    return {
        "leave_by":              latest_leave.strftime("%H:%M"),
        "leave_by_iso":          latest_leave.isoformat(),
        "minutes_until_leave":   round(mins_until, 1),
        "required_arrival":      required_arrival.strftime("%H:%M"),
        "route_duration_minutes": route_duration,
        "buffer_minutes":        buffer,
        "urgency":               urgency,
        "risk_score":            round(risk, 2),
        "already_late":          mins_until < 0,
    }


async def _get_user_history(inp: Dict) -> Dict:
    user_id = inp.get("user_id", "").strip()
    if not user_id:
        return {"error": "user_id is required", "trips": [], "count": 0}

    from database.supabase_client import get_client
    trips = get_client().get_trip_history(user_id, limit=10)
    return {
        "trips": trips,
        "count": len(trips),
        "has_history": len(trips) > 0,
    }
