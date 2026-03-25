# Predictive Commute Agent — Execution Plan

> **Stack**: Python · Streamlit · Gemini 2.5 Flash · Google Maps API · Supabase · GTFS  
> **Goal**: Build a genuinely agentic, India-first commute planner that impresses a hackathon panel.  
> **Approach**: One session = one shippable slice. Each session ends with a working, demo-able state.

---

## Session index

| # | Session | Core deliverable |
|---|---------|-----------------|
| 1 | Multi-city support | App works for Mumbai, Bangalore, Chennai — not just Delhi |
| 2 | Supabase wiring | User identity, saved commutes, trip log persisted |
| 3 | Commute memory + personalisation | Agent learns patterns, pre-weights scoring |
| 4 | What-if simulator | Leave-time sweep with agent narration |
| 5 | Heat index + crowding advisory | Two-source reasoning, safest mode recommendation |
| 6 | Monthly cost tracker | Spend aggregation + proactive savings insight |
| 7 | Agent narration + tool-chain visibility | Visible reasoning, Hindi/English toggle |
| 8 | UI polish + demo hardening | Demo flow, edge-case handling, README |

---

## Session 1 — Multi-city support

### Goal
The app currently hard-codes Delhi Metro logic. After this session, Option 1 (Google Maps Transit) and Option 2 (Cab) work for any Indian city. Option 3 (Metro Hybrid) works for Delhi via GTFS and gracefully falls back to a Google Maps places search for all other cities.

### Why this matters for the agent
City detection requires the agent to reason about which tool chain to invoke — GTFS path vs Places API path. This is the first genuine conditional branch in the agentic loop.

### Tasks

#### Task 1.1 — Fix GTFS path resolution
The app crashes when run from `ui/` because paths are relative. Fix before anything else.

**File**: `services/metro_service.py`

```python
# Add at top of file
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
When a user types "Connaught Place" or "Bandra", the agent needs to know which city they're in.

**File**: `mcp/google_maps_client.py`

Add a `detect_city()` method that extracts city name from a geocode result:

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
Replace the GTFS-only `find_nearest_station()` path with a two-track approach.

**File**: `services/metro_service.py`

```python
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

    # Non-Delhi: Places API fallback
    from mcp.google_maps_client import mcp_maps
    places = await mcp_maps.search_places(
        f"metro station near {lat},{lng}", lat=lat, lng=lng, radius=5000
    )
    if not places:
        return None
    nearest = places[0]
    # Calculate distance
    from services.metro_service import haversine  # reuse existing helper
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
4. Update the route label: append `(city)` for non-Delhi cities, e.g. `"Metro (Auto + Metro + Walk) [Mumbai]"`

---

#### Task 1.5 — Add city to agent tool: `get_route_options`
**File**: `agent/tools.py`

Add `city` as an optional string parameter to the `get_route_options` FunctionDeclaration. The agent should auto-populate it from geocoding when not provided by the user.

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

In the route tab header, show a small city badge next to the route label so the panel can see multi-city working live.

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
Add Supabase Auth (magic link + Google OAuth) with a soft gate UI. Guest users can plan commutes freely; signing in unlocks saved commutes, trip history, and personalisation. The agent gains access to a `get_user_history` tool backed by a real `user.id`.

### Auth design: soft gate
- App is fully usable without login
- Sidebar always shows a compact "Sign in" button when logged out
- Attempting to save a commute or view history triggers a polite auth prompt
- On sign-in success, `st.session_state["user"]` is populated with the Supabase user object
- `user.id` (UUID) replaces `session_id` everywhere in the data layer

---

### Tasks

#### Task 2.1 — Enable Supabase Auth providers
In your Supabase dashboard:
1. Go to **Authentication → Providers**
2. Enable **Email** (magic link — disable "Confirm email" for hackathon speed)
3. Enable **Google** — create OAuth credentials in Google Cloud Console, paste client ID + secret into Supabase
4. Set redirect URL to `http://localhost:8501` (and your deployed URL if applicable)

No code changes yet — this is dashboard config only.

---

#### Task 2.2 — Activate and harden Supabase schema
Run `database/migrations/001_initial_schema.sql`. Verify or add these tables:

```sql
-- Users (mirrors auth.users, add profile fields here)
create table if not exists public.profiles (
  id uuid references auth.users(id) primary key,
  display_name text,
  preferred_language text default 'en',
  created_at timestamptz default now()
);

-- Trips log
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

-- Saved commutes
create table if not exists public.saved_commutes (
  id uuid default gen_random_uuid() primary key,
  user_id uuid references auth.users(id),
  name text not null,  -- e.g. "Home → Office"
  origin text not null,
  destination text not null,
  created_at timestamptz default now()
);

-- Row-level security: users only see their own data
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
    """Returns Supabase user dict if logged in, else None."""
    return st.session_state.get("user")

def is_logged_in() -> bool:
    return get_current_user() is not None

def sign_in_magic_link(email: str) -> bool:
    """Sends magic link. Returns True on success."""
    try:
        supabase.auth.sign_in_with_otp({"email": email})
        return True
    except Exception:
        return False

def sign_in_google() -> str:
    """Returns Google OAuth redirect URL."""
    resp = supabase.auth.sign_in_with_oauth({
        "provider": "google",
        "options": {"redirect_to": "http://localhost:8501"}
    })
    return resp.url

def sign_out():
    supabase.auth.sign_out()
    st.session_state.pop("user", None)

def handle_auth_callback():
    """
    Call once at app startup. Checks URL params for OAuth callback tokens
    and populates st.session_state['user'] if valid.
    """
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
    # Also restore from existing Supabase session
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

Add to sidebar, above the existing controls:

```python
from auth.supabase_auth import (
    get_current_user, is_logged_in, sign_in_magic_link,
    sign_in_google, sign_out, handle_auth_callback
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
                    st.error("Failed to send link. Check your email address.")
        with tab_g:
            google_url = sign_in_google()
            st.link_button("Continue with Google", google_url)
```

---

#### Task 2.5 — Soft gate helper
**File**: `ui/streamlit_app.py`

Add a reusable helper that gates any feature behind auth:

```python
def require_auth(feature_name: str = "this feature") -> bool:
    """
    Returns True if user is logged in.
    If not, shows a polite inline prompt instead of the feature.
    """
    if is_logged_in():
        return True
    st.info(
        f"Sign in to use {feature_name}. "
        "You can still plan commutes as a guest."
    )
    return False
```

Use it like:
```python
if st.button("Save this commute"):
    if require_auth("saved commutes"):
        # ... save logic
```

---

#### Task 2.6 — Log trips after planning
**File**: `ui/streamlit_app.py`

After `agent.plan_commute()` returns, log the trip if user is signed in:

```python
from database.supabase_client import supabase

def log_trip_async(user_id: str, plan):
    try:
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
    except Exception as e:
        pass  # Never crash the UI for logging

# After plan is returned:
user = get_current_user()
if user:
    import threading
    threading.Thread(target=log_trip_async, args=(user.id, plan), daemon=True).start()
```

---

#### Task 2.7 — Implement saved commutes
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

Add to sidebar (only when logged in):
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

New FunctionDeclaration: `get_user_history` — takes `user_id`, queries Supabase `trips` table, returns last 10 trips. Agent uses this in Session 3 for personalisation.

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
- [ ] Guest can plan commutes with no restrictions
- [ ] "Save commute" button shows auth prompt for guests, saves for logged-in users
- [ ] Trip appears in Supabase `trips` table after planning (when logged in)
- [ ] Saved commutes appear in sidebar and one-tap replans correctly
- [ ] RLS: user A cannot query user B's trips

### Session 2 prompt for Claude Code
```
Adding Supabase Auth to my Streamlit commute app — magic link + Google OAuth, soft gate pattern.
Here are my current files: database/supabase_client.py, ui/streamlit_app.py, config.py [paste all three]
I need:
1. A new auth/supabase_auth.py module with magic link, Google OAuth, session restore, and sign-out
2. Sidebar auth widget — expander with magic link tab and Google button, shows email when signed in
3. require_auth() soft gate helper used on save/history features
4. Trip logging to Supabase after plan_commute() completes (background thread, non-blocking)
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
def detect_patterns(trips: list[dict]) -> dict:
    """
    Analyzes trip history and returns detected patterns.
    Example output:
    {
        "preferred_mode": "metro",         # most chosen route type
        "peak_cab_usage": True,            # takes cabs Mon-Fri 8-10am
        "usual_duration_min": 42,          # median trip duration
        "route_frequency": {               # most common O→D pairs
            "Dwarka → CP": 8
        }
    }
    """
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
2. Pass result to a new internal method `_extract_memory_context(history)` 
3. Inject the result as an additional message into the Gemini context: `"User memory context: {patterns}"`

This makes the memory retrieval visible in `tool_calls_made` — the panel can see it happening.

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

---

## Session 4 — What-if simulator

### Goal
User can drag a "leave time" slider and see route scores update in real time. The agent runs a sweep and identifies the optimal departure window with a one-sentence explanation.

### Tasks

#### Task 4.1 — Leave-time sweep engine
**File**: `services/what_if_service.py` _(new file)_

```python
async def sweep_leave_times(
    origin: str, destination: str,
    base_time: datetime, window_minutes: int = 60, step_minutes: int = 10,
    weather_impact: dict = None
) -> list[dict]:
    """
    Re-scores routes across a time window.
    Returns list of {leave_time, best_route_label, score, duration_min, cost}
    """
```

Key insight: only traffic conditions change across the sweep — weather and metro schedule are constant. Re-use cached weather, only re-call `get_traffic_conditions` with different departure time.

---

#### Task 4.2 — Add `simulate_leave_time` agent tool
**File**: `agent/tools.py`

New FunctionDeclaration: `simulate_leave_time` — takes origin, destination, base_time, window_minutes. Returns the sweep results plus the agent's recommendation: `{"optimal_leave": "08:20", "reason": "Avoids peak traffic, saves 18 min vs leaving now"}`.

---

#### Task 4.3 — What-if UI panel
**File**: `ui/streamlit_app.py`

After the main results, add a collapsible "What if I leave at a different time?" section:
- Slider: -60 min to +60 min from planned departure
- On slide: re-runs `what_if_service.sweep_leave_times()` (cached per origin/destination)
- Shows a small bar chart: duration vs leave time
- Highlights the optimal slot in amber
- Agent narrates: "Leaving at 8:20 saves 18 minutes and ₹0 extra cost"

---

#### Task 4.4 — Integrate sweep into plan_commute flow
**File**: `agent/core.py`

After main routing, call `simulate_leave_time` automatically and include the optimal window in `CommuteRecommendation.explanation`. This means the agent always tells the user the best departure time, not just the route.

---

### Session 4 test checklist
- [ ] What-if panel appears after results
- [ ] Slider updates chart without full re-plan
- [ ] Agent identifies and explains optimal departure window
- [ ] `simulate_leave_time` appears in `tool_calls_made`

---

## Session 5 — Heat index + crowding advisory

### Goal
The agent reasons across two independent data sources (weather + time-based crowding model) and makes a recommendation about the safest mode combination — not just the fastest or cheapest.

### Tasks

#### Task 5.1 — Heat index calculator
**File**: `services/weather_service.py`

Add `compute_heat_index(temp_c: float, humidity_pct: float) -> dict`:
```python
# Steadman formula (simplified)
# Returns: {"heat_index_c": float, "category": "comfortable|warm|hot|dangerous", "advisory": str}
```
Categories: <27°C comfortable, 27-32 warm, 32-41 hot (avoid prolonged outdoor exposure), >41 dangerous (minimise walking).

---

#### Task 5.2 — Metro crowding model
**File**: `services/crowding_service.py` _(new file)_

Build a time + line heuristic model. No live data needed — model from known patterns:

```python
CROWDING_PROFILES = {
    "Blue":   {"peak_am": (7.5, 10.5, 0.95), "peak_pm": (17.5, 20.5, 0.90), "off_peak": 0.40},
    "Yellow": {"peak_am": (8.0, 10.0, 0.92), "peak_pm": (17.0, 20.0, 0.88), "off_peak": 0.35},
    # ... other lines
}

def estimate_crowding(line: str, hour: float) -> dict:
    # Returns: {"occupancy": 0.0-1.0, "label": "empty|moderate|crowded|very crowded", "coach_tip": str}
```

Coach tip example: "Board from the rear — less crowded on this line at this hour."

---

#### Task 5.3 — Combined heat + crowding advisory
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
  "reasoning": "It's 39°C outside. The Blue Line is very crowded at this hour. Walking segments under 500m are acceptable; avoid longer outdoor exposure. Metro still preferred over cab for cost and certainty."
}
```

The `reasoning` field is the agent's synthesis — not just data passthrough.

---

#### Task 5.4 — Surface advisory in UI
**File**: `ui/streamlit_app.py`

Add a "Comfort advisory" card between the weather box and the route tabs:
- Heat index badge (colour-coded: green/amber/red)
- Metro crowding badge with coach tip
- One-line agent reasoning

---

#### Task 5.5 — Wire advisory into scoring
**File**: `services/hybrid_route_service.py`

If `heat_category == "dangerous"` and walk segment > 500m → penalise that route's comfort score by 0.3.
If `crowding_occupancy > 0.85` → penalise metro comfort score by 0.2 (but don't eliminate it).

---

### Session 5 test checklist
- [ ] Heat index displays correctly for current weather
- [ ] Crowding label changes by time of day (test 9am vs 2pm vs 6pm)
- [ ] Agent reasoning field synthesises both signals in plain language
- [ ] Scoring penalises dangerous walk segments
- [ ] `get_comfort_advisory` visible in `tool_calls_made`

---

## Session 6 — Monthly cost tracker

### Goal
The agent proactively surfaces monthly spend analysis and savings opportunities without being asked. This is the clearest demonstration of proactive agency.

### Tasks

#### Task 6.1 — Trip cost aggregation
**File**: `database/supabase_client.py`

Add `get_monthly_spend(user_id: str, month: str) -> dict`:
```python
# Returns:
{
    "total_spent": 3240,
    "by_mode": {"cab": 2800, "metro": 440},
    "trip_count": {"cab": 14, "metro": 6},
    "avg_trip_cost": {"cab": 200, "metro": 73},
}
```

---

#### Task 6.2 — Savings opportunity detector
**File**: `services/memory_service.py`

Add `detect_savings_opportunities(spend: dict, trips: list) -> list[dict]`:
```python
# Identifies specific trips where metro was available but cab was taken.
# Returns list of: {"date": ..., "route": ..., "cab_cost": 220, "metro_cost": 45, "saving": 175}
```

---

#### Task 6.3 — Add `get_cost_insights` agent tool
**File**: `agent/tools.py`

New FunctionDeclaration: returns monthly spend + top 3 savings opportunities + a one-line agent summary: `"You've spent ₹3,240 on cabs this month. Switching to metro on your 3 most frequent routes would save ₹1,840/month."`.

---

#### Task 6.4 — Cost tracker UI tab
**File**: `ui/streamlit_app.py`

Add a third tab "My commutes" (alongside Plan Commute, Chat):
- Monthly spend bar chart (cab vs metro)
- Savings opportunities table
- Agent insight card at top with the proactive summary
- Only shown if user has ≥5 logged trips

---

#### Task 6.5 — Proactive cost nudge in plan results
If the user is taking a cab but has taken this same route by metro before, the agent adds to its explanation: `"Note: You've taken metro on this route before (₹45). Today's cab option costs ₹220."` — without being asked.

**File**: `agent/core.py` — add check in `plan_commute()` post-processing.

---

### Session 6 test checklist
- [ ] Monthly spend aggregates correctly from Supabase trip log
- [ ] Savings opportunities identified for trips where cheaper option existed
- [ ] "My commutes" tab visible after 5+ trips
- [ ] Proactive cost nudge appears on cab routes where metro history exists
- [ ] `get_cost_insights` in `tool_calls_made`

---

## Session 7 — Agent narration + tool-chain visibility

### Goal
Make the agent's reasoning process visible and impressive to a panel. Show tool calls, show reasoning steps, add Hindi/English toggle.

### Tasks

#### Task 7.1 — Tool call trace UI
**File**: `ui/streamlit_app.py`

Expand the existing `tool_calls_made` display into a proper trace panel:
```
Agent reasoning trace
─────────────────────
[1] get_weather          → risk: 0.6 (heavy rain)
[2] get_comfort_advisory → heat: 38°C, crowding: high
[3] get_route_options    → 3 options scored
[4] get_user_history     → 12 past trips loaded
[5] simulate_leave_time  → optimal: 08:20 (saves 18 min)
```
Show this in a `st.expander("Agent reasoning trace")` — collapsed by default, open it during demo.

---

#### Task 7.2 — Reasoning narration in explanation
**File**: `agent/prompts.py`

Add to system prompt:
```
Structure your explanation in two parts:
1. SUMMARY (2 sentences max): what you recommend and the single most important reason.
2. REASONING (bullet points): the key factors you weighed — weather, crowding, cost, user history, time.
Label them clearly. The SUMMARY should be readable standalone.
```

---

#### Task 7.3 — Hindi language toggle
**File**: `ui/streamlit_app.py` + `agent/prompts.py`

Add a language toggle in the sidebar: English / Hindi. When Hindi is selected, pass `"Respond in Hindi. Use simple, conversational Hindi — not formal."` as an additional system instruction. Route labels and metrics stay in English; only the explanation text changes.

---

#### Task 7.4 — Uncertainty flagging
**File**: `agent/core.py`

If `tool_calls_made` contains a failed or empty tool result, the agent should include in explanation: `"Note: [tool name] returned no data — this recommendation may be less accurate."` Never silently ignore tool failures.

---

### Session 7 test checklist
- [ ] Reasoning trace shows all tools called with brief results
- [ ] Explanation has clear SUMMARY + REASONING structure
- [ ] Hindi toggle produces Hindi explanation text
- [ ] Failed tool calls are flagged in the explanation

---

## Session 8 — UI polish + demo hardening

### Goal
The app should be demo-ready: fast, resilient, with a clear narrative flow for the panel presentation.

### Tasks

#### Task 8.1 — Demo quick-fill buttons
Ensure sidebar has 3 demo buttons that showcase each major feature:
- "Delhi peak hour" → Dwarka Sector 21 → Connaught Place, 9:00am, weekday
- "Mumbai multi-city" → Andheri → Bandra Kurla Complex, 8:30am
- "Extreme heat scenario" → set weather mock to 43°C for dramatic heat advisory

---

#### Task 8.2 — Loading state improvements
Replace generic spinner with step-by-step progress: "Checking weather...", "Finding routes...", "Analysing your history...", "Scoring options...". Use `st.status()` if on Streamlit ≥1.28, otherwise `st.empty()` updates.

---

#### Task 8.3 — Error resilience
Wrap every external API call in try/except with graceful degradation:
- Google Maps down → show cached/estimated route with warning
- OpenWeatherMap down → skip weather, proceed with neutral scores
- Supabase down → skip personalisation, log warning in trace

---

#### Task 8.4 — README + GTFS setup instructions
Add to `README.md`:
- How to download DMRC-GTFS data (link to source)
- How to set up `.env` with all API keys
- How to run the app
- Screenshot of the UI

---

#### Task 8.5 — Requirements audit
Run `pip freeze > requirements.txt` and trim to only what's actually imported. Ensure `qrcode`, `folium`, `googlemaps`, `httpx`, `supabase`, `google-genai` are all present.

---

### Session 8 test checklist
- [ ] All 3 demo buttons produce impressive results within 8 seconds
- [ ] Loading steps visible during planning
- [ ] App doesn't crash if one API is unavailable
- [ ] README allows a fresh clone to run in under 10 minutes

---

## Prompts for Claude Code / pair programming

Use these verbatim when starting each session in Claude Code or a new chat:

### Session 1 prompt
```
I'm building a Streamlit commute planning app (Python, Gemini 2.5 Flash, Google Maps API, GTFS).
Currently it only works for Delhi using local GTFS data. I need to add multi-city support.
Here is my project context: [paste execution_plan.md Session 1 section]
Here are the relevant files: [paste metro_service.py, hybrid_route_service.py, google_maps_client.py]
Start with Task 1.1 (fix GTFS path resolution), then proceed through each task in order.
After each task, show me the complete updated file and confirm what to test.
```

### Session 2 prompt
```
Continuing my commute planning app. Session 1 (multi-city) is complete.
Now I need to wire up Supabase for user identity, saved commutes, and trip logging.
Here is my current supabase_client.py and the migration SQL: [paste both]
Follow the Session 2 tasks in execution_plan.md.
The session ID from Streamlit's st.session_state should be the user key — no login required.
```

### Session 3 prompt
```
Adding commute memory and personalisation to my commute agent.
Sessions 1 and 2 are complete — Supabase is wired, trips are being logged.
I need a pattern detector and memory injection into the Gemini agentic loop.
Here are agent/core.py and services/hybrid_route_service.py: [paste]
Follow Session 3 tasks. The key requirement: the agent must visibly call get_user_history
as part of tool_calls_made so the panel can see memory retrieval happening.
```

### Session 4 prompt
```
Adding a what-if leave-time simulator to my commute agent.
Here is the current plan_commute() loop in agent/core.py and hybrid_route_service.py: [paste]
I need a sweep_leave_times() service, a new Gemini tool, and a Streamlit slider UI.
The sweep should reuse cached weather and only re-call traffic for each time slot.
Follow Session 4 tasks in execution_plan.md.
```

### Session 5 prompt
```
Adding heat index + metro crowding advisory to my commute agent.
Here are weather_service.py and the Streamlit UI file: [paste]
I need: Steadman heat index formula, a time-based crowding heuristic for Delhi Metro lines,
a new get_comfort_advisory agent tool that synthesises both signals, and UI badges.
The agent's "reasoning" field must be a genuine synthesis sentence — not just data echo.
Follow Session 5 tasks.
```

### Session 6 prompt
```
Adding monthly cost tracking and proactive savings insights.
Supabase trip log is live from Session 2. Here is supabase_client.py: [paste]
I need: spend aggregation query, savings opportunity detector, get_cost_insights agent tool,
a "My commutes" UI tab, and a proactive cost nudge when agent recommends a cab
but user has metro history on the same route.
Follow Session 6 tasks.
```

### Session 7 prompt
```
Making the agent's reasoning visible and impressive for demo.
Here are agent/core.py, agent/prompts.py, and ui/streamlit_app.py: [paste]
I need: tool call trace UI, structured SUMMARY + REASONING explanation format,
Hindi language toggle, and uncertainty flagging when tools fail.
Follow Session 7 tasks.
```

### Session 8 prompt
```
Final polish and demo hardening for hackathon presentation.
All features are built. I need: demo quick-fill buttons for 3 scenarios,
step-by-step loading states, graceful API failure handling, and README setup instructions.
Follow Session 8 tasks. Prioritise resilience — the app must not crash during demo.
```