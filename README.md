# Sherpa: Predictive Commute Planning Agent

An AI-powered commute planner for Indian cities. The agent reasons about real-time weather, traffic, metro crowding, and your personal commute history to recommend the best route — and tells you exactly when to leave.

Built with **Gemini 2.5 Flash** · **Streamlit** · **Google Maps API** · **Supabase** · **Delhi Metro GTFS**

---

## Features

| Feature | Description |
|---|---|
| Multi-city routing | Works for Delhi, Mumbai, Bengaluru, Chennai, Hyderabad, Kolkata, Pune, Ahmedabad |
| Agentic tool loop | Gemini autonomously calls weather, routes, traffic, metro, and comfort tools |
| Commute memory | Learns your usual routes and pre-weights scoring from trip history |
| Proactive alerts | Notifies you before you ask — rain, crowding, traffic spikes |
| Heat + crowding advisory | Combined heat index and metro occupancy assessment with early-departure tips |
| Monthly cost tracker | Spend aggregation by mode, savings opportunities (cab vs metro) |
| Agent reasoning trace | Every tool call shown with a one-line result — open this during a demo |
| Hindi / English toggle | Agent explains in Hindi; labels and numbers stay in English |
| Cab deep links | Uber and Ola buttons with pre-filled pickup + drop coordinates |
| Saved commutes | Bookmark origin/destination pairs for one-click replanning |

---

## Setup

### 1. Clone and create virtual environment

```bash
git clone https://github.com/aadhyea/Predictive-commute-planning.git
cd Predictive-commute-planning
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Create `.env` at project root

```env
# ── Required ────────────────────────────────────────────────
GEMINI_API_KEY=...              # Google AI Studio → https://aistudio.google.com/app/apikey
GOOGLE_MAPS_API_KEY=...         # Google Cloud Console (see APIs to enable below)
OPENWEATHERMAP_API_KEY=...      # https://home.openweathermap.org/api_keys (free tier works)

SUPABASE_URL=...                # Settings → API → Project URL
SUPABASE_ANON_KEY=...           # Settings → API → anon / public key
SUPABASE_SERVICE_ROLE_KEY=...   # Settings → API → service_role key (keep secret)

# ── Optional ────────────────────────────────────────────────
ANTHROPIC_API_KEY=              # Not used — Gemini is the active model
DTC_BUS_API_KEY=                # Delhi DTC bus API (optional, graceful fallback if absent)
```

**Google Maps APIs to enable** in [Cloud Console](https://console.cloud.google.com/apis/library):
- Maps JavaScript API
- Geocoding API
- Directions API
- Places API

### 4. Set up Supabase database

Run the two migration files in order inside your Supabase **SQL Editor**:

```
database/migrations/001_initial_schema.sql   ← user preferences, personality, journey history
database/migrations/002_auth_schema.sql      ← auth-linked profiles, trips, saved commutes, RLS policies
```

Enable **Row Level Security** (RLS) — the migrations do this automatically. Each user can only read and write their own rows.

Enable **Magic Link** and (optionally) **Google OAuth** sign-in in your Supabase project under **Authentication → Providers**.

### 5. Add GTFS data

The `DMRC-GTFS/` folder must exist at project root with these files:

```
DMRC-GTFS/
  agency.txt
  calendar.txt
  routes.txt
  shapes.txt
  stop_times.txt
  stops.txt
  trips.txt
```

These are already included in the repository. If you need to re-download them, use the [DMRC open data source](https://github.com/justbejyk/delhi-metro-gtfs).

### 6. Run

```bash
streamlit run ui/streamlit_app.py
```

App opens at `http://localhost:8501`.

---

## Environment variables reference

| Variable | Required | Description |
|---|---|---|
| `GEMINI_API_KEY` | Yes | Gemini 2.5 Flash — primary AI model |
| `GOOGLE_MAPS_API_KEY` | Yes | Geocoding, Directions, Places APIs |
| `OPENWEATHERMAP_API_KEY` | Yes | Current weather and rain data |
| `SUPABASE_URL` | Yes | Supabase project URL |
| `SUPABASE_ANON_KEY` | Yes | Public key for client-side auth |
| `SUPABASE_SERVICE_ROLE_KEY` | Yes | Server-side key for RLS-bypassing writes |
| `ANTHROPIC_API_KEY` | No | Unused — Gemini is the active model |
| `DTC_BUS_API_KEY` | No | Delhi DTC bus realtime data (skipped if absent) |

---

## Project structure

```
Predictive-commute-planning/
├── agent/
│   ├── core.py          # Agentic loop — Gemini tool-calling, result assembly
│   ├── tools.py         # Tool schemas (Gemini FunctionDeclaration) + executors
│   └── prompts.py       # System prompt, response format, Hindi instruction
├── auth/
│   └── supabase_auth.py # Magic link + Google OAuth, session management
├── database/
│   ├── supabase_client.py   # All CRUD — trips, saved commutes, spend, preferences
│   ├── models.py            # Pydantic models for DB rows
│   └── migrations/
│       ├── 001_initial_schema.sql
│       └── 002_auth_schema.sql
├── services/
│   ├── hybrid_route_service.py  # Google Maps + GTFS combined routing
│   ├── metro_service.py         # Delhi Metro GTFS — stations, lines, fares
│   ├── weather_service.py       # OpenWeatherMap + heat index calculation
│   ├── crowding_service.py      # Metro occupancy model by line + time of day
│   └── memory_service.py        # Savings opportunity detection from trip history
├── maps/
│   └── google_maps_client.py    # Geocoding, Directions, Places autocomplete
├── ui/
│   └── streamlit_app.py         # Full Streamlit UI — 3 tabs, sidebar, maps, QR codes
├── DMRC-GTFS/                   # Delhi Metro GTFS static data
├── config.py                    # Pydantic settings, reads from .env
└── requirements.txt
```

---

## Database schema (key tables)

| Table | Description |
|---|---|
| `profiles` | One row per auth user, auto-created on sign-up |
| `trips` | Every planned commute — origin, destination, mode, cost, duration |
| `saved_commutes` | Bookmarked O→D pairs shown in sidebar |
| `user_preferences` | Buffer minutes, comfort preferences, walking limits |
| `user_personality` | Learned commute profile — risk tolerance, typical leave time |
| `journey_history` | Completed journeys with actual vs planned times |
| `disruption_events` | Detected metro disruptions |

All user tables use **Row Level Security** — each user sees only their own data.

---

## Agent tools

The Gemini agent autonomously decides which tools to call each commute plan:

| Tool | What it does |
|---|---|
| `get_weather` | Current conditions + commute impact at origin coordinates |
| `get_route_options` | Up to 3 ranked routes — transit, cab, metro hybrid |
| `get_traffic_conditions` | Real-time road traffic + delay estimate |
| `get_metro_status` | Whether a Delhi Metro line is operational right now |
| `find_nearest_metro` | Closest metro station to any address |
| `calculate_leave_time` | Latest safe departure given arrival deadline + buffer |
| `get_comfort_advisory` | Heat index + crowding + early-departure suggestion |
| `get_user_history` | Last 10 trips — personalises mode and route scoring |
| `get_cost_insights` | Monthly spend by mode + top savings opportunities |

---

## Authentication

- **Magic link** — enter email, click the link sent to inbox
- **Google OAuth** — one-click sign-in (configure in Supabase Authentication → Providers)
- **Guest mode** — plan commutes without signing in; trip history and saved commutes are not persisted

---

## Notes

- The app is optimised for Indian cities. Delhi Metro routing uses local GTFS data; all other cities use Google Maps Transit.
- Free-tier OpenWeatherMap is sufficient — the app only calls the current-conditions endpoint.
- Supabase free tier is sufficient for development and demo use.
- The `SUPABASE_SERVICE_ROLE_KEY` must be kept secret — never expose it client-side.
