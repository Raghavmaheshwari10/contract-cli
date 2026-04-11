-- ═══════════════════════════════════════════════════════════════════════════════
-- CONTRACT MANAGER v4 — Version History + Clause Library
-- Run this in Supabase SQL Editor
-- ═══════════════════════════════════════════════════════════════════════════════

-- 1. Contract Versions (track every edit)
CREATE TABLE IF NOT EXISTS contract_versions (
    id SERIAL PRIMARY KEY,
    contract_id INTEGER REFERENCES contracts(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL DEFAULT 1,
    content TEXT,
    content_html TEXT,
    changed_by TEXT DEFAULT 'User',
    change_summary TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Clause Library (reusable clauses)
CREATE TABLE IF NOT EXISTS clause_library (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT,
    created_by TEXT DEFAULT 'System',
    usage_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- RLS
ALTER TABLE contract_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE clause_library ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='contract_versions' AND policyname='Allow all') THEN
  CREATE POLICY "Allow all" ON contract_versions FOR ALL USING (true) WITH CHECK (true);
END IF;
IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='clause_library' AND policyname='Allow all') THEN
  CREATE POLICY "Allow all" ON clause_library FOR ALL USING (true) WITH CHECK (true);
END IF;
END $$;

-- Pre-populate clause library with common Indian business clauses
INSERT INTO clause_library (title, category, content, tags) VALUES
('Standard Confidentiality', 'Confidentiality', 'Both parties agree to maintain strict confidentiality of all proprietary and sensitive information shared during the course of this engagement. "Confidential Information" includes, but is not limited to, business plans, financial data, customer lists, trade secrets, technical specifications, and any other information marked as confidential. This obligation shall survive the termination of this Agreement for a period of three (3) years.', 'nda,privacy,data'),
('Payment Terms - Net 30', 'Payment', 'All invoices shall be raised monthly on the first business day of each month for services rendered in the preceding month. Payment shall be due within thirty (30) days from the date of invoice ("Net 30"). Late payments shall attract interest at the rate of 1.5% per month or the maximum rate permitted by law, whichever is lower. All payments shall be made in Indian Rupees (INR) unless otherwise specified.', 'payment,invoice,billing'),
('Termination for Convenience', 'Termination', 'Either party may terminate this Agreement at any time by providing sixty (60) days prior written notice to the other party. Upon termination: (a) Client shall pay for all services rendered up to the effective date of termination; (b) Provider shall deliver all work-in-progress and documentation; (c) All confidentiality obligations shall survive termination.', 'exit,notice,termination'),
('Intellectual Property Assignment', 'IP', 'All intellectual property, work product, inventions, and deliverables created by the Provider under this Agreement shall be the exclusive property of the Client upon full payment. Provider retains the right to use general knowledge, skills, and experience gained during the engagement. Pre-existing IP of either party shall remain with the respective party.', 'ip,copyright,ownership'),
('Indemnification - Mutual', 'Indemnification', 'Each party ("Indemnifying Party") shall indemnify, defend, and hold harmless the other party and its officers, directors, and employees from and against any third-party claims, damages, losses, and expenses arising from: (a) breach of this Agreement; (b) negligence or willful misconduct; (c) violation of applicable laws; (d) infringement of intellectual property rights.', 'liability,indemnity,protection'),
('Force Majeure', 'Force Majeure', 'Neither party shall be liable for any failure or delay in performing its obligations where such failure or delay results from Force Majeure events including but not limited to: natural disasters, pandemics, epidemics, government actions, war, terrorism, strikes, internet or power failures, and other events beyond reasonable control. The affected party shall notify the other party within 7 days and make reasonable efforts to mitigate the impact.', 'force,majeure,disaster'),
('Governing Law - India', 'Governing Law', 'This Agreement shall be governed by and construed in accordance with the laws of India. Any disputes arising out of or in connection with this Agreement shall be resolved through arbitration in accordance with the Arbitration and Conciliation Act, 1996, as amended. The seat of arbitration shall be [CITY], India. The language of arbitration shall be English. The arbitral award shall be final and binding on both parties.', 'law,jurisdiction,arbitration'),
('Data Protection - India', 'Data Protection', 'Both parties shall comply with all applicable data protection laws including the Information Technology Act, 2000 and the Digital Personal Data Protection Act, 2023 (DPDPA). The Provider shall: (a) process personal data only as instructed; (b) implement appropriate technical and organizational security measures; (c) notify the Client of any data breach within 72 hours; (d) not transfer data outside India without prior consent.', 'data,privacy,gdpr,dpdpa'),
('SLA - 99.9% Uptime', 'SLA', 'The Provider guarantees a minimum uptime of 99.9% measured on a monthly basis, excluding scheduled maintenance windows. Scheduled maintenance shall be communicated at least 48 hours in advance. Service credits: 10% of monthly fee for uptime between 99.0%-99.9%; 25% for uptime between 95.0%-99.0%; 50% for uptime below 95.0%. Service credits are the sole remedy for SLA breaches.', 'sla,uptime,availability'),
('Non-Compete & Non-Solicitation', 'Non-Compete', 'During the term of this Agreement and for a period of twelve (12) months thereafter: (a) Neither party shall directly or indirectly solicit, recruit, or hire any employee of the other party; (b) Provider shall not engage in competing services for Client''s direct competitors in the same geography. This clause shall be enforceable to the extent permitted by applicable Indian law.', 'compete,solicitation,restriction')
ON CONFLICT DO NOTHING;
