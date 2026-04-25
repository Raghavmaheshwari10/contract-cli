-- Migration v17: Standalone Receivables module
-- Run in Supabase SQL Editor after migration_v16

-- ─── Receivables (standalone, not tied to contracts) ─────────────────────
CREATE TABLE IF NOT EXISTS receivables (
    id BIGSERIAL PRIMARY KEY,
    client_name TEXT NOT NULL,
    client_email TEXT DEFAULT '',
    invoice_number TEXT DEFAULT '',
    description TEXT DEFAULT '',
    amount NUMERIC(15,2) NOT NULL CHECK (amount >= 0),
    currency TEXT DEFAULT 'INR',
    invoice_date DATE,
    due_date DATE,
    paid_date DATE,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'paid', 'overdue', 'cancelled', 'disputed')),
    notes TEXT DEFAULT '',
    created_by TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_receivables_status ON receivables(status);
CREATE INDEX IF NOT EXISTS idx_receivables_client ON receivables(client_name);
CREATE INDEX IF NOT EXISTS idx_receivables_due_date ON receivables(due_date DESC);
CREATE INDEX IF NOT EXISTS idx_receivables_invoice_date ON receivables(invoice_date DESC);
