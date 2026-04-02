# Predictive Commute Agent — Execution Plan v2

> **Stack**: Python · Streamlit · Gemini 2.5 Flash · Google Maps API · OpenWeatherMap · Supabase · GTFS
> **Goal**: Build a genuinely agentic, India-first commute planner that impresses a hackathon panel.
> **Approach**: One session = one shippable slice. Each session ends with a working, demo-able state.

---

## Gaps fixed in this version

The following issues from v1 have been resolved before the session tasks below:

1. **`detect_patterns()` missing fields** — `usual_departure_hour` and `most_frequent_route` added to Task 3.1 output spec. Both are required by the proactive alert system in Session 3.5.
2. **Circular import in Task 1.3** — `haversine` must not be imported from within `metro_service.py` itself. Moved to `utils/geo.py` and both callers import from there.
3. **Session 2 Claude Code prompt contradiction** — the prompt previously said "use session ID as the user key — no login required", which contradicts the entire Session 2 goal of Supabase Auth. Fixed to correctly say: use `user.id` from Supabase for logged-in users, fall back to a temporary `st.session_state["guest_id"]` (UUID generated once per browser session) for guests.
4. **`crowding_service.py` used before it exists** — Session 3.5 (proactive alerts) calls `estimate_crowding()`, which is only built in Session 5. Fixed with a conditional import and graceful fallback: if the module isn't present, the crowding check is skipped silently.
5. **APScheduler respawn on Streamlit rerun** — Streamlit reruns the full script on every interaction. The scheduler must be guarded with `if "scheduler_started" not in st.session_state` to prevent spawning duplicate background threads.
6. **Duplicate weather API calls** — the proactive alert service and `plan_commute()` both called the weather API independently. Fixed: alerts reuse `st.session_state["last_weather_cache"]` if fresh (< 15 min old), only fetching independently when the cache is stale.
7. **OpenWeatherMap API key not documented** — added to `.env` spec in Task 0 (pre-work) and Task 8.4 README section.
8. **Tab layout conflict between Session 6 and Session 7** — Session 6 adds a "My commutes" tab, Session 7 reworks the UI. Explicit tab structure defined once in Session 6 and carried forward: `[Plan Commute] [My Commutes] [Chat]`. Session 7 adds the reasoning trace *inside* the Plan Commute tab as a collapsible expander — not a new tab.

---

## Session index

| # | Session | Core deliverable |
|---|---------|-----------------|
| 0 | Pre-work | Env setup, API keys, project structure confirmed |
| 1 | Multi-city support | App works for Mumbai, Bangalore, Chennai — not just Delhi |
| 2 | Auth + Supabase wiring | User identity, saved commutes, trip log persisted |
| 3 | Commute memory + personalisation | Agent learns patterns, pre-weights scoring |
| 3.5 | Proactive departure alerts | Agent notifies user before they ask — rain, crowding, traffic |
| 4 | What-if simulator | Leave-time sweep with agent narration |
| 5 | Heat index + crowding advisory | Two-source reasoning, safest mode recommendation |
| 6 | Monthly cost tracker | Spend aggregation + proactive savings insight |
| 7 | Agent narration + tool-chain visibility | Visible reasoning, Hindi/English toggle |
| 8 | UI polish + demo hardening | Demo flow, edge-case handling, README |

---

## Session 0 — Pre-work (do once before Session 1)

### Goal
Confirm all external dependencies are available before writing any feature code. Failures here will silently break multiple sessions if not caught early.

### Tasks

#### Task 0.1 — Create `.env` with all required keys
```
GOOGLE_MAPS_API_KEY=...
OPENWEATHERMAP_API_KEY=...       # needed from Session 1 onward
SUPABASE_URL=...
SUPABASE_ANON_KEY=...
GEMINI_API_KEY=...
```

Enable these Google Maps APIs in your Cloud Console: Geocoding, Directions, Places.

#### Task 0.2 — Move `haversine` to shared utility
**New file**: `utils/geo.py`

```python
import math

def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Returns distance in km between two lat/lng points."""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
    return R * 2 * math.asin(math.sqrt(a))
```

Update any existing `haversine` callers in `metro_service.py` and `hybrid_route_service.py` to import from `utils.geo`.

#### Task 0.3 — Confirm GTFS files present
Verify `DMRC-GTFS/` exists at project root with: `stops.txt`, `routes.txt`, `trips.txt`, `stop_times.txt`.
If missing, download from: https://github.com/justbejyk/delhi-metro-gtfs or the official DMRC open data source.

#### Task 0.4 — Install all packages upfront
```
pip install streamlit google-generativeai googlemaps httpx supabase apscheduler python-dotenv
```
Add all to `requirements.txt` now so Session 8's audit is trivial.

---

## Session 1 — Multi-city support

### Goal
The app currently hard-codes Delhi Metro logic. After this session, Option 1 (Google Maps Transit) and Option 2 (Cab) work for any Indian city. Option 3 (Metro Hybrid) works for Delhi via GTFS and gracefully falls back to a Google Maps Places search for all other cities.

### Why this matters for the agent
City detection requires the agent to reason about which tool chain to invoke — GTFS path vs Places API path. This is the first genuine conditional branch in the agentic loop.

### Tasks

#### Task 1.1 — Fix GTFS path resolution
The app crashes when run from `ui/` because paths are relative. Fix before anything else.

**File**: `services/metro_service.py`

```python
import os
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GTFS_DIR = os.path.join(PROJECT_ROOT, "DMRC-GTFS")

# Replace any hardcoded "DMRC-GTFS/stops.txt" with:
os.path.join(GTFS_DIR, "stops.txt")
# etc. for routes.txt, trips.txt, stop_times.txt
```

**Test**: Run `streamlit run ui/streamlit_app.py` from project root — no "stops.txt not found" error.

---

#### Task 1.2 — Add city detection to geocoding
**File**: `mcp/google_maps_client.py`

```python
async def detect_city(self, address: str) -> str:
    """Returns city name from address string. Falls back to 'unknown'."""
    result = await self.geocode(address)
    if not result:
        return "unknown"
    for component in result.get("address_components", []):
        if "locality" in component["types"]:
            return component["long_name"]
        if "administrative_area_level_2" in component["types"]:
            return component["long_name"]
    return "unknown"
```

**Test**: `await mcp_maps.detect_city("Indiranagar, Bangalore")` → `"Bengaluru"` or `"Bangalore"`.

---

#### Task 1.3 — Build city-agnostic metro station finder
**File**: `services/metro_service.py`

Note: `haversine` is now imported from `utils.geo` — do not import from within this file.

```python
from utils.geo import haversine

DELHI_ALIASES = {"delhi", "new delhi", "delhi ncr"}

async def find_nearest_metro_any_city(city: str, lat: float, lng: float) -> dict | None:
    """
    Delhi → uses GTFS (fast, precise).
    Any other city → uses Google Maps Places search (city-agnostic).
    Returns: {"name": str, "lat": float, "lng": float, "distance_km": float}
    """
    if city.lower() in DELHI_ALIASES:
        station = delhi_metro.find_nearest_station(lat, lng)
        if station:
            return {
                "name": station.name,
                "lat": station.lat,
                "lng": station.lng,
                "distance_km": station.distance_km,
            }
        return None

    from mcp.google_maps_client import mcp_maps
    places = await mcp_maps.search_places(
        f"metro station near {lat},{lng}", lat=lat, lng=lng, radius=5000
    )
    if not places:
        return None
    nearest = places[0]
    dist = haversine(lat, lng, nearest["lat"], nearest["lng"])
    return {
        "name": nearest["name"],
        "lat": nearest["lat"],
        "lng": nearest["lng"],
        "distance_km": dist,
    }
```

---

#### Task 1.4 — Update hybrid route service to use new finder
**File**: `services/hybrid_route_service.py`

In `_build_metro_hybrid()`:
1. Call `detect_city()` on origin coordinates
2. Call `find_nearest_metro_any_city()` instead of `delhi_metro.find_nearest_station()`
3. Update the "skip" condition: skip only if `distance_km > 8` OR `station is None`
4. Update the route label: append city tag for non-Delhi — e.g. `"Metro (Auto + Metro + Walk) [Mumbai]"`

---

#### Task 1.5 — Add `city` to agent tool `get_route_options`
**File**: `agent/tools.py`

Add `city` as an optional string parameter to the `get_route_options` FunctionDeclaration. Auto-populate from geocoding when not provided.

---

#### Task 1.6 — Update agent prompt for multi-city awareness
**File**: `agent/prompts.py`

Add to system prompt:
```
You support commute planning across all major Indian cities.
For Delhi, you use local GTFS metro data (precise station names, lines, interchange info).
For Mumbai, Bangalore, Chennai, Hyderabad, Kolkata and other cities, you use Google Maps
transit data + Places API to find nearby metro/local train stations.
Always detect the city from the user's origin/destination before selecting a routing strategy.
If the city has no metro system, skip Option 3 and explain why.
```

---

#### Task 1.7 — Add city pill to UI route tabs
**File**: `ui/streamlit_app.py`

```python
city_badge = f"[{route.city}]" if hasattr(route, 'city') and route.city else ""
st.markdown(f"**{route.label}** {city_badge}")
```

---

### Session 1 test checklist
- [ ] App runs from project root without GTFS path errors
- [ ] Query "Andheri to Bandra, Mumbai" returns 3 route options
- [ ] Option 3 label shows `[Mumbai]` or similar city tag
- [ ] Query "Rajiv Chowk to Hauz Khas" still uses GTFS Delhi Metro data
- [ ] Query for a city with no metro (e.g. Jaipur) skips Option 3 gracefully with explanation

---

## Session 2 — Auth + Supabase wiring

### Goal
Add Supabase Auth (magic link + Google OAuth) with a soft gate UI. Guest users can plan commutes freely; signing in unlocks saved commutes, trip history, and personalisation.

### Auth design: soft gate
- App is fully usable without login
- Guests get a temporary `guest_id` (UUID, generated once and stored in `st.session_state`) — this lets them plan without errors but their trips are not persisted
- Sidebar always shows a compact "Sign in" button when logged out
- Attempting to save a commute or view history triggers a polite auth prompt
- On sign-in success, `st.session_state["user"]` is populated with the Supabase user object
- `user.id` (UUID from Supabase Auth) replaces `guest_id` everywhere in the data layer for logged-in users

---

### Tasks

#### Task 2.1 — Enable Supabase Auth providers
In your Supabase dashboard:
1. **Authentication → Providers → Email**: enable, disable "Confirm email" for hackathon speed
2. **Authentication → Providers → Google**: create OAuth credentials in Google Cloud Console, paste client ID + secret into Supabase
3. Set redirect URL to `http://localhost:8501` (and your deployed URL)

No code changes — dashboard config only.

---

#### Task 2.2 — Activate and harden Supabase schema
Run `database/migrations/001_initial_schema.sql`:

```sql
create table if not exists public.profiles (
  id uuid references auth.users(id) primary key,
  display_name text,
  preferred_language text default 'en',
  created_at timestamptz default now()
);

create table if not exists public.trips (
  id uuid default gen_random_uuid() primary key,
  user_id uuid references auth.users(id),
  origin text not null,
  destination text not null,
  city text,
  route_label text,
  mode text,  -- 'transit' | 'cab' | 'metro_hybrid'
  duration_min int,
  cost_inr int,
  planned_at timestamptz default now()
);

create table if not exists public.saved_commutes (
  id uuid default gen_random_uuid() primary key,
  user_id uuid references auth.users(id),
  name text not null,
  origin text not null,
  destination text not null,
  created_at timestamptz default now()
);

alter table public.trips enable row level security;
alter table public.saved_commutes enable row level security;
alter table public.profiles enable row level security;

create policy "Own trips only" on public.trips
  for all using (auth.uid() = user_id);
create policy "Own saved commutes only" on public.saved_commutes
  for all using (auth.uid() = user_id);
create policy "Own profile only" on public.profiles
  for all using (auth.uid() = id);
```

---

#### Task 2.3 — Auth helper module
**New file**: `auth/supabase_auth.py`

```python
from database.supabase_client import supabase
import streamlit as st

def get_current_user():
    return st.session_state.get("user")

def is_logged_in() -> bool:
    return get_current_user() is not None

def get_user_id() -> str | None:
    """Returns user.id for logged-in users, guest_id for guests."""
    user = get_current_user()
    if user:
        return user.id
    if "guest_id" not in st.session_state:
        import uuid
        st.session_state["guest_id"] = str(uuid.uuid4())
    return None  # Guests do not write to Supabase — return None so callers can skip DB ops

def sign_in_magic_link(email: str) -> bool:
    try:
        supabase.auth.sign_in_with_otp({"email": email})
        return True
    except Exception:
        return False

def sign_in_google() -> str:
    resp = supabase.auth.sign_in_with_oauth({
        "provider": "google",
        "options": {"redirect_to": "http://localhost:8501"}
    })
    return resp.url

def sign_out():
    supabase.auth.sign_out()
    st.session_state.pop("user", None)

def handle_auth_callback():
    """Call once at app startup to handle OAuth redirects and restore sessions."""
    params = st.query_params
    if "access_token" in params:
        try:
            session = supabase.auth.set_session(
                params["access_token"], params.get("refresh_token", "")
            )
            st.session_state["user"] = session.user
            st.query_params.clear()
        except Exception:
            pass
    elif "user" not in st.session_state:
        try:
            session = supabase.auth.get_session()
            if session and session.user:
                st.session_state["user"] = session.user
        except Exception:
            pass
```

---

#### Task 2.4 — Sidebar auth widget
**File**: `ui/streamlit_app.py`

```python
from auth.supabase_auth import (
    get_current_user, is_logged_in, get_user_id,
    sign_in_magic_link, sign_in_google, sign_out, handle_auth_callback
)

# At very top of app, before any rendering:
handle_auth_callback()

# In sidebar:
user = get_current_user()
if user:
    st.sidebar.success(f"Signed in as {user.email}")
    if st.sidebar.button("Sign out"):
        sign_out()
        st.rerun()
else:
    with st.sidebar.expander("Sign in to save commutes"):
        tab_ml, tab_g = st.tabs(["Magic link", "Google"])
        with tab_ml:
            email = st.text_input("Email", key="auth_email")
            if st.button("Send magic link"):
                if sign_in_magic_link(email):
                    st.success("Check your email for a sign-in link.")
                else:
                    st.error("Failed to send link.")
        with tab_g:
            google_url = sign_in_google()
            st.link_button("Continue with Google", google_url)
```

---

#### Task 2.5 — Soft gate helper
**File**: `ui/streamlit_app.py`

```python
def require_auth(feature_name: str = "this feature") -> bool:
    if is_logged_in():
        return True
    st.info(f"Sign in to use {feature_name}. You can still plan commutes as a guest.")
    return False
```

---

#### Task 2.6 — Log trips after planning
**File**: `ui/streamlit_app.py`

```python
def log_trip_async(user_id: str, plan):
    try:
        from database.supabase_client import supabase
        supabase.table("trips").insert({
            "user_id": user_id,
            "origin": plan.origin,
            "destination": plan.destination,
            "city": getattr(plan, "city", None),
            "route_label": plan.recommended_route.label,
            "mode": plan.recommended_route.mode_type,
            "duration_min": plan.recommended_route.duration_min,
            "cost_inr": int(plan.recommended_route.cost or 0),
        }).execute()
    except Exception:
        pass  # Never crash UI for logging

user = get_current_user()
if user:  # Only log for authenticated users — guests are not persisted
    import threading
    threading.Thread(target=log_trip_async, args=(user.id, plan), daemon=True).start()
```

---

#### Task 2.7 — Saved commutes
**File**: `database/supabase_client.py`

```python
def save_commute(user_id: str, name: str, origin: str, destination: str):
    return supabase.table("saved_commutes").insert({
        "user_id": user_id, "name": name,
        "origin": origin, "destination": destination
    }).execute()

def get_saved_commutes(user_id: str) -> list:
    resp = supabase.table("saved_commutes")\
        .select("*").eq("user_id", user_id)\
        .order("created_at", desc=True).execute()
    return resp.data or []

def delete_saved_commute(commute_id: str):
    supabase.table("saved_commutes").delete().eq("id", commute_id).execute()
```

Sidebar (logged-in users only):
```python
if is_logged_in():
    saved = get_saved_commutes(get_current_user().id)
    if saved:
        st.sidebar.markdown("**Saved commutes**")
        for c in saved:
            if st.sidebar.button(c["name"], key=f"saved_{c['id']}"):
                st.session_state["prefill_origin"] = c["origin"]
                st.session_state["prefill_destination"] = c["destination"]
                st.rerun()
```

---

#### Task 2.8 — Add `get_user_history` agent tool
**File**: `agent/tools.py`

```python
async def _tool_get_user_history(user_id: str) -> dict:
    from database.supabase_client import supabase
    resp = supabase.table("trips")\
        .select("origin,destination,route_label,mode,duration_min,cost_inr,planned_at")\
        .eq("user_id", user_id)\
        .order("planned_at", desc=True)\
        .limit(10).execute()
    return {"trips": resp.data or [], "count": len(resp.data or [])}
```

---

### Session 2 test checklist
- [ ] Magic link email arrives and logs user in on click
- [ ] Google OAuth redirects back to app with user signed in
- [ ] Sidebar shows email when logged in, sign-in expander when guest
- [ ] Guest can plan commutes with no restrictions — no errors, no DB writes
- [ ] "Save commute" button shows auth prompt for guests, saves for logged-in users
- [ ] Trip appears in Supabase `trips` table after planning (when logged in)
- [ ] Saved commutes appear in sidebar and one-tap replans correctly
- [ ] RLS: user A cannot query user B's trips

### Session 2 Claude Code prompt
```
Adding Supabase Auth to my Streamlit commute app — magic link + Google OAuth, soft gate pattern.
Here are my current files: database/supabase_client.py, ui/streamlit_app.py, config.py [paste all three]
I need:
1. A new auth/supabase_auth.py with magic link, Google OAuth, session restore, sign-out, and get_user_id()
   — get_user_id() returns user.id for logged-in users and None for guests (guests don't write to DB)
2. Sidebar auth widget — expander with magic link tab and Google button, shows email when signed in
3. require_auth() soft gate helper used on save/history features
4. Trip logging to Supabase after plan_commute() completes — only for logged-in users, background thread
5. Saved commutes: save button on results, sidebar list with one-tap replan
6. RLS policies on trips and saved_commutes tables
7. get_user_history tool in agent/tools.py querying the trips table
The app must remain fully functional for guests — auth is additive, not a wall.
```

---

## Session 3 — Commute memory + personalisation

### Goal
The agent retrieves user history before planning and adjusts scoring weights based on detected patterns. This is the first session where the agent visibly reasons over memory.

### Tasks

#### Task 3.1 — Pattern detector
**File**: `services/memory_service.py` _(new file)_

```python
from collections import Counter
import statistics

def detect_patterns(trips: list[dict]) -> dict:
    """
    Analyses trip history and returns detected patterns.

    Returns:
    {
        "preferred_mode": "metro",
        "peak_cab_usage": True,
        "usual_duration_min": 42,
        "route_frequency": {"Dwarka → CP": 8},

        # Required by Session 3.5 proactive alerts — must be present:
        "usual_departure_hour": 17.5,       # float, e.g. 17.5 = 5:30pm. None if < 5 trips
        "most_frequent_route": {            # origin/destination of top O→D pair
            "origin": "Dwarka Sector 21",
            "destination": "Connaught Place"
        }
    }
    """
    if not trips:
        return {}

    modes = [t["mode"] for t in trips if t.get("mode")]
    preferred_mode = Counter(modes).most_common(1)[0][0] if modes else None

    durations = [t["duration_min"] for t in trips if t.get("duration_min")]
    usual_duration = int(statistics.median(durations)) if durations else None

    route_pairs = [f"{t['origin']} → {t['destination']}" for t in trips
                   if t.get("origin") and t.get("destination")]
    route_frequency = dict(Counter(route_pairs).most_common(5))

    # Departure hour — parse from planned_at timestamps
    from datetime import datetime
    hours = []
    for t in trips:
        try:
            dt = datetime.fromisoformat(t["planned_at"])
            hours.append(dt.hour + dt.minute / 60)
        except Exception:
            pass
    usual_departure_hour = round(statistics.median(hours), 1) if len(hours) >= 5 else None

    # Most frequent O→D pair as structured dict
    most_frequent_route = None
    if route_pairs:
        top = Counter(route_pairs).most_common(1)[0][0]
        parts = top.split(" → ", 1)
        if len(parts) == 2:
            most_frequent_route = {"origin": parts[0], "destination": parts[1]}

    cab_trips = [t for t in trips if t.get("mode") == "cab"]
    peak_hours = [8, 9, 10]
    peak_cab = any(
        datetime.fromisoformat(t["planned_at"]).hour in peak_hours
        for t in cab_trips
        if t.get("planned_at")
    )

    return {
        "preferred_mode": preferred_mode,
        "peak_cab_usage": peak_cab,
        "usual_duration_min": usual_duration,
        "route_frequency": route_frequency,
        "usual_departure_hour": usual_departure_hour,
        "most_frequent_route": most_frequent_route,
    }
```

---

#### Task 3.2 — Inject memory into scoring weights
**File**: `services/hybrid_route_service.py`

Add `user_patterns: dict = None` parameter to `score_and_rank()`. If `preferred_mode == "metro"`, boost `WEIGHT_COMFORT` for metro options by 0.1. If user historically prefers cabs at this time of day, boost cab comfort score.

---

#### Task 3.3 — Agent pre-planning memory retrieval
**File**: `agent/core.py`

In `plan_commute()`, before the main agentic loop:
1. Call `get_user_history` tool
2. Pass result to `_extract_memory_context(history)` which calls `detect_patterns()`
3. Inject patterns as an additional message into the Gemini context: `"User memory context: {patterns}"`

This makes memory retrieval visible in `tool_calls_made`.

---

#### Task 3.4 — Proactive pattern surfacing in explanation
**File**: `agent/prompts.py`

Add to system prompt:
```
If user history is available, begin your explanation with one personalised insight.
Examples:
- "Based on your past trips, you usually take 42 minutes on this route."
- "You've been taking cabs on Friday evenings — metro would save you ₹180 today."
- "This is your most frequent commute. Here's how today compares."
Keep it to one sentence. Don't repeat it in the detail.
```

---

### Session 3 test checklist
- [ ] After 3+ logged trips, agent explanation opens with a personalised insight
- [ ] `tool_calls_made` in UI shows `get_user_history` was called
- [ ] Scoring visibly changes when user has strong mode preference (check score values)
- [ ] First-time users (no history) get no personalisation, no errors
- [ ] `detect_patterns()` returns `usual_departure_hour` and `most_frequent_route` for users with 5+ trips

---

## Session 3.5 — Proactive departure alerts

### Goal
The agent notifies the user about upcoming commute conditions — rain, metro crowding, traffic — before they ask. This is the clearest demonstration of true proactive agency to a hackathon panel.

### Design
- A background scheduler checks every 30 minutes: is the user's usual departure time 60–90 minutes away?
- If yes: fetch weather + (optionally) crowding + traffic for the user's most frequent route
- Synthesise into one or more alerts and write them to `st.session_state["pending_alerts"]`
- On next app open or refresh, a banner appears at the top of the UI
- No new tab, no new button — it just appears

### Dependencies
- Requires Session 2 (user.id) and Session 3 (`detect_patterns()` with `usual_departure_hour` and `most_frequent_route`)
- `crowding_service.py` is imported conditionally — alerts work without it until Session 5 builds it
- Weather call reuses `st.session_state["last_weather_cache"]` if less than 15 minutes old

### Tasks

#### Task 3.5.1 — Alert service
**New file**: `services/alert_service.py`

```python
import asyncio
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import streamlit as st

_scheduler = BackgroundScheduler()

def _get_cached_weather(origin: str) -> dict | None:
    """Reuse recent weather fetch from planning flow. Avoids duplicate API calls."""
    cache = st.session_state.get("last_weather_cache")
    if not cache:
        return None
    age = datetime.now() - cache.get("fetched_at", datetime.min)
    if age < timedelta(minutes=15):
        return cache.get("data")
    return None

async def _fetch_weather_for_alert(origin: str) -> dict:
    cached = _get_cached_weather(origin)
    if cached:
        return cached
    from services.weather_service import get_weather
    return await get_weather(origin)

def _is_departure_window(usual_hour: float) -> bool:
    """Returns True if the user's usual departure is 30–90 minutes from now."""
    if usual_hour is None:
        return False
    now = datetime.now()
    now_hour = now.hour + now.minute / 60
    diff = usual_hour - now_hour
    return 0.5 <= diff <= 1.5

async def generate_alerts(patterns: dict) -> list[dict]:
    """
    Returns a list of alert dicts for the user's most frequent route.
    Each alert: {type, severity, message, suggestion}
    severity: "warning" (red) | "info" (blue)
    """
    alerts = []
    route = patterns.get("most_frequent_route")
    if not route:
        return alerts

    # --- Weather check ---
    try:
        weather = await _fetch_weather_for_alert(route["origin"])
        rain_prob = weather.get("rain_probability", 0)
        if rain_prob > 0.5:
            alerts.append({
                "type": "rain",
                "severity": "warning",
                "message": f"Rain likely ({int(rain_prob*100)}% chance) around your usual departure time.",
                "suggestion": "Consider leaving 15 min early or switching to metro to avoid cab surge pricing."
            })
    except Exception:
        pass  # Never block on weather failure

    # --- Crowding check (optional — only if Session 5 has been built) ---
    try:
        from services.crowding_service import estimate_crowding
        usual_line = patterns.get("usual_metro_line")
        usual_hour = patterns.get("usual_departure_hour")
        if usual_line and usual_hour:
            crowding = estimate_crowding(usual_line, usual_hour)
            if crowding.get("occupancy", 0) > 0.80:
                alerts.append({
                    "type": "crowding",
                    "severity": "info",
                    "message": f"{usual_line} will be {crowding['label']} at your usual departure time.",
                    "suggestion": crowding.get("coach_tip", "Consider travelling 15 min off-peak.")
                })
    except ImportError:
        pass  # crowding_service not built yet — skip silently

    return alerts

def _run_alert_check(patterns: dict):
    """Synchronous wrapper for the async alert generator — runs in background thread."""
    if not _is_departure_window(patterns.get("usual_departure_hour")):
        return
    loop = asyncio.new_event_loop()
    try:
        alerts = loop.run_until_complete(generate_alerts(patterns))
        if alerts:
            st.session_state["pending_alerts"] = alerts
    finally:
        loop.close()

def start_alert_scheduler(patterns: dict):
    """
    Call once when patterns are loaded (after Session 3 memory retrieval).
    Guards against re-spawning on every Streamlit rerun.
    """
    if st.session_state.get("scheduler_started"):
        return
    if not patterns.get("usual_departure_hour"):
        return  # No departure pattern — nothing to schedule

    if not _scheduler.running:
        _scheduler.add_job(
            _run_alert_check,
            "interval",
            minutes=30,
            args=[patterns],
            id="departure_alerts",
            replace_existing=True
        )
        _scheduler.start()

    st.session_state["scheduler_started"] = True
```

---

#### Task 3.5.2 — Start scheduler after memory retrieval
**File**: `agent/core.py`

After `_extract_memory_context()` returns patterns in `plan_commute()`, add:

```python
# Start proactive alert scheduler (non-blocking, guards against re-spawn)
from services.alert_service import start_alert_scheduler
if user_patterns:
    start_alert_scheduler(user_patterns)
```

This is the only change to `agent/core.py`. It is additive — no existing logic is modified.

---

#### Task 3.5.3 — Alert banner in UI
**File**: `ui/streamlit_app.py`

At the very top of the main content area (after sidebar, before tabs), add:

```python
# Proactive alerts — written by background scheduler, consumed once on render
pending_alerts = st.session_state.pop("pending_alerts", [])
for alert in pending_alerts:
    if alert["severity"] == "warning":
        st.warning(f"**Heads up for your usual commute:** {alert['message']}  \n_{alert['suggestion']}_")
    else:
        st.info(f"**Commute update:** {alert['message']}  \n_{alert['suggestion']}_")
```

`st.session_state.pop` ensures alerts show once and don't repeat on the next rerun.

---

#### Task 3.5.4 — Add `usual_metro_line` to pattern detector
**File**: `services/memory_service.py`

In `detect_patterns()`, add one more field to the return dict:

```python
# Most used metro line — derived from route_label field in trip history
metro_trips = [t for t in trips if t.get("mode") == "metro_hybrid" and t.get("route_label")]
line_mentions = []
for t in metro_trips:
    label = t["route_label"]
    for line in ["Blue", "Yellow", "Red", "Green", "Violet", "Pink", "Magenta", "Orange"]:
        if line in label:
            line_mentions.append(line)
            break
usual_metro_line = Counter(line_mentions).most_common(1)[0][0] if line_mentions else None
```

Add `"usual_metro_line": usual_metro_line` to the return dict.

---

### Session 3.5 test checklist
- [ ] Log 5+ trips with a consistent departure time (e.g. all around 5:30pm)
- [ ] `detect_patterns()` returns `usual_departure_hour ≈ 17.5` and a `most_frequent_route`
- [ ] Set system time to ~4:15pm and open/refresh the app → alert banner appears
- [ ] Alert banner disappears after one render (not repeated on next rerun)
- [ ] No alert for users with < 5 trips or no `usual_departure_hour`
- [ ] App does not slow down — scheduler is background thread, non-blocking
- [ ] If `crowding_service` is not yet built, no ImportError — crowding check is silently skipped

### Session 3.5 Claude Code prompt
```
Adding proactive departure alerts to my Streamlit commute agent.
Sessions 1, 2, and 3 are complete. detect_patterns() now returns usual_departure_hour,
most_frequent_route, and usual_metro_line.
I need:
1. A new services/alert_service.py with APScheduler background job
   - Checks every 30 min: is user's usual departure 30–90 min away?
   - If yes: fetch weather for most_frequent_route origin (reuse st.session_state cache if < 15 min old)
   - Optional crowding check — import crowding_service conditionally, skip if ImportError
   - Write results to st.session_state["pending_alerts"]
   - Guard against scheduler re-spawn on Streamlit reruns with st.session_state["scheduler_started"]
2. One 3-line addition to agent/core.py after _extract_memory_context() — calls start_alert_scheduler(patterns)
3. Alert banner in ui/streamlit_app.py — st.warning/st.info, consumed once with session_state.pop()
Here are agent/core.py, services/memory_service.py, ui/streamlit_app.py: [paste]
Do not modify any existing functions — all changes must be purely additive.
```

---

## Session 4 — What-if simulator

### Goal
User can drag a "leave time" slider and see route scores update in real time. The agent runs a sweep and identifies the optimal departure window with a one-sentence explanation.

### Tasks

#### Task 4.1 — Leave-time sweep engine
**New file**: `services/what_if_service.py`

```python
async def sweep_leave_times(
    origin: str, destination: str,
    base_time: datetime, window_minutes: int = 60, step_minutes: int = 10,
    weather_impact: dict = None
) -> list[dict]:
    """
    Re-scores routes across a time window.
    Returns list of {leave_time, best_route_label, score, duration_min, cost}
    Key insight: only traffic changes across the sweep — weather is constant.
    Reuse cached weather, only re-call get_traffic_conditions per time slot.
    """
```

---

#### Task 4.2 — Add `simulate_leave_time` agent tool
**File**: `agent/tools.py`

New FunctionDeclaration: takes origin, destination, base_time, window_minutes. Returns sweep results plus recommendation: `{"optimal_leave": "08:20", "reason": "Avoids peak traffic, saves 18 min vs leaving now"}`.

---

#### Task 4.3 — What-if UI panel
**File**: `ui/streamlit_app.py`

After the main results, add a collapsible "What if I leave at a different time?" section:
- Slider: -60 min to +60 min from planned departure
- On slide: re-runs `what_if_service.sweep_leave_times()` (cached per origin/destination)
- Small bar chart: duration vs leave time, optimal slot highlighted in amber
- Agent narrates: "Leaving at 8:20 saves 18 minutes and ₹0 extra cost"

---

#### Task 4.4 — Integrate sweep into `plan_commute` flow
**File**: `agent/core.py`

After main routing, call `simulate_leave_time` automatically. Include the optimal window in `CommuteRecommendation.explanation` — the agent always tells the user the best departure time, not just the route.

---

### Session 4 test checklist
- [ ] What-if panel appears after results
- [ ] Slider updates chart without full re-plan
- [ ] Agent identifies and explains optimal departure window
- [ ] `simulate_leave_time` appears in `tool_calls_made`

---

## Session 5 — Heat index + crowding advisory

### Goal
The agent reasons across two independent data sources (weather + time-based crowding model) and recommends the safest mode combination. After this session, `crowding_service.py` exists — the Session 3.5 alert system automatically starts using crowding data with no code changes required.

### Tasks

#### Task 5.1 — Heat index calculator
**File**: `services/weather_service.py`

```python
def compute_heat_index(temp_c: float, humidity_pct: float) -> dict:
    """
    Steadman formula (simplified).
    Returns: {"heat_index_c": float, "category": "comfortable|warm|hot|dangerous", "advisory": str}
    Categories: <27 comfortable, 27-32 warm, 32-41 hot, >41 dangerous
    """
```

---

#### Task 5.2 — Metro crowding model
**New file**: `services/crowding_service.py`

```python
CROWDING_PROFILES = {
    "Blue":   {"peak_am": (7.5, 10.5, 0.95), "peak_pm": (17.5, 20.5, 0.90), "off_peak": 0.40},
    "Yellow": {"peak_am": (8.0, 10.0, 0.92), "peak_pm": (17.0, 20.0, 0.88), "off_peak": 0.35},
    "Red":    {"peak_am": (7.5, 10.0, 0.88), "peak_pm": (17.5, 20.0, 0.85), "off_peak": 0.38},
    "Green":  {"peak_am": (8.0, 10.5, 0.82), "peak_pm": (17.0, 19.5, 0.80), "off_peak": 0.30},
    "Violet": {"peak_am": (7.5, 10.0, 0.85), "peak_pm": (17.0, 20.0, 0.83), "off_peak": 0.35},
}

def estimate_crowding(line: str, hour: float) -> dict:
    """Returns: {"occupancy": 0.0-1.0, "label": "empty|moderate|crowded|very crowded", "coach_tip": str}"""
```

Once this file exists, the Session 3.5 alert service will automatically pick it up via its conditional import — no further changes needed.

---

#### Task 5.3 — Combined heat + crowding advisory tool
**File**: `agent/tools.py`

New FunctionDeclaration: `get_comfort_advisory` — takes origin_lat, origin_lng, destination_lat, destination_lng, departure_time. Returns:
```json
{
  "heat_index_c": 39,
  "heat_category": "hot",
  "metro_line": "Blue Line",
  "crowding_occupancy": 0.92,
  "crowding_label": "very crowded",
  "recommendation": "metro",
  "reasoning": "It's 39°C outside. The Blue Line is very crowded at this hour. Walking segments under 500m are acceptable. Metro still preferred for cost certainty."
}
```

The `reasoning` field is the agent's synthesis — not just data passthrough.

---

#### Task 5.4 — Comfort advisory card in UI
**File**: `ui/streamlit_app.py`

Add a card between the weather box and the route tabs:
- Heat index badge (green/amber/red)
- Metro crowding badge with coach tip
- One-line agent reasoning

---

#### Task 5.5 — Wire advisory into scoring
**File**: `services/hybrid_route_service.py`

- If `heat_category == "dangerous"` and walk segment > 500m → penalise comfort score by 0.3
- If `crowding_occupancy > 0.85` → penalise metro comfort score by 0.2 (don't eliminate)

---

### Session 5 test checklist
- [ ] Heat index displays correctly for current weather
- [ ] Crowding label changes by time of day (test 9am vs 2pm vs 6pm)
- [ ] Agent reasoning synthesises both signals in plain language
- [ ] Scoring penalises dangerous walk segments
- [ ] `get_comfort_advisory` visible in `tool_calls_made`
- [ ] Proactive alerts (Session 3.5) now also show crowding warnings — verify no extra code needed

---

## Session 6 — Monthly cost tracker

### Goal
The agent proactively surfaces monthly spend analysis and savings opportunities without being asked.

### UI tab structure (canonical — carry forward to Sessions 7 and 8)
The app has three tabs from this session onward:
```
[Plan Commute]  [My Commutes]  [Chat]
```
Session 7 adds the reasoning trace *inside* the Plan Commute tab as a `st.expander` — not a new tab. Do not change the tab structure after Session 6.

### Tasks

#### Task 6.1 — Trip cost aggregation
**File**: `database/supabase_client.py`

```python
def get_monthly_spend(user_id: str, month: str) -> dict:
    # month format: "2025-01"
    # Returns: {total_spent, by_mode, trip_count, avg_trip_cost}
```

---

#### Task 6.2 — Savings opportunity detector
**File**: `services/memory_service.py`

```python
def detect_savings_opportunities(spend: dict, trips: list) -> list[dict]:
    # Identifies trips where metro was available but cab was taken
    # Returns: [{"date", "route", "cab_cost", "metro_cost", "saving"}]
```

---

#### Task 6.3 — Add `get_cost_insights` agent tool
**File**: `agent/tools.py`

Returns monthly spend + top 3 savings opportunities + agent summary:
`"You've spent ₹3,240 on cabs this month. Switching to metro on your 3 most frequent routes would save ₹1,840/month."`

---

#### Task 6.4 — "My Commutes" tab
**File**: `ui/streamlit_app.py`

Third tab in the canonical `[Plan Commute] [My Commutes] [Chat]` layout:
- Monthly spend bar chart (cab vs metro, side by side)
- Savings opportunities table
- Agent insight card at top with proactive summary
- Only shown if user has ≥ 5 logged trips; otherwise shows "Plan a few more commutes to unlock insights."

---

#### Task 6.5 — Proactive cost nudge in plan results
**File**: `agent/core.py`

In `plan_commute()` post-processing: if the recommended route is a cab and the user has previously taken metro on the same O→D pair, add to explanation:
`"Note: You've taken metro on this route before (₹45). Today's cab option costs ₹220."`

---

### Session 6 test checklist
- [ ] Monthly spend aggregates correctly from Supabase trip log
- [ ] Savings opportunities identified for trips where cheaper option existed
- [ ] "My Commutes" tab visible after 5+ trips
- [ ] Proactive cost nudge appears on cab routes where metro history exists
- [ ] `get_cost_insights` in `tool_calls_made`
- [ ] Tab layout is `[Plan Commute] [My Commutes] [Chat]` — confirm structure before Session 7

---

## Session 7 — Agent narration + tool-chain visibility

### Goal
Make the agent's reasoning process visible and impressive to a panel. The tab structure from Session 6 is not changed — all additions go inside existing tabs.

### Tasks

#### Task 7.1 — Tool call trace UI
**File**: `ui/streamlit_app.py`

Inside the **Plan Commute tab**, add a `st.expander("Agent reasoning trace", expanded=False)`:
```
[1] get_weather          → risk: 0.6 (heavy rain)
[2] get_comfort_advisory → heat: 38°C, crowding: high
[3] get_route_options    → 3 options scored
[4] get_user_history     → 12 past trips loaded
[5] simulate_leave_time  → optimal: 08:20 (saves 18 min)
```
Open this expander during the demo to show the panel the full tool chain.

---

#### Task 7.2 — Structured explanation format
**File**: `agent/prompts.py`

Add to system prompt:
```
Structure your explanation in two parts:
1. SUMMARY (2 sentences max): what you recommend and the single most important reason.
2. REASONING (bullet points): key factors — weather, crowding, cost, user history, time.
Label both sections clearly. SUMMARY must be readable standalone.
```

---

#### Task 7.3 — Hindi language toggle
**File**: `ui/streamlit_app.py` + `agent/prompts.py`

Sidebar toggle: English / Hindi. When Hindi is selected, pass as additional system instruction:
`"Respond in Hindi. Use simple, conversational Hindi — not formal."`
Route labels and numeric metrics stay in English — only the explanation text changes.

---

#### Task 7.4 — Uncertainty flagging
**File**: `agent/core.py`

If any tool in `tool_calls_made` returned an empty or failed result, append to the explanation:
`"Note: [tool name] returned no data — this recommendation may be less accurate."`
Never silently ignore tool failures.

---

### Session 7 test checklist
- [ ] Reasoning trace expander shows all tools called with brief one-line results
- [ ] Explanation has clear SUMMARY + REASONING structure
- [ ] Hindi toggle produces Hindi explanation text
- [ ] Failed tool calls are flagged in the explanation
- [ ] Tab structure unchanged: `[Plan Commute] [My Commutes] [Chat]`

---

## Session 8 — UI polish + demo hardening

### Goal
The app should be demo-ready: fast, resilient, with a clear narrative flow.

### Tasks

#### Task 8.1 — Demo quick-fill buttons
Three sidebar buttons showcasing each major feature:
- **"Delhi peak hour"** → Dwarka Sector 21 → Connaught Place, 9:00am weekday
- **"Mumbai multi-city"** → Andheri → Bandra Kurla Complex, 8:30am
- **"Extreme heat"** → mock weather to 43°C for dramatic heat advisory + proactive alert demo

---

#### Task 8.2 — Step-by-step loading states
Replace generic spinner with named progress steps using `st.status()` (Streamlit ≥ 1.28) or `st.empty()` fallback:
```
Checking weather...
Finding routes...
Analysing your commute history...
Scoring options...
```

---

#### Task 8.3 — Error resilience
Wrap every external call in try/except with graceful degradation:
- Google Maps down → show cached/estimated route with warning banner
- OpenWeatherMap down → skip weather scoring, proceed with neutral weights, note in trace
- Supabase down → skip personalisation and trip logging, note in trace

---

#### Task 8.4 — README
```markdown
## Setup

### API keys — create `.env` at project root:
GOOGLE_MAPS_API_KEY=...       # Geocoding, Directions, Places APIs must be enabled
OPENWEATHERMAP_API_KEY=...    # Free tier sufficient
SUPABASE_URL=...
SUPABASE_ANON_KEY=...
GEMINI_API_KEY=...

### GTFS data:
Download DMRC-GTFS from [source URL] and place the folder at project root as `DMRC-GTFS/`.
Required files: stops.txt, routes.txt, trips.txt, stop_times.txt

### Run:
pip install -r requirements.txt
streamlit run ui/streamlit_app.py
```

---

#### Task 8.5 — Requirements audit
```
pip freeze > requirements.txt
```
Confirm present: `streamlit`, `google-generativeai`, `googlemaps`, `httpx`, `supabase`, `apscheduler`, `python-dotenv`.

---

### Session 8 test checklist
- [ ] All 3 demo buttons produce results within 8 seconds
- [ ] Loading steps visible during planning
- [ ] App doesn't crash if one API is unavailable
- [ ] README allows fresh clone to run in under 10 minutes
- [ ] Proactive alert demo works: set system time to 60 min before "usual departure", refresh, see banner

---

## Prompts for Claude Code

### Session 1 prompt
```
I'm building a Streamlit commute planning app (Python, Gemini 2.5 Flash, Google Maps API, GTFS).
Currently it only works for Delhi using local GTFS data. I need to add multi-city support.
Note: haversine() has been moved to utils/geo.py — import from there, never from metro_service.py itself.
Here are the relevant files: [paste metro_service.py, hybrid_route_service.py, google_maps_client.py, utils/geo.py]
Follow Session 1 tasks in order. After each task, show the complete updated file and confirm what to test.
```

### Session 2 prompt
```
Adding Supabase Auth to my Streamlit commute app — magic link + Google OAuth, soft gate pattern.
Here are my current files: database/supabase_client.py, ui/streamlit_app.py, config.py [paste all three]
Key design: use user.id (Supabase UUID) for logged-in users. Guests get no DB writes — get_user_id()
returns None for guests so all DB operations are skipped gracefully.
Follow Session 2 tasks. Auth must be additive — guests can plan commutes without restrictions.
```

### Session 3 prompt
```
Adding commute memory and personalisation to my commute agent.
Sessions 1 and 2 are complete — Supabase is wired, trips are being logged.
I need a pattern detector and memory injection into the Gemini agentic loop.
IMPORTANT: detect_patterns() must return these fields in addition to the basics:
  - usual_departure_hour (float, e.g. 17.5 for 5:30pm) — median departure hour from planned_at timestamps
  - most_frequent_route (dict: {origin, destination}) — top O→D pair as structured fields
  - usual_metro_line (str) — most used line from route_label history
These are required by Session 3.5 (proactive alerts). Build them now so 3.5 has no rework.
Here are agent/core.py and services/hybrid_route_service.py: [paste]
```

### Session 3.5 prompt
```
Adding proactive departure alerts to my Streamlit commute agent.
Sessions 1–3 are complete. detect_patterns() returns usual_departure_hour, most_frequent_route,
and usual_metro_line.
I need:
1. New file services/alert_service.py — APScheduler background job, checks every 30 min,
   writes to st.session_state["pending_alerts"], guards against re-spawn with session_state["scheduler_started"]
2. Weather reuses st.session_state["last_weather_cache"] if < 15 min old
3. Crowding import is conditional (try/except ImportError) — crowding_service.py not built until Session 5
4. 3-line addition to agent/core.py after _extract_memory_context() — calls start_alert_scheduler(patterns)
5. Alert banner at top of ui/streamlit_app.py — consumed once with session_state.pop()
Do not modify any existing functions — all changes are additive.
Here are agent/core.py, services/memory_service.py, ui/streamlit_app.py: [paste]
```

### Session 4 prompt
```
Adding a what-if leave-time simulator to my commute agent.
Here is the current plan_commute() loop in agent/core.py and hybrid_route_service.py: [paste]
I need a sweep_leave_times() service, a new Gemini tool, and a Streamlit slider UI.
The sweep should reuse cached weather and only re-call traffic per time slot.
Follow Session 4 tasks.
```

### Session 5 prompt
```
Adding heat index + metro crowding advisory to my commute agent.
Here are weather_service.py, the Streamlit UI file, and services/alert_service.py: [paste all three]
I need: Steadman heat index formula, a time-based crowding heuristic for Delhi Metro lines
(in a new services/crowding_service.py), a get_comfort_advisory agent tool, and UI badges.
IMPORTANT: once crowding_service.py exists, the Session 3.5 alert service automatically picks it up
via its conditional import — verify this works with no additional changes.
Follow Session 5 tasks.
```

### Session 6 prompt
```
Adding monthly cost tracking and proactive savings insights.
Supabase trip log is live from Session 2. Here is supabase_client.py: [paste]
I need: spend aggregation, savings opportunity detector, get_cost_insights agent tool,
a "My Commutes" tab (second tab alongside Plan Commute and Chat), and a proactive cost nudge.
IMPORTANT: The canonical tab layout after this session is [Plan Commute] [My Commutes] [Chat].
Lock this in place — Session 7 adds content inside Plan Commute, not new tabs.
Follow Session 6 tasks.
```

### Session 7 prompt
```
Making the agent's reasoning visible for demo.
Here are agent/core.py, agent/prompts.py, ui/streamlit_app.py: [paste]
I need: tool call trace as a st.expander inside the Plan Commute tab (NOT a new tab),
SUMMARY + REASONING explanation structure, Hindi toggle, and uncertainty flagging.
Tab layout must remain [Plan Commute] [My Commutes] [Chat] — do not add or remove tabs.
Follow Session 7 tasks.
```

### Session 8 prompt
```
Final polish and demo hardening for hackathon presentation.
All features are built including proactive alerts (Session 3.5).
I need: 3 demo quick-fill buttons (including an "Extreme heat" mock that also triggers a proactive alert),
step-by-step loading states, graceful API failure handling, and complete README with all API key setup.
Prioritise resilience — the app must not crash during demo.
Follow Session 8 tasks.
```
