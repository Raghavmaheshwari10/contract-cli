-- Email Preferences table for notification settings
CREATE TABLE IF NOT EXISTS email_preferences (
    id SERIAL PRIMARY KEY,
    user_email TEXT NOT NULL UNIQUE,
    enabled BOOLEAN DEFAULT FALSE,
    on_status_change BOOLEAN DEFAULT TRUE,
    on_approval BOOLEAN DEFAULT TRUE,
    on_comment BOOLEAN DEFAULT TRUE,
    on_expiry BOOLEAN DEFAULT TRUE,
    on_workflow BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast lookup
CREATE INDEX IF NOT EXISTS idx_email_prefs_email ON email_preferences(user_email);
CREATE INDEX IF NOT EXISTS idx_email_prefs_enabled ON email_preferences(enabled);
