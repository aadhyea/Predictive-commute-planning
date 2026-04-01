"""
Delhi Commute Agent — core agentic loop (Gemini 2.0 Flash).

Uses the google-genai SDK with function calling to let Gemini autonomously:
  1. Gather data (weather, routes, metro status)
  2. Reason about the best option
  3. Return a structured recommendation + natural language explanation

Entry point:
    agent = CommuteAgent()
    result = await agent.plan_commute(
        origin="Rajiv Chowk, Delhi",
        destination="Cyber City, Gurugram",
        required_arrival="2026-03-10T09:30:00",
        user_prefs={"buffer_minutes": 15},
    )
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types

from config import settings
from agent.prompts import COMMUTE_AGENT_SYSTEM_PROMPT, HINDI_LANGUAGE_INSTRUCTION
from agent.tools import GEMINI_TOOLS, execute_tool
from maps.google_maps_client import maps_client

logger = logging.getLogger(__name__)

MODEL         = "gemini-2.5-flash"
MAX_TOKENS    = 4096
MAX_TOOL_ROUNDS = 6


# ============================================
# RESULT DATA CLASS
# ============================================

@dataclass
class CommuteRecommendation:
    explanation: str

    recommended_route: Optional[Dict[str, Any]] = None
    alternative_routes: List[Dict[str, Any]] = field(default_factory=list)

    urgency: str = "LOW"
    risk_score: float = 0.0
    leave_by: Optional[str] = None

    weather_summary: Optional[str] = None
    disruptions: List[str] = field(default_factory=list)

    uber_link: Optional[str] = None
    ola_link: Optional[str] = None

    tool_calls_made: List[str] = field(default_factory=list)
    # Each entry: {"name": str, "summary": str}  — shown in the reasoning trace UI
    tool_trace: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "explanation":        self.explanation,
            "recommended_route":  self.recommended_route,
            "alternative_routes": self.alternative_routes,
            "urgency":            self.urgency,
            "risk_score":         self.risk_score,
            "leave_by":           self.leave_by,
            "weather_summary":    self.weather_summary,
            "disruptions":        self.disruptions,
            "uber_link":          self.uber_link,
            "ola_link":           self.ola_link,
            "tool_calls_made":    self.tool_calls_made,
            "tool_trace":         self.tool_trace,
        }


# ============================================
# AGENT
# ============================================

class CommuteAgent:
    """
    Autonomous commute planning agent backed by Gemini 2.0 Flash.
    """

    def __init__(self):
        self._client = genai.Client(api_key=settings.GEMINI_API_KEY)

    def _make_config(self, language: Optional[str] = None) -> types.GenerateContentConfig:
        system_instruction = COMMUTE_AGENT_SYSTEM_PROMPT
        if language and language.lower().startswith("hi"):
            system_instruction = system_instruction + "\n\n" + HINDI_LANGUAGE_INSTRUCTION
        return types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=GEMINI_TOOLS,
            max_output_tokens=MAX_TOKENS,
        )

    async def plan_commute(
        self,
        origin: str,
        destination: str,
        required_arrival: Optional[str] = None,
        departure_time: Optional[str] = None,
        user_prefs: Optional[Dict[str, Any]] = None,
        extra_context: Optional[str] = None,
        user_id: Optional[str] = None,
        language: Optional[str] = None,
    ) -> CommuteRecommendation:
        user_prefs    = user_prefs or {}
        departure_str = departure_time or datetime.now().isoformat()

        # Geocode origin upfront so we can pass exact coordinates to get_weather.
        # If geocoding fails we skip weather gracefully (no Delhi fallback).
        origin_lat: Optional[float] = None
        origin_lng: Optional[float] = None
        try:
            geo = await maps_client.geocode(origin)
            if geo:
                origin_lat = geo["lat"]
                origin_lng = geo["lng"]
        except Exception as e:
            logger.warning(f"Could not geocode origin '{origin}' for weather: {e}")

        user_message = self._build_user_message(
            origin, destination, required_arrival, departure_str, user_prefs, extra_context,
            user_id=user_id,
            origin_lat=origin_lat,
            origin_lng=origin_lng,
        )

        config = self._make_config(language)

        contents: List[types.Content] = [
            types.Content(role="user", parts=[types.Part(text=user_message)])
        ]
        tool_calls_made: List[str] = []
        tool_results_store: List[Dict] = []   # raw dicts for result extraction
        failed_tools: List[str] = []          # tools that returned errors/empty

        for round_num in range(MAX_TOOL_ROUNDS):
            response = self._client.models.generate_content(
                model=MODEL,
                contents=contents,
                config=config,
            )

            candidate = response.candidates[0]
            logger.debug(f"Round {round_num+1}: finish_reason={candidate.finish_reason.name}, "
                         f"parts={[type(p).__name__ for p in candidate.content.parts]}")

            # Append model turn to history
            contents.append(candidate.content)

            # Collect any function calls in this response
            fn_calls = [p for p in candidate.content.parts if p.function_call]

            if not fn_calls:
                # No function calls — Gemini is done
                final_text = self._extract_text(candidate.content)
                return self._build_result(
                    explanation=    final_text,
                    tool_results=   tool_results_store,
                    tool_calls_made=tool_calls_made,
                    failed_tools=   failed_tools,
                    origin=         origin,
                    destination=    destination,
                )

            # Execute all function calls and collect responses
            fn_response_parts: List[types.Part] = []

            for part in fn_calls:
                fn       = part.function_call
                fn_name  = fn.name
                fn_args  = dict(fn.args) if fn.args else {}
                tool_calls_made.append(fn_name)

                logger.info(f"Tool call: {fn_name}({json.dumps(fn_args, default=str)[:120]})")

                result_str = await execute_tool(fn_name, fn_args)
                logger.debug(f"Tool result ({fn_name}): {result_str[:200]}")

                # Store for result extraction later; track failures
                try:
                    parsed = json.loads(result_str)
                    tool_results_store.append({"name": fn_name, "result": parsed})
                    if "error" in parsed or not parsed:
                        failed_tools.append(fn_name)
                except json.JSONDecodeError:
                    tool_results_store.append({"name": fn_name, "result": {}})
                    failed_tools.append(fn_name)

                fn_response_parts.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            name=fn_name,
                            response={"output": result_str},
                        )
                    )
                )

            # Feed all results back in a single user turn
            contents.append(
                types.Content(role="user", parts=fn_response_parts)
            )

        # Loop exhausted — grab whatever text we have
        final_text = self._extract_text(contents[-1]) if contents else "No response generated."
        return self._build_result(
            explanation=    final_text or "Agent loop limit reached.",
            tool_results=   tool_results_store,
            tool_calls_made=tool_calls_made,
            failed_tools=   failed_tools,
            origin=         origin,
            destination=    destination,
        )

    # ============================================
    # FREE-FORM CHAT
    # ============================================

    async def chat(self, user_message: str, history: Optional[List[Dict]] = None,
                   language: Optional[str] = None) -> str:
        """
        Free-form chat with the commute agent.
        `history` is a list of {"role": "user"|"model", "text": "..."} dicts.
        Returns the agent's text reply.
        """
        config   = self._make_config(language)
        contents: List[types.Content] = []

        for h in (history or []):
            role = "user" if h.get("role") == "user" else "model"
            contents.append(types.Content(
                role=role,
                parts=[types.Part(text=h.get("text", ""))]
            ))

        contents.append(types.Content(role="user", parts=[types.Part(text=user_message)]))

        for _ in range(MAX_TOOL_ROUNDS):
            response = self._client.models.generate_content(
                model=MODEL,
                contents=contents,
                config=config,
            )
            candidate = response.candidates[0]
            contents.append(candidate.content)

            fn_calls = [p for p in candidate.content.parts if p.function_call]

            if not fn_calls:
                return self._extract_text(candidate.content)

            fn_response_parts = []
            for part in fn_calls:
                fn         = part.function_call
                result_str = await execute_tool(fn.name, dict(fn.args) if fn.args else {})
                fn_response_parts.append(types.Part(
                    function_response=types.FunctionResponse(
                        name=fn.name,
                        response={"output": result_str},
                    )
                ))

            contents.append(types.Content(role="user", parts=fn_response_parts))

        return self._extract_text(contents[-1])

    # ============================================
    # HELPERS
    # ============================================

    def _build_user_message(
        self,
        origin: str,
        destination: str,
        required_arrival: Optional[str],
        departure_time: str,
        user_prefs: Dict,
        extra_context: Optional[str],
        user_id: Optional[str] = None,
        origin_lat: Optional[float] = None,
        origin_lng: Optional[float] = None,
    ) -> str:
        lines = [
            f"Plan my commute from **{origin}** to **{destination}**.",
            f"Current time: {datetime.now().strftime('%A, %d %B %Y %H:%M')}",
            f"Planned departure: {departure_time}",
        ]
        if origin_lat is not None and origin_lng is not None:
            lines.append(
                f"Origin coordinates (for get_weather): lat={origin_lat:.5f}, lon={origin_lng:.5f} — "
                "use these exact values when calling get_weather."
            )
        if required_arrival:
            lines.append(f"I must arrive by: {required_arrival}")

        buffer = user_prefs.get("buffer_minutes", 15)
        lines.append(f"My preferred safety buffer: {buffer} minutes")

        if user_prefs.get("prefer_comfort_over_speed"):
            lines.append("I prefer comfort over speed (less crowded is better).")

        if max_walk := user_prefs.get("max_walking_minutes"):
            lines.append(f"I can walk up to {max_walk} minutes.")

        if extra_context:
            lines.append(f"Additional context: {extra_context}")

        if user_id:
            lines.append(
                f"\nUser ID: {user_id} — call get_user_history with this ID to personalise the recommendation."
            )

        lines.append(
            "\nPlease check current weather, fetch route options, and give me a clear recommendation."
        )
        return "\n".join(lines)

    @staticmethod
    def _extract_text(content: types.Content) -> str:
        parts = []
        for part in content.parts:
            if hasattr(part, "text") and part.text:
                parts.append(part.text)
        return "\n".join(parts).strip()

    def _build_result(
        self,
        explanation: str,
        tool_results: List[Dict],
        tool_calls_made: List[str],
        origin: str,
        destination: str,
        failed_tools: Optional[List[str]] = None,
    ) -> CommuteRecommendation:
        routes:     List[Dict] = []
        leave_by:   Optional[str] = None
        urgency:    str = "LOW"
        risk_score: float = 0.0
        weather_summary: Optional[str] = None

        for tr in tool_results:
            data = tr.get("result", {})
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except json.JSONDecodeError:
                    continue

            # Route options
            if "options" in data:
                routes = data["options"]

            # Leave time / urgency
            if "leave_by" in data and "urgency" in data:
                leave_by   = data["leave_by"]
                urgency    = data["urgency"]
                risk_score = data.get("risk_score", risk_score)

            # Weather
            if "commute_impact" in data:
                ci = data["commute_impact"]
                weather_summary = ci.get("recommendation")
                if ci.get("delay_risk", 0) > risk_score:
                    risk_score = ci["delay_risk"]

        disruptions = []
        for r in routes:
            disruptions.extend(r.get("disruptions", []))

        if "calculate_leave_time" not in tool_calls_made and risk_score >= 0.7:
            urgency = "HIGH"
        if risk_score >= 0.85:
            urgency = "CRITICAL"

        # Proactive cost nudge: if recommended route is cab but user has metro history on same O→D
        cost_nudge = _build_cost_nudge(routes[0] if routes else None, tool_results, origin, destination)
        if cost_nudge:
            explanation = explanation + f"\n\n{cost_nudge}"

        # Uncertainty flagging (Task 7.4): append a note for every tool that returned no data
        for fn in (failed_tools or []):
            explanation = explanation + f"\n\nNote: {fn} returned no data — this recommendation may be less accurate."

        # Build reasoning trace (Task 7.1)
        tool_trace = [
            {"name": tr["name"], "summary": _summarise_tool_result(tr["name"], tr.get("result", {}))}
            for tr in tool_results
        ]

        return CommuteRecommendation(
            explanation=       explanation,
            recommended_route= routes[0] if routes else None,
            alternative_routes=routes[1:] if len(routes) > 1 else [],
            urgency=           urgency,
            risk_score=        round(risk_score, 2),
            leave_by=          leave_by,
            weather_summary=   weather_summary,
            disruptions=       list(set(disruptions)),
            uber_link=         self._build_uber_link(origin, destination),
            ola_link=          self._build_ola_link(origin, destination),
            tool_calls_made=   tool_calls_made,
            tool_trace=        tool_trace,
        )

    @staticmethod
    def _build_uber_link(origin: str, destination: str) -> str:
        from urllib.parse import quote
        return (
            f"https://m.uber.com/ul/?"
            f"action=setPickup"
            f"&pickup[formatted_address]={quote(origin)}"
            f"&dropoff[formatted_address]={quote(destination)}"
        )

    @staticmethod
    def _build_ola_link(origin: str, destination: str) -> str:
        from urllib.parse import quote
        return (
            f"https://book.olacabs.com/?"
            f"pickup_name={quote(origin)}"
            f"&drop_name={quote(destination)}"
            f"&utm_source=commuteagent"
        )


# ── Standalone helper (not a method) ─────────────────────────────────────────

def _route_key(location: str) -> str:
    return location.lower().strip().split(",")[0].strip()


def _summarise_tool_result(name: str, result: Dict[str, Any]) -> str:
    """One-line summary of a tool result for the reasoning trace UI."""
    if not result or "error" in result:
        return f"error: {result.get('error', 'no data')}"

    if name == "get_weather":
        risk = result.get("commute_impact", {}).get("delay_risk", "?")
        cond = result.get("condition", "?")
        return f"risk: {risk} ({cond})"

    if name == "get_route_options":
        n = result.get("num_options", 0)
        return f"{n} options scored"

    if name == "get_comfort_advisory":
        heat  = result.get("heat_index_c", "?")
        crowd = result.get("crowding_label", "?")
        return f"heat: {heat}°C, crowding: {crowd}"

    if name == "get_user_history":
        count = result.get("count", 0)
        return f"{count} past trips loaded"

    if name == "calculate_leave_time":
        leave_by = result.get("leave_by", "?")
        urgency  = result.get("urgency", "?")
        return f"leave by: {leave_by}, urgency: {urgency}"

    if name == "get_traffic_conditions":
        level = result.get("traffic_level", "?")
        delay = result.get("delay_seconds") or 0
        delay_min = int(delay) // 60
        return f"traffic: {level}, delay: {delay_min}min" if delay_min else f"traffic: {level}"

    if name == "get_metro_status":
        line = result.get("line", "?")
        op   = "operational" if result.get("operational") else "not operational"
        return f"{line}: {op}"

    if name == "find_nearest_metro":
        station = result.get("nearest_station", "?")
        dist    = result.get("distance_km", "?")
        return f"nearest: {station} ({dist}km)"

    if name == "get_cost_insights":
        total = result.get("total_spent", 0)
        opps  = len(result.get("savings_opportunities", []))
        return f"spend: ₹{total}, {opps} savings found"

    return "ok"


def _build_cost_nudge(
    recommended: Optional[Dict[str, Any]],
    tool_results: List[Dict],
    origin: str,
    destination: str,
) -> Optional[str]:
    """Return a cost-comparison note when a cab is recommended but metro history exists."""
    if not recommended:
        return None
    label = (recommended.get("label") or "").lower()
    if "cab" not in label:
        return None

    cab_cost = recommended.get("total_cost_rupees")
    if not cab_cost:
        return None

    # Extract trip history from tool results
    past_trips: List[Dict] = []
    for tr in tool_results:
        if tr.get("name") == "get_user_history":
            past_trips = tr.get("result", {}).get("trips", [])
            break

    if not past_trips:
        return None

    o_key = _route_key(origin)
    d_key = _route_key(destination)

    for trip in past_trips:
        if trip.get("mode") not in ("metro", "metro_hybrid", "transit"):
            continue
        if _route_key(trip.get("origin", "")) != o_key:
            continue
        if _route_key(trip.get("destination", "")) != d_key:
            continue
        metro_cost = trip.get("cost_inr")
        if metro_cost:
            return (
                f"Note: You've taken metro on this route before (₹{metro_cost}). "
                f"Today's cab option costs ₹{cab_cost}."
            )

    return None
