-- Migration v11: Multi-party support for contracts
-- Allows multiple parties (clients, vendors, subcontractors) per contract
-- Run in Supabase SQL Editor after migration_v10

-- ─── Contract Parties Table ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS contract_parties (
    id SERIAL PRIMARY KEY,
    contract_id INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    party_name TEXT NOT NULL,
    party_type TEXT NOT NULL CHECK (party_type IN ('client', 'vendor', 'subcontractor')),
    role TEXT DEFAULT '',                    -- e.g., "Primary Client", "Cloud Provider"
    party_value TEXT DEFAULT '',             -- per-party contract value
    scope TEXT DEFAULT '',                   -- what this party is responsible for
    status TEXT DEFAULT 'active' CHECK (status IN ('active', 'inactive', 'terminated')),
    contact_name TEXT DEFAULT '',
    contact_email TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_contract_parties_contract ON contract_parties(contract_id);
CREATE INDEX IF NOT EXISTS idx_contract_parties_type ON contract_parties(party_type);
CREATE INDEX IF NOT EXISTS idx_contract_parties_name ON contract_parties(party_name);

-- ─── Migrate existing party_name data into contract_parties ──────────────────
-- This copies the existing single party from each contract into the new table
INSERT INTO contract_parties (contract_id, party_name, party_type, role, party_value, created_at)
SELECT id, party_name, contract_type, 'Primary', COALESCE(value, ''), NOW()
FROM contracts
WHERE party_name IS NOT NULL AND party_name != ''
ON CONFLICT DO NOTHING;

-- Note: We keep party_name and contract_type on the contracts table for backward compatibility
-- The contract_parties table allows adding additional parties beyond the primary one
