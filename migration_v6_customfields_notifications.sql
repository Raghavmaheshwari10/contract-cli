-- ═══════════════════════════════════════════════════════════════════════════════
-- CONTRACT MANAGER v6 — Custom Fields + In-App Notifications
-- Run this in Supabase SQL Editor
-- ═══════════════════════════════════════════════════════════════════════════════

-- 1. Custom Fields definitions (what fields exist)
CREATE TABLE IF NOT EXISTS custom_field_defs (
    id SERIAL PRIMARY KEY,
    field_name TEXT NOT NULL,
    field_type TEXT NOT NULL DEFAULT 'text' CHECK (field_type IN ('text','number','date','select','url','email')),
    field_options TEXT,  -- comma-separated options for 'select' type
    is_required BOOLEAN DEFAULT FALSE,
    display_order INTEGER DEFAULT 0,
    created_by TEXT DEFAULT 'System',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Custom Field values (per contract)
CREATE TABLE IF NOT EXISTS custom_field_values (
    id SERIAL PRIMARY KEY,
    contract_id INTEGER REFERENCES contracts(id) ON DELETE CASCADE,
    field_id INTEGER REFERENCES custom_field_defs(id) ON DELETE CASCADE,
    field_value TEXT,
    updated_by TEXT DEFAULT 'User',
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(contract_id, field_id)
);

-- 3. In-App Notifications
CREATE TABLE IF NOT EXISTS notifications (
    id SERIAL PRIMARY KEY,
    user_email TEXT,  -- NULL = broadcast to all
    title TEXT NOT NULL,
    message TEXT,
    type TEXT DEFAULT 'info' CHECK (type IN ('info','warning','success','error','approval','comment','expiry','workflow')),
    contract_id INTEGER REFERENCES contracts(id) ON DELETE CASCADE,
    is_read BOOLEAN DEFAULT FALSE,
    link TEXT,  -- e.g., contract ID to navigate to
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- RLS
ALTER TABLE custom_field_defs ENABLE ROW LEVEL SECURITY;
ALTER TABLE custom_field_values ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='custom_field_defs' AND policyname='Allow all') THEN
  CREATE POLICY "Allow all" ON custom_field_defs FOR ALL USING (true) WITH CHECK (true);
END IF;
IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='custom_field_values' AND policyname='Allow all') THEN
  CREATE POLICY "Allow all" ON custom_field_values FOR ALL USING (true) WITH CHECK (true);
END IF;
IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='notifications' AND policyname='Allow all') THEN
  CREATE POLICY "Allow all" ON notifications FOR ALL USING (true) WITH CHECK (true);
END IF;
END $$;

-- Pre-populate common custom fields for Indian business contracts
INSERT INTO custom_field_defs (field_name, field_type, field_options, is_required, display_order) VALUES
('PO Number', 'text', NULL, false, 1),
('Cost Center', 'text', NULL, false, 2),
('Budget Code', 'text', NULL, false, 3),
('Payment Mode', 'select', 'NEFT,RTGS,Cheque,Wire Transfer,UPI', false, 4),
('GST Number', 'text', NULL, false, 5),
('PAN Number', 'text', NULL, false, 6),
('Renewal Type', 'select', 'Auto-Renew,Manual,One-Time,Evergreen', false, 7),
('Risk Rating', 'select', 'Low,Medium,High,Critical', false, 8),
('Business Unit', 'select', 'Cloud Resell,Resource Augmentation,AI Development,Software Services,Corporate', false, 9),
('Relationship Manager', 'text', NULL, false, 10)
ON CONFLICT DO NOTHING;
