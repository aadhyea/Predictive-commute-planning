"""
Proactive departure alert service.

Checks every 30 minutes whether the user's usual departure time is 30–90
minutes away. If so, fetches weather (reusing the planning-flow cache where
possible) and optionally crowding data, then writes any alerts to
st.session_state["pending_alerts"] for the UI banner to consume.
"""

import asyncio
import logging
from datetime import datetime, timedelta

import streamlit as st
from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

_scheduler = BackgroundScheduler()


# ── Weather helpers ───────────────────────────────────────────────────────────

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


# ── Window check ──────────────────────────────────────────────────────────────

def _is_departure_window(usual_hour: float | None) -> bool:
    """Returns True if the user's usual departure is 30–90 minutes from now."""
    if usual_hour is None:
        return False
    now = datetime.now()
    now_hour = now.hour + now.minute / 60
    diff = usual_hour - now_hour
    return 0.5 <= diff <= 1.5
    #return True


# ── Alert generation ──────────────────────────────────────────────────────────
async def generate_alerts(patterns: dict) -> list[dict]:
    """
    Returns a list of alert dicts for the user's most frequent route.
    Each alert: {type, severity, message, suggestion}
    """
    alerts = []

    route = patterns.get("most_frequent_route")
    usual_hour = patterns.get("usual_departure_hour")

    if not route or usual_hour is None:
        return alerts

    # ── Base departure alert (ALWAYS present) ──
    from datetime import datetime

    now = datetime.now()
    now_hour = now.hour + now.minute / 60

    # Handle midnight rollover
    diff = (usual_hour - now_hour) % 24
    mins_left = int(diff * 60)

    departure_hour = int(usual_hour)
    departure_min = int((usual_hour % 1) * 60)

    alerts.append({
        "type": "departure",
        "severity": "warning" if mins_left < 30 else "info",
        "message": f"Your usual commute starts in ~{mins_left} minutes.",
        "suggestion": (
            f"Route: {route['origin']} → {route['destination']}. "
            f"Leave around {departure_hour:02d}:{departure_min:02d}."
        ),
    })

    # ── Weather check ──
    try:
        weather = await _fetch_weather_for_alert(route["origin"])
        rain_prob = weather.get("rain_probability", 0)

        # Lower threshold → more useful alerts
        if rain_prob > 0.3:
            alerts.append({
                "type": "rain",
                "severity": "warning",
                "message": f"Rain expected (~{int(rain_prob * 100)}%) during your commute.",
                "suggestion": "Leave 10–15 min early or consider metro to avoid delays.",
            })

    except Exception:
        pass  # never block alerts on weather failure

    # ── Crowding check (optional) ──
    try:
        from services.crowding_service import estimate_crowding

        usual_line = patterns.get("usual_metro_line")

        if usual_line:
            crowding = estimate_crowding(usual_line, usual_hour)

            if crowding.get("occupancy", 0) > 0.75:  # slightly relaxed
                alerts.append({
                    "type": "crowding",
                    "severity": "info",
                    "message": f"{usual_line} may be crowded at your departure time.",
                    "suggestion": crowding.get(
                        "coach_tip",
                        "Try boarding from the front/rear coaches or leave slightly earlier.",
                    ),
                })

    except ImportError:
        pass  # crowding not implemented yet

    return alerts


# ── SMS alerts ────────────────────────────────────────────────────────────────

def send_sms_alerts(alerts: list[dict]):
    """Send alert summary as a single SMS via Twilio Messaging Service."""
    if not alerts:
        return
    try:
        import os
        from twilio.rest import Client
        account_sid  = os.getenv("TWILIO_ACCOUNT_SID")
        auth_token   = os.getenv("TWILIO_AUTH_TOKEN")
        service_sid  = os.getenv("TWILIO_MESSAGING_SERVICE_SID")
        to_number    = os.getenv("TWILIO_TO_NUMBER")
        if not all([account_sid, auth_token, service_sid, to_number]):
            logger.warning("Twilio credentials missing — skipping SMS")
            return
        client = Client(account_sid, auth_token)
        lines = ["🧭 Sherpa Alert:"]
        for a in alerts:
            lines.append(f"• {a['message']}")
            if a.get("suggestion"):
                lines.append(f"  → {a['suggestion']}")
        body = "\n".join(lines)
        client.messages.create(
            messaging_service_sid=service_sid,
            body=body,
            to=to_number
        )
        logger.info(f"SMS alert sent: {len(alerts)} alert(s)")
    except Exception as e:
        logger.error(f"SMS send failed: {e}")


# ── Background runner ─────────────────────────────────────────────────────────

def _run_alert_check(patterns: dict):
    """Synchronous wrapper for the async alert generator — runs in background thread."""
    if not _is_departure_window(patterns.get("usual_departure_hour")):
        return
    loop = asyncio.new_event_loop()
    try:
        alerts = loop.run_until_complete(generate_alerts(patterns))
        if alerts:
            st.session_state["pending_alerts"] = alerts
    except Exception as e:
        logger.error(f"Alert check failed: {e}")
    finally:
        loop.close()


# ── Scheduler entry point ─────────────────────────────────────────────────────

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
            replace_existing=True,
        )
        _scheduler.start()

    st.session_state["scheduler_started"] = True
