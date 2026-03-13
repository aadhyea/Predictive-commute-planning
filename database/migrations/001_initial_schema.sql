-- Delhi Commute Agent - Initial Database Schema
-- Run this on your Supabase project via the SQL Editor
-- https://supabase.com/dashboard/project/<your-project>/sql

-- ============================================
-- PREREQUISITES
-- ============================================

-- Enable pgvector extension for semantic similarity search
CREATE EXTENSION IF NOT EXISTS vector;


-- ============================================
-- USER PREFERENCES
-- ============================================

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id                 TEXT PRIMARY KEY,
    home_location           TEXT NOT NULL,
    home_lat                FLOAT NOT NULL,
    home_lng                FLOAT NOT NULL,
    office_location         TEXT NOT NULL,
    office_lat              FLOAT NOT NULL,
    office_lng              FLOAT NOT NULL,
    arrival_time            TIME NOT NULL,
    buffer_minutes          INTEGER NOT NULL DEFAULT 15,
    prefer_comfort_over_speed BOOLEAN NOT NULL DEFAULT TRUE,
    max_walking_minutes     INTEGER NOT NULL DEFAULT 10,
    cost_tolerance_rupees   INTEGER NOT NULL DEFAULT 100,
    crowding_tolerance      FLOAT NOT NULL DEFAULT 0.5,
    notification_lead_time  INTEGER NOT NULL DEFAULT 15,
    enable_sms              BOOLEAN NOT NULL DEFAULT FALSE,
    enable_email            BOOLEAN NOT NULL DEFAULT TRUE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================
-- USER PERSONALITY (learned from history)
-- ============================================

CREATE TABLE IF NOT EXISTS user_personality (
    user_id                     TEXT PRIMARY KEY
                                    REFERENCES user_preferences(user_id) ON DELETE CASCADE,
    personality_type            TEXT NOT NULL,  -- 'Early Bird Optimizer', 'Balanced Commuter', 'Last-Minute Rusher'
    avg_buffer_minutes          FLOAT NOT NULL,
    prefers_speed_over_comfort  BOOLEAN NOT NULL,
    risk_tolerance              FLOAT NOT NULL, -- 0=risk-averse, 1=risk-taking
    cost_sensitivity            TEXT NOT NULL,  -- 'LOW', 'MEDIUM', 'HIGH'
    on_time_percentage          FLOAT NOT NULL,
    average_actual_leave_time   TIME NOT NULL,
    preferred_routes            TEXT[] NOT NULL DEFAULT '{}',
    avoided_routes              TEXT[] NOT NULL DEFAULT '{}',
    calculated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    based_on_journeys           INTEGER NOT NULL DEFAULT 0
);


-- ============================================
-- JOURNEY PLANS
-- ============================================

CREATE TABLE IF NOT EXISTS journey_plans (
    journey_id          TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL REFERENCES user_preferences(user_id) ON DELETE CASCADE,
    origin              TEXT NOT NULL,
    origin_lat          FLOAT NOT NULL,
    origin_lng          FLOAT NOT NULL,
    destination         TEXT NOT NULL,
    destination_lat     FLOAT NOT NULL,
    destination_lng     FLOAT NOT NULL,
    planned_departure   TIMESTAMPTZ NOT NULL,
    required_arrival    TIMESTAMPTZ NOT NULL,
    recommended_route   JSONB NOT NULL,
    alternative_routes  JSONB NOT NULL DEFAULT '[]',
    urgency_level       TEXT NOT NULL,  -- 'low', 'medium', 'high', 'critical'
    risk_score          FLOAT NOT NULL,
    reasoning           TEXT NOT NULL,
    notifications_sent  JSONB NOT NULL DEFAULT '[]',
    status              TEXT NOT NULL DEFAULT 'planned', -- 'planned', 'in_progress', 'completed', 'cancelled'
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_journey_plans_user_id ON journey_plans(user_id);
CREATE INDEX IF NOT EXISTS idx_journey_plans_status  ON journey_plans(status);
CREATE INDEX IF NOT EXISTS idx_journey_plans_created ON journey_plans(created_at DESC);


-- ============================================
-- JOURNEY HISTORY
-- ============================================

CREATE TABLE IF NOT EXISTS journey_history (
    journey_id                  TEXT PRIMARY KEY,
    user_id                     TEXT NOT NULL REFERENCES user_preferences(user_id) ON DELETE CASCADE,
    planned_route               JSONB NOT NULL,
    planned_departure           TIMESTAMPTZ NOT NULL,
    planned_arrival             TIMESTAMPTZ NOT NULL,
    actual_departure            TIMESTAMPTZ,
    actual_arrival              TIMESTAMPTZ,
    actual_duration_minutes     INTEGER,
    route_taken                 TEXT,
    weather                     TEXT,
    disruptions_encountered     TEXT[] NOT NULL DEFAULT '{}',
    user_feedback               TEXT,
    user_rating                 INTEGER CHECK (user_rating BETWEEN 1 AND 5),
    was_on_time                 BOOLEAN NOT NULL,
    delay_minutes               INTEGER NOT NULL DEFAULT 0,
    prediction_accuracy         FLOAT NOT NULL,
    date                        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_journey_history_user_id ON journey_history(user_id);
CREATE INDEX IF NOT EXISTS idx_journey_history_date    ON journey_history(date DESC);


-- ============================================
-- DISRUPTION EVENTS
-- ============================================

CREATE TABLE IF NOT EXISTS disruption_events (
    event_id                    TEXT PRIMARY KEY,
    event_type                  TEXT NOT NULL,  -- 'metro_delay', 'bus_breakdown', 'traffic_jam', 'weather_alert'
    severity                    TEXT NOT NULL,  -- 'minor', 'moderate', 'major', 'critical'
    affected_line               TEXT,
    affected_stations           TEXT[] NOT NULL DEFAULT '{}',
    affected_area               TEXT,
    estimated_delay_minutes     INTEGER NOT NULL,
    message                     TEXT NOT NULL,
    source                      TEXT NOT NULL,  -- 'metro_api', 'social_media', 'weather_api'
    confidence                  FLOAT NOT NULL,
    detected_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expected_resolution         TIMESTAMPTZ     -- NULL = still active
);

CREATE INDEX IF NOT EXISTS idx_disruption_events_line     ON disruption_events(affected_line);
CREATE INDEX IF NOT EXISTS idx_disruption_events_detected ON disruption_events(detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_disruption_events_active   ON disruption_events(expected_resolution)
    WHERE expected_resolution IS NULL;


-- ============================================
-- ROUTE EMBEDDINGS (pgvector)
-- ============================================

CREATE TABLE IF NOT EXISTS route_embeddings (
    route_id            TEXT PRIMARY KEY,
    embedding           vector(1536) NOT NULL,
    origin              TEXT NOT NULL,
    destination         TEXT NOT NULL,
    typical_duration    INTEGER NOT NULL,
    typical_cost        INTEGER NOT NULL,
    modes_used          TEXT[] NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_route_embeddings_origin_dest
    ON route_embeddings(origin, destination);

-- IVFFlat index for fast approximate nearest-neighbour search
-- Adjust `lists` based on table size: ~sqrt(row_count) is a good rule of thumb
CREATE INDEX IF NOT EXISTS idx_route_embeddings_vector
    ON route_embeddings USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);


-- ============================================
-- FEEDBACK EMBEDDINGS (pgvector)
-- ============================================

CREATE TABLE IF NOT EXISTS feedback_embeddings (
    feedback_id             TEXT PRIMARY KEY,
    user_id                 TEXT NOT NULL REFERENCES user_preferences(user_id) ON DELETE CASCADE,
    feedback_text           TEXT NOT NULL,
    embedding               vector(1536) NOT NULL,
    route_id                TEXT NOT NULL,
    sentiment               TEXT NOT NULL,  -- 'positive', 'negative', 'neutral'
    extracted_preferences   JSONB NOT NULL DEFAULT '{}',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_feedback_embeddings_user_id
    ON feedback_embeddings(user_id);

CREATE INDEX IF NOT EXISTS idx_feedback_embeddings_vector
    ON feedback_embeddings USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);


-- ============================================
-- AUTO-UPDATE updated_at TRIGGER
-- ============================================

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER user_preferences_updated_at
    BEFORE UPDATE ON user_preferences
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER journey_plans_updated_at
    BEFORE UPDATE ON journey_plans
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();


-- ============================================
-- PGVECTOR SIMILARITY SEARCH FUNCTIONS
-- ============================================

-- Find semantically similar routes for a given origin/destination pair
CREATE OR REPLACE FUNCTION match_route_embeddings(
    query_embedding     vector(1536),
    match_origin        TEXT,
    match_destination   TEXT,
    match_count         INTEGER DEFAULT 5
)
RETURNS TABLE (
    route_id            TEXT,
    origin              TEXT,
    destination         TEXT,
    typical_duration    INTEGER,
    typical_cost        INTEGER,
    modes_used          TEXT[],
    similarity          FLOAT
)
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT
        re.route_id,
        re.origin,
        re.destination,
        re.typical_duration,
        re.typical_cost,
        re.modes_used,
        1 - (re.embedding <=> query_embedding) AS similarity
    FROM route_embeddings re
    WHERE re.origin      = match_origin
      AND re.destination = match_destination
    ORDER BY re.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;


-- Find similar past feedback entries for preference-pattern detection
CREATE OR REPLACE FUNCTION match_feedback_embeddings(
    query_embedding vector(1536),
    match_user_id   TEXT,
    match_count     INTEGER DEFAULT 10
)
RETURNS TABLE (
    feedback_id             TEXT,
    feedback_text           TEXT,
    route_id                TEXT,
    sentiment               TEXT,
    extracted_preferences   JSONB,
    similarity              FLOAT
)
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT
        fe.feedback_id,
        fe.feedback_text,
        fe.route_id,
        fe.sentiment,
        fe.extracted_preferences,
        1 - (fe.embedding <=> query_embedding) AS similarity
    FROM feedback_embeddings fe
    WHERE fe.user_id = match_user_id
    ORDER BY fe.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;


-- ============================================
-- CALCULATE USER PERSONALITY FROM HISTORY
-- ============================================

CREATE OR REPLACE FUNCTION calculate_user_personality(p_user_id TEXT)
RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_on_time_pct   FLOAT;
    v_avg_buffer    FLOAT;
    v_personality   TEXT;
    v_based_on      INTEGER;
BEGIN
    -- Derive metrics from journey history
    SELECT
        COUNT(*) FILTER (WHERE was_on_time) * 1.0 / NULLIF(COUNT(*), 0),
        AVG(EXTRACT(EPOCH FROM (planned_departure - actual_departure)) / 60.0),
        COUNT(*)
    INTO v_on_time_pct, v_avg_buffer, v_based_on
    FROM journey_history
    WHERE user_id = p_user_id;

    -- Sensible defaults when no history exists yet
    v_on_time_pct := COALESCE(v_on_time_pct, 0.8);
    v_avg_buffer  := COALESCE(v_avg_buffer,  10.0);
    v_based_on    := COALESCE(v_based_on,    0);

    -- Classify personality
    IF v_avg_buffer >= 20 THEN
        v_personality := 'Early Bird Optimizer';
    ELSIF v_avg_buffer >= 5 THEN
        v_personality := 'Balanced Commuter';
    ELSE
        v_personality := 'Last-Minute Rusher';
    END IF;

    INSERT INTO user_personality (
        user_id,
        personality_type,
        avg_buffer_minutes,
        prefers_speed_over_comfort,
        risk_tolerance,
        cost_sensitivity,
        on_time_percentage,
        average_actual_leave_time,
        calculated_at,
        based_on_journeys
    )
    VALUES (
        p_user_id,
        v_personality,
        v_avg_buffer,
        (v_avg_buffer < 5),  -- last-minute rushers prefer speed
        CASE
            WHEN v_avg_buffer >= 20 THEN 0.2
            WHEN v_avg_buffer >= 5  THEN 0.5
            ELSE                         0.8
        END,
        'MEDIUM',
        v_on_time_pct,
        '09:00:00'::TIME,
        NOW(),
        v_based_on
    )
    ON CONFLICT (user_id) DO UPDATE SET
        personality_type           = EXCLUDED.personality_type,
        avg_buffer_minutes         = EXCLUDED.avg_buffer_minutes,
        prefers_speed_over_comfort = EXCLUDED.prefers_speed_over_comfort,
        risk_tolerance             = EXCLUDED.risk_tolerance,
        on_time_percentage         = EXCLUDED.on_time_percentage,
        calculated_at              = NOW(),
        based_on_journeys          = EXCLUDED.based_on_journeys;
END;
$$;
