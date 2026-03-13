"""
Database layer for Delhi Commute Agent
"""

from .supabase_client import SupabaseClient, get_client

__all__ = ["SupabaseClient", "get_client"]
