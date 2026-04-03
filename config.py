"""
Configuration management with Pydantic settings
"""

from pydantic_settings import BaseSettings
from pydantic import Field, validator
from typing import Optional
from pathlib import Path

# Get project root
PROJECT_ROOT = Path(__file__).parent


class Settings(BaseSettings):
    """Application settings with validation"""
    
    # ============================================
    # CORE API KEYS
    # ============================================
    ANTHROPIC_API_KEY: Optional[str] = Field(None, description="Claude API key (unused — using Gemini)")
    GEMINI_API_KEY: str = Field(..., description="Google Gemini API key (from Google AI Studio)")
    GOOGLE_MAPS_API_KEY: str = Field(..., description="Google Maps API key")
    OPENWEATHER_API_KEY: str = Field(..., description="OpenWeather API key", alias="OPENWEATHERMAP_API_KEY")
    
    # Delhi Transit APIs
    DTC_BUS_API_KEY: Optional[str] = Field(None, description="DTC Bus API key")
    DTC_BUS_API_URL: str = Field(
        "https://api.dtc.delhi.gov.in/v1",
        description="DTC Bus API base URL"
    )
    
    # ============================================
    # GOOGLE MAPS
    # ============================================
    GOOGLE_MAPS_ENABLED: bool = Field(
        True,
        description="Enable Google Maps SDK client"
    )
    
    # ============================================
    # DELHI METRO STATIC DATA
    # ============================================
    DELHI_METRO_DATA_DIR: Path = Field(
        default_factory=lambda: PROJECT_ROOT / "DMRC-GTFS",
        description="Directory containing Delhi Metro GTFS data files"
    )
    DELHI_METRO_FARES_FILE: str = Field(
        "fares.txt",
        description="Filename for fare data"
    )
    DELHI_METRO_ROUTES_FILE: str = Field(
        "routes.txt",
        description="Filename for routes data"
    )
    DELHI_METRO_STOPS_FILE: str = Field(
        "stops.txt",
        description="Filename for stops/stations data"
    )
    DELHI_METRO_TIMINGS_FILE: str = Field(
        "timings.txt",
        description="Filename for timing data"
    )
    
    @validator("DELHI_METRO_DATA_DIR", pre=True)
    def ensure_metro_data_dir_exists(cls, v):
        """Ensure Delhi Metro data directory exists, resolving relative paths from project root"""
        path = Path(v) if not isinstance(v, Path) else v
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path.mkdir(parents=True, exist_ok=True)
        return path
    
    def get_metro_data_path(self, filename: str) -> Path:
        """Get full path to a metro data file"""
        return self.DELHI_METRO_DATA_DIR / filename
    
    # ============================================
    # SUPABASE
    # ============================================
    SUPABASE_URL: str = Field(..., description="Supabase project URL")
    SUPABASE_KEY: str = Field(..., description="Supabase anon/public key")
    SUPABASE_SERVICE_ROLE_KEY: str = Field(..., description="Supabase service role key")
    ENABLE_PGVECTOR: bool = Field(True, description="Enable pgvector for embeddings")
    
    # ============================================
    # AUTH0 (Optional - Final Stages)
    # ============================================
    AUTH0_DOMAIN: Optional[str] = Field(None, description="Auth0 tenant domain")
    AUTH0_CLIENT_ID: Optional[str] = Field(None, description="Auth0 client ID")
    AUTH0_CLIENT_SECRET: Optional[str] = Field(None, description="Auth0 client secret")
    AUTH0_API_AUDIENCE: Optional[str] = Field(None, description="Auth0 API audience")
    AUTH0_CALLBACK_URL: str = Field(
        "http://localhost:8501/callback",
        description="Auth0 callback URL"
    )
    
    # Okta (Optional - Enterprise)
    OKTA_DOMAIN: Optional[str] = Field(None, description="Okta domain")
    OKTA_CLIENT_ID: Optional[str] = Field(None, description="Okta client ID")
    OKTA_CLIENT_SECRET: Optional[str] = Field(None, description="Okta client secret")
    
    # ============================================
    # APP CONFIG
    # ============================================
    DEBUG: bool = Field(False, description="Debug mode")
    DEMO_MODE: bool = Field(False, description="Demo mode with mock data")
    LOG_LEVEL: str = Field("INFO", description="Logging level")
    
    # Agent settings
    MONITORING_INTERVAL_SECONDS: int = Field(
        120,
        description="How often to check conditions (seconds)"
    )
    NOTIFICATION_LEAD_TIME_MINUTES: int = Field(
        15,
        description="How early to notify user before departure"
    )
    RISK_THRESHOLD: float = Field(
        0.7,
        ge=0.0,
        le=1.0,
        description="Risk score threshold for alerts"
    )
    MAX_ROUTES_TO_COMPARE: int = Field(3, description="Max routes to analyze")
    
    # ============================================
    # DEMO DEFAULTS
    # ============================================
    DEFAULT_HOME: str = Field(
        "Rajiv Chowk Metro Station, Delhi",
        description="Default home location"
    )
    DEFAULT_OFFICE: str = Field(
        "Cyber City Metro Station, Gurugram",
        description="Default office location"
    )
    DEFAULT_ARRIVAL_TIME: str = Field(
        "10:00:00",
        description="Default office arrival time"
    )
    DEFAULT_BUFFER_MINUTES: int = Field(
        15,
        description="Default safety buffer (minutes)"
    )
    
    # ============================================
    # PATHS
    # ============================================
    DATA_DIR: Path = Field(
        default_factory=lambda: PROJECT_ROOT / "data",
        description="Data directory"
    )
    
    @validator("DATA_DIR", pre=True)
    def ensure_data_dir_exists(cls, v):
        """Ensure data directory exists"""
        path = Path(v)
        path.mkdir(parents=True, exist_ok=True)
        return path
    
    # ============================================
    # FEATURE FLAGS
    # ============================================
    ENABLE_AUTH: bool = Field(False, description="Enable Auth0 authentication")
    ENABLE_REAL_TIME_MONITORING: bool = Field(
        True,
        description="Enable real-time journey monitoring"
    )
    ENABLE_LEARNING: bool = Field(
        True,
        description="Enable preference learning from feedback"
    )
    ENABLE_HYBRID_ROUTES: bool = Field(
        True,
        description="Enable Metro+Cab hybrid routes"
    )
    LLM_SCORING_ENABLED: bool = Field(
        False,
        description=(
            "When True, route options are passed to Gemini unranked and the LLM "
            "reasons about the best choice based on today's context (weather, time "
            "pressure, user patterns). When False, Python weights are used. "
            "Set to True in .env to enable LLM-powered ranking."
        ),
    )
    
    class Config:
        env_file = str(PROJECT_ROOT / ".env")
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "ignore"  # silently skip .env keys not declared in Settings


# Singleton settings instance
settings = Settings()


# Helper functions
def is_auth_enabled() -> bool:
    """Check if authentication is enabled and configured"""
    return (
        settings.ENABLE_AUTH
        and settings.AUTH0_DOMAIN is not None
        and settings.AUTH0_CLIENT_ID is not None
    )


def is_google_maps_enabled() -> bool:
    """Check if Google Maps client is enabled"""
    return settings.GOOGLE_MAPS_ENABLED


def get_data_path(filename: str) -> Path:
    """Get full path to data file"""
    return settings.DATA_DIR / filename


def get_metro_data_path(filename: str) -> Path:
    """Get full path to Delhi Metro data file"""
    return settings.get_metro_data_path(filename)