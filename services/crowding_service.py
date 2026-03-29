"""
Metro crowding service — time-based heuristic model.

No live data required. Crowding levels are estimated from known peak-hour patterns
for each metro line. The model also provides proactive departure advice when a user's
planned departure coincides with heavy crowding.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

# ============================================
# CROWDING PROFILES
# Each entry: {"peak_am": (start_h, end_h, occupancy), "peak_pm": (...), "off_peak": occupancy}
# occupancy: 0.0 (empty) → 1.0 (absolutely packed)
# ============================================

CROWDING_PROFILES: Dict[str, Dict] = {
    "Blue": {
        "peak_am":  (7.5, 10.5, 0.95),
        "peak_pm":  (17.5, 20.5, 0.90),
        "off_peak": 0.40,
        "coach_tip_am": "Board from rear coaches — less crowded toward Dwarka end.",
        "coach_tip_pm": "Board from front coaches — less crowded toward Noida end.",
    },
    "Yellow": {
        "peak_am":  (8.0, 10.0, 0.92),
        "peak_pm":  (17.0, 20.0, 0.88),
        "off_peak": 0.35,
        "coach_tip_am": "Avoid Rajiv Chowk interchange — board 1 stop early if possible.",
        "coach_tip_pm": "Board from middle coaches at Rajiv Chowk to avoid the crowd surge.",
    },
    "Magenta": {
        "peak_am":  (8.0, 10.5, 0.85),
        "peak_pm":  (17.0, 20.0, 0.82),
        "off_peak": 0.30,
        "coach_tip_am": "Less crowded compared to Blue/Yellow — board any coach freely.",
        "coach_tip_pm": "Rear coaches tend to be emptier on this line.",
    },
    "Pink": {
        "peak_am":  (8.0, 10.5, 0.80),
        "peak_pm":  (17.0, 20.0, 0.78),
        "off_peak": 0.30,
        "coach_tip_am": "Spreads load well — any coach is fine.",
        "coach_tip_pm": "Front coaches slightly less crowded on the Pink line.",
    },
    "Red": {
        "peak_am":  (8.0, 10.0, 0.75),
        "peak_pm":  (17.5, 20.0, 0.72),
        "off_peak": 0.28,
        "coach_tip_am": "Rear coaches are emptier toward Rithala end.",
        "coach_tip_pm": "Board from middle coaches.",
    },
    "Green": {
        "peak_am":  (8.0, 10.0, 0.70),
        "peak_pm":  (17.5, 19.5, 0.68),
        "off_peak": 0.25,
        "coach_tip_am": "Generally manageable — board from any coach.",
        "coach_tip_pm": "Any coach works comfortably on this line.",
    },
    "Violet": {
        "peak_am":  (8.0, 10.5, 0.78),
        "peak_pm":  (17.0, 20.0, 0.75),
        "off_peak": 0.30,
        "coach_tip_am": "Front coaches less crowded toward Kashmere Gate.",
        "coach_tip_pm": "Rear coaches less crowded on reverse peak direction.",
    },
    "Grey": {
        "peak_am":  (8.5, 10.0, 0.60),
        "peak_pm":  (17.5, 19.5, 0.58),
        "off_peak": 0.20,
        "coach_tip_am": "Short line — very manageable even in peak.",
        "coach_tip_pm": "No significant crowding issues.",
    },
    # Generic fallback for non-Delhi metro systems (Mumbai, Bengaluru, etc.)
    "Generic": {
        "peak_am":  (8.0, 10.5, 0.80),
        "peak_pm":  (17.0, 20.5, 0.82),
        "off_peak": 0.35,
        "coach_tip_am": "Board from the rear — typically less crowded in peak AM.",
        "coach_tip_pm": "Try front or rear coaches to avoid the crowd at peak PM.",
    },
}

# Occupancy → human label
def _occupancy_label(occ: float) -> str:
    if occ < 0.35:  return "empty"
    if occ < 0.60:  return "moderate"
    if occ < 0.80:  return "crowded"
    return "very crowded"


def _resolve_profile(line: str) -> Dict:
    """Match a line name to its crowding profile (case-insensitive prefix match)."""
    line_lower = line.lower()
    for key in CROWDING_PROFILES:
        if key.lower() in line_lower or line_lower.startswith(key.lower()):
            return CROWDING_PROFILES[key]
    return CROWDING_PROFILES["Generic"]


def estimate_crowding(line: str, departure_time: datetime) -> Dict[str, Any]:
    """
    Estimate crowding for a metro line at a given departure datetime.

    Returns:
        occupancy      — float 0.0–1.0
        label          — 'empty' | 'moderate' | 'crowded' | 'very crowded'
        is_peak        — bool
        peak_type      — 'am_peak' | 'pm_peak' | 'off_peak'
        coach_tip      — practical boarding tip for this hour
    """
    profile = _resolve_profile(line)
    hour = departure_time.hour + departure_time.minute / 60.0

    am_start, am_end, am_occ = profile["peak_am"]
    pm_start, pm_end, pm_occ = profile["peak_pm"]

    if am_start <= hour <= am_end:
        occupancy  = am_occ
        is_peak    = True
        peak_type  = "am_peak"
        coach_tip  = profile.get("coach_tip_am", "Board from rear coaches.")
    elif pm_start <= hour <= pm_end:
        occupancy  = pm_occ
        is_peak    = True
        peak_type  = "pm_peak"
        coach_tip  = profile.get("coach_tip_pm", "Board from front coaches.")
    else:
        occupancy  = profile["off_peak"]
        is_peak    = False
        peak_type  = "off_peak"
        coach_tip  = "Off-peak — board from any coach comfortably."

    return {
        "occupancy":  round(occupancy, 2),
        "label":      _occupancy_label(occupancy),
        "is_peak":    is_peak,
        "peak_type":  peak_type,
        "coach_tip":  coach_tip,
    }


def get_early_departure_suggestion(
    line: str,
    planned_departure: datetime,
    lead_minutes: int = 30,
) -> Optional[Dict[str, Any]]:
    """
    If the planned departure falls in a crowded peak window, suggest leaving earlier.

    Returns a dict with suggested_departure_time and reasoning, or None if not needed.
    `lead_minutes` — how many minutes before peak to suggest departing.
    """
    profile = _resolve_profile(line)
    hour = planned_departure.hour + planned_departure.minute / 60.0

    am_start, am_end, am_occ = profile["peak_am"]
    pm_start, pm_end, pm_occ = profile["peak_pm"]

    suggestion = None

    # Check if departure is in AM peak and crowding is high
    if am_start <= hour <= am_end and am_occ >= 0.75:
        # Suggest departing `lead_minutes` before peak starts
        peak_start_dt = planned_departure.replace(
            hour=int(am_start), minute=int((am_start % 1) * 60), second=0, microsecond=0
        )
        suggest_dt = peak_start_dt - timedelta(minutes=lead_minutes)
        if suggest_dt < planned_departure:
            # Already past the early window — suggest leaving right now, earlier
            suggest_dt = planned_departure - timedelta(minutes=lead_minutes)
        suggestion = {
            "suggested_departure": suggest_dt.strftime("%H:%M"),
            "suggested_departure_iso": suggest_dt.isoformat(),
            "minutes_saved": lead_minutes,
            "reason": (
                f"Morning peak on {line} ({_occupancy_label(am_occ)} at {int(am_start):02d}:{int((am_start%1)*60):02d}–"
                f"{int(am_end):02d}:{int((am_end%1)*60):02d}). "
                f"Leaving by {suggest_dt.strftime('%H:%M')} avoids the worst crowding."
            ),
        }

    # Check if departure is in PM peak and crowding is high
    elif pm_start <= hour <= pm_end and pm_occ >= 0.75:
        peak_start_dt = planned_departure.replace(
            hour=int(pm_start), minute=int((pm_start % 1) * 60), second=0, microsecond=0
        )
        suggest_dt = peak_start_dt - timedelta(minutes=lead_minutes)
        if suggest_dt < planned_departure:
            suggest_dt = planned_departure - timedelta(minutes=lead_minutes)
        suggestion = {
            "suggested_departure": suggest_dt.strftime("%H:%M"),
            "suggested_departure_iso": suggest_dt.isoformat(),
            "minutes_saved": lead_minutes,
            "reason": (
                f"Evening peak on {line} ({_occupancy_label(pm_occ)} at {int(pm_start):02d}:{int((pm_start%1)*60):02d}–"
                f"{int(pm_end):02d}:{int((pm_end%1)*60):02d}). "
                f"Leaving by {suggest_dt.strftime('%H:%M')} avoids the rush."
            ),
        }

    # Also check if departure is just about to enter peak (within 30 min of peak start)
    elif (am_start - 0.5) <= hour < am_start and am_occ >= 0.75:
        suggest_dt = planned_departure  # leave now — before peak hits
        suggestion = {
            "suggested_departure": suggest_dt.strftime("%H:%M"),
            "suggested_departure_iso": suggest_dt.isoformat(),
            "minutes_saved": int((am_start - hour) * 60),
            "reason": (
                f"Morning peak on {line} starts at {int(am_start):02d}:00. "
                f"Leave now to travel before the crowd builds."
            ),
        }
    elif (pm_start - 0.5) <= hour < pm_start and pm_occ >= 0.75:
        suggest_dt = planned_departure
        suggestion = {
            "suggested_departure": suggest_dt.strftime("%H:%M"),
            "suggested_departure_iso": suggest_dt.isoformat(),
            "minutes_saved": int((pm_start - hour) * 60),
            "reason": (
                f"Evening peak on {line} starts at {int(pm_start):02d}:00. "
                f"Leave now to travel before the crowd builds."
            ),
        }

    return suggestion


# Singleton-style convenience reference
crowding_service = type("CrowdingService", (), {
    "estimate": staticmethod(estimate_crowding),
    "suggest_early_departure": staticmethod(get_early_departure_suggestion),
})()
