-- ===============================================================================
-- CONTRACT MANAGER v8 — Production Hardening: Constraints, Indexes & Audit
-- Run this in Supabase SQL Editor (Dashboard > SQL Editor > New Query)
--
-- This migration is fully idempotent — safe to run multiple times.
-- It adds foreign keys, CHECK constraints, NOT NULL guards, performance
-- indexes, an audit-immutability trigger, a token-revocation table,
-- and trigram search support.
-- ===============================================================================


-- =============================================================================
-- 1. ADD updated_at COLUMN TO contracts (optimistic locking)
-- =============================================================================

ALTER TABLE contracts ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

-- Backfill from added_on where updated_at is still NULL
UPDATE contracts SET updated_at = COALESCE(added_on::timestamptz, NOW()) WHERE updated_at IS NULL;


-- =============================================================================
-- 2. FOREIGN KEY ON contract_chunks
-- =============================================================================

ALTER TABLE contract_chunks DROP CONSTRAINT IF EXISTS fk_chunks_contract;
ALTER TABLE contract_chunks ADD CONSTRAINT fk_chunks_contract
    FOREIGN KEY (contract_id) REFERENCES contracts(id) ON DELETE CASCADE;


-- =============================================================================
-- 3. FOREIGN KEY ON contract_versions
-- =============================================================================

ALTER TABLE contract_versions DROP CONSTRAINT IF EXISTS fk_versions_contract;
ALTER TABLE contract_versions ADD CONSTRAINT fk_versions_contract
    FOREIGN KEY (contract_id) REFERENCES contracts(id) ON DELETE CASCADE;


-- =============================================================================
-- 4. CHECK CONSTRAINTS — enforce valid enum values at the DB level
-- =============================================================================

-- contracts.status
ALTER TABLE contracts DROP CONSTRAINT IF EXISTS chk_contract_status;
ALTER TABLE contracts ADD CONSTRAINT chk_contract_status
    CHECK (status IN ('draft', 'pending', 'in_review', 'executed', 'rejected'));

-- contracts.contract_type
ALTER TABLE contracts DROP CONSTRAINT IF EXISTS chk_contract_type;
ALTER TABLE contracts ADD CONSTRAINT chk_contract_type
    CHECK (contract_type IN ('client', 'vendor'));

-- contract_obligations.status
ALTER TABLE contract_obligations DROP CONSTRAINT IF EXISTS chk_obligation_status;
ALTER TABLE contract_obligations ADD CONSTRAINT chk_obligation_status
    CHECK (status IN ('pending', 'in_progress', 'completed'));

-- contract_approvals.status
ALTER TABLE contract_approvals DROP CONSTRAINT IF EXISTS chk_approval_status;
ALTER TABLE contract_approvals ADD CONSTRAINT chk_approval_status
    CHECK (status IN ('pending', 'approved', 'rejected'));


-- =============================================================================
-- 5. UNIQUE CONSTRAINT ON contract_tags — prevent duplicate tags per contract
-- =============================================================================

ALTER TABLE contract_tags DROP CONSTRAINT IF EXISTS uq_contract_tag;
ALTER TABLE contract_tags ADD CONSTRAINT uq_contract_tag UNIQUE (contract_id, tag_name);


-- =============================================================================
-- 6. NOT NULL ON CRITICAL COLUMNS
-- =============================================================================

ALTER TABLE contracts ALTER COLUMN name SET NOT NULL;
ALTER TABLE contracts ALTER COLUMN party_name SET NOT NULL;
ALTER TABLE contracts ALTER COLUMN contract_type SET NOT NULL;
ALTER TABLE contracts ALTER COLUMN status SET DEFAULT 'draft';
ALTER TABLE contracts ALTER COLUMN status SET NOT NULL;


-- =============================================================================
-- 7. AUDIT LOG IMMUTABILITY TRIGGER
--    Prevents UPDATE or DELETE on contract_activity so the audit trail
--    cannot be tampered with after the fact.
-- =============================================================================

CREATE OR REPLACE FUNCTION prevent_audit_modification()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Audit log entries cannot be modified or deleted';
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_immutable ON contract_activity;
CREATE TRIGGER audit_immutable
    BEFORE DELETE OR UPDATE ON contract_activity
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_modification();


-- =============================================================================
-- 8. TOKEN REVOCATION TABLE — persistent logout / token blacklist
-- =============================================================================

CREATE TABLE IF NOT EXISTS revoked_tokens (
    id SERIAL PRIMARY KEY,
    token_sig TEXT NOT NULL,
    revoked_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

-- Fast lookup by signature
CREATE INDEX IF NOT EXISTS idx_revoked_tokens_sig ON revoked_tokens(token_sig);

-- Supports scheduled cleanup of expired entries
CREATE INDEX IF NOT EXISTS idx_revoked_tokens_expires ON revoked_tokens(expires_at);


-- =============================================================================
-- 9. PERFORMANCE INDEXES — cover common filter / sort / join patterns
-- =============================================================================

-- contracts
CREATE INDEX IF NOT EXISTS idx_contracts_status ON contracts(status);
CREATE INDEX IF NOT EXISTS idx_contracts_type ON contracts(contract_type);
CREATE INDEX IF NOT EXISTS idx_contracts_party ON contracts(party_name);
CREATE INDEX IF NOT EXISTS idx_contracts_end_date ON contracts(end_date);
CREATE INDEX IF NOT EXISTS idx_contracts_department ON contracts(department);
CREATE INDEX IF NOT EXISTS idx_contracts_added_on ON contracts(added_on DESC);

-- contract_activity
CREATE INDEX IF NOT EXISTS idx_activity_contract ON contract_activity(contract_id);
CREATE INDEX IF NOT EXISTS idx_activity_created ON contract_activity(created_at DESC);

-- contract_obligations
CREATE INDEX IF NOT EXISTS idx_obligations_contract ON contract_obligations(contract_id);
CREATE INDEX IF NOT EXISTS idx_obligations_deadline ON contract_obligations(deadline);

-- contract_approvals
CREATE INDEX IF NOT EXISTS idx_approvals_contract ON contract_approvals(contract_id);

-- contract_versions
CREATE INDEX IF NOT EXISTS idx_versions_contract ON contract_versions(contract_id);

-- contract_comments
CREATE INDEX IF NOT EXISTS idx_comments_contract ON contract_comments(contract_id);

-- contract_tags
CREATE INDEX IF NOT EXISTS idx_tags_contract ON contract_tags(contract_id);

-- clm_users
CREATE INDEX IF NOT EXISTS idx_users_email ON clm_users(email);


-- =============================================================================
-- 10. TRIGRAM SEARCH EXTENSION — fuzzy / partial-match search on key text cols
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_contracts_name_trgm ON contracts USING gin (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_contracts_party_trgm ON contracts USING gin (party_name gin_trgm_ops);
