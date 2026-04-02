"""
Database layer for Sherpa
"""

from .supabase_client import SupabaseClient, get_client

__all__ = ["SupabaseClient", "get_client"]
