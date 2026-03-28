-- Migration 002: Auth-linked tables for user identity, trips, and saved commutes
-- Run in Supabase SQL Editor after 001_initial_schema.sql

-- ============================================
-- PROFILES
-- Mirrors auth.users — add app-specific profile fields here
-- ============================================

CREATE TABLE IF NOT EXISTS public.profiles (
    id               UUID REFERENCES auth.users(id) ON DELETE CASCADE PRIMARY KEY,
    display_name     TEXT,
    preferred_language TEXT DEFAULT 'en',
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

-- Auto-create a profile row whenever a new auth user signs up
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
    INSERT INTO public.profiles (id, display_name)
    VALUES (NEW.id, NEW.raw_user_meta_data->>'full_name')
    ON CONFLICT (id) DO NOTHING;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();


-- ============================================
-- TRIPS
-- Logged automatically after each commute plan
-- ============================================

CREATE TABLE IF NOT EXISTS public.trips (
    id             UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id        UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    origin         TEXT NOT NULL,
    destination    TEXT NOT NULL,
    city           TEXT,
    route_label    TEXT,
    mode           TEXT,           -- 'transit' | 'cab' | 'metro_hybrid'
    duration_min   INT,
    cost_inr       INT,
    planned_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trips_user_id    ON public.trips(user_id);
CREATE INDEX IF NOT EXISTS idx_trips_planned_at ON public.trips(planned_at DESC);


-- ============================================
-- SAVED COMMUTES
-- User-bookmarked origin/destination pairs
-- ============================================

CREATE TABLE IF NOT EXISTS public.saved_commutes (
    id             UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id        UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    name           TEXT NOT NULL,   -- e.g. "Home → Office"
    origin         TEXT NOT NULL,
    destination    TEXT NOT NULL,
    created_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_saved_commutes_user_id ON public.saved_commutes(user_id);


-- ============================================
-- ROW-LEVEL SECURITY
-- Users can only read/write their own rows
-- ============================================

ALTER TABLE public.profiles       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.trips          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.saved_commutes ENABLE ROW LEVEL SECURITY;

-- Profiles
DROP POLICY IF EXISTS "Own profile only" ON public.profiles;
CREATE POLICY "Own profile only" ON public.profiles
    FOR ALL USING (auth.uid() = id);

-- Trips
DROP POLICY IF EXISTS "Own trips only" ON public.trips;
CREATE POLICY "Own trips only" ON public.trips
    FOR ALL USING (auth.uid() = user_id);

-- Saved commutes
DROP POLICY IF EXISTS "Own saved commutes only" ON public.saved_commutes;
CREATE POLICY "Own saved commutes only" ON public.saved_commutes
    FOR ALL USING (auth.uid() = user_id);
