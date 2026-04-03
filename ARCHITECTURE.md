# Sherpa — Predictive Commute Planning Agent

> An AI-powered commute planning agent for Indian cities that uses real-time weather, traffic, metro crowding, and personal commute history to recommend the optimal route and exact departure time.

---

## Table of Contents

1. [Overview](#overview)
2. [Technology Stack](#technology-stack)
3. [Project Structure](#project-structure)
4. [Agent System](#agent-system)
5. [Agent Tools](#agent-tools)
6. [Agent Reasoning & Decision-Making](#agent-reasoning--decision-making)
7. [Services Layer](#services-layer)
8. [Database Architecture](#database-architecture)
9. [Authentication](#authentication)
10. [Maps Integration](#maps-integration)
11. [UI / Frontend](#ui--frontend)
12. [Configuration](#configuration)
13. [Docker & Deployment](#docker--deployment)
14. [Key Workflows](#key-workflows)
15. [External API Integrations](#external-api-integrations)
16. [Data Models](#data-models)
17. [Error Handling & Fallbacks](#error-handling--fallbacks)
18. [Security](#security)

---

## Overview

**Sherpa** is a full-stack agentic application that plans commutes for users in 8 Indian cities — Delhi, Mumbai, Bengaluru, Chennai, Hyderabad, Kolkata, Pune, and Ahmedabad. It goes beyond static routing by reasoning about:

- Live weather conditions and rain impact on road/metro delays
- Real-time road traffic from Google Maps
- Delhi Metro GTFS static timetable data
- Metro line crowding levels based on time-of-day heuristics
- User's own trip history and mode preferences
- Monthly cost tracking and savings opportunities

The core reasoning engine is **Google Gemini 2.5 Flash** operating in a function-calling loop. The agent calls specialist tools, synthesizes the results, and returns a single ranked recommendation with full reasoning trace.

---

## Technology Stack

| Layer | Technology |
|-------|-----------|
| **LLM** | Google Gemini 2.5 Flash (function calling) |
| **Agent Framework** | `google-genai` SDK — no LangChain / LangGraph |
| **Frontend** | Streamlit |
| **Maps** | Google Maps SDK (`googlemaps`) + Folium |
| **Weather** | OpenWeatherMap API |
| **Transit Data** | DMRC GTFS (CSV static files) + Google Maps Transit |
| **Database** | Supabase PostgreSQL 15+ with pgvector |
| **Auth** | Supabase Auth (Magic Link, Google OAuth) |
| **Async I/O** | `asyncio` + `httpx` throughout |
| **Scheduling** | APScheduler (background departure alerts) |
| **Containerization** | Docker (Python 3.11-slim) |

---

## Project Structure

```
Predictive-commute-planning/
├── agent/
│   ├── core.py                 # Agent orchestration — Gemini function-calling loop
│   ├── tools.py                # Tool schema declarations + executor dispatcher
│   └── prompts.py              # System prompt + per-language instructions
│
├── services/
│   ├── hybrid_route_service.py # Multi-modal routing (Google Maps + GTFS hybrid)
│   ├── metro_service.py        # Delhi Metro GTFS loader and query layer
│   ├── weather_service.py      # OpenWeatherMap client + commute impact scorer
│   ├── crowding_service.py     # Time-based metro occupancy heuristics
│   ├── memory_service.py       # Pattern detection from trip history
│   └── alert_service.py        # Background proactive departure alerts
│
├── database/
│   ├── supabase_client.py      # CRUD operations (trips, saved commutes)
│   ├── models.py               # Pydantic data models
│   └── migrations/
│       ├── 001_initial_schema.sql   # Core tables + pgvector
│       └── 002_auth_schema.sql      # Auth-linked tables + RLS policies
│
├── maps/
│   └── google_maps_client.py   # Directions, geocoding, autocomplete, traffic
│
├── auth/
│   └── supabase_auth.py        # Magic link + Google OAuth session management
│
├── ui/
│   └── streamlit_app.py        # Full Streamlit UI (3 tabs, maps, QR codes)
│
├── DMRC-GTFS/                  # Delhi Metro static GTFS data (stops, routes, trips)
├── GTFS/                       # Generic transit GTFS data
├── config.py                   # Pydantic Settings — loads from .env
├── dockerfile                  # Streamlit Docker image
├── requirements.txt
└── tests/
```

---

## Agent System

### Entry Point: `agent/core.py` — `CommuteAgent`

The agent is built as a **raw function-calling loop** against the Gemini API — no agent framework wrappers.

### Core Method

```python
async plan_commute(
    origin: str,
    destination: str,
    required_arrival: datetime,
    user_prefs: dict,
    user_id: str | None,
    language: str = "en"
) -> CommuteRecommendation
```

### Execution Flow

```
1.  Geocode origin  →  get lat/lng for weather
2.  Fetch user patterns (detect_patterns) from database  →  inject into context
3.  Build rich user message:
      - origin, destination, current time, planned departure
      - required arrival deadline
      - user preferences (buffer minutes, comfort level, walking limits)
      - memory context (preferred mode, usual duration, cost averages)
4.  Enter _run_agent_loop (max 6 rounds):
      a. Call Gemini: models.generate_content(messages, tools, system_prompt)
      b. If response has no function_call parts → done, extract final text
      c. If function_call parts present → execute each via execute_tool()
      d. Append FunctionResponse parts → next round
5.  _build_result:
      - Extract routes, leave_by, weather, urgency from accumulated tool results
      - LLM scoring (if enabled): reorder routes to match Gemini's expressed ranking
      - Build reasoning trace for UI (one-line summary per tool call)
      - Construct Uber / Ola / Rapido deep links
      - Return CommuteRecommendation dataclass
```

### `CommuteRecommendation` — Final Output Shape

```python
@dataclass
class CommuteRecommendation:
    explanation: str           # Full Gemini narrative (SUMMARY + REASONING)
    recommended_route: dict    # Top route option with steps, cost, duration
    alternatives: list[dict]   # Other route options
    urgency: UrgencyLevel      # low / medium / high / critical
    leave_by: datetime         # Computed latest safe departure time
    weather: dict              # Weather snapshot + delay risk
    tool_trace: list[str]      # Human-readable trace of tool calls
    ride_links: dict           # Uber, Ola, Rapido deep links + QR codes
```

---

## Agent Tools

Nine function-calling tools declared as Gemini `FunctionDeclaration` objects in `agent/tools.py`.

### Tool Catalog

| Tool | Purpose | Key Inputs | Returns |
|------|---------|-----------|---------|
| `get_weather` | Current weather at origin + commute impact score | `lat, lon` | `condition, temp_c, rain_mm, wind_kmh, delay_risk (0–1), alerts[], recommendation` |
| `get_route_options` | Ranked multi-modal route options | `origin, destination, departure_time_iso, required_arrival_iso, city` | `options[], ranking_required, ranking_context` |
| `get_traffic_conditions` | Real-time road traffic + estimated delay | `origin, destination` | `traffic_level, delay_seconds, duration_in_traffic` |
| `get_metro_status` | Check if a Delhi Metro line is operational | `line_name` | `operational, frequency_minutes, first_train, last_train` |
| `find_nearest_metro` | Closest metro station + walking time | `location` | `nearest_station, distance_km, walking_minutes, walk_directions_url` |
| `calculate_leave_time` | Latest safe departure given arrival deadline | `required_arrival_iso, route_duration_minutes, buffer_minutes` | `leave_by, urgency, risk_score, minutes_until_leave` |
| `get_comfort_advisory` | Heat index + metro crowding + coach tip | `lat, lon, metro_line, departure_time_iso` | `heat_index_c, crowding_label, coach_tip, early_departure, recommended_mode` |
| `get_user_history` | Last 10 trips from database | `user_id` | `trips[], count, patterns` |
| `get_cost_insights` | Monthly spend by mode + savings opportunities | `user_id, month` | `total_spent, by_mode, savings_opportunities[]` |

### Tool Executor: `execute_tool(tool_name, tool_args)`

A central dispatcher in `agent/tools.py`. Routes tool name strings to their implementation, wraps each call in try/except, and always returns a JSON string (so Gemini receives a valid `FunctionResponse` even on error).

---

## Agent Reasoning & Decision-Making

### System Prompt (`agent/prompts.py`)

The system prompt hardcodes the agent's reasoning strategy:

**Required calling order:**
1. `get_weather` first — using origin lat/lng
2. `get_route_options` — after weather context is known
3. `calculate_leave_time` — once a route duration is chosen
4. `get_comfort_advisory` — for heat and crowding check
5. Optionally: `get_user_history` (if authenticated), `get_cost_insights`

**Reasoning instructions:**
- Compare metro vs cab honestly: trade-offs on cost, reliability, and comfort
- Factor weather delay risk when recommending modes
- Personalise using injected memory context when available
- Rank routes if `ranking_required=true` in route response

### Urgency Logic

`calculate_leave_time` computes urgency based on buffer minutes remaining:

| Urgency | Buffer Left | Agent Behaviour |
|---------|------------|-----------------|
| `LOW` | ≥ 15 min | Recommend most comfortable route |
| `MEDIUM` | 5–15 min | Recommend fastest reliable route |
| `HIGH` | 0–5 min | Advise leaving immediately, prefer cab |
| `CRITICAL` | < 0 min | Cannot make it — cab/auto deep link as fallback |

### LLM Scoring Mode

When `settings.LLM_SCORING_ENABLED = true`:

- `get_route_options` returns routes **unranked** with a `ranking_context` block:
  ```json
  {
    "is_peak_hour": true,
    "buffer_minutes": 12,
    "user_preferred_mode": "metro_hybrid"
  }
  ```
- Gemini reasons about ranking based on current conditions
- `_build_result` reorders routes by matching keywords in Gemini's final explanation to route mode names

### Response Format Enforced by Prompt

```
**SUMMARY** (2 sentences max)
  → Single most important recommendation + primary reason

**REASONING**
  → Weather: [what conditions mean for the commute]
  → Route: [why this option was ranked first]
  → Crowding: [metro occupancy + coach tip if applicable]
  → Time: [urgency level + leave-by time]
  → History: [personalisation note if trip history available]
```

### Language Support

If `language="hi"` is passed, the system prompt appends:

> "Respond in Hindi (Devanagari script). Route labels and station names stay in English."

---

## Services Layer

### `hybrid_route_service.py` — Multi-modal Routing

**Class**: `HybridRouteService`

Builds up to 3 route options and scores them:

#### Option Builders

**1. Transit (Google Maps)**
- Calls Directions API with `mode="transit"`
- Extracts steps, counts transfers
- Estimates cost: metro ₹30, bus ₹15 per leg

**2. Cab / Ride-share**
- Uses Google Maps driving with traffic
- Cost model: `₹30 base + ₹12/km`
- Peak-hour surge: `1.5×` during 8–11 AM and 5–8 PM

**3. Metro Hybrid** (Delhi only)
- Finds nearest metro station to origin and destination using geopy
- Calculates walk times at 4.5 km/h
- Queries Delhi GTFS for metro segment
- Assembles: Walk → Metro → Walk
- Cost: walk (₹0) + metro fare (GTFS-derived)

#### Scoring (`_score_and_rank`)

Composite score formula with four factors:

| Factor | Weight |
|--------|--------|
| Duration | 40% |
| Cost | 25% |
| Comfort | 20% |
| Certainty | 15% |

Personalisation adjustments (when user patterns are available):
- Boost score for user's preferred mode
- Penalise crowded options if user avoids peak crowds
- Apply weather risk penalty based on `delay_risk`

---

### `metro_service.py` — Delhi Metro GTFS

**Class**: `DelhiMetroService`

Loads DMRC static GTFS CSV files at startup.

**Load Process:**
1. Parse `stops.txt` → station names + coordinates
2. Parse `routes.txt` → route colours → line name mapping
3. Join `trips.txt` + `stop_times.txt`:
   - Pick one representative trip per route
   - Extract station sequence and timings
   - Build `MetroConnection` objects (consecutive station pairs)
   - Estimate fares from distance
   - Detect interchange stations (present on >1 line)

**Key Methods:**

| Method | Description |
|--------|-------------|
| `get_line_info(line_name)` | Returns `MetroLine` with full station list |
| `is_operational(line_name, time)` | Checks 05:30–23:30 operating window |
| `find_nearest_station(lat, lng)` | Returns closest `MetroStation` (geopy) |
| `get_frequency(line_name, is_peak)` | Peak: 4 min, Off-peak: 8 min |

**Line Colour Mapping:**
```
YELLOW  → Yellow Line    BLUE    → Blue Line
MAGENTA → Magenta Line   PINK    → Pink Line
RED     → Red Line       GREEN   → Green Line
VIOLET  → Violet Line    ORANGE  → Airport Express
```

---

### `weather_service.py` — OpenWeatherMap

**Class**: `WeatherService`

**Commute Impact Scoring:**

| Condition | Delay Risk |
|-----------|-----------|
| Drizzle / light rain | 0.30 |
| Heavy rain | 0.65 |
| Thunderstorm | 0.85 |
| Fog / low visibility | 0.70 |
| Strong wind | 0.45 |
| Extreme heat (≥42°C) | 0.25 |

When `delay_risk ≥ 0.35` → sets `prefer_metro: true` in response.

**Heat Index** (Steadman formula):
- Active for `temp ≥ 27°C`; returns raw temp below that
- Categories: comfortable, warm, hot, dangerous

---

### `crowding_service.py` — Metro Crowding Heuristics

Pure time-based model — no live feed required.

**Peak Windows:**
- AM peak: 08:00–10:30 → occupancy 0.75–0.95
- PM peak: 17:00–20:30 → occupancy 0.70–0.90
- Off-peak: occupancy 0.20–0.40

**Key Functions:**

- `estimate_crowding(line, departure_time)` → `{occupancy, label, is_peak, coach_tip}`
  - Example coach tip: *"Board from rear coaches — less crowded toward Dwarka end"*
- `get_early_departure_suggestion(line, planned_departure, lead_minutes=30)` → Optional suggestion to leave 30 min earlier if departure is in peak window

**Occupancy Labels:** `empty`, `moderate`, `crowded`, `very crowded`

---

### `memory_service.py` — Pattern Detection

**Main Function:** `detect_patterns(trips: list[dict]) -> dict | None`

Patterns detected from trip history (minimum 3 trips):

| Pattern | Logic |
|---------|-------|
| `preferred_mode` | Mode used in ≥40% of trips |
| `peak_cab_usage` | Takes cabs during 8–11 AM or 5–8 PM (min 2 trips) |
| `usual_duration` | Median trip duration across history |
| `avg_cost_by_mode` | Mean cost per transport mode |
| `frequent_routes` | Top origin→destination pairs by frequency |
| `usual_departure_hour` | Median departure hour (IST) |
| `most_frequent_route` | Single top route |

Returns `None` if fewer than 3 trips exist. Sets `reliable_data: false` if fewer than 10 trips.

**Savings Detection** (`detect_savings_opportunities`):
- Finds routes where user paid for a cab but metro was available
- Calculates potential monthly savings
- Returns top 3 opportunities

---

### `alert_service.py` — Proactive Alerts

**Scheduler:** APScheduler, 30-minute check interval

**Trigger Condition:** Current time within 30–90 min of user's `usual_departure_hour`

**Alert Types Generated:**

| Alert | Trigger |
|-------|---------|
| Departure alert | Within alert window |
| Weather alert | Rain probability > 0.30 |
| Crowding alert | Metro occupancy > 0.85 during peak |

Alerts are written to `st.session_state["pending_alerts"]` and rendered as a banner in the UI.

---

## Database Architecture

**Provider:** Supabase (PostgreSQL 15+)

### Core Tables (Migration 001)

| Table | Purpose | Key Fields |
|-------|---------|-----------|
| `user_preferences` | Commute preferences | `home_location, office_location, arrival_time, buffer_minutes` |
| `user_personality` | Learned profile | `personality_type (Early Bird/Balanced/Rusher), risk_tolerance, on_time_percentage` |
| `journey_plans` | Planned commutes | `origin, destination, recommended_route (JSONB), urgency_level, risk_score` |
| `journey_history` | Completed journeys | `planned_vs_actual times, route_taken, disruptions, user_rating, prediction_accuracy` |
| `disruption_events` | Detected disruptions | `event_type, severity, affected_line, estimated_delay_minutes` |
| `route_embeddings` | pgvector route data | `embedding (vector 1536), origin, destination, typical_duration, modes_used` |
| `feedback_embeddings` | pgvector feedback | `embedding, feedback_text, sentiment, extracted_preferences` |

### Auth-Linked Tables (Migration 002)

| Table | Purpose | Key Fields |
|-------|---------|-----------|
| `public.profiles` | Auth user profiles | `id (UUID → auth.users), display_name, preferred_language` |
| `public.trips` | Trip logs | `user_id (UUID), origin, destination, city, mode, duration_min, cost_inr, planned_at` |
| `public.saved_commutes` | Bookmarked routes | `user_id, name, origin, destination` |

### Row-Level Security

RLS is enabled on all user-scoped tables. Users can only read and write their own rows. The service role key bypasses RLS for backend operations.

### Database Functions

| Function | Purpose |
|----------|---------|
| `handle_new_user()` | Trigger: auto-create profile on auth signup |
| `calculate_user_personality()` | Derive personality type from journey history |
| `match_route_embeddings()` | pgvector cosine similarity search |
| `match_feedback_embeddings()` | pgvector cosine similarity search |

### Indexes

- `idx_trips_user_id`, `idx_trips_planned_at`
- `idx_journey_history_user_id`, `idx_journey_history_date`
- IVFFlat indexes on pgvector columns for fast ANN search

---

## Authentication

**File:** `auth/supabase_auth.py`

| Method | Flow |
|--------|------|
| Magic Link | Email OTP via `auth.sign_in_with_otp()` |
| Google OAuth | Redirect URL via `auth.sign_in_with_oauth()` |
| Guest Mode | No sign-in; trip data not persisted |

**Session Management:**
- `handle_auth_callback()` — restores session from URL params on page reload
- Tokens stored in `st.session_state` (`_sb_access_token`, `_sb_refresh_token`, `user`)
- All database writes carry the user's JWT via `_authed_client(access_token)`

---

## Maps Integration

**File:** `maps/google_maps_client.py` — `GoogleMapsClient`

| Method | Purpose |
|--------|---------|
| `async get_directions(origin, destination, mode, departure_time, alternatives)` | Route + step breakdown |
| `async get_traffic_conditions(origin, destination)` | Real-time traffic level + delay seconds |
| `async geocode(address)` | Lat/lng + address components |
| `async autocomplete_places(query, lat, lng, radius)` | Place suggestions for `st_searchbox` |

All blocking `googlemaps` SDK calls are offloaded to a thread-pool executor to remain non-blocking in the async context.

---

## UI / Frontend

**File:** `ui/streamlit_app.py`

### Page Routing

`st.session_state["page"]` toggles between `"login"` and `"app"`.

### Login Page
- SVG hero illustration (left panel)
- Magic link sign-in form (right panel)
- Google OAuth button
- Guest mode link

### Main App (3 Tabs)

**Tab 1 — Plan Commute**
- Location search using `st_searchbox` with Google Places Autocomplete
- City selector for location bias (Delhi / Mumbai / Bengaluru / …)
- Arrival time picker + buffer slider + preference toggles
- Runs agent on submit
- Renders:
  - Recommended route card (duration, cost, transfers, on-time probability)
  - Step-by-step leg breakdown
  - Alternative routes
  - Folium map with origin/destination markers
  - Uber / Ola / Rapido deep links with QR codes
  - Agent reasoning trace (tools called + one-line result summaries)

**Tab 2 — Monthly Cost Tracker**
- Spend aggregation by transport mode
- Top 3 savings opportunities (switch cab → metro)
- Spending trends

**Tab 3 — Saved Commutes**
- Bookmarked origin→destination pairs
- Quick "Plan Again" button per saved route
- Delete option

### Async Integration

`run_async(coro)` helper bridges Streamlit's synchronous execution model with the async agent and service layer.

---

## Configuration

**File:** `config.py` — Pydantic `Settings` (loads from `.env`)

| Setting | Description |
|---------|-------------|
| `GEMINI_API_KEY` | Google Gemini API |
| `GOOGLE_MAPS_API_KEY` | Google Maps SDK |
| `OPENWEATHER_API_KEY` | OpenWeatherMap |
| `SUPABASE_URL` / `SUPABASE_KEY` | Supabase project |
| `SUPABASE_SERVICE_ROLE_KEY` | Backend RLS bypass |
| `ENABLE_PGVECTOR` | Enable embedding tables |
| `DELHI_METRO_DATA_DIR` | Path to `DMRC-GTFS/` |
| `MAX_ROUTES_TO_COMPARE` | Default: 3 |
| `RISK_THRESHOLD` | Triggers HIGH urgency (default 0.7) |
| `LLM_SCORING_ENABLED` | Gemini-ranked route ordering |
| `ENABLE_AUTH` | Supabase authentication |
| `ENABLE_HYBRID_ROUTES` | Metro + cab hybrid options |
| `ENABLE_REAL_TIME_MONITORING` | Journey tracking |
| `DEFAULT_HOME` / `DEFAULT_OFFICE` | Fallback addresses |
| `DEFAULT_BUFFER_MINUTES` | Minutes added to route duration |

---

## Docker & Deployment

**`dockerfile`:**

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY DMRC-GTFS/ ./DMRC-GTFS/
COPY . .
EXPOSE 8501
HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health
ENTRYPOINT ["streamlit", "run", "ui/streamlit_app.py",
            "--server.port=8501",
            "--server.address=0.0.0.0",
            "--server.headless=true"]
```

Key notes:
- DMRC GTFS data is bundled inside the image
- Health check uses Streamlit's internal `/_stcore/health` endpoint
- Runs headless (no browser launch)

---

## Key Workflows

### A. Plan Commute

```
User input (origin, destination, arrival time)
  └─> Agent geocodes origin
  └─> Fetches user patterns from DB (if authenticated)
  └─> Enters Gemini function-calling loop:
        get_weather           → delay risk + alerts
        get_route_options     → 3 route variants
        get_traffic_conditions→ real-time delays
        get_metro_status      → operational check
        calculate_leave_time  → urgency + leave_by
        get_comfort_advisory  → heat index + crowding
        get_user_history      → personalisation (auth only)
  └─> _build_result: rank routes, build trace, construct deep links
  └─> UI renders recommendation + map + QR codes
  └─> Background thread logs trip to Supabase
```

### B. Trip Logging

After the agent returns, a daemon thread calls `supabase_client.log_trip()` with:
origin, destination, mode, cost, duration, city — non-blocking, fire-and-forget.

### C. Monthly Cost Insight

```
User opens "Cost Tracker" tab
  └─> get_cost_insights(user_id, month) tool
  └─> Fetch all trips for month from public.trips
  └─> Aggregate cost by mode
  └─> detect_savings_opportunities() → top 3 cab→metro switches
  └─> Render spend breakdown + savings cards
```

### D. Proactive Departure Alert

```
APScheduler runs every 30 minutes
  └─> Check if now is within alert window of usual_departure_hour
  └─> If yes: fetch weather (cached if available)
  └─> Generate alerts (departure / weather / crowding)
  └─> Write to st.session_state["pending_alerts"]
  └─> UI renders alert banner on next interaction
```

---

## External API Integrations

| API | Provider | Usage |
|-----|---------|-------|
| **Gemini 2.5 Flash** | Google AI | Core LLM reasoning + function calling |
| **Directions API** | Google Maps | Route finding, ETA with traffic |
| **Geocoding API** | Google Maps | Address → lat/lng |
| **Places Autocomplete** | Google Maps | Location search in UI |
| **Current Weather API** | OpenWeatherMap | Conditions, rain, wind, temperature |
| **OneCall API** | OpenWeatherMap | Active weather alerts |
| **Supabase Auth** | Supabase | Magic link + Google OAuth |
| **DMRC GTFS** | Static (bundled) | Delhi Metro stations, lines, timetables |

---

## Data Models

**`database/models.py`** (Pydantic):

```python
class TransportMode(Enum):   # metro, bus, cab, walk, hybrid
class UrgencyLevel(Enum):    # low, medium, high, critical

class UserPreferences:
    home_location, office_location, arrival_time
    buffer_minutes, avoid_crowds, max_walking_minutes

class UserPersonality:
    personality_type  # "Early Bird" | "Balanced" | "Rusher"
    risk_tolerance    # 0.0 – 1.0
    on_time_percentage

class RouteStep:
    mode, instruction, duration_minutes
    distance_km, cost_inr, line, stations

class Route:
    steps: list[RouteStep]
    total_duration_minutes, total_fare_inr
    on_time_probability, transfers
```

**Internal** (`services/hybrid_route_service.py`):

```python
class RouteOption:
    mode, summary, steps: list[RouteStep]
    duration_minutes, cost_inr
    on_time_probability, score
    disruptions: list[str]
```

---

## Error Handling & Fallbacks

| Failure | Behaviour |
|---------|-----------|
| Weather API unreachable | Returns `unknown` conditions; agent continues |
| Google Maps API down | Metro hybrid still works via GTFS |
| Supabase write fails (RLS) | Trip not logged; user session unaffected |
| Tool execution exception | Tool marked in `failed_tools`; Gemini notified via `FunctionResponse` |
| Geocoding fails | Agent skips weather; notes absence in output |
| DMRC GTFS missing | Metro hybrid option skipped; transit + cab only |

---

## Security

- **RLS** enforced on all user-scoped Supabase tables
- **JWT** required for all writes via `_authed_client(access_token)`
- **Service role key** kept server-side in `.env`, never exposed client-side
- **No hardcoded secrets** — all API keys loaded via Pydantic `Settings` from `.env`
- **`.env` in `.gitignore`** — secrets never committed to version control
