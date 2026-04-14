-- Migration v16: Chat Learning & Session Persistence
-- Run in Supabase SQL Editor after all previous migrations

-- ─── Chat Sessions ─────────────────────────────────────────────────────────
-- Stores conversation history per user + contract scope for persistence
CREATE TABLE IF NOT EXISTS chat_sessions (
    id BIGSERIAL PRIMARY KEY,
    user_email TEXT NOT NULL,
    scope_label TEXT DEFAULT 'All Contracts',
    contract_ids INTEGER[] DEFAULT '{}',
    messages JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_sessions_user ON chat_sessions(user_email);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated ON chat_sessions(updated_at DESC);

-- ─── Chat Feedback ─────────────────────────────────────────────────────────
-- Stores thumbs up/down ratings on AI responses for learning
CREATE TABLE IF NOT EXISTS chat_feedback (
    id BIGSERIAL PRIMARY KEY,
    user_email TEXT NOT NULL,
    contract_ids INTEGER[] DEFAULT '{}',
    query TEXT NOT NULL,
    response_snippet TEXT NOT NULL,
    rating TEXT NOT NULL CHECK (rating IN ('up', 'down')),
    comment TEXT DEFAULT '',
    query_types TEXT[] DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_feedback_rating ON chat_feedback(rating);
CREATE INDEX IF NOT EXISTS idx_chat_feedback_contracts ON chat_feedback USING GIN(contract_ids);

-- ─── RLS Policies ──────────────────────────────────────────────────────────
ALTER TABLE chat_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_feedback ENABLE ROW LEVEL SECURITY;

CREATE POLICY chat_sessions_policy ON chat_sessions USING (true) WITH CHECK (true);
CREATE POLICY chat_feedback_policy ON chat_feedback USING (true) WITH CHECK (true);
