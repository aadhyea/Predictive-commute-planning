"""
Memory and personalisation utilities.

Currently provides savings opportunity detection — identifies trips where a cheaper
transport option (metro) was available but a more expensive one (cab) was taken.
"""

from typing import Any, Dict, List


def detect_savings_opportunities(spend: Dict[str, Any], trips: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Identify trips where metro was available but cab was taken.

    Compares each cab trip against known metro trips on the same O→D pair.
    When no metro history exists for a route, estimates ₹45 if cab cost > ₹100.

    Args:
        spend: Output of get_monthly_spend (not used directly but kept for signature symmetry).
        trips: List of trip dicts from get_trip_history.

    Returns:
        List of dicts sorted by saving (descending):
        [{"date", "route", "cab_cost", "metro_cost", "saving"}, ...]
    """
    # Build cheapest known metro cost per (origin_key, dest_key)
    metro_costs: Dict[tuple, int] = {}
    for trip in trips:
        if trip.get("mode") in ("metro", "metro_hybrid", "transit"):
            key  = (_route_key(trip.get("origin", "")), _route_key(trip.get("destination", "")))
            cost = trip.get("cost_inr") or 0
            if cost and (key not in metro_costs or cost < metro_costs[key]):
                metro_costs[key] = cost

    opportunities: List[Dict[str, Any]] = []
    seen: set = set()  # deduplicate by route

    for trip in trips:
        if trip.get("mode") != "cab":
            continue
        cab_cost = trip.get("cost_inr") or 0
        if not cab_cost:
            continue

        key = (_route_key(trip.get("origin", "")), _route_key(trip.get("destination", "")))

        # Use known metro cost; fall back to ₹45 estimate when cab is expensive
        metro_cost = metro_costs.get(key)
        if metro_cost is None:
            if cab_cost > 100:
                metro_cost = 45
            else:
                continue

        saving = cab_cost - metro_cost
        if saving <= 0:
            continue

        route_label = f"{trip.get('origin', '').split(',')[0].strip()} → {trip.get('destination', '').split(',')[0].strip()}"

        # Keep only the highest-saving entry per route
        if key in seen:
            continue
        seen.add(key)

        opportunities.append({
            "date":        (trip.get("planned_at") or "")[:10],
            "route":       route_label,
            "cab_cost":    cab_cost,
            "metro_cost":  metro_cost,
            "saving":      saving,
        })

    opportunities.sort(key=lambda x: x["saving"], reverse=True)
    return opportunities


# ── Helpers ───────────────────────────────────────────────────────────────────

def _route_key(location: str) -> str:
    """Normalise a location string to a simple key for route matching."""
    return location.lower().strip().split(",")[0].strip()
