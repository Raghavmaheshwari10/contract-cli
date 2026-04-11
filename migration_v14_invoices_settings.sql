-- Migration v14: PO/Invoice linkage + App settings + Approval SLA
-- Run in Supabase SQL Editor after migration_v13

-- ─── Contract Invoices ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS contract_invoices (
    id SERIAL PRIMARY KEY,
    contract_id INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    invoice_number TEXT NOT NULL,
    po_number TEXT DEFAULT '',
    amount TEXT DEFAULT '',
    invoice_date DATE,
    due_date DATE,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'paid', 'overdue', 'cancelled')),
    notes TEXT DEFAULT '',
    created_by TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_invoices_contract ON contract_invoices(contract_id);
CREATE INDEX IF NOT EXISTS idx_invoices_status ON contract_invoices(status);

-- ─── App Settings (for Slack webhook, etc) ───────────────────────────────
CREATE TABLE IF NOT EXISTS app_settings (
    id SERIAL PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    value TEXT DEFAULT '',
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
