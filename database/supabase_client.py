"""
Supabase client for Delhi Commute Agent - all CRUD operations
"""

import logging
from typing import Optional, List, Dict, Any
from datetime import datetime

from supabase import create_client, Client

from config import settings
from database.models import (
    UserPreferences,
    UserPersonality,
    JourneyPlan,
    JourneyHistory,
    DisruptionEvent,
    RouteEmbedding,
    FeedbackEmbedding,
)

logger = logging.getLogger(__name__)


class SupabaseClient:
    """Supabase database client with all CRUD operations"""

    def __init__(self):
        self._client: Optional[Client] = None

    @property
    def client(self) -> Client:
        if self._client is None:
            self._client = create_client(
                settings.SUPABASE_URL,
                settings.SUPABASE_KEY,
            )
        return self._client

    # ============================================
    # USER PREFERENCES
    # ============================================

    async def save_user_preferences(self, prefs: UserPreferences) -> bool:
        """Upsert user preferences"""
        try:
            data = prefs.model_dump(mode="json")
            data["arrival_time"] = str(prefs.arrival_time)
            response = self.client.table("user_preferences").upsert(data).execute()
            return bool(response.data)
        except Exception as e:
            logger.error(f"Failed to save user preferences: {e}")
            return False

    async def get_user_preferences(self, user_id: str) -> Optional[UserPreferences]:
        """Get user preferences by user_id"""
        try:
            response = (
                self.client.table("user_preferences")
                .select("*")
                .eq("user_id", user_id)
                .single()
                .execute()
            )
            if response.data:
                return UserPreferences(**response.data)
            return None
        except Exception as e:
            logger.error(f"Failed to get user preferences: {e}")
            return None

    async def update_user_preferences(
        self, user_id: str, updates: Dict[str, Any]
    ) -> bool:
        """Partial update of user preferences"""
        try:
            updates["updated_at"] = datetime.utcnow().isoformat()
            response = (
                self.client.table("user_preferences")
                .update(updates)
                .eq("user_id", user_id)
                .execute()
            )
            return bool(response.data)
        except Exception as e:
            logger.error(f"Failed to update user preferences: {e}")
            return False

    # ============================================
    # USER PERSONALITY
    # ============================================

    async def save_user_personality(self, personality: UserPersonality) -> bool:
        """Upsert user personality profile"""
        try:
            data = personality.model_dump(mode="json")
            data["average_actual_leave_time"] = str(personality.average_actual_leave_time)
            response = self.client.table("user_personality").upsert(data).execute()
            return bool(response.data)
        except Exception as e:
            logger.error(f"Failed to save user personality: {e}")
            return False

    async def get_user_personality(self, user_id: str) -> Optional[UserPersonality]:
        """Get user personality by user_id"""
        try:
            response = (
                self.client.table("user_personality")
                .select("*")
                .eq("user_id", user_id)
                .single()
                .execute()
            )
            if response.data:
                return UserPersonality(**response.data)
            return None
        except Exception as e:
            logger.error(f"Failed to get user personality: {e}")
            return None

    async def recalculate_personality(self, user_id: str) -> bool:
        """Trigger the DB function to recalculate user personality from history"""
        try:
            self.client.rpc(
                "calculate_user_personality", {"p_user_id": user_id}
            ).execute()
            return True
        except Exception as e:
            logger.error(f"Failed to recalculate user personality: {e}")
            return False

    # ============================================
    # JOURNEY PLANS
    # ============================================

    async def save_journey_plan(self, plan: JourneyPlan) -> bool:
        """Save a new journey plan"""
        try:
            data = plan.model_dump(mode="json")
            response = self.client.table("journey_plans").insert(data).execute()
            return bool(response.data)
        except Exception as e:
            logger.error(f"Failed to save journey plan: {e}")
            return False

    async def get_journey_plan(self, journey_id: str) -> Optional[JourneyPlan]:
        """Get journey plan by ID"""
        try:
            response = (
                self.client.table("journey_plans")
                .select("*")
                .eq("journey_id", journey_id)
                .single()
                .execute()
            )
            if response.data:
                return JourneyPlan(**response.data)
            return None
        except Exception as e:
            logger.error(f"Failed to get journey plan: {e}")
            return None

    async def get_active_journey(self, user_id: str) -> Optional[JourneyPlan]:
        """Get the current in-progress journey for a user"""
        try:
            response = (
                self.client.table("journey_plans")
                .select("*")
                .eq("user_id", user_id)
                .eq("status", "in_progress")
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if response.data:
                return JourneyPlan(**response.data[0])
            return None
        except Exception as e:
            logger.error(f"Failed to get active journey: {e}")
            return None

    async def update_journey_status(self, journey_id: str, status: str) -> bool:
        """Update journey plan status"""
        try:
            response = (
                self.client.table("journey_plans")
                .update(
                    {"status": status, "updated_at": datetime.utcnow().isoformat()}
                )
                .eq("journey_id", journey_id)
                .execute()
            )
            return bool(response.data)
        except Exception as e:
            logger.error(f"Failed to update journey status: {e}")
            return False

    # ============================================
    # JOURNEY HISTORY
    # ============================================

    async def save_journey_history(self, history: JourneyHistory) -> bool:
        """Save a completed journey to history"""
        try:
            data = history.model_dump(mode="json")
            response = self.client.table("journey_history").insert(data).execute()
            return bool(response.data)
        except Exception as e:
            logger.error(f"Failed to save journey history: {e}")
            return False

    async def get_journey_history(
        self, user_id: str, limit: int = 30
    ) -> List[JourneyHistory]:
        """Get recent journey history for a user"""
        try:
            response = (
                self.client.table("journey_history")
                .select("*")
                .eq("user_id", user_id)
                .order("date", desc=True)
                .limit(limit)
                .execute()
            )
            return [JourneyHistory(**item) for item in (response.data or [])]
        except Exception as e:
            logger.error(f"Failed to get journey history: {e}")
            return []

    async def update_journey_history(
        self, journey_id: str, updates: Dict[str, Any]
    ) -> bool:
        """Update a journey history record (e.g. add actual arrival time after trip)"""
        try:
            response = (
                self.client.table("journey_history")
                .update(updates)
                .eq("journey_id", journey_id)
                .execute()
            )
            return bool(response.data)
        except Exception as e:
            logger.error(f"Failed to update journey history: {e}")
            return False

    # ============================================
    # DISRUPTION EVENTS
    # ============================================

    async def save_disruption(self, event: DisruptionEvent) -> bool:
        """Save a detected disruption event"""
        try:
            data = event.model_dump(mode="json")
            response = self.client.table("disruption_events").insert(data).execute()
            return bool(response.data)
        except Exception as e:
            logger.error(f"Failed to save disruption event: {e}")
            return False

    async def get_active_disruptions(
        self, line: Optional[str] = None
    ) -> List[DisruptionEvent]:
        """Get unresolved disruptions, optionally filtered by metro line"""
        try:
            query = (
                self.client.table("disruption_events")
                .select("*")
                .is_("expected_resolution", "null")
                .order("detected_at", desc=True)
            )
            if line:
                query = query.eq("affected_line", line)
            response = query.execute()
            return [DisruptionEvent(**item) for item in (response.data or [])]
        except Exception as e:
            logger.error(f"Failed to get active disruptions: {e}")
            return []

    async def resolve_disruption(self, event_id: str) -> bool:
        """Mark a disruption as resolved"""
        try:
            response = (
                self.client.table("disruption_events")
                .update({"expected_resolution": datetime.utcnow().isoformat()})
                .eq("event_id", event_id)
                .execute()
            )
            return bool(response.data)
        except Exception as e:
            logger.error(f"Failed to resolve disruption: {e}")
            return False

    # ============================================
    # VECTOR EMBEDDINGS (pgvector)
    # ============================================

    async def save_route_embedding(self, embedding: RouteEmbedding) -> bool:
        """Save route embedding for future similarity search"""
        if not settings.ENABLE_PGVECTOR:
            return False
        try:
            data = embedding.model_dump(mode="json")
            data["modes_used"] = [m.value for m in embedding.modes_used]
            response = self.client.table("route_embeddings").upsert(data).execute()
            return bool(response.data)
        except Exception as e:
            logger.error(f"Failed to save route embedding: {e}")
            return False

    async def find_similar_routes(
        self,
        query_embedding: List[float],
        origin: str,
        destination: str,
        match_count: int = 5,
    ) -> List[Dict[str, Any]]:
        """Find similar routes using pgvector cosine similarity"""
        if not settings.ENABLE_PGVECTOR:
            return []
        try:
            response = self.client.rpc(
                "match_route_embeddings",
                {
                    "query_embedding": query_embedding,
                    "match_origin": origin,
                    "match_destination": destination,
                    "match_count": match_count,
                },
            ).execute()
            return response.data or []
        except Exception as e:
            logger.error(f"Failed to find similar routes: {e}")
            return []

    async def save_feedback_embedding(self, embedding: FeedbackEmbedding) -> bool:
        """Save feedback embedding for preference pattern detection"""
        if not settings.ENABLE_PGVECTOR:
            return False
        try:
            data = embedding.model_dump(mode="json")
            response = self.client.table("feedback_embeddings").insert(data).execute()
            return bool(response.data)
        except Exception as e:
            logger.error(f"Failed to save feedback embedding: {e}")
            return False

    async def find_similar_feedback(
        self,
        query_embedding: List[float],
        user_id: str,
        match_count: int = 10,
    ) -> List[Dict[str, Any]]:
        """Find similar past feedback to detect preference patterns"""
        if not settings.ENABLE_PGVECTOR:
            return []
        try:
            response = self.client.rpc(
                "match_feedback_embeddings",
                {
                    "query_embedding": query_embedding,
                    "match_user_id": user_id,
                    "match_count": match_count,
                },
            ).execute()
            return response.data or []
        except Exception as e:
            logger.error(f"Failed to find similar feedback: {e}")
            return []


# Singleton instance
_client: Optional[SupabaseClient] = None


def get_client() -> SupabaseClient:
    """Get the singleton Supabase client"""
    global _client
    if _client is None:
        _client = SupabaseClient()
    return _client
