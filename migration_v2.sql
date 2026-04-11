-- ═══════════════════════════════════════════════════════════════════════════════
-- CONTRACT MANAGER v2 — CLM Migration
-- Run this in Supabase SQL Editor (Dashboard → SQL Editor → New Query)
-- ═══════════════════════════════════════════════════════════════════════════════

-- 1. Add new columns to existing contracts table
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'draft';
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS template_id INTEGER;
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS department TEXT;
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS jurisdiction TEXT;
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS governing_law TEXT;
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS created_by TEXT DEFAULT 'System';
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS executed_at TIMESTAMPTZ;
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS content_html TEXT;

-- Update existing contracts to 'executed' status (they're already added)
UPDATE contracts SET status = 'executed' WHERE status IS NULL OR status = 'draft';

-- 2. Contract Templates
CREATE TABLE IF NOT EXISTS contract_templates (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    contract_type TEXT NOT NULL,
    description TEXT,
    content TEXT NOT NULL,
    clauses JSONB DEFAULT '[]',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. Comments
CREATE TABLE IF NOT EXISTS contract_comments (
    id SERIAL PRIMARY KEY,
    contract_id INTEGER REFERENCES contracts(id) ON DELETE CASCADE,
    user_name TEXT NOT NULL,
    content TEXT NOT NULL,
    clause_ref TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. Obligations / Tasks
CREATE TABLE IF NOT EXISTS contract_obligations (
    id SERIAL PRIMARY KEY,
    contract_id INTEGER REFERENCES contracts(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT,
    deadline DATE,
    status TEXT DEFAULT 'pending',
    assigned_to TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5. Approvals
CREATE TABLE IF NOT EXISTS contract_approvals (
    id SERIAL PRIMARY KEY,
    contract_id INTEGER REFERENCES contracts(id) ON DELETE CASCADE,
    approver_name TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    comments TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 6. Signatures
CREATE TABLE IF NOT EXISTS contract_signatures (
    id SERIAL PRIMARY KEY,
    contract_id INTEGER REFERENCES contracts(id) ON DELETE CASCADE,
    signer_name TEXT NOT NULL,
    signer_email TEXT,
    signer_designation TEXT,
    signature_data TEXT,
    ip_address TEXT,
    signed_at TIMESTAMPTZ DEFAULT NOW()
);

-- 7. Activity Log / Timeline
CREATE TABLE IF NOT EXISTS contract_activity (
    id SERIAL PRIMARY KEY,
    contract_id INTEGER REFERENCES contracts(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    user_name TEXT DEFAULT 'System',
    details TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 8. Webhook Configs
CREATE TABLE IF NOT EXISTS webhook_configs (
    id SERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    url TEXT NOT NULL,
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ═══════════════════════════════════════════════════════════════════════════════
-- RLS Policies
-- ═══════════════════════════════════════════════════════════════════════════════

ALTER TABLE contract_templates ENABLE ROW LEVEL SECURITY;
ALTER TABLE contract_comments ENABLE ROW LEVEL SECURITY;
ALTER TABLE contract_obligations ENABLE ROW LEVEL SECURITY;
ALTER TABLE contract_approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE contract_signatures ENABLE ROW LEVEL SECURITY;
ALTER TABLE contract_activity ENABLE ROW LEVEL SECURITY;
ALTER TABLE webhook_configs ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='contract_templates' AND policyname='Allow all') THEN
  CREATE POLICY "Allow all" ON contract_templates FOR ALL USING (true) WITH CHECK (true);
END IF;
IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='contract_comments' AND policyname='Allow all') THEN
  CREATE POLICY "Allow all" ON contract_comments FOR ALL USING (true) WITH CHECK (true);
END IF;
IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='contract_obligations' AND policyname='Allow all') THEN
  CREATE POLICY "Allow all" ON contract_obligations FOR ALL USING (true) WITH CHECK (true);
END IF;
IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='contract_approvals' AND policyname='Allow all') THEN
  CREATE POLICY "Allow all" ON contract_approvals FOR ALL USING (true) WITH CHECK (true);
END IF;
IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='contract_signatures' AND policyname='Allow all') THEN
  CREATE POLICY "Allow all" ON contract_signatures FOR ALL USING (true) WITH CHECK (true);
END IF;
IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='contract_activity' AND policyname='Allow all') THEN
  CREATE POLICY "Allow all" ON contract_activity FOR ALL USING (true) WITH CHECK (true);
END IF;
IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='webhook_configs' AND policyname='Allow all') THEN
  CREATE POLICY "Allow all" ON webhook_configs FOR ALL USING (true) WITH CHECK (true);
END IF;
END $$;

-- ═══════════════════════════════════════════════════════════════════════════════
-- Enable Realtime on key tables
-- ═══════════════════════════════════════════════════════════════════════════════

DO $$ BEGIN
  ALTER PUBLICATION supabase_realtime ADD TABLE contract_comments;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

DO $$ BEGIN
  ALTER PUBLICATION supabase_realtime ADD TABLE contract_activity;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

DO $$ BEGIN
  ALTER PUBLICATION supabase_realtime ADD TABLE contracts;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

-- ═══════════════════════════════════════════════════════════════════════════════
-- Pre-built Contract Templates
-- ═══════════════════════════════════════════════════════════════════════════════

INSERT INTO contract_templates (name, category, contract_type, description, content, clauses) VALUES

('Non-Disclosure Agreement (NDA)', 'nda', 'client',
 'Standard mutual NDA for protecting confidential information between parties.',
 'NON-DISCLOSURE AGREEMENT

This Non-Disclosure Agreement ("Agreement") is entered into as of [EFFECTIVE_DATE] ("Effective Date"), between [PARTY_1_NAME], a company organized under the laws of India, having its registered office at [PARTY_1_ADDRESS] (hereinafter referred to as the "Disclosing Party") and [PARTY_2_NAME], having its registered office at [PARTY_2_ADDRESS] (hereinafter referred to as the "Receiving Party").

1. DEFINITIONS
"Confidential Information" shall mean any data or information, oral or written, that is proprietary to the Disclosing Party and not generally known to the public, whether in tangible or intangible form.

2. OBLIGATIONS OF RECEIVING PARTY
The Receiving Party agrees to:
(a) Hold and maintain the Confidential Information in strict confidence;
(b) Not to disclose any Confidential Information to third parties without prior written consent;
(c) Not to use Confidential Information for any purpose other than the Purpose;
(d) Protect Confidential Information using the same degree of care it uses for its own confidential information.

3. TERM
This Agreement shall remain in effect for a period of [TERM_YEARS] years from the Effective Date. The confidentiality obligations shall survive termination for a period of 3 years.

4. EXCLUSIONS
Confidential Information shall not include information that:
(a) Is or becomes publicly available through no fault of the Receiving Party;
(b) Was already in the Receiving Party possession prior to disclosure;
(c) Is independently developed by the Receiving Party;
(d) Is rightfully received from a third party without restriction.

5. RETURN OF INFORMATION
Upon termination or request, the Receiving Party shall promptly return or destroy all Confidential Information and any copies thereof.

6. REMEDIES
The Receiving Party acknowledges that any breach may cause irreparable harm and the Disclosing Party shall be entitled to seek injunctive relief.

7. GOVERNING LAW
This Agreement shall be governed by and construed in accordance with the laws of India. Any disputes shall be subject to the exclusive jurisdiction of the courts at [JURISDICTION].

8. MISCELLANEOUS
(a) This Agreement constitutes the entire agreement between the parties.
(b) No modification shall be effective unless in writing signed by both parties.
(c) Neither party may assign this Agreement without prior written consent.

IN WITNESS WHEREOF, the parties have executed this Agreement as of the date first written above.

[PARTY_1_NAME]                    [PARTY_2_NAME]
Signature: ___________           Signature: ___________
Name: [SIGNATORY_1]              Name: [SIGNATORY_2]
Designation: [DESIGNATION_1]     Designation: [DESIGNATION_2]
Date: [DATE]                     Date: [DATE]',
 '[{"name":"Definitions","section":"1"},{"name":"Obligations","section":"2"},{"name":"Term","section":"3"},{"name":"Exclusions","section":"4"},{"name":"Return of Information","section":"5"},{"name":"Remedies","section":"6"},{"name":"Governing Law","section":"7"},{"name":"Miscellaneous","section":"8"}]'),

('Master Service Agreement (MSA)', 'msa', 'client',
 'Comprehensive MSA for technology services engagements — cloud, RA, software development.',
 'MASTER SERVICE AGREEMENT

This Master Service Agreement ("Agreement") is entered into as of [EFFECTIVE_DATE] by and between:

[PARTY_1_NAME] ("Service Provider"), a company incorporated under the laws of India, having its registered office at [PARTY_1_ADDRESS];
AND
[PARTY_2_NAME] ("Client"), a company having its registered office at [PARTY_2_ADDRESS].

1. SCOPE OF SERVICES
The Service Provider shall provide technology services as described in individual Statements of Work ("SOW") executed under this Agreement. Services may include but are not limited to:
(a) Cloud Migration and Managed Services
(b) Resource Augmentation
(c) AI/ML Development
(d) Software Development

2. STATEMENT OF WORK
Each engagement shall be governed by a separate SOW that references this Agreement. Each SOW shall specify: scope, deliverables, timeline, fees, and acceptance criteria.

3. FEES AND PAYMENT
(a) Fees shall be as specified in each SOW.
(b) Invoices shall be raised monthly/as per SOW milestones.
(c) Payment terms: Net [PAYMENT_DAYS] days from invoice date.
(d) Late payments shall attract interest at [INTEREST_RATE]% per month.
(e) All fees are exclusive of applicable taxes (GST/TDS).

4. TERM AND TERMINATION
(a) This Agreement shall commence on the Effective Date and continue for [TERM_YEARS] year(s).
(b) Either party may terminate with [NOTICE_DAYS] days written notice.
(c) Either party may terminate immediately for material breach if not cured within 30 days of notice.
(d) Upon termination, Client shall pay for all services rendered till the termination date.

5. INTELLECTUAL PROPERTY
(a) Pre-existing IP remains with the respective party.
(b) Work product created under a SOW shall be owned by [IP_OWNER] upon full payment.
(c) Service Provider retains the right to use general knowledge, skills, and experience.

6. CONFIDENTIALITY
Both parties shall maintain strict confidentiality of all proprietary information. This obligation survives termination for 3 years.

7. INDEMNIFICATION
Each party shall indemnify the other against claims arising from:
(a) Breach of this Agreement;
(b) Negligence or willful misconduct;
(c) Infringement of third-party IP rights.

8. LIMITATION OF LIABILITY
(a) Neither party shall be liable for indirect, consequential, or punitive damages.
(b) Total liability shall not exceed the fees paid in the preceding 12 months.

9. FORCE MAJEURE
Neither party shall be liable for delays caused by events beyond reasonable control including natural disasters, pandemics, government actions, or internet/power failures.

10. GOVERNING LAW AND DISPUTE RESOLUTION
(a) This Agreement shall be governed by the laws of India.
(b) Disputes shall be resolved through arbitration under the Arbitration and Conciliation Act, 1996.
(c) Seat of arbitration: [ARBITRATION_CITY], India.
(d) Language of arbitration: English.

11. GENERAL PROVISIONS
(a) This Agreement constitutes the entire understanding between the parties.
(b) Amendments must be in writing signed by authorized representatives.
(c) Neither party may assign without prior written consent.
(d) Notices shall be sent to the addresses specified herein.

IN WITNESS WHEREOF, the parties have executed this Agreement.

Service Provider: [PARTY_1_NAME]     Client: [PARTY_2_NAME]
Signature: ___________               Signature: ___________
Name: [SIGNATORY_1]                  Name: [SIGNATORY_2]
Date: [DATE]                         Date: [DATE]',
 '[{"name":"Scope of Services","section":"1"},{"name":"Statement of Work","section":"2"},{"name":"Fees and Payment","section":"3"},{"name":"Term and Termination","section":"4"},{"name":"Intellectual Property","section":"5"},{"name":"Confidentiality","section":"6"},{"name":"Indemnification","section":"7"},{"name":"Limitation of Liability","section":"8"},{"name":"Force Majeure","section":"9"},{"name":"Governing Law","section":"10"},{"name":"General Provisions","section":"11"}]'),

('Cloud Services / AWS Bill Transfer Agreement', 'cloud', 'client',
 'Agreement for AWS billing transfer, managed cloud services, and discount pass-through.',
 'CLOUD SERVICES AGREEMENT

This Cloud Services Agreement ("Agreement") is entered into as of [EFFECTIVE_DATE] between:

[PARTY_1_NAME] ("Cloud Partner" / "EMB"), having its office at [PARTY_1_ADDRESS];
AND
[PARTY_2_NAME] ("Customer"), having its office at [PARTY_2_ADDRESS].

1. SERVICES
EMB shall provide the following cloud services:
(a) AWS Billing Transfer — Customer''s AWS billing shall be transferred to EMB''s partner account.
(b) Discount Pass-through — EMB shall provide a discount of [DISCOUNT]% on the Customer''s monthly AWS consumption.
(c) Cloud Management — Optional managed services including monitoring, optimization, and support.

2. BILLING AND PAYMENT
(a) EMB shall invoice the Customer monthly based on actual AWS consumption minus the agreed discount.
(b) AWS Marketplace charges shall be billed separately in USD at the prevailing exchange rate.
(c) AWS Support charges: [SUPPORT_TERMS].
(d) Payment terms: Net [PAYMENT_DAYS] days from invoice date.
(e) All amounts are exclusive of applicable GST.

3. CUSTOMER OBLIGATIONS
(a) Customer shall transfer billing to EMB''s designated AWS account within [TRANSFER_DAYS] days.
(b) Customer shall not purchase Savings Plans or Reserved Instances directly from AWS during the term.
(c) Customer shall provide EMB with read-only access to AWS Cost Explorer.

4. SERVICE LEVELS
(a) EMB shall ensure 99.9% billing accuracy.
(b) Support response times as per the selected support tier.
(c) Monthly consumption reports shall be provided within 5 business days.

5. TERM AND TERMINATION
(a) Initial term: [TERM_MONTHS] months from billing transfer date.
(b) Auto-renewal for successive [RENEWAL_MONTHS]-month periods unless [NOTICE_DAYS] days notice is given.
(c) Upon termination, billing shall be transferred back to Customer within 30 days.

6. DATA AND SECURITY
(a) EMB shall not access Customer''s AWS resources without explicit permission.
(b) All billing data shall be treated as confidential.
(c) EMB maintains SOC 2 Type II compliance.

7. LIMITATION OF LIABILITY
(a) EMB is not liable for AWS service outages or SLA breaches by AWS.
(b) Total liability limited to fees paid in the preceding 3 months.

8. GOVERNING LAW
This Agreement shall be governed by the laws of India. Disputes shall be subject to arbitration in [ARBITRATION_CITY].

Signatures:

EMB: [PARTY_1_NAME]                 Customer: [PARTY_2_NAME]
Signature: ___________              Signature: ___________
Name: [SIGNATORY_1]                 Name: [SIGNATORY_2]
Date: [DATE]                        Date: [DATE]',
 '[{"name":"Services","section":"1"},{"name":"Billing and Payment","section":"2"},{"name":"Customer Obligations","section":"3"},{"name":"Service Levels","section":"4"},{"name":"Term and Termination","section":"5"},{"name":"Data and Security","section":"6"},{"name":"Limitation of Liability","section":"7"},{"name":"Governing Law","section":"8"}]'),

('Resource Augmentation Agreement', 'ra', 'client',
 'Agreement for placing IT professionals at client sites on time & material basis.',
 'RESOURCE AUGMENTATION AGREEMENT

This Resource Augmentation Agreement ("Agreement") is entered into as of [EFFECTIVE_DATE] between:

[PARTY_1_NAME] ("Provider"), having its office at [PARTY_1_ADDRESS];
AND
[PARTY_2_NAME] ("Client"), having its office at [PARTY_2_ADDRESS].

1. ENGAGEMENT MODEL
(a) Provider shall deploy qualified IT resources ("Resources") at Client''s project as per the Resource Order Form.
(b) Resources shall work under the day-to-day supervision of Client.
(c) Resources remain employees of Provider for all statutory purposes.

2. RESOURCE RATES AND BILLING
(a) Monthly rate per resource: as specified in the Resource Order Form.
(b) Billing basis: [BILLING_BASIS] (monthly fixed / time & material).
(c) Working hours: [WORKING_HOURS] hours per day, [WORKING_DAYS] days per week.
(d) Overtime: billed at [OVERTIME_RATE]x the hourly rate.
(e) Public holidays and leaves as per Client''s holiday calendar.

3. PAYMENT TERMS
(a) Invoices raised on the 1st of each month for the preceding month.
(b) Payment due within [PAYMENT_DAYS] days of invoice.
(c) TDS shall be deducted as applicable.

4. RESOURCE MANAGEMENT
(a) Replacement: If a Resource leaves, Provider shall provide a replacement within [REPLACEMENT_DAYS] working days.
(b) Ramp-up: [RAMP_UP_DAYS] days notice for additional resources.
(c) Ramp-down: [RAMP_DOWN_DAYS] days notice for releasing resources.
(d) Background verification: Provider shall complete BGV before deployment.

5. CONFIDENTIALITY AND IP
(a) Resources shall sign Client''s NDA before deployment.
(b) All work product created by Resources shall be owned by Client.
(c) Provider shall not deploy Resources on competing projects during the engagement.

6. NON-SOLICITATION
Neither party shall directly solicit or hire the other party''s employees for a period of 12 months after the engagement ends.

7. TERM AND TERMINATION
(a) Initial term: [TERM_MONTHS] months.
(b) Either party may terminate with [NOTICE_DAYS] days written notice.
(c) Client shall pay for services rendered till the termination date.
(d) Bench period: Provider shall not charge for days a Resource is on bench.

8. INDEMNIFICATION
Provider shall indemnify Client against claims related to employment disputes, statutory non-compliance, or IP infringement by Resources.

9. GOVERNING LAW
This Agreement shall be governed by the laws of India. Courts at [JURISDICTION] shall have exclusive jurisdiction.

Provider: [PARTY_1_NAME]           Client: [PARTY_2_NAME]
Signature: ___________             Signature: ___________
Name: [SIGNATORY_1]                Name: [SIGNATORY_2]
Date: [DATE]                       Date: [DATE]',
 '[{"name":"Engagement Model","section":"1"},{"name":"Resource Rates","section":"2"},{"name":"Payment Terms","section":"3"},{"name":"Resource Management","section":"4"},{"name":"Confidentiality and IP","section":"5"},{"name":"Non-Solicitation","section":"6"},{"name":"Term and Termination","section":"7"},{"name":"Indemnification","section":"8"},{"name":"Governing Law","section":"9"}]'),

('Software Development Agreement', 'sda', 'client',
 'Agreement for custom software/AI development projects with milestones.',
 'SOFTWARE DEVELOPMENT AGREEMENT

This Software Development Agreement ("Agreement") is entered into as of [EFFECTIVE_DATE] between:

[PARTY_1_NAME] ("Developer"), having its office at [PARTY_1_ADDRESS];
AND
[PARTY_2_NAME] ("Client"), having its office at [PARTY_2_ADDRESS].

1. PROJECT SCOPE
Developer shall design, develop, test, and deploy the software application as described in Schedule A ("Project Specifications").

2. MILESTONES AND DELIVERABLES
Development shall proceed in the following phases:
Phase 1: Requirements & Design — [MILESTONE_1_DATE]
Phase 2: Development — [MILESTONE_2_DATE]
Phase 3: Testing & QA — [MILESTONE_3_DATE]
Phase 4: Deployment & Go-Live — [MILESTONE_4_DATE]
Phase 5: Post-launch Support — [SUPPORT_PERIOD]

3. FEES AND PAYMENT
(a) Total project fee: [TOTAL_FEE].
(b) Payment schedule: [ADVANCE]% advance, balance as per milestone completion.
(c) Change requests shall be quoted separately and approved in writing.
(d) Payment terms: Net [PAYMENT_DAYS] days.

4. ACCEPTANCE
(a) Client shall review each deliverable within [REVIEW_DAYS] business days.
(b) Acceptance criteria as defined in Project Specifications.
(c) If Client does not respond within the review period, deliverable is deemed accepted.

5. INTELLECTUAL PROPERTY
(a) Upon full payment, all custom-developed IP shall transfer to Client.
(b) Developer retains rights to pre-existing tools, frameworks, and libraries.
(c) Open source components shall be disclosed and licensed appropriately.

6. WARRANTY
(a) Developer warrants the software shall function as per specifications for [WARRANTY_MONTHS] months post go-live.
(b) Bug fixes during warranty period shall be provided at no additional cost.
(c) Warranty does not cover issues arising from unauthorized modifications.

7. CONFIDENTIALITY
Both parties shall maintain confidentiality of proprietary information. This obligation survives for 3 years post-termination.

8. LIMITATION OF LIABILITY
Total liability shall not exceed the total fees paid under this Agreement.

9. TERMINATION
(a) Either party may terminate with 30 days written notice.
(b) Client shall pay for all work completed till termination.
(c) Developer shall deliver all work-in-progress upon termination.

10. GOVERNING LAW
This Agreement shall be governed by the laws of India. Disputes shall be resolved through arbitration in [ARBITRATION_CITY].

Developer: [PARTY_1_NAME]          Client: [PARTY_2_NAME]
Signature: ___________             Signature: ___________
Name: [SIGNATORY_1]                Name: [SIGNATORY_2]
Date: [DATE]                       Date: [DATE]',
 '[{"name":"Project Scope","section":"1"},{"name":"Milestones","section":"2"},{"name":"Fees and Payment","section":"3"},{"name":"Acceptance","section":"4"},{"name":"Intellectual Property","section":"5"},{"name":"Warranty","section":"6"},{"name":"Confidentiality","section":"7"},{"name":"Limitation of Liability","section":"8"},{"name":"Termination","section":"9"},{"name":"Governing Law","section":"10"}]')

ON CONFLICT DO NOTHING;
