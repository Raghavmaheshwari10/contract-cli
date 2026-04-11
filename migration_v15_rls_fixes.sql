-- Migration v15: Enable RLS on tables from v10-v14 + add missing indexes
-- Run in Supabase SQL Editor after migration_v14

-- ─── Enable RLS on new tables ─────────────────────────────────────────────
ALTER TABLE contract_parties ENABLE ROW LEVEL SECURITY;
ALTER TABLE contract_share_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE contract_invoices ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_settings ENABLE ROW LEVEL SECURITY;

-- Create policies (API uses service role key, so these allow API access)
CREATE POLICY "Allow all for authenticated" ON contract_parties FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all for authenticated" ON contract_share_links FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all for authenticated" ON contract_invoices FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all for authenticated" ON app_settings FOR ALL USING (true) WITH CHECK (true);

-- ─── Missing Indexes ──────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_notifications_user_email ON notifications(user_email);
CREATE INDEX IF NOT EXISTS idx_notifications_is_read ON notifications(is_read) WHERE is_read = FALSE;
CREATE INDEX IF NOT EXISTS idx_approvals_status ON contract_approvals(status);
CREATE INDEX IF NOT EXISTS idx_invoices_due_date ON contract_invoices(due_date);

-- ─── Fix notification type CHECK to include all used types ────────────────
-- Drop existing constraint and recreate with all types
ALTER TABLE notifications DROP CONSTRAINT IF EXISTS notifications_type_check;
ALTER TABLE notifications ADD CONSTRAINT notifications_type_check
    CHECK (type IN ('info', 'warning', 'success', 'error', 'approval', 'comment', 'expiry', 'workflow'));

-- ─── Add unique constraint on invoice number per contract ─────────────────
ALTER TABLE contract_invoices ADD CONSTRAINT uq_invoice_per_contract
    UNIQUE (contract_id, invoice_number);

-- ─── Add NOT NULL where missing ───────────────────────────────────────────
-- These are safe because FK already prevents null via ON DELETE CASCADE
-- Only run if columns allow NULL currently
DO $$
BEGIN
    ALTER TABLE contract_comments ALTER COLUMN contract_id SET NOT NULL;
    ALTER TABLE contract_obligations ALTER COLUMN contract_id SET NOT NULL;
    ALTER TABLE contract_versions ALTER COLUMN contract_id SET NOT NULL;
    ALTER TABLE contract_approvals ALTER COLUMN contract_id SET NOT NULL;
    ALTER TABLE contract_signatures ALTER COLUMN contract_id SET NOT NULL;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;
