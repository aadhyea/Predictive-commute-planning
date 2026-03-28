"""
Weather service using OpenWeatherMap API.
Provides current conditions and commute impact assessment for any city.
"""

import logging
from datetime import datetime
from typing import Any, Dict, Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)

# Thresholds for commute impact
RAIN_HEAVY_MM = 7.5        # mm/h considered heavy rain
WIND_STRONG_KMH = 40       # km/h considered strong wind
VISIBILITY_LOW_KM = 1.0    # km considered low visibility
AQI_POOR = 150             # AQI considered poor air quality


class WeatherService:
    """
    Fetches weather data from OpenWeatherMap and translates it into
    commute-relevant signals (delay risk, mode recommendations, alerts).
    """

    BASE_URL = "https://api.openweathermap.org/data/2.5"

    def __init__(self):
        self._api_key = settings.OPENWEATHER_API_KEY

    # ============================================
    # PUBLIC API
    # ============================================

    async def get_current_conditions(
        self,
        lat: float = None,
        lon: float = None,
    ) -> Dict[str, Any]:
        """
        Fetch current weather and return a commute-ready summary.
        Returns unknown conditions if coordinates are not provided.
        """
        if lat is None or lon is None:
            return self._unknown_conditions()

        raw = await self._fetch_current(lat, lon)
        if raw is None:
            return self._unknown_conditions()

        parsed = self._parse_current(raw)
        parsed["commute_impact"] = self._assess_commute_impact(parsed)
        return parsed

    async def get_commute_impact(
        self,
        lat: float = None,
        lon: float = None,
    ) -> Dict[str, Any]:
        """
        Convenience method — returns only the commute impact block.
        """
        conditions = await self.get_current_conditions(lat, lon)
        return conditions.get("commute_impact", self._unknown_impact())

    async def get_weather_alerts(
        self,
        lat: float = None,
        lon: float = None,
    ) -> list:
        """
        Return active weather alerts from the One Call API.
        Empty list if none, on error, or if coordinates are not provided.
        """
        if lat is None or lon is None:
            return []

        raw = await self._fetch_onecall(lat, lon)
        if raw is None:
            return []

        alerts = raw.get("alerts", [])
        return [
            {
                "event":       a.get("event", "Unknown"),
                "description": a.get("description", "")[:200],
                "start":       datetime.fromtimestamp(a["start"]).isoformat() if "start" in a else None,
                "end":         datetime.fromtimestamp(a["end"]).isoformat() if "end" in a else None,
                "sender":      a.get("sender_name", ""),
            }
            for a in alerts
        ]

    # ============================================
    # HTTP HELPERS
    # ============================================

    async def _fetch_current(self, lat: float, lon: float) -> Optional[Dict]:
        url = f"{self.BASE_URL}/weather"
        params = {
            "lat":   lat,
            "lon":   lon,
            "appid": self._api_key,
            "units": "metric",
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(url, params=params)
                r.raise_for_status()
                return r.json()
        except Exception as e:
            logger.error(f"weather fetch failed: {e}")
            return None

    async def _fetch_onecall(self, lat: float, lon: float) -> Optional[Dict]:
        """One Call API 3.0 for alerts (requires subscription, falls back gracefully)."""
        url = f"{self.BASE_URL}/onecall"
        params = {
            "lat":     lat,
            "lon":     lon,
            "appid":   self._api_key,
            "units":   "metric",
            "exclude": "minutely,hourly,daily",
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(url, params=params)
                r.raise_for_status()
                return r.json()
        except Exception as e:
            logger.warning(f"onecall fetch failed (alerts unavailable): {e}")
            return None

    # ============================================
    # PARSING
    # ============================================

    def _parse_current(self, raw: Dict) -> Dict[str, Any]:
        main    = raw.get("main", {})
        wind    = raw.get("wind", {})
        weather = raw.get("weather", [{}])[0]
        rain    = raw.get("rain", {})
        clouds  = raw.get("clouds", {})
        sys     = raw.get("sys", {})
        vis_m   = raw.get("visibility", 10000)   # metres

        rain_1h = rain.get("1h", 0.0)            # mm in last hour
        wind_ms = wind.get("speed", 0.0)          # m/s
        wind_kmh = wind_ms * 3.6

        return {
            "timestamp":        datetime.utcnow().isoformat(),
            "location":         raw.get("name", ""),
            "condition":        weather.get("main", "Clear"),
            "description":      weather.get("description", ""),
            "icon":             weather.get("icon", ""),
            "temperature_c":    round(main.get("temp", 25), 1),
            "feels_like_c":     round(main.get("feels_like", 25), 1),
            "humidity_pct":     main.get("humidity", 50),
            "pressure_hpa":     main.get("pressure", 1013),
            "visibility_km":    round(vis_m / 1000, 2),
            "wind_speed_kmh":   round(wind_kmh, 1),
            "wind_direction":   wind.get("deg", 0),
            "rain_1h_mm":       round(rain_1h, 2),
            "cloud_cover_pct":  clouds.get("all", 0),
            "sunrise":          datetime.fromtimestamp(sys.get("sunrise", 0)).strftime("%H:%M") if sys.get("sunrise") else None,
            "sunset":           datetime.fromtimestamp(sys.get("sunset", 0)).strftime("%H:%M") if sys.get("sunset") else None,
        }

    # ============================================
    # COMMUTE IMPACT
    # ============================================

    def _assess_commute_impact(self, w: Dict) -> Dict[str, Any]:
        """
        Translate weather numbers into commute signals.
        Returns delay_risk (0-1), affected_modes, alerts, recommendation.
        """
        alerts      = []
        delay_risk  = 0.0
        bad_modes   = []

        rain  = w["rain_1h_mm"]
        wind  = w["wind_speed_kmh"]
        vis   = w["visibility_km"]
        cond  = w["condition"]       # "Rain", "Thunderstorm", "Fog", etc.
        temp  = w["temperature_c"]

        # --- Rain ---
        if cond == "Thunderstorm":
            delay_risk = max(delay_risk, 0.85)
            bad_modes  += ["auto", "bike", "walk"]
            alerts.append("Thunderstorm active — outdoor modes unsafe")
        elif rain >= RAIN_HEAVY_MM or cond in ("Rain", "Drizzle") and rain >= 3:
            delay_risk = max(delay_risk, 0.65)
            bad_modes  += ["auto", "bike"]
            alerts.append(f"Heavy rain ({rain} mm/h) — expect road flooding & delays")
        elif cond in ("Rain", "Drizzle"):
            delay_risk = max(delay_risk, 0.30)
            alerts.append("Light rain — carry an umbrella")

        # --- Fog / Low visibility ---
        if vis <= VISIBILITY_LOW_KM:
            delay_risk = max(delay_risk, 0.70)
            bad_modes  += ["auto", "cab"]
            alerts.append(f"Low visibility ({vis} km) — road travel risky")
        elif vis <= 2.0:
            delay_risk = max(delay_risk, 0.40)
            alerts.append("Reduced visibility — metro preferred")

        # --- Strong wind ---
        if wind >= WIND_STRONG_KMH:
            delay_risk = max(delay_risk, 0.45)
            bad_modes  += ["bike"]
            alerts.append(f"Strong winds ({wind} km/h) — avoid open-top travel")

        # --- Extreme heat ---
        if temp >= 42:
            delay_risk = max(delay_risk, 0.25)
            bad_modes  += ["walk", "bike"]
            alerts.append(f"Extreme heat ({temp}°C) — minimize outdoor exposure")

        # --- Recommendation ---
        if delay_risk >= 0.7:
            recommendation = "Leave 20-30 min earlier. Prefer metro/rail — more reliable than road transport."
        elif delay_risk >= 0.4:
            recommendation = "Leave 10-15 min earlier. Metro preferred over road."
        elif delay_risk >= 0.2:
            recommendation = "Minor delays possible. Normal commute plan should work."
        else:
            recommendation = "Clear conditions. Normal commute plan."

        return {
            "delay_risk":        round(delay_risk, 2),
            "severity":          self._risk_to_severity(delay_risk),
            "affected_modes":    list(set(bad_modes)),
            "alerts":            alerts,
            "recommendation":    recommendation,
            "prefer_metro":      delay_risk >= 0.35,
        }

    @staticmethod
    def _risk_to_severity(risk: float) -> str:
        if risk >= 0.7:  return "high"
        if risk >= 0.4:  return "moderate"
        if risk >= 0.2:  return "low"
        return "none"

    # ============================================
    # FALLBACKS
    # ============================================

    @staticmethod
    def _unknown_conditions() -> Dict[str, Any]:
        return {
            "timestamp":       datetime.utcnow().isoformat(),
            "location":        "unknown",
            "condition":       "Unknown",
            "description":     "Weather data unavailable",
            "temperature_c":   None,
            "humidity_pct":    None,
            "wind_speed_kmh":  None,
            "rain_1h_mm":      0.0,
            "visibility_km":   10.0,
            "commute_impact":  WeatherService._unknown_impact(),
        }

    @staticmethod
    def _unknown_impact() -> Dict[str, Any]:
        return {
            "delay_risk":      0.0,
            "severity":        "unknown",
            "affected_modes":  [],
            "alerts":          ["Weather data unavailable"],
            "recommendation":  "Check weather manually before leaving.",
            "prefer_metro":    False,
        }


# Singleton
weather_service = WeatherService()
