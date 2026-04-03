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
from services.crowding_service import estimate_crowding, get_early_departure_suggestion

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
                "Fetch route options between an origin and destination. "
                "Returns up to 3 options: transit (Google Maps), cab (estimated), "
                "and metro hybrid (walk + metro + walk). Each includes duration, cost, "
                "on-time probability, and step-by-step breakdown. "
                "When ranking_required is True in the response, YOU must rank the routes "
                "and explain which factor drove your choice today."
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
            name="get_comfort_advisory",
            description=(
                "Get a combined heat index + metro crowding advisory for the planned commute. "
                "Call this after get_weather and get_route_options to assess passenger comfort. "
                "Returns heat category, crowding occupancy, a coach boarding tip, and — when the "
                "departure falls in a crowded peak window — a proactive suggestion to leave earlier "
                "with the time and reasoning."
            ),
            parameters=S(
                type=T.OBJECT,
                properties={
                    "lat": S(type=T.NUMBER, description="Origin latitude for weather lookup"),
                    "lon": S(type=T.NUMBER, description="Origin longitude for weather lookup"),
                    "metro_line": S(type=T.STRING, description=(
                        "Metro line name used in the recommended route, "
                        "e.g. 'Yellow Line', 'Blue Line'. Use 'Generic' if unknown."
                    )),
                    "departure_time_iso": S(type=T.STRING, description=(
                        "Planned departure time in ISO 8601 format. Defaults to now if omitted."
                    )),
                },
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

        types.FunctionDeclaration(
            name="get_cost_insights",
            description=(
                "Get monthly commute cost analysis and savings opportunities for the user. "
                "Returns total spend this month, breakdown by transport mode, and top 3 savings "
                "opportunities (routes where metro was available but cab was taken). "
                "Call this proactively when the user asks about cost, budget, or spending."
            ),
            parameters=S(
                type=T.OBJECT,
                properties={
                    "user_id": S(type=T.STRING, description="The authenticated user's UUID"),
                    "month": S(type=T.STRING, description="Month in YYYY-MM format, e.g. '2025-01'. Defaults to current month if omitted."),
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

        elif tool_name == "get_comfort_advisory":
            result = await _get_comfort_advisory(tool_input)
        elif tool_name == "get_user_history":
            result = await _get_user_history(tool_input)

        elif tool_name == "get_cost_insights":
            result = await _get_cost_insights(tool_input)

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
    from config import settings

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

    if not settings.LLM_SCORING_ENABLED:
        return {
            "num_options": len(options),
            "options":     [o.to_dict() for o in options],
        }

    # ── LLM scoring mode ─────────────────────────────────────────────────────
    # Strip the pre-computed score so Gemini doesn't anchor on a Python number.
    # Include a ranking_context block with the factors that should drive the choice.
    route_dicts = []
    for o in options:
        d = o.to_dict()
        d.pop("score", None)   # Gemini decides the ranking, not Python weights
        route_dicts.append(d)

    # Build context from what we know at this point
    departure_dt  = departure or datetime.now()
    is_peak       = hybrid_route_service._is_peak(departure_dt)
    patterns      = hybrid_route_service._user_patterns  # set by core.py before loop

    ranking_context: Dict[str, Any] = {
        "is_peak_hour":         is_peak,
        "has_required_arrival": required is not None,
    }
    if required and departure:
        buffer_min = (required - departure_dt).total_seconds() / 60
        ranking_context["buffer_minutes"] = round(buffer_min)
    if patterns:
        ranking_context["user_preferred_mode"]   = patterns.get("preferred_mode")
        ranking_context["user_peak_cab_pattern"] = patterns.get("peak_cab_usage", False)
        if patterns.get("avg_cost_by_mode"):
            ranking_context["user_avg_cost_by_mode"] = patterns["avg_cost_by_mode"]

    return {
        "num_options":        len(options),
        "options":            route_dicts,
        "ranking_required":   True,
        "ranking_context":    ranking_context,
        "ranking_instruction": (
            "These routes are NOT pre-ranked. You must decide the best order. "
            "IMPORTANT: Start your entire response with exactly this line (nothing before it):\n"
            "BEST_ROUTE: <route_id>\n"
            "where <route_id> is the route_id field of the option you recommend. "
            "Then write your SUMMARY and REASONING as normal. "
            "In your SUMMARY, name the route you recommend AND state the single most "
            "important factor that drove your choice TODAY — be specific about the "
            "tradeoff (e.g. 'prioritising cost over the 7-min time difference because "
            "weather is clear and buffer is comfortable' or 'choosing cab despite higher "
            "cost because the 5-min buffer leaves no room for metro delays')."
        ),
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


async def _get_comfort_advisory(inp: Dict) -> Dict:
    """
    Combines heat index (from live weather) + metro crowding model into a single
    comfort advisory. Also emits a proactive early-departure suggestion when the
    departure time falls in a heavily crowded peak window.
    """
    lat  = inp.get("lat")
    lon  = inp.get("lon")
    line = inp.get("metro_line", "Generic")

    dep_str = inp.get("departure_time_iso")
    departure = datetime.now()
    if dep_str:
        try:
            departure = datetime.fromisoformat(dep_str)
        except (ValueError, TypeError):
            # Handle time-only strings like "08:30" or "08:30:00"
            # (result.leave_by is stored as HH:MM, not a full ISO datetime)
            try:
                from datetime import time as _time
                t = _time.fromisoformat(dep_str.strip())
                departure = datetime.combine(datetime.now().date(), t)
            except (ValueError, TypeError):
                departure = datetime.now()

    # --- Weather / heat index ---
    conditions = await weather_service.get_current_conditions(lat=lat, lon=lon)
    temp_c     = conditions.get("temperature_c") or 25.0
    humidity   = conditions.get("humidity_pct")  or 50

    from services.weather_service import WeatherService
    heat = WeatherService.compute_heat_index(temp_c, humidity)

    # --- Crowding ---
    crowding = estimate_crowding(line, departure)

    # --- Early-departure suggestion ---
    early = get_early_departure_suggestion(line, departure, lead_minutes=30)

    # --- Synthesise reasoning ---
    heat_c    = heat["heat_index_c"]
    heat_cat  = heat["category"]
    crowd_lbl = crowding["label"]
    crowd_occ = crowding["occupancy"]

    # Build a plain-language synthesis
    parts = []

    if heat_cat in ("hot", "dangerous"):
        parts.append(
            f"Heat index is {heat_c}°C ({heat_cat}) — minimise outdoor walking segments."
        )
    else:
        parts.append(f"Heat index is {heat_c}°C — outdoor conditions are {heat_cat}.")

    if crowding["is_peak"]:
        parts.append(
            f"{line} is {crowd_lbl} right now ({int(crowd_occ*100)}% occupancy). "
            f"{crowding['coach_tip']}"
        )
    else:
        parts.append(
            f"{line} is {crowd_lbl} (off-peak, {int(crowd_occ*100)}% occupancy). "
            "Good time to travel."
        )

    if early:
        parts.append(f"Proactive tip: {early['reason']}")

    # Mode recommendation
    if heat_cat == "dangerous" and crowd_occ > 0.85:
        mode_rec = "cab"
        rec_reason = (
            "Dangerous heat + very crowded metro — enclosed cab avoids both risks."
        )
    elif heat_cat == "dangerous":
        mode_rec = "metro"
        rec_reason = (
            "Dangerous heat — metro is air-conditioned; minimise walk segments."
        )
    elif crowd_occ > 0.85 and heat_cat in ("hot", "dangerous"):
        mode_rec = "metro"
        rec_reason = (
            "Despite crowding, metro stays cooler than outdoor cab waiting. "
            "Use the coach tip above."
        )
    else:
        mode_rec = "metro"
        rec_reason = "Metro is the most reliable and comfortable option given current conditions."

    return {
        "heat_index_c":       heat["heat_index_c"],
        "heat_category":      heat["category"],
        "heat_advisory":      heat["advisory"],
        "temperature_c":      temp_c,
        "humidity_pct":       humidity,
        "metro_line":         line,
        "crowding_occupancy": crowding["occupancy"],
        "crowding_label":     crowding["label"],
        "is_peak":            crowding["is_peak"],
        "peak_type":          crowding["peak_type"],
        "coach_tip":          crowding["coach_tip"],
        "early_departure":    early,
        "recommended_mode":   mode_rec,
        "reasoning":          " ".join(parts),
        "rec_reason":         rec_reason,
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


async def _get_cost_insights(inp: Dict) -> Dict:
    user_id = inp.get("user_id", "").strip()
    if not user_id:
        return {"error": "user_id is required"}

    month = inp.get("month") or datetime.now().strftime("%Y-%m")

    from database.supabase_client import get_client
    from services.memory_service import detect_savings_opportunities

    db     = get_client()
    spend  = db.get_monthly_spend(user_id, month)
    trips  = db.get_trip_history(user_id, limit=50)

    opportunities = detect_savings_opportunities(spend, trips)[:3]

    total        = spend.get("total_spent", 0)
    total_saving = sum(o["saving"] for o in opportunities)

    if total_saving > 0 and opportunities:
        summary = (
            f"You've spent ₹{total} on commutes this month. "
            f"Switching to metro on your {len(opportunities)} most frequent routes "
            f"would save ₹{total_saving}/month."
        )
    elif total > 0:
        summary = (
            f"You've spent ₹{total} on commutes this month "
            f"across {spend.get('trip_count', 0)} trips."
        )
    else:
        summary = "No commute spend data found for this month."

    return {
        "month":                month,
        "total_spent":          total,
        "by_mode":              spend.get("by_mode", {}),
        "trip_count":           spend.get("trip_count", 0),
        "avg_trip_cost":        spend.get("avg_trip_cost", 0),
        "savings_opportunities": opportunities,
        "summary":              summary,
    }
