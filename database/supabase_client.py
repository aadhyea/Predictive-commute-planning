"""
Supabase client for Delhi Commute Agent - all CRUD operations
"""

import calendar
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

# Shared client used for auth operations (supabase.auth.*) and unauthenticated reads.
supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)


def _authed_client(access_token: str) -> Client:
    """
    Return a fresh Supabase client with the user's JWT set on the PostgREST layer.
    Use this for every write that must pass RLS — never reuse a shared instance for
    user-scoped inserts, because the shared client may not carry the current session.
    """
    client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
    client.postgrest.auth(access_token)
    return client


class SupabaseClient:
    """Supabase database client with all CRUD operations"""

    @property
    def client(self) -> Client:
        return supabase

    # ============================================
    # TRIPS
    # ============================================

    def log_trip(
        self,
        access_token: str,
        user_id: str,
        origin: str,
        destination: str,
        city: Optional[str],
        route_label: str,
        mode: str,
        duration_min: int,
        cost_inr: int,
    ) -> bool:
        """Insert a trip record. Silently returns False on failure."""
        try:
            _authed_client(access_token).table("trips").insert({
                "user_id":     user_id,
                "origin":      origin,
                "destination": destination,
                "city":        city,
                "route_label": route_label,
                "mode":        mode,
                "duration_min": duration_min,
                "cost_inr":    cost_inr,
            }).execute()
            return True
        except Exception as e:
            logger.error(f"Failed to log trip: {e}")
            return False

    def get_monthly_spend(self, user_id: str, month: str) -> Dict[str, Any]:
        """Aggregate trip costs for a user in a given month.

        Args:
            user_id: The user's UUID
            month: Month in "YYYY-MM" format, e.g. "2025-01"

        Returns:
            {total_spent, by_mode, trip_count, avg_trip_cost, month}
        """
        try:
            year_str, mon_str = month.split("-")
            year, mon = int(year_str), int(mon_str)
            last_day = calendar.monthrange(year, mon)[1]
            start_dt = f"{month}-01T00:00:00"
            end_dt   = f"{month}-{last_day:02d}T23:59:59"

            resp = (
                self.client.table("trips")
                .select("mode,cost_inr,planned_at")
                .eq("user_id", user_id)
                .gte("planned_at", start_dt)
                .lte("planned_at", end_dt)
                .execute()
            )
            trips = resp.data or []

            total_spent = sum(t.get("cost_inr", 0) for t in trips)
            trip_count  = len(trips)
            avg_trip_cost = round(total_spent / trip_count, 1) if trip_count else 0

            by_mode: Dict[str, int] = {}
            for t in trips:
                mode = t.get("mode", "unknown")
                by_mode[mode] = by_mode.get(mode, 0) + (t.get("cost_inr") or 0)

            return {
                "total_spent":    total_spent,
                "by_mode":        by_mode,
                "trip_count":     trip_count,
                "avg_trip_cost":  avg_trip_cost,
                "month":          month,
            }
        except Exception as e:
            logger.error(f"Failed to get monthly spend: {e}")
            return {"total_spent": 0, "by_mode": {}, "trip_count": 0, "avg_trip_cost": 0, "month": month}

    def get_trip_history(self, user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Return the most recent trips for a user."""
        try:
            resp = (
                self.client.table("trips")
                .select("origin,destination,route_label,mode,duration_min,cost_inr,planned_at")
                .eq("user_id", user_id)
                .order("planned_at", desc=True)
                .limit(limit)
                .execute()
            )
            return resp.data or []
        except Exception as e:
            logger.error(f"Failed to get trip history: {e}")
            return []

    # ============================================
    # SAVED COMMUTES
    # ============================================

    def save_commute(
        self, access_token: str, user_id: str, name: str, origin: str, destination: str
    ) -> bool:
        """Bookmark an origin/destination pair for quick replan."""
        try:
            _authed_client(access_token).table("saved_commutes").insert({
                "user_id":     user_id,
                "name":        name,
                "origin":      origin,
                "destination": destination,
            }).execute()
            return True
        except Exception as e:
            logger.error(f"Failed to save commute: {e}")
            return False

    def get_saved_commutes(self, user_id: str) -> List[Dict[str, Any]]:
        """Return all saved commutes for a user, newest first."""
        try:
            resp = (
                self.client.table("saved_commutes")
                .select("*")
                .eq("user_id", user_id)
                .order("created_at", desc=True)
                .execute()
            )
            return resp.data or []
        except Exception as e:
            logger.error(f"Failed to get saved commutes: {e}")
            return []

    def delete_saved_commute(self, access_token: str, commute_id: str) -> bool:
        """Delete a saved commute by its UUID."""
        try:
            _authed_client(access_token).table("saved_commutes").delete().eq("id", commute_id).execute()
            return True
        except Exception as e:
            logger.error(f"Failed to delete saved commute: {e}")
            return False

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


# Singleton wrapper instance
_client: Optional[SupabaseClient] = None


def get_client() -> SupabaseClient:
    """Get the singleton SupabaseClient wrapper."""
    global _client
    if _client is None:
        _client = SupabaseClient()
    return _client
