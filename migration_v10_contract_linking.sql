-- ═══════════════════════════════════════════════════════════════════════════════
-- MIGRATION v10 — Contract Linking (Client ↔ Vendor)
-- Links client contracts to their corresponding vendor contracts
-- ═══════════════════════════════════════════════════════════════════════════════

-- Contract links table (many-to-many: one client can have multiple vendors, one vendor can serve multiple clients)
CREATE TABLE IF NOT EXISTS contract_links (
    id SERIAL PRIMARY KEY,
    client_contract_id INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    vendor_contract_id INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    notes TEXT DEFAULT '',
    created_by TEXT DEFAULT 'System',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(client_contract_id, vendor_contract_id),
    CHECK (client_contract_id != vendor_contract_id)
);

-- Index for fast lookups
CREATE INDEX IF NOT EXISTS idx_contract_links_client ON contract_links(client_contract_id);
CREATE INDEX IF NOT EXISTS idx_contract_links_vendor ON contract_links(vendor_contract_id);
