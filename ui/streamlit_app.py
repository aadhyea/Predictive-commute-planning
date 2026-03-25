"""
Delhi Commute Agent — Streamlit UI
Run: streamlit run ui/streamlit_app.py
"""

import asyncio
import concurrent.futures
import sys
import os
from datetime import datetime, date, time, timedelta
from typing import Any, Dict, List, Optional

# Make sure project root is on path when running from ui/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project root
GTFS_PATH = os.path.join(BASE_DIR, "DMRC-GTFS")
stops_path = os.path.join(GTFS_PATH, "stops.txt")

import folium
import googlemaps.convert
import streamlit as st
from streamlit_folium import st_folium
from streamlit_searchbox import st_searchbox

# ── Page config (must be first Streamlit call) ──────────────────────────────
st.set_page_config(
    page_title="Delhi Commute Agent",
    page_icon="🚇",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS ─────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* ── General ── */
.main .block-container { padding-top: 1.5rem; }
h1 { font-size: 2rem !important; }

/* ── Urgency badges ── */
.badge {
    display: inline-block;
    padding: 4px 14px;
    border-radius: 20px;
    font-weight: 700;
    font-size: 0.85rem;
    letter-spacing: 0.05em;
}
.badge-low      { background:#d4edda; color:#155724; }
.badge-medium   { background:#fff3cd; color:#856404; }
.badge-high     { background:#ffe0b2; color:#b34800; }
.badge-critical { background:#f8d7da; color:#721c24; }

/* ── Route cards ── */
.route-card {
    border: 1px solid #dee2e6;
    border-radius: 10px;
    padding: 16px;
    margin-bottom: 12px;
    background: #ffffff;
    color: #212529 !important;
}
.route-card-best {
    border: 2px solid #0d6efd;
    border-radius: 10px;
    padding: 16px;
    margin-bottom: 12px;
    background: #f0f5ff;
    color: #212529 !important;
}
.route-label {
    font-size: 1.1rem;
    font-weight: 700;
    color: #212529 !important;
    margin-bottom: 8px;
}

/* ── Metric text — inherit from Streamlit theme ── */
[data-testid="stMetricValue"]  { font-size: 1.25rem !important; font-weight: 700 !important; }
[data-testid="stMetricLabel"]  { font-size: 0.8rem !important; opacity: 0.75; }


/* ── Chat ── */
.chat-user  { background:#e9ecef; border-radius:12px 12px 2px 12px; padding:10px 14px; margin:6px 0; text-align:right; }
.chat-agent { background:#f0f5ff; border-radius:12px 12px 12px 2px; padding:10px 14px; margin:6px 0; }
</style>
""", unsafe_allow_html=True)


# ── Async helper ────────────────────────────────────────────────────────────
def run_async(coro):
    """Run an async coroutine from synchronous Streamlit code."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result(timeout=120)
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


# ── Cached singletons ────────────────────────────────────────────────────────
@st.cache_resource(show_spinner=False)
def get_agent():
    from agent.core import CommuteAgent
    return CommuteAgent()


@st.cache_resource(show_spinner=False)
def get_maps_client():
    from maps.google_maps_client import maps_client
    return maps_client


# ── Formatting helpers ───────────────────────────────────────────────────────


def mode_icon(mode: str) -> str:
    return {"metro": "🚇", "walk": "🚶", "cab": "🚕",
            "bus": "🚌", "transit": "🚌", "auto": "🛺",
            "bike": "🏍️", "2wheeler": "🏍️"}.get(mode.lower(), "🔹")


def fmt_duration(mins: int) -> str:
    if mins < 60:
        return f"{mins} min"
    h, m = divmod(mins, 60)
    return f"{h}h {m}m" if m else f"{h}h"


# City centre coords for autocomplete location bias (lat, lng, radius_m)
_CITY_COORDS = {
    "Delhi / NCR":  (28.6139, 77.2090, 60000),
    "Mumbai":       (19.0760, 72.8777, 45000),
    "Bengaluru":    (12.9716, 77.5946, 45000),
    "Chennai":      (13.0827, 80.2707, 40000),
    "Hyderabad":    (17.3850, 78.4867, 45000),
    "Kolkata":      (22.5726, 88.3639, 40000),
    "Pune":         (18.5204, 73.8567, 40000),
    "Ahmedabad":    (23.0225, 72.5714, 40000),
}


def search_places_autocomplete(query: str) -> list:
    """Sync wrapper for Google Places Autocomplete — used by st_searchbox.
    Biases results to the city selected in the sidebar."""
    if not query or len(query) < 2:
        return []
    try:
        city = st.session_state.get("city_override", "Auto-detect")
        coords = _CITY_COORDS.get(city)
        if coords:
            lat, lng, radius = coords
            return run_async(get_maps_client().autocomplete_places(query, lat=lat, lng=lng, radius=radius))
        return run_async(get_maps_client().autocomplete_places(query))
    except Exception:
        return []


def extract_city_from_geo(geo: dict) -> str:
    """Extract city name from geocode result address_components."""
    if not geo:
        return "unknown"
    for comp in geo.get("address_components", []):
        if "locality" in comp["types"]:
            return comp["long_name"]
        if "administrative_area_level_2" in comp["types"]:
            return comp["long_name"]
    return "unknown"


# ── Map builder ──────────────────────────────────────────────────────────────
def geocode_endpoints(origin: str, destination: str):
    """Geocode origin + destination once. Returns (o_geo, d_geo) dicts or (None, None)."""
    try:
        maps = get_maps_client()
        import asyncio as _asyncio
        async def _both():
            import asyncio
            return await asyncio.gather(maps.geocode(origin), maps.geocode(destination))
        return run_async(_both())
    except Exception:
        return None, None


def build_route_map(
    o_lat: float, o_lng: float,
    d_lat: float, d_lng: float,
    origin_label: str = "Origin",
    destination_label: str = "Destination",
    overview_polyline: str = "",
) -> folium.Map:
    center_lat = (o_lat + d_lat) / 2
    center_lng = (o_lng + d_lng) / 2
    m = folium.Map(location=[center_lat, center_lng], zoom_start=12,
                   tiles="CartoDB positron")

    folium.Marker(
        [o_lat, o_lng],
        popup=f"<b>Origin</b><br>{origin_label}",
        tooltip="Origin",
        icon=folium.Icon(color="green", icon="home", prefix="fa"),
    ).add_to(m)

    folium.Marker(
        [d_lat, d_lng],
        popup=f"<b>Destination</b><br>{destination_label}",
        tooltip="Destination",
        icon=folium.Icon(color="red", icon="briefcase", prefix="fa"),
    ).add_to(m)

    if overview_polyline:
        try:
            decoded = googlemaps.convert.decode_polyline(overview_polyline)
            points  = [(p["lat"], p["lng"]) for p in decoded]
            folium.PolyLine(points, color="#0d6efd", weight=5, opacity=0.8).add_to(m)
        except Exception:
            overview_polyline = ""   # fall through to dashed line

    if not overview_polyline:
        # Fallback: dashed straight line
        folium.PolyLine(
            [[o_lat, o_lng], [d_lat, d_lng]],
            color="#0d6efd", weight=3, opacity=0.5, dash_array="8",
        ).add_to(m)

    return m


# ── QR code helper ───────────────────────────────────────────────────────────
def _url_to_qr_bytes(url: str) -> bytes:
    """Return PNG bytes of a QR code for the given URL."""
    import io
    import qrcode
    qr = qrcode.QRCode(box_size=4, border=2,
                       error_correction=qrcode.constants.ERROR_CORRECT_L)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── Cab deep-link builders ────────────────────────────────────────────────────
def _build_uber_link(origin: str, dest: str, o_geo=None, d_geo=None) -> str:
    """Web link for the button (opens uber.com)."""
    from urllib.parse import quote
    url = "https://m.uber.com/ul/?action=setPickup"
    if o_geo:
        url += f"&pickup[latitude]={o_geo['lat']}&pickup[longitude]={o_geo['lng']}"
    url += f"&pickup[nickname]={quote(origin)}&pickup[formatted_address]={quote(origin)}"
    if d_geo:
        url += f"&dropoff[latitude]={d_geo['lat']}&dropoff[longitude]={d_geo['lng']}"
    url += f"&dropoff[nickname]={quote(dest)}&dropoff[formatted_address]={quote(dest)}"
    return url


def _build_uber_app_link(origin: str, dest: str, o_geo=None, d_geo=None) -> str:
    """Native app-scheme link for QR code — opens Uber app directly."""
    from urllib.parse import quote
    url = "uber://?action=setPickup"
    if o_geo:
        url += f"&pickup[latitude]={o_geo['lat']}&pickup[longitude]={o_geo['lng']}"
    url += f"&pickup[nickname]={quote(origin)}"
    if d_geo:
        url += f"&dropoff[latitude]={d_geo['lat']}&dropoff[longitude]={d_geo['lng']}"
    url += f"&dropoff[nickname]={quote(dest)}"
    return url


def _build_ola_link(origin: str, dest: str, o_geo=None, d_geo=None) -> str:
    from urllib.parse import quote
    url = "https://book.olacabs.com/?"
    if o_geo:
        url += f"pickup_lat={o_geo['lat']}&pickup_lng={o_geo['lng']}&"
    url += f"pickup_name={quote(origin)}"
    if d_geo:
        url += f"&drop_lat={d_geo['lat']}&drop_lng={d_geo['lng']}"
    url += f"&drop_name={quote(dest)}&utm_source=commuteagent"
    return url


# ── Route card renderer ───────────────────────────────────────────────────────
def render_route_card(route: Dict[str, Any], is_best: bool = False):
    # Metrics row
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("⏱ Duration",  fmt_duration(route.get("total_duration_minutes", 0)))
    c2.metric("💰 Cost",      f"₹{route.get('total_cost_rupees', 0)}")
    c3.metric("✅ On-time",   f"{round(route.get('on_time_probability', 0) * 100)}%")
    c4.metric("🔀 Transfers", route.get("num_transfers", 0))

    # Arrival time + distance
    meta_parts = []
    try:
        arr = datetime.fromisoformat(route["arrival_time"])
        meta_parts.append(f"Arrives **{arr.strftime('%H:%M')}**")
    except (KeyError, ValueError):
        pass
    dist = route.get("total_distance_km", 0)
    if dist:
        meta_parts.append(f"{dist:.1f} km")
    if meta_parts:
        st.caption("  ·  ".join(meta_parts))

    # Steps
    steps = route.get("steps", [])
    if steps:
        with st.expander("Step-by-step", expanded=is_best):
            for step in steps:
                icon = mode_icon(step.get("mode", ""))
                dur  = fmt_duration(step.get("duration_minutes", 0))
                dist_s = step.get("distance_km", 0)
                inst = step.get("instruction", "")
                line = step.get("line", "")
                line_str = f" · *{line}*" if line else ""
                cost_s = step.get("cost_rupees", 0)
                cost_str = f" · ₹{cost_s}" if cost_s else ""
                st.markdown(
                    f"{icon} **{dur}** · {dist_s:.1f} km{line_str}{cost_str}  \n"
                    f"<small style='color:#555'>{inst}</small>",
                    unsafe_allow_html=True,
                )

    # Notes
    for note in route.get("notes", []):
        st.warning(note, icon="⚠️")


# ── SIDEBAR ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚙️ Preferences")
    st.divider()

    buffer_minutes = st.slider(
        "Safety buffer (min)", min_value=5, max_value=45, value=15, step=5,
        help="Extra time you want before your arrival deadline",
    )
    prefer_comfort = st.toggle("Prefer comfort over speed", value=True)
    max_walk = st.slider(
        "Max walking (min)", min_value=5, max_value=30, value=10, step=5
    )

    st.divider()
    st.markdown("### 🌆 City")
    CITY_OPTIONS = [
        "Auto-detect", "Delhi / NCR", "Mumbai", "Bengaluru",
        "Chennai", "Hyderabad", "Kolkata", "Pune", "Ahmedabad",
    ]
    city_override = st.selectbox(
        "City (overrides auto-detect)",
        options=CITY_OPTIONS,
        index=0,
        help="Auto-detect reads the city from your origin address. Override if detection is wrong.",
    )
    # Persist so it survives reruns without being inside the form
    st.session_state["city_override"] = city_override

    st.divider()
    st.markdown("### 📍 Quick Fill")
    def _quick_fill(origin: str, dest: str):
        st.session_state["origin_input"]      = origin
        st.session_state["destination_input"] = dest
        # Clear searchbox internal state so they re-render with new defaults
        st.session_state.pop("origin_searchbox", None)
        st.session_state.pop("dest_searchbox", None)

    if st.button("🏠 Delhi: Rajiv Chowk → Cyber City", use_container_width=True):
        _quick_fill("Rajiv Chowk Metro Station, Delhi", "Cyber City, Gurugram")
    if st.button("📍 Delhi: CP → Noida Sector 62", use_container_width=True):
        _quick_fill("Connaught Place, New Delhi", "Noida Sector 62, Uttar Pradesh")
    if st.button("✈️ Bangalore: Indiranagar → Whitefield", use_container_width=True):
        _quick_fill("Indiranagar, Bengaluru", "Whitefield, Bengaluru")
    if st.button("🌊 Mumbai: Andheri → Bandra Kurla Complex", use_container_width=True):
        _quick_fill("Andheri Station, Mumbai", "Bandra Kurla Complex, Mumbai")

    st.divider()
    st.caption("Delhi Commute Agent · Powered by Gemini 2.5 Flash")


# ── HEADER ────────────────────────────────────────────────────────────────────
st.markdown("# 🚇 India Commute Agent")
st.markdown("*AI-powered commute planning across Indian cities — real-time routes, weather, and smart timing*")
st.divider()


# ── TABS ─────────────────────────────────────────────────────────────────────
tab_plan, tab_chat = st.tabs(["🗺️  Plan Commute", "💬  Chat with Agent"])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — PLAN COMMUTE
# ══════════════════════════════════════════════════════════════════════════════
with tab_plan:

    # ── Origin / Destination searchboxes (must be outside st.form for autocomplete) ──
    col_o, col_d = st.columns(2)
    with col_o:
        st.markdown("**🏠 From**")
        origin = st_searchbox(
            search_places_autocomplete,
            placeholder="Start typing your origin…",
            default=st.session_state.get("origin_input", "Rajiv Chowk Metro Station, Delhi"),
            key="origin_searchbox",
            clear_on_submit=False,
        )
    with col_d:
        st.markdown("**🏢 To**")
        destination = st_searchbox(
            search_places_autocomplete,
            placeholder="Start typing your destination…",
            default=st.session_state.get("destination_input", "Cyber City, Gurugram"),
            key="dest_searchbox",
            clear_on_submit=False,
        )

    # ── Remaining inputs + submit (inside form to batch the submit action) ────
    with st.form("plan_form"):
        col_date, col_time, col_extra = st.columns([1, 1, 2])

        with col_date:
            travel_date = st.date_input("📅 Date", value=date.today())
        with col_time:
            arrival_time = st.time_input("⏰ Arrive by", value=time(9, 30))
        with col_extra:
            extra_context = st.text_input(
                "💬 Extra context (optional)",
                placeholder="e.g. carrying heavy luggage, it's raining outside…",
            )

        submitted = st.form_submit_button(
            "🔍  Plan My Commute", use_container_width=True, type="primary"
        )

    # ── Run agent on submit ───────────────────────────────────────────────────
    if submitted:
        # st_searchbox returns None until user selects; fall back to session default
        origin      = origin      or st.session_state.get("origin_input", "")
        destination = destination or st.session_state.get("destination_input", "")
        if not (origin or "").strip() or not (destination or "").strip():
            st.error("Please enter both origin and destination.")
        else:
            required_arrival = datetime.combine(travel_date, arrival_time).isoformat()

            user_prefs = {
                "buffer_minutes":         buffer_minutes,
                "prefer_comfort_over_speed": prefer_comfort,
                "max_walking_minutes":    max_walk,
            }

            with st.spinner("🤖 Agent is planning your commute…"):
                try:
                    result = run_async(
                        get_agent().plan_commute(
                            origin=           origin,
                            destination=      destination,
                            required_arrival= required_arrival,
                            user_prefs=       user_prefs,
                            extra_context=    extra_context or None,
                        )
                    )
                    st.session_state["plan_result"]      = result
                    st.session_state["plan_origin"]      = origin
                    st.session_state["plan_destination"] = destination
                    # Geocode once and cache for maps + city detection
                    o_geo, d_geo = geocode_endpoints(origin, destination)
                    st.session_state["plan_o_geo"] = o_geo
                    st.session_state["plan_d_geo"] = d_geo
                    # Detect city from geocode result
                    auto_city = extract_city_from_geo(o_geo)
                    chosen_city = st.session_state.get("city_override", "Auto-detect")
                    st.session_state["detected_city"] = (
                        chosen_city if chosen_city != "Auto-detect" else auto_city
                    )
                except Exception as e:
                    st.error(f"Agent error: {e}")
                    st.session_state.pop("plan_result", None)

    # ── Display results ───────────────────────────────────────────────────────
    if result := st.session_state.get("plan_result"):
        st.divider()

        # City detection banner
        detected_city = st.session_state.get("detected_city", "unknown")
        city_override_val = st.session_state.get("city_override", "Auto-detect")
        if detected_city and detected_city != "unknown":
            source_note = " (manually selected)" if city_override_val != "Auto-detect" else " (auto-detected)"
            st.info(f"📍 **City: {detected_city}**{source_note} · Routing strategy selected accordingly. Wrong city? Change it in the sidebar.", icon=None)

        # Row 1: Weather | Leave-by | Urgency
        r1c1, r1c2, r1c3 = st.columns([2, 2, 1])

        with r1c1:
            wx   = result.weather_summary or "Weather data not available."
            risk = result.risk_score
            wx_icon = "🌤️" if risk < 0.3 else ("🌧️" if risk < 0.6 else "⛈️")
            if risk < 0.3:
                st.success(f"{wx_icon} **Weather** — {wx}")
            elif risk < 0.6:
                st.warning(f"{wx_icon} **Weather** — {wx}")
            else:
                st.error(f"{wx_icon} **Weather** — {wx}")

        with r1c2:
            if result.leave_by:
                st.metric(
                    label="⏰ Leave by",
                    value=result.leave_by,
                    help=f"Includes your {buffer_minutes} min safety buffer",
                )
            else:
                st.info("See agent recommendation for departure time.")

        with r1c3:
            urgency_icons = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🟠", "CRITICAL": "🔴"}
            icon = urgency_icons.get(result.urgency.upper(), "⚪")
            st.metric(
                label="Urgency",
                value=f"{icon} {result.urgency}",
                help=f"Risk score: {result.risk_score:.0%}",
            )

        st.divider()

        # Row 2: Agent recommendation — summary + expandable full text
        st.markdown("### 🤖 Agent Recommendation")

        # Show only the first paragraph/block as the headline summary
        paragraphs = [p.strip() for p in result.explanation.split("\n\n") if p.strip()]
        if paragraphs:
            st.info(paragraphs[0])          # first paragraph prominent
            if len(paragraphs) > 1:
                with st.expander("Full analysis", expanded=False):
                    st.markdown("\n\n".join(paragraphs[1:]))
        else:
            st.info(result.explanation[:300] + ("…" if len(result.explanation) > 300 else ""))

        # Disruptions
        for d in result.disruptions:
            st.warning(d, icon="🚨")

        # Tools used (debug expandable)
        if result.tool_calls_made:
            with st.expander("🔧 Tools used by agent", expanded=False):
                st.write(" → ".join(result.tool_calls_made))

        st.divider()

        # Row 3: Route options in tabs (full width per route)
        st.markdown("### 🛣️ Route Options")

        routes = []
        if result.recommended_route:
            routes.append(result.recommended_route)
        routes.extend(result.alternative_routes or [])

        if routes:
            tab_labels = []
            for i, r in enumerate(routes[:3]):
                label      = r.get("label", f"Route {i+1}")
                dur        = fmt_duration(r.get("total_duration_minutes", 0))
                cost       = r.get("total_cost_rupees", 0)
                city_badge = f" [{r['city']}]" if r.get("city") and r["city"].lower() not in ("delhi", "new delhi", "unknown") else ""
                tab_labels.append(f"{'⭐ ' if i==0 else ''}{label}{city_badge}  ·  {dur}  ·  ₹{cost}")

            o_geo = st.session_state.get("plan_o_geo")
            d_geo = st.session_state.get("plan_d_geo")

            route_tabs = st.tabs(tab_labels)
            for i, (tab, route, is_best) in enumerate(zip(route_tabs, routes[:3], [True, False, False])):
                with tab:
                    render_route_card(route, is_best=is_best)

                    # ── Route map (per tab) ───────────────────────────────
                    if o_geo and d_geo:
                        polyline = route.get("overview_polyline", "")
                        fmap = build_route_map(
                            o_geo["lat"], o_geo["lng"],
                            d_geo["lat"], d_geo["lng"],
                            origin_label=st.session_state.get("plan_origin", ""),
                            destination_label=st.session_state.get("plan_destination", ""),
                            overview_polyline=polyline,
                        )
                        st_folium(fmap, use_container_width=True, height=380,
                                  returned_objects=[], key=f"route_map_{i}")

                    # ── Cab booking buttons + QR codes ───────────────────
                    route_label = route.get("label", "").lower()
                    if "cab" in route_label:
                        est_cost = route.get("total_cost_rupees", 0)
                        st.markdown(f"**🚕 Book this ride** · Estimated ₹{est_cost}")
                        origin_txt = st.session_state.get("plan_origin", "")
                        dest_txt   = st.session_state.get("plan_destination", "")
                        uber_url = _build_uber_link(origin_txt, dest_txt, o_geo, d_geo)
                        ola_url  = _build_ola_link(origin_txt, dest_txt, o_geo, d_geo)

                        uber_col, ola_col, note_col = st.columns([1, 1, 2])
                        with uber_col:
                            st.link_button("Open in Uber 🟡", uber_url, use_container_width=True)
                            try:
                                uber_app_url = _build_uber_app_link(origin_txt, dest_txt, o_geo, d_geo)
                                st.image(_url_to_qr_bytes(uber_app_url), width=120,
                                         caption="Scan to open Uber app")
                            except Exception:
                                pass
                        with ola_col:
                            st.link_button("Open in Ola 🟢", ola_url, use_container_width=True)
                            try:
                                st.image(_url_to_qr_bytes(ola_url), width=120,
                                         caption="Scan to open Ola app")
                            except Exception:
                                pass
                        with note_col:
                            st.caption("📱 Scan the QR code with your phone to open the app with source & destination pre-filled.")
        else:
            st.info("No route data returned — see agent explanation above.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — CHAT
# ══════════════════════════════════════════════════════════════════════════════
with tab_chat:
    st.markdown("Ask anything about your commute — the agent has access to real-time weather, routes, and metro data.")

    # Initialise history
    if "chat_history" not in st.session_state:
        st.session_state["chat_history"] = []

    # Display existing messages
    for msg in st.session_state["chat_history"]:
        role = msg["role"]
        text = msg["text"]
        if role == "user":
            st.markdown(f'<div class="chat-user">👤 {text}</div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="chat-agent">🤖 {text}</div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # Input
    with st.form("chat_form", clear_on_submit=True):
        chat_col1, chat_col2 = st.columns([5, 1])
        with chat_col1:
            user_input = st.text_input(
                "Message",
                placeholder="e.g. What's the fastest way from Noida to Aerocity right now?",
                label_visibility="collapsed",
            )
        with chat_col2:
            chat_submitted = st.form_submit_button("Send", use_container_width=True, type="primary")

    if chat_submitted and user_input.strip():
        # Build history for agent (only last 10 turns to keep context short)
        history = st.session_state["chat_history"][-10:]

        st.session_state["chat_history"].append({"role": "user", "text": user_input})

        with st.spinner("Agent is thinking…"):
            try:
                reply = run_async(
                    get_agent().chat(
                        user_message=user_input,
                        history=history,
                    )
                )
            except Exception as e:
                reply = f"Error: {e}"

        st.session_state["chat_history"].append({"role": "agent", "text": reply})
        st.rerun()

    if st.button("🗑️  Clear chat", use_container_width=False):
        st.session_state["chat_history"] = []
        st.rerun()
