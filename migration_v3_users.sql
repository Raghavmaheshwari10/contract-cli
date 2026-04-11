-- ═══════════════════════════════════════════════════════════════════════════════
-- CONTRACT MANAGER v3 — User Management Migration
-- Run this in Supabase SQL Editor
-- ═══════════════════════════════════════════════════════════════════════════════

-- Users table
CREATE TABLE IF NOT EXISTS clm_users (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT DEFAULT 'viewer' CHECK (role IN ('admin', 'manager', 'editor', 'viewer')),
    department TEXT,
    designation TEXT,
    phone TEXT,
    avatar_url TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    last_login TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- RLS
ALTER TABLE clm_users ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='clm_users' AND policyname='Allow all') THEN
  CREATE POLICY "Allow all" ON clm_users FOR ALL USING (true) WITH CHECK (true);
END IF;
END $$;

-- Create default admin user (password: admin123 — SHA256 hash)
INSERT INTO clm_users (email, name, password_hash, role, department, designation)
VALUES ('admin@emb.com', 'Admin', 'a665a45920422f9d417e4867efdc4fb8a04a1f3fff1fa07e998e86f7f7a27ae3', 'admin', 'Finance', 'CLM Administrator')
ON CONFLICT (email) DO NOTHING;
