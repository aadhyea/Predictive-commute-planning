from .metro_service import DelhiMetroService, delhi_metro
MetroService = DelhiMetroService   # alias
metro_service = delhi_metro        # alias
from .weather_service import WeatherService, weather_service
from .hybrid_route_service import HybridRouteService, hybrid_route_service

__all__ = [
    "DelhiMetroService", "delhi_metro",
    "MetroService", "metro_service",
    "WeatherService", "weather_service",
    "HybridRouteService", "hybrid_route_service",
]
