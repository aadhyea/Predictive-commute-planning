"""
Sherpa — Streamlit UI
Run: streamlit run ui/streamlit_app.py
"""

import asyncio
import base64
import concurrent.futures
import sys
import os
from datetime import datetime, date, time, timedelta
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
load_dotenv()

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

from auth.supabase_auth import (
    get_current_user, is_logged_in, sign_in_magic_link,
    sign_in_google, sign_out, handle_auth_callback,
)
from database.supabase_client import get_client, supabase

# ── Bootstrap (before any rendering) ─────────────────────────────────────────
handle_auth_callback()
if "page" not in st.session_state:
    st.session_state["page"] = "login"
# Auto-redirect if a session was resolved by handle_auth_callback
if is_logged_in() and st.session_state["page"] == "login":
    st.session_state["page"] = "app"


# ── Trip logging ──────────────────────────────────────────────────────────────
def _mode_from_label(label: str) -> str:
    """Derive a clean mode key from the route label string."""
    l = label.lower()
    if "cab" in l:
        return "cab"
    if "metro" in l:
        return "metro_hybrid"
    return "transit"


def _log_trip_background(access_token: str, user_id: str, result, origin: str, destination: str):
    """Fire-and-forget trip logging — runs in a daemon thread."""
    import threading

    def _run():
        try:
            route = result.recommended_route or {}
            get_client().log_trip(
                access_token= access_token,
                user_id=      user_id,
                origin=       origin,
                destination=  destination,
                city=         route.get("city"),
                route_label=  route.get("label", ""),
                mode=         _mode_from_label(route.get("label", "")),
                duration_min= route.get("total_duration_minutes", 0),
                cost_inr=     int(route.get("total_cost_rupees") or 0),
            )
        except Exception:
            pass  # Never crash the UI for a logging failure

    threading.Thread(target=_run, daemon=True).start()


# ── Auth gate helper ──────────────────────────────────────────────────────────
def require_auth(feature_name: str = "this feature") -> bool:
    """
    Returns True if the user is signed in.
    If not, shows a polite inline prompt instead of the feature.
    """
    if is_logged_in():
        return True
    st.info(
        f"Sign in to use {feature_name}. "
        "You can still plan commutes as a guest.",
        icon="🔒",
    )
    return False


# ── Async helper ──────────────────────────────────────────────────────────────
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


# ── Cached singletons ─────────────────────────────────────────────────────────
@st.cache_resource(show_spinner=False)
def get_agent():
    from agent.core import CommuteAgent
    return CommuteAgent()


@st.cache_resource(show_spinner=False)
def get_maps_client():
    from maps.google_maps_client import maps_client
    return maps_client


# ── Formatting helpers ────────────────────────────────────────────────────────


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


# ── Map builder ───────────────────────────────────────────────────────────────
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


# ── QR code helper ────────────────────────────────────────────────────────────
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


def _build_uber_qr_link(origin: str, dest: str, o_geo=None, d_geo=None) -> str:
    """Universal Link for Uber QR code — opens app if installed, mobile web otherwise."""
    return _build_uber_link(origin, dest, o_geo, d_geo)


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
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("⏱ Duration",  fmt_duration(route.get("total_duration_minutes", 0)))
    c2.metric("💰 Cost",      f"₹{route.get('total_cost_rupees', 0)}")
    c3.metric("✅ On-time",   f"{round(route.get('on_time_probability', 0) * 100)}%")
    c4.metric("🔀 Transfers", route.get("num_transfers", 0))

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

    steps = route.get("steps", [])
    if steps:
        with st.expander("Step-by-step", expanded=is_best):
            for step in steps:
                icon   = mode_icon(step.get("mode", ""))
                dur    = fmt_duration(step.get("duration_minutes", 0))
                dist_s = step.get("distance_km", 0)
                inst   = step.get("instruction", "")
                line   = step.get("line", "")
                line_str = f" · *{line}*" if line else ""
                cost_s   = step.get("cost_rupees", 0)
                cost_str = f" · ₹{cost_s}" if cost_s else ""
                st.markdown(
                    f"{icon} **{dur}** · {dist_s:.1f} km{line_str}{cost_str}  \n"
                    f"<small style='color:#555'>{inst}</small>",
                    unsafe_allow_html=True,
                )

    for note in route.get("notes", []):
        st.warning(note, icon="⚠️")


# ══════════════════════════════════════════════════════════════════════════════
# LOGIN PAGE
# ══════════════════════════════════════════════════════════════════════════════
def render_login_page():
    st.set_page_config(
        page_title="Sherpa",
        page_icon="🧭",
        layout="wide",
    )

    # Read SVG hero at runtime so the rendered HTML gets the raw SVG block inlined
    _svg_path = os.path.join(os.path.dirname(__file__), "assets", "commute_landing_hero.svg")
    with open(_svg_path, encoding="utf-8") as _f:
        _svg = _f.read()
    # Ensure the svg tag fills the panel with cover behaviour
    _svg = _svg.replace("<svg ", '<svg height="100%" preserveAspectRatio="xMidYMid slice" ', 1)

    # ── Chrome hiding + login page CSS ────────────────────────────────────────
    st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Great+Vibes&display=swap');



.great-vibes-regular {
  font-family: "Great Vibes", cursive !important;
  font-weight: 400 !important;
  font-style: normal !important;
}

/* Global font override for the login page */
.stApp, .stApp * {
  font-family: 'Plus Jakarta Sans', sans-serif !important;
}

/* Hide all Streamlit chrome */
#MainMenu, footer, header { display: none !important; }
[data-testid="stToolbar"] { display: none !important; }

/* App background = right panel dark colour */
.stApp { background: #0F1F4A !important; }

/* Right panel: occupies the remaining 45vw to the right of the fixed left panel */
section[data-testid="stMain"],
section.main {
  position: fixed !important;
  right: 0 !important;
  top: 0 !important;
  width: 45vw !important;
  height: 100vh !important;
  overflow-y: auto !important;
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
  padding: 60px 56px !important;
  background: #0F1F4A !important;
  box-sizing: border-box !important;
}
section[data-testid="stMain"] .block-container,
section.main .block-container {
  max-width: 380px !important;
  width: 100% !important;
  padding: 0 !important;
  margin: 0 !important;
}

/* ── Left panel (fixed) ── */
.login-left-panel {
  position: fixed;
  top: 0; left: 0;
  width: 55vw;
  height: 100vh;
  overflow: hidden;
  z-index: 100;
}
.login-left-panel svg {
  width: 100%;
  height: 100%;
  display: block;
}
.login-brand-top {
  position: absolute;
  top: 32px; left: 40px;
  font-size: 1.1rem;
  font-weight: 800;
  color: #FFFFFF;
  font-family: 'Plus Jakarta Sans', sans-serif;
  letter-spacing: -0.02em;
  display: flex;
  align-items: center;
  gap: 8px;
  z-index: 10;
}
.login-left-overlay {
  position: absolute;
  bottom: 0; left: 0; right: 0;
  padding: 40px 48px;
  background: linear-gradient(to top, rgba(11,27,62,0.92) 0%, transparent 100%);
}
.login-tagline {
  font-size: 0.95rem;
  color: rgba(255,255,255,0.6);
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-weight: 400;
}

/* ── Right panel typography ── */
.login-heading {
  font-size: 2rem;
  font-weight: 800;
  color: #FFFFFF;
  font-family: 'Plus Jakarta Sans', sans-serif;
  letter-spacing: -0.03em;
  margin-bottom: 6px;
}
.login-subheading {
  font-size: 0.95rem;
  color: rgba(255,255,255,0.5);
  font-family: 'Plus Jakarta Sans', sans-serif;
  margin-bottom: 36px;
}
.login-label {
  font-size: 0.78rem;
  font-weight: 600;
  color: rgba(255,255,255,0.5);
  font-family: 'Plus Jakarta Sans', sans-serif;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  margin-bottom: 8px;
  display: block;
}
.login-divider {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 24px 0;
  color: rgba(255,255,255,0.25);
  font-size: 0.82rem;
  font-family: 'Plus Jakarta Sans', sans-serif;
}
.login-divider::before,
.login-divider::after {
  content: '';
  flex: 1;
  height: 1px;
  background: rgba(255,255,255,0.12);
}
.login-guest-row {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 32px;
}
.login-guest-link {
  font-size: 0.82rem;
  color: rgba(255,255,255,0.45);
  text-decoration: none;
  cursor: pointer;
  transition: color 0.15s ease;
}
.login-guest-link:hover { color: rgba(255,255,255,0.8); }
/* Guest Streamlit button hidden off-screen — clicked via JS from top link */
[data-testid="stBaseButton-secondary"] {
  position: fixed !important;
  left: -9999px !important;
  opacity: 0 !important;
}

/* ── Widget overrides for dark login theme ── */
.stTextInput > div > div > input {
  background: rgba(255,255,255,0.06) !important;
  border: 1px solid rgba(255,255,255,0.15) !important;
  border-radius: 10px !important;
  color: #FFFFFF !important;
  font-size: 0.95rem !important;
  padding: 12px 16px !important;
}
.stTextInput > div > div > input::placeholder {
  color: rgba(255,255,255,0.35) !important;
}
.stTextInput > div > div > input:focus {
  border-color: #00C2FF !important;
  box-shadow: 0 0 0 3px rgba(0,194,255,0.15) !important;
}
.stTextInput label { display: none !important; }

/* Primary button — steel blue */
.stButton > button[kind="primary"],
button[data-testid="baseButton-primary"] {
  background: #3090C7 !important;
  border: none !important;
  color: #FFFFFF !important;
  font-weight: 800 !important;
  font-size: 0.5rem !important;
  letter-spacing: 0.1em !important;
  text-transform: uppercase !important;
  border-radius: 10px !important;
  padding: 12px !important;
  transition: all 0.2s ease !important;
  font-family: 'Plus Jakarta Sans', sans-serif !important;
}
.stButton > button[kind="primary"]:hover,
button[data-testid="baseButton-primary"]:hover {
  background: #4aa8db !important;
  transform: translateY(-1px) !important;
}

/* Secondary / ghost button */
.stButton > button[kind="secondary"],
button[data-testid="baseButton-secondary"] {
  background: transparent !important;
  border: 1px solid rgba(255,255,255,0.2) !important;
  color: rgba(255,255,255,0.7) !important;
  border-radius: 10px !important;
  font-weight: 500 !important;
  font-family: 'Plus Jakarta Sans', sans-serif !important;
  transition: all 0.2s ease !important;
}
.stButton > button[kind="secondary"]:hover,
button[data-testid="baseButton-secondary"]:hover {
  border-color: rgba(255,255,255,0.45) !important;
  color: #FFFFFF !important;
  background: rgba(255,255,255,0.05) !important;
}

/* Link button (Google) */
.stLinkButton a {
  background: transparent !important;
  border: 1px solid rgba(255,255,255,0.2) !important;
  color: rgba(255,255,255,0.85) !important;
  border-radius: 10px !important;
  font-weight: 800 !important;
  font-size: 0.5rem !important;
  letter-spacing: 0.1em !important;
  text-transform: uppercase !important;
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
  padding: 11px !important;
  width: 100% !important;
  font-family: 'Plus Jakarta Sans', sans-serif !important;
  transition: all 0.2s ease !important;
}
.stLinkButton a:hover {
  border-color: rgba(255,255,255,0.4) !important;
  color: #FFFFFF !important;
  background: rgba(255,255,255,0.05) !important;
}
</style>
""", unsafe_allow_html=True)

    # ── Left panel — fixed, full-viewport SVG hero ─────────────────────────────
    st.markdown(f"""
<div class="login-left-panel">
  <div class="login-brand-top">🧭 <span class="great-vibes-regular" style="font-size:1.5rem">Sherpa</span></div>
  {_svg}
</div>
""", unsafe_allow_html=True)

    # ── Right panel — guest shortcut at top ───────────────────────────────────
    st.markdown("""
<div class="login-guest-row">
  <span class="login-guest-link"
    onclick="(function(){var btns=window.parent.document.querySelectorAll('button');for(var b of btns){if(b.innerText.includes('guest')){b.click();break;}}})()">
    Continue as guest →
  </span>
</div>
<div class="login-heading">Welcome !</div>
<div class="login-subheading">Plan smarter. Commute better.</div>
<span class="login-label">Sign in with email</span>
""", unsafe_allow_html=True)

    # ── Email magic link ───────────────────────────────────────────────────────
    email = st.text_input("Email address", placeholder="you@example.com", key="login_email", label_visibility="collapsed")
    if st.button("SEND MAGIC LINK →", use_container_width=True, type="primary"):
        if sign_in_magic_link(email):
            st.success("Check your inbox for a sign-in link.")
        else:
            st.error("Could not send link — check the email address.")

    st.markdown('<div class="login-divider">or</div>', unsafe_allow_html=True)

    # ── Google OAuth ───────────────────────────────────────────────────────────
    google_url = sign_in_google()
    if google_url:
        st.link_button("CONTINUE WITH GOOGLE", google_url, use_container_width=True)
    else:
        st.error("Google sign-in is unavailable right now.")

    # ── Guest access — hidden button clicked via JS from the top link ────────
    if st.button("Continue as guest →", type="secondary", key="guest_btn"):
        st.session_state["page"] = "app"
        st.session_state["user"] = None
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# MAIN APP
# ══════════════════════════════════════════════════════════════════════════════
def render_app():
    st.set_page_config(
        page_title="Sherpa",
        page_icon="🧭",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    # ── CSS ───────────────────────────────────────────────────────────────────
    _css_path = os.path.join(os.path.dirname(__file__), "styles.css")
    with open(_css_path, encoding="utf-8") as _f:
        st.markdown(f"<style>{_f.read()}</style>", unsafe_allow_html=True)

    # ── Main page background image (sidebar excluded) ─────────────────────────
    _img_path = os.path.join(os.path.dirname(__file__), "assets", "image.png")
    with open(_img_path, "rb") as _img_f:
        _img_b64 = base64.b64encode(_img_f.read()).decode()
    st.markdown(f"""
    <style>
    [data-testid="stMain"] {{
        background-image: url("data:image/png;base64,{_img_b64}") !important;
        background-size: cover !important;
        background-position: center !important;
        background-attachment: fixed !important;
    }}
    [data-testid="stMain"]::before {{
        content: "";
        position: fixed;
        inset: 0;
        background: rgba(6, 15, 30, 0.83);
        pointer-events: none;
        z-index: 0;
    }}
    [data-testid="stMain"] > div {{
        position: relative;
        z-index: 1;
    }}
    </style>
    """, unsafe_allow_html=True)

    # ── Welcome bar ───────────────────────────────────────────────────────────
    user = get_current_user()
    _wb_spacer, _wb_right = st.columns([3, 2])
    if user:
        _full_name = (getattr(user, "user_metadata", None) or {}).get("full_name")
        _display = _full_name or getattr(user, "email", "")
        _first = _display.split()[0] if _display else _display
        with _wb_right:
            _wc_col, _so_col = st.columns([3, 2])
            with _wc_col:
                st.markdown(f"""
                <div class="welcome-chip">
                  <span class="wc-dot"></span>
                  <span class="wc-name">Welcome, {_first}</span>
                </div>
                """, unsafe_allow_html=True)
            with _so_col:
                if st.button("⏻  Sign out", key="wb_signout", use_container_width=True):
                    sign_out()
                    st.session_state["page"] = "login"
                    st.rerun()
    else:
        with _wb_right:
            _wc_col, _si_col = st.columns([3, 2])
            with _wc_col:
                st.markdown("""
                <div class="welcome-chip">
                  <span class="wc-dot wc-dot-guest"></span>
                  <span class="wc-name" style="color:var(--text-muted)!important">Guest</span>
                </div>
                """, unsafe_allow_html=True)
            with _si_col:
                if st.button("→  Sign in", key="wb_signin", use_container_width=True):
                    st.session_state["page"] = "login"
                    st.rerun()

    # ── SIDEBAR ───────────────────────────────────────────────────────────────
    with st.sidebar:

        # ── Brand header ──────────────────────────────────────────────
        st.markdown("""
        <div class="sidebar-brand">
          <div class="sidebar-brand-text">
            <div class="sidebar-brand-name">🧭 Sherpa</div>
          </div>
        </div>
        """, unsafe_allow_html=True)

        # ── Preferences section ───────────────────────────────────────
        with st.expander("⚙️ Preferences", expanded=True):
            buffer_minutes = st.slider(
                "Safety buffer", min_value=5, max_value=45, value=15, step=5,
                help="Extra time you want before your arrival deadline",
                format="%d min",
            )
            prefer_comfort = st.toggle("Prefer comfort over speed", value=True)
            max_walk = st.slider(
                "Max walking", min_value=5, max_value=30, value=10, step=5,
                format="%d min",
            )

        # City is always auto-detected from origin address
        if "city_override" not in st.session_state:
            st.session_state["city_override"] = "Auto-detect"

        # ── Quick Fill section ────────────────────────────────────────
        def _quick_fill(origin: str, dest: str):
            import time as _time
            st.session_state["origin_input"]      = origin
            st.session_state["destination_input"] = dest
            # _origin_fill / _dest_fill drive default_searchterm in the st_searchbox
            # calls below — that prop is what React actually shows in the input on mount.
            st.session_state["_origin_fill"] = origin
            st.session_state["_dest_fill"]   = dest
            # Changing key_react forces React to remount, picking up default_searchterm.
            _t = _time.time()
            st.session_state["origin_searchbox"] = {
                "result": origin, "search": origin,
                "options_js": [], "key_react": f"origin_searchbox_react_{_t}",
            }
            st.session_state["dest_searchbox"] = {
                "result": dest, "search": dest,
                "options_js": [], "key_react": f"dest_searchbox_react_{_t}",
            }

        with st.expander("⚡ Quick Fill", expanded=False):
            if st.button("🏠 Delhi: Rajiv Chowk → Cyber City", use_container_width=True):
                _quick_fill("Rajiv Chowk Metro Station, Delhi", "Cyber City, Gurugram")
                st.rerun()
            if st.button("📍 Delhi: CP → Noida Sector 62", use_container_width=True):
                _quick_fill("Connaught Place, New Delhi", "Noida Sector 62, Uttar Pradesh")
                st.rerun()
            if st.button("✈️ Bangalore: Indiranagar → Whitefield", use_container_width=True):
                _quick_fill("Indiranagar, Bengaluru", "Whitefield, Bengaluru")
                st.rerun()
            if st.button("🌊 Mumbai: Andheri → Bandra Kurla Complex", use_container_width=True):
                _quick_fill("Andheri Station, Mumbai", "Bandra Kurla Complex, Mumbai")
                st.rerun()

        # ── Language section ──────────────────────────────────────────
        with st.expander("🌐 Language", expanded=False):
            language_choice = st.radio(
                "Response language",
                options=["English", "Hindi"],
                index=0,
                horizontal=True,
                label_visibility="collapsed",
            )
            st.session_state["language"] = "hi" if language_choice == "Hindi" else "en"
            if language_choice == "Hindi":
                st.caption("Agent will respond in Hindi. Labels and numbers stay in English.")

        # ── Saved Commutes ────────────────────────────────────────────
        _sidebar_user = get_current_user()
        if _sidebar_user:
            saved = get_client().get_saved_commutes(_sidebar_user.id)
            if saved:
                with st.expander("🔖 Saved Commutes", expanded=False):
                    for c in saved:
                        col_btn, col_del = st.columns([5, 1])
                        with col_btn:
                            if st.button(c["name"], key=f"saved_{c['id']}", use_container_width=True):
                                _quick_fill(c["origin"], c["destination"])
                        with col_del:
                            if st.button("✕", key=f"del_{c['id']}", help="Remove", type="tertiary"):
                                _del_session = supabase.auth.get_session()
                                if _del_session and _del_session.access_token:
                                    get_client().delete_saved_commute(_del_session.access_token, c["id"])
                                st.rerun()

        # ── Status card (bottom) ──────────────────────────────────────
        if st.session_state.get("city_override") == "Auto-detect":
            _active_city = st.session_state.get("detected_city", "Detecting...")
        else:
            _active_city = st.session_state.get("city_override")

        st.markdown(f"""
        <div class="sidebar-status-card">
          <div class="sidebar-status-label">📍 Active City</div>
          <div class="sidebar-status-value">{_active_city}</div>
        </div>
        """, unsafe_allow_html=True)

        # ── Dev tools ─────────────────────────────────────────────────
        with st.expander("🧪 Dev tools", expanded=False):
            if st.button("Reset alert cooldown", use_container_width=True):
                st.session_state.pop("alerts_last_checked_at", None)
                st.session_state.pop("pending_alerts", None)
                st.session_state.pop("last_sms_sent_at", None)
                st.rerun()
            st.caption(f"Last check: {st.session_state.get('alerts_last_checked_at', 'never')}")
            st.caption(f"Last SMS: {st.session_state.get('last_sms_sent_at', 'never')}")


    # ── HEADER ────────────────────────────────────────────────────────────────
    st.markdown('# 🧭 <span class="great-vibes-regular" style="font-size:2.8rem">Sherpa</span>', unsafe_allow_html=True)
    st.markdown("*AI-powered commute planning across Indian cities — real-time routes, weather, and smart timing*")
    st.divider()

    # ── Proactive alerts — checked inline on each page load ──────────────────
    _alert_user  = get_current_user()
    _last_check  = st.session_state.get("alerts_last_checked_at")
    _should_check = (
        _last_check is None or
        (datetime.now() - _last_check) > timedelta(minutes=15)
    )
    if _alert_user and _should_check:
        try:
            from services.memory_service import detect_patterns
            from services.alert_service import _is_departure_window, generate_alerts
            _history  = get_client().get_trip_history(_alert_user.id)
            _patterns = detect_patterns(_history)
            if _patterns and _is_departure_window(_patterns.get("usual_departure_hour")):
                _alerts = run_async(generate_alerts(_patterns))
                st.session_state["pending_alerts"] = _alerts
                from services.alert_service import send_sms_alerts
                _last_sms = st.session_state.get("last_sms_sent_at")
                if _alerts and (not _last_sms or (datetime.now() - _last_sms) > timedelta(hours=2)):
                    send_sms_alerts(_alerts)
                    st.session_state["last_sms_sent_at"] = datetime.now()
            else:
                st.session_state["pending_alerts"] = []
            st.session_state["alerts_last_checked_at"] = datetime.now()
        except Exception as e:
            st.sidebar.error(f"Alert check error: {e}")

    for _alert in st.session_state.get("pending_alerts", []):
        if _alert["severity"] == "warning":
            st.warning(f"**Heads up for your usual commute:** {_alert['message']}  \n_{_alert['suggestion']}_")
        else:
            st.info(f"**Commute update:** {_alert['message']}  \n_{_alert['suggestion']}_")

    # ── TABS ──────────────────────────────────────────────────────────────────
    tab_plan, tab_commutes, tab_chat = st.tabs(["🗺️  Plan Commute", "📊  My Commutes", "💬  Chat with Agent"])


    # ══════════════════════════════════════════════════════════════════════════
    # TAB 1 — PLAN COMMUTE
    # ══════════════════════════════════════════════════════════════════════════
    with tab_plan:

        col_o, col_d = st.columns(2)
        with col_o:
            st.markdown("**🏠 From**")
            origin = st_searchbox(
                search_places_autocomplete,
                placeholder="Start typing your origin…",
                default=st.session_state.get("origin_input", "Rajiv Chowk Metro Station, Delhi"),
                default_searchterm=st.session_state.get("_origin_fill", ""),
                key="origin_searchbox",
                clear_on_submit=False,
            )
        with col_d:
            st.markdown("**🏢 To**")
            destination = st_searchbox(
                search_places_autocomplete,
                placeholder="Start typing your destination…",
                default=st.session_state.get("destination_input", "Cyber City, Gurugram"),
                default_searchterm=st.session_state.get("_dest_fill", ""),
                key="dest_searchbox",
                clear_on_submit=False,
            )

        # ── Auto-detect city on input change (without submit) ──
        if origin and destination:
            prev_origin = st.session_state.get("last_origin_for_city")

            # Only run when origin actually changes (avoid rerun spam)
            if origin != prev_origin:
                try:
                    o_geo, _ = geocode_endpoints(origin, destination)
                    auto_city = extract_city_from_geo(o_geo)

                    if auto_city and auto_city != "unknown":
                        st.session_state["detected_city"] = auto_city

                    st.session_state["last_origin_for_city"] = origin

                except Exception:
                    pass
                
        # ── Remaining inputs + submit (inside form to batch the submit action) ──
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

        # ── Run agent on submit ───────────────────────────────────────────────
        if submitted:
            origin      = origin      or st.session_state.get("origin_input", "")
            destination = destination or st.session_state.get("destination_input", "")
            if not (origin or "").strip() or not (destination or "").strip():
                st.error("Please enter both origin and destination.")
            else:
                required_arrival = datetime.combine(travel_date, arrival_time).isoformat()

                user_prefs = {
                    "buffer_minutes":            buffer_minutes,
                    "prefer_comfort_over_speed": prefer_comfort,
                    "max_walking_minutes":       max_walk,
                }

                with st.spinner("🤖 Agent is planning your commute…"):
                    try:
                        _current_user = get_current_user()
                        result = run_async(
                            get_agent().plan_commute(
                                origin=           origin,
                                destination=      destination,
                                required_arrival= required_arrival,
                                user_prefs=       user_prefs,
                                extra_context=    extra_context or None,
                                user_id=          _current_user.id if _current_user else None,
                                language=         st.session_state.get("language", "en"),
                            )
                        )
                        st.session_state["plan_result"]      = result
                        st.session_state["plan_origin"]      = origin
                        st.session_state["plan_destination"] = destination

                        # Log trip silently if signed in
                        _user = get_current_user()
                        if _user:
                            _session = supabase.auth.get_session()
                            if _session and _session.access_token:
                                _log_trip_background(_session.access_token, _user.id, result, origin, destination)

                        # Geocode once and cache for maps + city detection
                        o_geo, d_geo = geocode_endpoints(origin, destination)
                        st.session_state["plan_o_geo"] = o_geo
                        st.session_state["plan_d_geo"] = d_geo
                        auto_city   = extract_city_from_geo(o_geo)
                        chosen_city = st.session_state.get("city_override", "Auto-detect")
                        st.session_state["detected_city"] = (
                            chosen_city if chosen_city != "Auto-detect" else auto_city
                        )

                        # Comfort advisory
                        try:
                            from agent.tools import _get_comfort_advisory
                            rec        = result.recommended_route or {}
                            metro_line = "Generic"
                            for step in rec.get("steps", []):
                                if step.get("mode") == "metro" and step.get("line"):
                                    metro_line = step["line"]
                                    break
                            if rec.get("departure_time"):
                                dep_iso = rec["departure_time"]
                            elif result.leave_by:
                                dep_iso = result.leave_by
                            else:
                                try:
                                    dep_iso = (datetime.fromisoformat(required_arrival) - timedelta(minutes=45)).isoformat()
                                except Exception:
                                    dep_iso = required_arrival
                            comfort_inp = {
                                "lat":               o_geo["lat"] if o_geo else None,
                                "lon":               o_geo["lng"] if o_geo else None,
                                "metro_line":        metro_line,
                                "departure_time_iso": dep_iso,
                            }
                            comfort_data = run_async(_get_comfort_advisory(comfort_inp))
                            st.session_state["comfort_advisory"] = comfort_data
                        except Exception:
                            st.session_state.pop("comfort_advisory", None)

                    except Exception as e:
                        st.error(f"Agent error: {e}")
                        st.session_state.pop("plan_result", None)

        # ── Display results ───────────────────────────────────────────────────
        if result := st.session_state.get("plan_result"):
            st.divider()

            # Route preview card
            _po = st.session_state.get("plan_origin", "")
            _pd = st.session_state.get("plan_destination", "")
            _o_html = _po if _po else '<span class="rp-text-muted">Unknown origin</span>'
            _d_html = _pd if _pd else '<span class="rp-text-muted">Unknown destination</span>'
            st.markdown(
                f"""
<div class="rp-wrap">
  <div class="rp-card">
    <div class="rp-endpoint"><span class="rp-dot rp-dot-o"></span><span class="rp-text">{_o_html}</span></div>
    <div class="rp-arrow">→</div>
    <div class="rp-endpoint"><span class="rp-dot rp-dot-d"></span><span class="rp-text">{_d_html}</span></div>
  </div>
</div>""",
                unsafe_allow_html=True,
            )

            # City detection banner
            detected_city     = st.session_state.get("detected_city", "unknown")
            city_override_val = st.session_state.get("city_override", "Auto-detect")
            if detected_city and detected_city != "unknown":
                source_note = " (manually selected)" if city_override_val != "Auto-detect" else " (auto-detected)"
                st.info(f"📍 **City: {detected_city}**{source_note} · Routing strategy selected accordingly. Wrong city? Change it in the sidebar.", icon=None)

            # Row 1: Weather | Leave-by | Urgency
            r1c1, r1c2, r1c3 = st.columns([2, 2, 1])
            with r1c1:
                wx      = result.weather_summary or "Weather data not available."
                risk    = result.risk_score
                wx_icon = "🌤️" if risk < 0.3 else ("🌧️" if risk < 0.6 else "⛈️")
                _wx_city  = st.session_state.get("detected_city") or ""
                _wx_label = f"Weather in {_wx_city}" if _wx_city and _wx_city != "unknown" else "Weather"
                if risk < 0.3:
                    st.success(f"{wx_icon} **{_wx_label}** — {wx}")
                elif risk < 0.6:
                    st.warning(f"{wx_icon} **{_wx_label}** — {wx}")
                else:
                    st.error(f"{wx_icon} **{_wx_label}** — {wx}")
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

            # Agent recommendation
            st.markdown("### 🤖 Agent Recommendation")
            paragraphs = [p.strip() for p in result.explanation.split("\n\n") if p.strip()]
            if paragraphs:
                st.info(paragraphs[0])
                if len(paragraphs) > 1:
                    with st.expander("Full analysis", expanded=False):
                        st.markdown("\n\n".join(paragraphs[1:]))
            else:
                st.info(result.explanation[:300] + ("…" if len(result.explanation) > 300 else ""))

            for d in result.disruptions:
                st.warning(d, icon="🚨")

            # Agent reasoning trace (Task 7.1)
            if result.tool_trace:
                with st.expander("🔍 Agent reasoning trace", expanded=False):
                    for i, step in enumerate(result.tool_trace, 1):
                        name    = step.get("name", "")
                        summary = step.get("summary", "")
                        st.markdown(f"`[{i}]` **{name}** &nbsp;→&nbsp; {summary}", unsafe_allow_html=True)
            elif result.tool_calls_made:
                with st.expander("🔍 Agent reasoning trace", expanded=False):
                    for i, name in enumerate(result.tool_calls_made, 1):
                        st.markdown(f"`[{i}]` **{name}**")

            # Comfort Advisory
            comfort = st.session_state.get("comfort_advisory")
            if comfort:
                st.divider()
                st.markdown("### 🌡️ Comfort Advisory")
                ca_col1, ca_col2 = st.columns(2)
                with ca_col1:
                    heat_cat   = comfort.get("heat_category", "comfortable")
                    heat_index = comfort.get("heat_index_c", "—")
                    heat_adv   = comfort.get("heat_advisory", "")
                    heat_color = {"comfortable": "🟢", "warm": "🟡", "hot": "🟠", "dangerous": "🔴"}.get(heat_cat, "⚪")
                    st.metric(label=f"{heat_color} Heat Index", value=f"{heat_index}°C", help=heat_adv)
                    st.caption(heat_adv)
                with ca_col2:
                    crowd_lbl  = comfort.get("crowding_label", "unknown")
                    crowd_occ  = comfort.get("crowding_occupancy", 0)
                    metro_line = comfort.get("metro_line", "Metro")
                    crowd_icon = {"empty": "🟢", "moderate": "🟡", "crowded": "🟠", "very crowded": "🔴"}.get(crowd_lbl, "⚪")
                    peak_badge = " (Peak)" if comfort.get("is_peak") else " (Off-peak)"
                    st.metric(
                        label=f"{crowd_icon} {metro_line} Crowding",
                        value=f"{crowd_lbl.title()}{peak_badge}",
                        help=f"{int(crowd_occ * 100)}% occupancy",
                    )
                    if coach_tip := comfort.get("coach_tip", ""):
                        st.caption(f"💡 {coach_tip}")
                if reasoning := comfort.get("reasoning"):
                    st.info(reasoning, icon="🤖")
                if early := comfort.get("early_departure"):
                    st.warning(
                        f"**Leave earlier — {early.get('suggested_departure', '')}** "
                        f"saves ~{early.get('minutes_saved', 0)} min of crowding.  \n"
                        f"{early.get('reason', '')}",
                        icon="⚡",
                    )

            st.divider()

            # Save this commute
            with st.expander("🔖 Save this commute", expanded=False):
                if require_auth("saved commutes"):
                    _origin      = st.session_state.get("plan_origin", "")
                    _destination = st.session_state.get("plan_destination", "")
                    _default_name = f"{_origin.split(',')[0]} → {_destination.split(',')[0]}"
                    save_col1, save_col2 = st.columns([3, 1])
                    with save_col1:
                        save_name = st.text_input(
                            "Name", value=_default_name, key="save_commute_name",
                            label_visibility="collapsed", placeholder="e.g. Home → Office",
                        )
                    with save_col2:
                        if st.button("Save", key="save_commute_btn", use_container_width=True):
                            _u       = get_current_user()
                            _session = supabase.auth.get_session()
                            if save_name.strip() and _u and _session and _session.access_token:
                                ok = get_client().save_commute(
                                    access_token= _session.access_token,
                                    user_id=      _u.id,
                                    name=         save_name.strip(),
                                    origin=       _origin,
                                    destination=  _destination,
                                )
                                if ok:
                                    st.success("Saved! It'll appear in your sidebar.")
                                else:
                                    st.error("Could not save — please try again.")
                            elif _u and not (_session and _session.access_token):
                                st.error("Session expired — please sign in again.")

            st.divider()

            # Route options
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

                        route_label = route.get("label", "").lower()
                        if "cab" in route_label:
                            est_cost   = route.get("total_cost_rupees", 0)
                            origin_txt = st.session_state.get("plan_origin", "")
                            dest_txt   = st.session_state.get("plan_destination", "")
                            st.markdown(f"**🚕 Book this ride** · Estimated ₹{est_cost}")
                            uber_url = _build_uber_link(origin_txt, dest_txt, o_geo, d_geo)
                            ola_url  = _build_ola_link(origin_txt, dest_txt, o_geo, d_geo)

                            uber_col, ola_col, note_col = st.columns([1, 1, 2])
                            with uber_col:
                                st.link_button("Open in Uber 🟡", uber_url, use_container_width=True)
                                try:
                                    uber_qr_url = _build_uber_qr_link(origin_txt, dest_txt, o_geo, d_geo)
                                    st.image(_url_to_qr_bytes(uber_qr_url), width=120,
                                             caption="Scan → opens Uber with route pre-filled")
                                except Exception:
                                    pass
                            with ola_col:
                                st.link_button("Open in Ola 🟢", ola_url, use_container_width=True)
                                try:
                                    st.image(_url_to_qr_bytes(ola_url), width=120,
                                             caption="Scan → opens Ola booking page")
                                except Exception:
                                    pass
                            with note_col:
                                st.info(
                                    "**Pickup & drop are pre-filled with coordinates.**  \n"
                                    "If the app asks to confirm your pickup location, "
                                    "tap **Confirm** — your address is already shown on the map.",
                                    icon="📍",
                                )
            else:
                st.info("No route data returned — see agent explanation above.")


    # ══════════════════════════════════════════════════════════════════════════
    # TAB 2 — MY COMMUTES
    # ══════════════════════════════════════════════════════════════════════════
    with tab_commutes:
        if not require_auth("My Commutes"):
            pass
        else:
            _mc_user  = get_current_user()
            _mc_trips = get_client().get_trip_history(_mc_user.id, limit=50)

            if len(_mc_trips) < 5:
                st.info(
                    "Plan a few more commutes to unlock insights. "
                    f"({len(_mc_trips)}/5 trips logged)",
                    icon="📊",
                )
            else:
                _mc_month = datetime.now().strftime("%Y-%m")
                _mc_spend = get_client().get_monthly_spend(_mc_user.id, _mc_month)

                from services.memory_service import detect_savings_opportunities
                _mc_opps = detect_savings_opportunities(_mc_spend, _mc_trips)

                _mc_total       = _mc_spend.get("total_spent", 0)
                _mc_saving      = sum(o["saving"] for o in _mc_opps[:3])
                _mc_month_label = datetime.now().strftime("%B %Y")

                if _mc_saving > 0 and _mc_opps:
                    st.success(
                        f"**{_mc_month_label}:** You've spent ₹{_mc_total} on commutes. "
                        f"Switching to metro on your {min(3, len(_mc_opps))} most frequent routes "
                        f"would save **₹{_mc_saving}/month**.",
                        icon="💡",
                    )
                elif _mc_total > 0:
                    st.info(
                        f"**{_mc_month_label}:** You've spent ₹{_mc_total} on commutes "
                        f"across {_mc_spend.get('trip_count', 0)} trips.",
                        icon="💡",
                    )
                else:
                    st.info("No spend recorded for this month yet.", icon="💡")

                _mc_by_mode = _mc_spend.get("by_mode", {})
                if _mc_by_mode:
                    st.markdown(f"### Monthly Spend — {_mc_month_label}")
                    import pandas as pd
                    _mc_chart = pd.DataFrame({
                        "Mode":      list(_mc_by_mode.keys()),
                        "Spend (₹)": list(_mc_by_mode.values()),
                    }).set_index("Mode")
                    st.bar_chart(_mc_chart, use_container_width=True, height=260)
                    _mc_cols = st.columns(len(_mc_by_mode))
                    for _col, (_mode, _amt) in zip(_mc_cols, _mc_by_mode.items()):
                        _col.metric(f"{_mode.title()} spend", f"₹{_amt}")

                st.divider()

                if _mc_opps:
                    st.markdown("### 💰 Savings Opportunities")
                    st.caption("Trips where you took a cab but metro was available or cheaper.")
                    import pandas as pd
                    _opp_df = pd.DataFrame(_mc_opps[:10])[
                        ["date", "route", "cab_cost", "metro_cost", "saving"]
                    ]
                    _opp_df.columns = ["Date", "Route", "Cab Cost (₹)", "Metro Cost (₹)", "Saving (₹)"]
                    st.dataframe(_opp_df, use_container_width=True, hide_index=True)
                else:
                    st.success("No savings opportunities found — you're already commuting efficiently!", icon="✅")


    # ══════════════════════════════════════════════════════════════════════════
    # TAB 3 — CHAT
    # ══════════════════════════════════════════════════════════════════════════
    with tab_chat:
        st.markdown("Ask anything about your commute — the agent has access to real-time weather, routes, and metro data.")

        if "chat_history" not in st.session_state:
            st.session_state["chat_history"] = []

        for msg in st.session_state["chat_history"]:
            role = msg["role"]
            text = msg["text"]
            if role == "user":
                st.markdown(f'<div class="chat-user">👤 {text}</div>', unsafe_allow_html=True)
            else:
                st.markdown(f'<div class="chat-agent">🤖 {text}</div>', unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

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
            history = st.session_state["chat_history"][-10:]
            st.session_state["chat_history"].append({"role": "user", "text": user_input})

            with st.spinner("Agent is thinking…"):
                try:
                    reply = run_async(
                        get_agent().chat(
                            user_message=user_input,
                            history=history,
                            language=st.session_state.get("language", "en"),
                        )
                    )
                except Exception as e:
                    reply = f"Error: {e}"

            st.session_state["chat_history"].append({"role": "agent", "text": reply})
            st.rerun()

        if st.button("🗑️  Clear chat", use_container_width=False):
            st.session_state["chat_history"] = []
            st.rerun()


# ── Router ────────────────────────────────────────────────────────────────────
if st.session_state["page"] == "login":
    render_login_page()
else:
    render_app()