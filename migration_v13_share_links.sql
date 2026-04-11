-- Migration v13: Shareable review links for external collaboration
-- Allows counterparties to review contracts without a login
-- Run in Supabase SQL Editor after migration_v12

CREATE TABLE IF NOT EXISTS contract_share_links (
    id SERIAL PRIMARY KEY,
    contract_id INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    token TEXT NOT NULL UNIQUE,
    created_by TEXT NOT NULL,
    recipient_name TEXT DEFAULT '',
    recipient_email TEXT DEFAULT '',
    permissions TEXT DEFAULT 'view' CHECK (permissions IN ('view', 'comment')),
    expires_at TIMESTAMPTZ NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    accessed_count INTEGER DEFAULT 0,
    last_accessed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_share_links_token ON contract_share_links(token);
CREATE INDEX IF NOT EXISTS idx_share_links_contract ON contract_share_links(contract_id);
CREATE INDEX IF NOT EXISTS idx_share_links_active ON contract_share_links(is_active) WHERE is_active = TRUE;
