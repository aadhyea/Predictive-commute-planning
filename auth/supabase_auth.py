"""
Supabase Auth helpers for the Commute Agent.

Supports:
  - Magic link (email OTP)
  - Google OAuth
  - Session restore on app reload
  - Sign out

All functions are safe to call from any Streamlit page — they never raise,
they log and return a sensible default on failure.
"""

import logging

import streamlit as st

from database.supabase_client import supabase

logger = logging.getLogger(__name__)

# Redirect URL must match what was configured in Supabase Auth → URL Configuration
_REDIRECT_URL = "http://localhost:8501"


# ============================================
# SESSION ACCESSORS
# ============================================

def get_current_user():
    """Return the Supabase user object if signed in, else None."""
    return st.session_state.get("user")


def is_logged_in() -> bool:
    return get_current_user() is not None


# ============================================
# SIGN IN
# ============================================

def sign_in_magic_link(email: str) -> bool:
    """
    Send a magic link to the given email address.
    Returns True if the request was accepted, False on error.
    """
    if not email or "@" not in email:
        return False
    try:
        supabase.auth.sign_in_with_otp({"email": email})
        return True
    except Exception as e:
        logger.error(f"Magic link send failed: {e}")
        return False


def sign_in_google() -> str:
    """
    Initiate Google OAuth flow.
    Returns the redirect URL to open in the browser.
    Returns empty string on error.
    """
    try:
        resp = supabase.auth.sign_in_with_oauth({
            "provider": "google",
            "options": {"redirect_to": _REDIRECT_URL},
        })
        return resp.url or ""
    except Exception as e:
        logger.error(f"Google OAuth initiation failed: {e}")
        return ""


# ============================================
# SIGN OUT
# ============================================

def sign_out():
    """Sign out and clear session state."""
    try:
        supabase.auth.sign_out()
    except Exception as e:
        logger.warning(f"Sign out error (ignored): {e}")
    finally:
        st.session_state.pop("user", None)
        st.session_state.pop("_sb_access_token", None)
        st.session_state.pop("_sb_refresh_token", None)


# ============================================
# SESSION RESTORE
# ============================================

def _store_session(resp) -> None:
    """
    Persist the user and session tokens from an AuthResponse into st.session_state,
    and apply the access token to the shared supabase client so PostgREST calls
    carry the user's JWT and pass RLS checks.
    """
    if not (resp and resp.user):
        return
    st.session_state["user"] = resp.user
    session = getattr(resp, "session", None)
    if session and session.access_token:
        st.session_state["_sb_access_token"]  = session.access_token
        st.session_state["_sb_refresh_token"] = session.refresh_token or ""
        try:
            supabase.postgrest.auth(session.access_token)
        except Exception as e:
            logger.warning(f"Failed to apply access token to PostgREST client: {e}")


def handle_auth_callback():
    """
    Call once at the very top of the Streamlit app, before any rendering.

    Supabase can deliver tokens in three different ways depending on the auth
    flow configured in the dashboard. We handle all three:

      1. token_hash + type  — magic link PKCE flow (recommended for Streamlit).
                              Requires the email template to use token_hash.
      2. code               — Google OAuth PKCE exchange.
      3. access_token       — implicit flow fallback (fragment-based flows won't
                              reach here; this covers cases where Supabase puts
                              the token in query params directly).
      4. Session restore    — no URL params; re-apply stored tokens so the shared
                              supabase client carries the JWT after a Streamlit rerun.
    """
    params = st.query_params

    # --- Case 1: magic link via token_hash (PKCE email OTP) ---
    token_hash = params.get("token_hash")
    if token_hash:
        otp_type = params.get("type", "email")
        try:
            resp = supabase.auth.verify_otp({"token_hash": token_hash, "type": otp_type})
            _store_session(resp)
        except Exception as e:
            logger.warning(f"Magic link verification failed: {e}")
        finally:
            st.query_params.clear()
        return

    # --- Case 2: OAuth PKCE code exchange ---
    code = params.get("code")
    if code:
        try:
            resp = supabase.auth.exchange_code_for_session({"auth_code": code})
            _store_session(resp)
        except Exception as e:
            logger.warning(f"OAuth code exchange failed: {e}")
        finally:
            st.query_params.clear()
        return

    # --- Case 3: implicit flow — access_token in query params ---
    access_token = params.get("access_token")
    if access_token:
        refresh_token = params.get("refresh_token", "")
        try:
            resp = supabase.auth.set_session(access_token, refresh_token)
            _store_session(resp)
        except Exception as e:
            logger.warning(f"Failed to set session from URL token: {e}")
        finally:
            st.query_params.clear()
        return

    # --- Case 4: restore session on every rerun ---
    # Re-apply the stored JWT to the PostgREST client so RLS passes on inserts.
    # The supabase-py client loses its in-memory session between Streamlit reruns
    # if the worker process was recycled; st.session_state survives across reruns.
    stored_token = st.session_state.get("_sb_access_token")
    stored_refresh = st.session_state.get("_sb_refresh_token", "")
    if stored_token:
        try:
            supabase.auth.set_session(stored_token, stored_refresh)
            supabase.postgrest.auth(stored_token)
        except Exception as e:
            logger.debug(f"Could not re-apply stored session: {e}")
        return

    # No stored tokens — try the supabase-py internal session cache.
    if "user" not in st.session_state:
        try:
            resp = supabase.auth.get_session()
            if resp and resp.user:
                st.session_state["user"] = resp.user
                if hasattr(resp, "access_token") and resp.access_token:
                    st.session_state["_sb_access_token"]  = resp.access_token
                    st.session_state["_sb_refresh_token"] = getattr(resp, "refresh_token", "") or ""
                    supabase.postgrest.auth(resp.access_token)
        except Exception as e:
            logger.debug(f"No active session to restore: {e}")
