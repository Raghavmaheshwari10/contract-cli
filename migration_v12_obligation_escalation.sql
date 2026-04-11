-- Migration v12: Obligation escalation support
-- Adds escalation tracking fields to contract_obligations
-- Run in Supabase SQL Editor after migration_v11

ALTER TABLE contract_obligations ADD COLUMN IF NOT EXISTS escalated BOOLEAN DEFAULT FALSE;
ALTER TABLE contract_obligations ADD COLUMN IF NOT EXISTS escalated_to TEXT DEFAULT '';
ALTER TABLE contract_obligations ADD COLUMN IF NOT EXISTS escalated_at TIMESTAMPTZ;
ALTER TABLE contract_obligations ADD COLUMN IF NOT EXISTS description TEXT DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_obligations_overdue ON contract_obligations(status, deadline) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_obligations_escalated ON contract_obligations(escalated) WHERE escalated = TRUE;
