-- Migration V9: Contract Collaborators
-- Run in Supabase SQL Editor

-- 1. Create collaborators table
CREATE TABLE IF NOT EXISTS contract_collaborators (
    id SERIAL PRIMARY KEY,
    contract_id INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    user_email TEXT NOT NULL,
    user_name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'viewer' CHECK (role IN ('viewer', 'editor', 'reviewer')),
    added_by TEXT DEFAULT 'System',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(contract_id, user_email)
);

-- 2. Enable RLS
ALTER TABLE contract_collaborators ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='contract_collaborators' AND policyname='Allow all') THEN
  CREATE POLICY "Allow all" ON contract_collaborators FOR ALL USING (true) WITH CHECK (true);
END IF;
END $$;

-- 3. Performance indexes
CREATE INDEX IF NOT EXISTS idx_collab_contract ON contract_collaborators(contract_id);
CREATE INDEX IF NOT EXISTS idx_collab_user ON contract_collaborators(user_email);
