-- ═══════════════════════════════════════════════════════════════════════════════
-- CONTRACT MANAGER v5 — Tags, Labels & Workflow Automation
-- Run this in Supabase SQL Editor
-- ═══════════════════════════════════════════════════════════════════════════════

-- 1. Contract Tags / Labels
CREATE TABLE IF NOT EXISTS contract_tags (
    id SERIAL PRIMARY KEY,
    contract_id INTEGER REFERENCES contracts(id) ON DELETE CASCADE,
    tag_name TEXT NOT NULL,
    tag_color TEXT DEFAULT '#2563eb',
    created_by TEXT DEFAULT 'User',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Tag presets (reusable tag definitions)
CREATE TABLE IF NOT EXISTS tag_presets (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    color TEXT NOT NULL DEFAULT '#2563eb',
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. Workflow Rules (automation engine)
CREATE TABLE IF NOT EXISTS workflow_rules (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    trigger_event TEXT NOT NULL,  -- 'status_change', 'contract_created', 'expiry_approaching', 'approval_completed'
    trigger_condition JSONB DEFAULT '{}',  -- e.g., {"from_status":"pending","to_status":"in_review"} or {"days_before_expiry":30}
    action_type TEXT NOT NULL,  -- 'auto_approve', 'add_tag', 'change_status', 'create_obligation', 'notify_webhook'
    action_config JSONB DEFAULT '{}',  -- e.g., {"approver":"CFO","tag":"Urgent","status":"executed"}
    is_active BOOLEAN DEFAULT TRUE,
    priority INTEGER DEFAULT 0,
    created_by TEXT DEFAULT 'System',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. Workflow execution log
CREATE TABLE IF NOT EXISTS workflow_log (
    id SERIAL PRIMARY KEY,
    rule_id INTEGER REFERENCES workflow_rules(id) ON DELETE SET NULL,
    rule_name TEXT,
    contract_id INTEGER REFERENCES contracts(id) ON DELETE CASCADE,
    trigger_event TEXT,
    action_taken TEXT,
    details TEXT,
    executed_at TIMESTAMPTZ DEFAULT NOW()
);

-- Add tags column to contracts for quick text-based tag storage
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS tags TEXT DEFAULT '';

-- RLS
ALTER TABLE contract_tags ENABLE ROW LEVEL SECURITY;
ALTER TABLE tag_presets ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_log ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='contract_tags' AND policyname='Allow all') THEN
  CREATE POLICY "Allow all" ON contract_tags FOR ALL USING (true) WITH CHECK (true);
END IF;
IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='tag_presets' AND policyname='Allow all') THEN
  CREATE POLICY "Allow all" ON tag_presets FOR ALL USING (true) WITH CHECK (true);
END IF;
IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='workflow_rules' AND policyname='Allow all') THEN
  CREATE POLICY "Allow all" ON workflow_rules FOR ALL USING (true) WITH CHECK (true);
END IF;
IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='workflow_log' AND policyname='Allow all') THEN
  CREATE POLICY "Allow all" ON workflow_log FOR ALL USING (true) WITH CHECK (true);
END IF;
END $$;

-- Pre-populate tag presets with common contract labels
INSERT INTO tag_presets (name, color, description) VALUES
('Urgent', '#dc2626', 'Requires immediate attention'),
('High Value', '#7c3aed', 'Contract value exceeds threshold'),
('Auto-Renew', '#2563eb', 'Contract auto-renews on expiry'),
('Expiring Soon', '#ea580c', 'Expiring within 30 days'),
('Confidential', '#0f172a', 'Contains sensitive information'),
('Strategic', '#059669', 'Key strategic partnership'),
('Under Negotiation', '#d97706', 'Terms being negotiated'),
('SLA Critical', '#0891b2', 'Has critical SLA requirements'),
('Compliance Required', '#be185d', 'Requires regulatory compliance'),
('Internal', '#64748b', 'Internal agreement between departments')
ON CONFLICT (name) DO NOTHING;

-- Pre-populate workflow rules with common automations
INSERT INTO workflow_rules (name, trigger_event, trigger_condition, action_type, action_config, is_active) VALUES
('Auto-tag high value contracts', 'contract_created', '{"min_value": 5000000}', 'add_tag', '{"tag": "High Value", "color": "#7c3aed"}', true),
('Auto-request approval on submit', 'status_change', '{"to_status": "pending"}', 'auto_approve', '{"approver": "Legal Team", "comments": "Auto-generated: Contract submitted for review"}', true),
('Tag expiring contracts', 'expiry_approaching', '{"days_before": 30}', 'add_tag', '{"tag": "Expiring Soon", "color": "#ea580c"}', true),
('Auto-execute on all approvals', 'approval_completed', '{"all_approved": true}', 'change_status', '{"status": "executed"}', false),
('Notify on rejection', 'status_change', '{"to_status": "rejected"}', 'notify_webhook', '{"message": "Contract has been rejected"}', true)
ON CONFLICT DO NOTHING;
