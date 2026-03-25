"""
Google Maps client using the official googlemaps Python SDK.

Install:  pip install googlemaps
"""

import asyncio
import logging
from datetime import datetime
from functools import partial
from typing import Any, Dict, List, Optional

import googlemaps

from config import settings

logger = logging.getLogger(__name__)


class GoogleMapsClient:
    """
    Google Maps wrapper using the official SDK.
    All methods are async (sync SDK calls are run in a thread-pool executor).
    """


    def __init__(self):
        self._gmaps = googlemaps.Client(key=settings.GOOGLE_MAPS_API_KEY)

    async def _run(self, func, *args, **kwargs):
        """Run a blocking googlemaps SDK call in a thread-pool so we stay async."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(func, *args, **kwargs))

    # ============================================
    # DIRECTIONS & ROUTING
    # ============================================

    async def get_directions(
        self,
        origin: str,
        destination: str,
        mode: str = "transit",
        departure_time: Optional[datetime] = None,
        alternatives: bool = True,
        transit_mode: Optional[List[str]] = None,
        transit_routing_preference: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get directions from origin to destination.
        Returns a list of routes, each with duration, distance, and steps.
        """
        kwargs: Dict[str, Any] = {
            "origin": origin,
            "destination": destination,
            "mode": mode,
            "alternatives": alternatives,
            "departure_time": departure_time or datetime.now(),
        }
        if transit_mode:
            kwargs["transit_mode"] = transit_mode
        if transit_routing_preference:
            kwargs["transit_routing_preference"] = transit_routing_preference

        try:
            result = await self._run(self._gmaps.directions, **kwargs)
            return [self._parse_route(r) for r in (result or [])]
        except Exception as e:
            logger.error(f"get_directions failed: {e}")
            return []

    def _parse_route(self, route_data: Dict[str, Any]) -> Dict[str, Any]:
        leg = route_data["legs"][0]
        route = {
            "summary":          route_data.get("summary", ""),
            "duration_seconds": leg["duration"]["value"],
            "duration_text":    leg["duration"]["text"],
            "distance_meters":  leg["distance"]["value"],
            "distance_text":    leg["distance"]["text"],
            "steps":            [],
            "departure_time":   leg.get("departure_time", {}).get("text", "Now"),
            "arrival_time":     leg.get("arrival_time",   {}).get("text", "Unknown"),
            "warnings":         route_data.get("warnings", []),
            "fare":             route_data.get("fare"),
            "overview_polyline": route_data.get("overview_polyline", {}).get("points", ""),
        }
        for step in leg["steps"]:
            step_info = {
                "mode":             step["travel_mode"],
                "instruction":      step.get("html_instructions", ""),
                "duration_seconds": step["duration"]["value"],
                "duration_text":    step["duration"]["text"],
                "distance_meters":  step["distance"]["value"],
                "distance_text":    step["distance"]["text"],
            }
            if "transit_details" in step:
                t = step["transit_details"]
                step_info["transit"] = {
                    "line_name":       t["line"].get("name", t["line"].get("short_name", "")),
                    "line_short_name": t["line"].get("short_name", ""),
                    "vehicle_type":    t["line"].get("vehicle", {}).get("type", ""),
                    "vehicle_icon":    t["line"].get("vehicle", {}).get("icon"),
                    "departure_stop":  t["departure_stop"]["name"],
                    "arrival_stop":    t["arrival_stop"]["name"],
                    "num_stops":       t.get("num_stops", 0),
                    "headsign":        t.get("headsign", ""),
                    "departure_time":  t.get("departure_time", {}).get("text"),
                    "arrival_time":    t.get("arrival_time",   {}).get("text"),
                }
            route["steps"].append(step_info)
        return route

    # ============================================
    # TRAFFIC
    # ============================================

    async def get_traffic_conditions(
        self,
        origin: str,
        destination: str,
        mode: str = "driving",
    ) -> Dict[str, Any]:
        """Return current traffic conditions and delay estimate."""
        try:
            result = await self._run(
                self._gmaps.directions,
                origin=origin,
                destination=destination,
                mode=mode,
                departure_time=datetime.now(),
                traffic_model="best_guess",
            )
            if not result:
                return {"duration_in_traffic_seconds": 0, "traffic_level": "unknown"}

            leg = result[0]["legs"][0]
            in_traffic = leg.get("duration_in_traffic", leg["duration"])
            normal     = leg["duration"]
            return {
                "duration_in_traffic_seconds": in_traffic["value"],
                "duration_in_traffic_text":    in_traffic["text"],
                "normal_duration_seconds":     normal["value"],
                "traffic_delay_seconds":       in_traffic["value"] - normal["value"],
                "traffic_level": self._categorize_traffic(
                    in_traffic["value"], normal["value"]
                ),
            }
        except Exception as e:
            logger.error(f"get_traffic_conditions failed: {e}")
            return {"duration_in_traffic_seconds": 0, "traffic_level": "unknown"}

    @staticmethod
    def _categorize_traffic(in_traffic: int, normal: int) -> str:
        if normal == 0:   return "unknown"
        r = in_traffic / normal
        if r < 1.1:       return "light"
        if r < 1.3:       return "moderate"
        if r < 1.5:       return "heavy"
        return "severe"

    # ============================================
    # GEOCODING
    # ============================================

    async def geocode(self, address: str) -> Optional[Dict[str, Any]]:
        """Convert an address string to coordinates + place info."""
        try:
            result = await self._run(self._gmaps.geocode, address)
            if result:
                loc = result[0]
                return {
                    "lat":                loc["geometry"]["location"]["lat"],
                    "lng":                loc["geometry"]["location"]["lng"],
                    "formatted_address":  loc["formatted_address"],
                    "place_id":           loc.get("place_id"),
                    "types":              loc.get("types", []),
                    "address_components": loc.get("address_components", []),
                }
            return None
        except Exception as e:
            logger.error(f"geocode failed: {e}")
            return None

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

    async def reverse_geocode(self, lat: float, lng: float) -> Optional[str]:
        """Convert coordinates to a formatted address string."""
        try:
            result = await self._run(self._gmaps.reverse_geocode, (lat, lng))
            return result[0]["formatted_address"] if result else None
        except Exception as e:
            logger.error(f"reverse_geocode failed: {e}")
            return None

    # ============================================
    # DISTANCE MATRIX
    # ============================================

    async def get_distance_matrix(
        self,
        origins: List[str],
        destinations: List[str],
        mode: str = "transit",
        departure_time: Optional[datetime] = None,
    ) -> List[List[Optional[Dict[str, Any]]]]:
        """Travel times for all origin x destination combinations."""
        try:
            result = await self._run(
                self._gmaps.distance_matrix,
                origins=origins,
                destinations=destinations,
                mode=mode,
                departure_time=departure_time or datetime.now(),
            )
            matrix = []
            for row in result.get("rows", []):
                row_data = []
                for el in row["elements"]:
                    if el["status"] == "OK":
                        row_data.append({
                            "duration_seconds": el["duration"]["value"],
                            "duration_text":    el["duration"]["text"],
                            "distance_meters":  el["distance"]["value"],
                            "distance_text":    el["distance"]["text"],
                        })
                    else:
                        row_data.append(None)
                matrix.append(row_data)
            return matrix
        except Exception as e:
            logger.error(f"get_distance_matrix failed: {e}")
            return []

    # ============================================
    # PLACES
    # ============================================

    async def autocomplete_places(
        self,
        query: str,
        lat: Optional[float] = None,
        lng: Optional[float] = None,
        radius: Optional[int] = None,
    ) -> List[str]:
        """
        Return up to 5 place name suggestions for the given partial query.
        Restricted to India. Pass lat/lng/radius to bias results to a city.
        """
        if not query or len(query) < 2:
            return []
        try:
            kwargs: Dict[str, Any] = {"components": {"country": "in"}}
            if lat is not None and lng is not None:
                kwargs["location"] = (lat, lng)
                kwargs["radius"] = radius or 40000
                kwargs["strict_bounds"] = False   # bias not hard-filter
            results = await self._run(
                self._gmaps.places_autocomplete,
                query,
                **kwargs,
            )
            return [r["description"] for r in (results or [])][:5]
        except Exception as e:
            logger.error(f"autocomplete_places failed: {e}")
            return []

    async def search_places(
        self,
        query: str,
        location: Optional[str] = None,
        radius: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Search for places by text query."""
        try:
            kwargs: Dict[str, Any] = {"query": query}
            if location:
                kwargs["location"] = location
            if radius:
                kwargs["radius"] = radius
            result = await self._run(self._gmaps.places, **kwargs)
            return [
                {
                    "name":               p.get("name"),
                    "address":            p.get("formatted_address"),
                    "lat":                p["geometry"]["location"]["lat"],
                    "lng":                p["geometry"]["location"]["lng"],
                    "place_id":           p.get("place_id"),
                    "types":              p.get("types", []),
                    "rating":             p.get("rating"),
                    "user_ratings_total": p.get("user_ratings_total"),
                }
                for p in result.get("results", [])
            ]
        except Exception as e:
            logger.error(f"search_places failed: {e}")
            return []

    async def get_place_details(self, place_id: str) -> Optional[Dict[str, Any]]:
        """Fetch detailed information about a place by its Place ID."""
        try:
            result = await self._run(self._gmaps.place, place_id)
            place = result.get("result")
            if not place:
                return None
            return {
                "name":               place.get("name"),
                "address":            place.get("formatted_address"),
                "phone":              place.get("formatted_phone_number"),
                "website":            place.get("website"),
                "rating":             place.get("rating"),
                "user_ratings_total": place.get("user_ratings_total"),
                "opening_hours":      place.get("opening_hours"),
                "lat":                place["geometry"]["location"]["lat"],
                "lng":                place["geometry"]["location"]["lng"],
                "types":              place.get("types", []),
            }
        except Exception as e:
            logger.error(f"get_place_details failed: {e}")
            return None

    async def close(self):
        """No-op: googlemaps SDK manages its own session."""
        pass


# Singleton — import this everywhere
maps_client = GoogleMapsClient()
