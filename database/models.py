"""
Pydantic models and Supabase schema definitions
"""

from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any
from datetime import datetime, time
from enum import Enum


class TransportMode(str, Enum):
    """Supported transport modes"""
    METRO = "metro"
    BUS = "bus"
    CAB = "cab"
    WALK = "walk"
    HYBRID = "hybrid"  # Combination of modes


class UrgencyLevel(str, Enum):
    """Agent urgency levels"""
    LOW = "low"           # On time, plenty of buffer
    MEDIUM = "medium"     # Slightly late, need faster route
    HIGH = "high"         # Very late, crisis mode
    CRITICAL = "critical" # Cannot make it via public transport


# ============================================
# USER MODELS
# ============================================

class UserPreferences(BaseModel):
    """User commute preferences"""
    user_id: str = Field(..., description="Auth0 user ID")
    home_location: str = Field(..., description="Home address/station")
    home_lat: float = Field(..., ge=-90, le=90)
    home_lng: float = Field(..., ge=-180, le=180)
    
    office_location: str = Field(..., description="Office address/station")
    office_lat: float = Field(..., ge=-90, le=90)
    office_lng: float = Field(..., ge=-180, le=180)
    
    arrival_time: time = Field(..., description="Required office arrival time")
    buffer_minutes: int = Field(15, ge=0, le=60, description="Safety buffer")
    
    # Preferences
    prefer_comfort_over_speed: bool = Field(
        True,
        description="Prefer less crowded routes"
    )
    max_walking_minutes: int = Field(10, ge=0, le=30)
    cost_tolerance_rupees: int = Field(100, ge=0, description="Max daily cost")
    crowding_tolerance: float = Field(
        0.5,
        ge=0.0,
        le=1.0,
        description="0=avoid crowds, 1=don't care"
    )
    
    # Notification preferences
    notification_lead_time: int = Field(
        15,
        ge=5,
        le=60,
        description="Minutes before departure to notify"
    )
    enable_sms: bool = Field(False)
    enable_email: bool = Field(True)
    
    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        json_schema_extra = {
            "example": {
                "user_id": "auth0|123456",
                "home_location": "Rajiv Chowk Metro Station",
                "home_lat": 28.6328,
                "home_lng": 77.2197,
                "office_location": "Cyber City Metro Station",
                "office_lat": 28.4955,
                "office_lng": 77.0883,
                "arrival_time": "10:00:00",
                "buffer_minutes": 15
            }
        }


class UserPersonality(BaseModel):
    """Learned user commute personality"""
    user_id: str
    personality_type: str = Field(
        ...,
        description="Early Bird, Balanced, Last-Minute Rusher"
    )
    
    # Behavioral patterns
    avg_buffer_minutes: float = Field(..., description="Average actual buffer")
    prefers_speed_over_comfort: bool
    risk_tolerance: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="0=very risk-averse, 1=risk-taking"
    )
    cost_sensitivity: str = Field(
        ...,
        description="LOW, MEDIUM, HIGH"
    )
    
    # Calculated from history
    on_time_percentage: float = Field(..., ge=0.0, le=1.0)
    average_actual_leave_time: time
    preferred_routes: List[str] = Field(default_factory=list)
    avoided_routes: List[str] = Field(default_factory=list)
    
    # Metadata
    calculated_at: datetime = Field(default_factory=datetime.utcnow)
    based_on_journeys: int = Field(..., ge=0, description="Number of trips analyzed")


# ============================================
# JOURNEY MODELS
# ============================================

class RouteStep(BaseModel):
    """Single step in a route"""
    mode: TransportMode
    instruction: str
    duration_minutes: int
    distance_meters: int
    
    # Mode-specific details
    transit_line: Optional[str] = None  # "Yellow Line", "Bus 764"
    departure_stop: Optional[str] = None
    arrival_stop: Optional[str] = None
    num_stops: Optional[int] = None
    
    # For walking
    walking_instructions: Optional[str] = None


class Route(BaseModel):
    """Complete route from A to B"""
    route_id: str = Field(..., description="Unique route identifier")
    summary: str = Field(..., description="Human-readable summary")
    
    steps: List[RouteStep]
    
    # Metrics
    total_duration_minutes: int
    total_distance_meters: int
    total_cost_rupees: int
    num_transfers: int
    
    # Timing
    departure_time: datetime
    arrival_time: datetime
    
    # Reliability
    confidence_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Predicted reliability"
    )
    on_time_probability: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Probability of on-time arrival"
    )
    
    # Context
    weather_condition: Optional[str] = None
    traffic_level: Optional[str] = None
    disruptions: List[str] = Field(default_factory=list)


class JourneyPlan(BaseModel):
    """Complete journey plan with recommendations"""
    user_id: str
    journey_id: str = Field(..., description="Unique journey ID")
    
    # Origin & Destination
    origin: str
    origin_lat: float
    origin_lng: float
    destination: str
    destination_lat: float
    destination_lng: float
    
    # Timing
    planned_departure: datetime
    required_arrival: datetime
    
    # Routes
    recommended_route: Route
    alternative_routes: List[Route] = Field(default_factory=list)
    
    # Agent analysis
    urgency_level: UrgencyLevel
    risk_score: float = Field(..., ge=0.0, le=1.0)
    reasoning: str = Field(..., description="Agent's natural language explanation")
    
    # Notifications
    notifications_sent: List[Dict[str, Any]] = Field(default_factory=list)
    
    # Status
    status: str = Field(
        "planned",
        description="planned, in_progress, completed, cancelled"
    )
    
    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class JourneyHistory(BaseModel):
    """Historical journey record for learning"""
    journey_id: str
    user_id: str
    
    # What was planned
    planned_route: Route
    planned_departure: datetime
    planned_arrival: datetime
    
    # What actually happened
    actual_departure: Optional[datetime] = None
    actual_arrival: Optional[datetime] = None
    actual_duration_minutes: Optional[int] = None
    route_taken: Optional[str] = None  # May differ from plan
    
    # Conditions
    weather: Optional[str] = None
    disruptions_encountered: List[str] = Field(default_factory=list)
    
    # User feedback
    user_feedback: Optional[str] = None  # "too crowded", "perfect", etc.
    user_rating: Optional[int] = Field(None, ge=1, le=5)
    
    # Analysis
    was_on_time: bool
    delay_minutes: int = Field(0, description="Positive=late, negative=early")
    prediction_accuracy: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="How accurate was our ETA?"
    )
    
    # Metadata
    date: datetime = Field(default_factory=datetime.utcnow)


# ============================================
# REAL-TIME MODELS
# ============================================

class DisruptionEvent(BaseModel):
    """Real-time disruption detection"""
    event_id: str
    event_type: str = Field(
        ...,
        description="metro_delay, bus_breakdown, traffic_jam, weather_alert"
    )
    severity: str = Field(..., description="minor, moderate, major, critical")
    
    # Location
    affected_line: Optional[str] = None
    affected_stations: List[str] = Field(default_factory=list)
    affected_area: Optional[str] = None
    
    # Impact
    estimated_delay_minutes: int
    message: str
    
    # Source
    source: str = Field(..., description="metro_api, social_media, weather_api")
    confidence: float = Field(..., ge=0.0, le=1.0)
    
    # Timing
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    expected_resolution: Optional[datetime] = None


class MonitoringStatus(BaseModel):
    """Real-time journey monitoring status"""
    journey_id: str
    current_status: str = Field(
        ...,
        description="on_track, delayed, reroute_suggested, completed"
    )
    
    # Progress
    expected_progress_pct: float = Field(..., ge=0.0, le=100.0)
    actual_progress_pct: float = Field(..., ge=0.0, le=100.0)
    
    # Current state
    current_location: Optional[str] = None
    current_step: int
    total_steps: int
    
    # Alerts
    active_alerts: List[DisruptionEvent] = Field(default_factory=list)
    reroute_recommended: bool = Field(False)
    new_route_if_rerouted: Optional[Route] = None
    
    # ETA
    original_eta: datetime
    current_eta: datetime
    eta_confidence: float = Field(..., ge=0.0, le=1.0)
    
    # Metadata
    last_updated: datetime = Field(default_factory=datetime.utcnow)


# ============================================
# VECTOR EMBEDDING MODELS (for pgvector)
# ============================================

class RouteEmbedding(BaseModel):
    """Vector embedding of a route for similarity search"""
    route_id: str
    embedding: List[float] = Field(
        ...,
        description="1536-dim vector from Claude/OpenAI"
    )
    
    # Metadata for filtering
    origin: str
    destination: str
    typical_duration: int
    typical_cost: int
    modes_used: List[TransportMode]
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    @validator("embedding")
    def validate_embedding_dimension(cls, v):
        """Ensure embedding is correct dimension"""
        if len(v) != 1536:  # Standard embedding size
            raise ValueError("Embedding must be 1536 dimensions")
        return v


class FeedbackEmbedding(BaseModel):
    """Vector embedding of user feedback for pattern detection"""
    feedback_id: str
    user_id: str
    feedback_text: str
    embedding: List[float]
    
    # Context
    route_id: str
    sentiment: str = Field(..., description="positive, negative, neutral")
    extracted_preferences: Dict[str, Any] = Field(default_factory=dict)
    
    created_at: datetime = Field(default_factory=datetime.utcnow)