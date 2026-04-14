# Business Requirements Document (BRD)

## EMB CLM -- Contract Lifecycle Management Platform

| Field | Detail |
|-------|--------|
| **Document Version** | 2.0 |
| **Date** | April 13, 2026 |
| **Organization** | EMB (Expand My Business) / Mantarav Private Limited |
| **Project** | EMB CLM -- Contract Lifecycle Management |
| **Platform URL** | https://contract-cli-six.vercel.app |
| **Status** | Production |

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Business Objectives](#2-business-objectives)
3. [Scope](#3-scope)
4. [System Architecture](#4-system-architecture)
5. [User Roles & Access Control](#5-user-roles--access-control)
6. [Functional Requirements](#6-functional-requirements)
7. [Non-Functional Requirements](#7-non-functional-requirements)
8. [Database Schema](#8-database-schema)
9. [API Specification](#9-api-specification)
10. [Integration Points](#10-integration-points)
11. [Security Requirements](#11-security-requirements)
12. [Deployment & Infrastructure](#12-deployment--infrastructure)
13. [Quality Assurance](#13-quality-assurance)
14. [Glossary](#14-glossary)

---

## 1. Executive Summary

EMB CLM is a full-featured Contract Lifecycle Management platform built for EMB (Expand My Business / Mantarav Private Limited), a technology services broker operating across Cloud Resell (AWS/Azure/GCP), Resource Augmentation, and AI/Software Development verticals.

The platform manages the entire contract lifecycle -- from creation and negotiation through review, approval, execution, and renewal -- with AI-powered analysis, automated workflows, real-time collaboration, and comprehensive audit trails. It serves as the central system of record for all client and vendor contracts, enabling margin tracking, risk assessment, compliance monitoring, and operational efficiency.

**Key Capabilities:**
- End-to-end contract lifecycle management with enforced state machine
- AI-powered contract review and risk analysis (GPT-4o)
- OCR for scanned/image PDFs via GPT-4o Vision
- RAG-based intelligent chatbot for contract Q&A (hybrid semantic + keyword search)
- Multi-party collaboration with role-based access
- Automated workflow engine with 5 configurable triggers and 5 action types
- E-signature integration (Leegality)
- Email notification system with per-user preferences (Resend API)
- Redline/track changes with version history
- Invoice tracking and margin analysis
- Shareable contract links with granular permissions
- Contract linking and party management
- Auto-renewal detection and tracking
- Kanban board, calendar view, and analytics dashboards
- Bulk import/export, backup/restore, and audit log retention management
- 132 API endpoints with standardized error handling
- 514 automated tests covering all endpoints and edge cases

---

## 2. Business Objectives

| # | Objective | Success Metric |
|---|-----------|---------------|
| 1 | Centralize all contract data in a single platform | 100% of active contracts digitized and searchable |
| 2 | Reduce contract review cycle time | AI-assisted review available for all contracts |
| 3 | Enforce approval workflows before execution | Zero contracts executed without completed approvals |
| 4 | Provide real-time visibility into contract status | Dashboard with live status, value, and renewal tracking |
| 5 | Enable margin analysis (client value vs vendor cost) | All contracts tagged with financial data for reporting |
| 6 | Ensure compliance through audit trails | Complete activity log for every contract action |
| 7 | Reduce manual effort through automation | Workflow rules auto-trigger on contract events |
| 8 | Enable secure digital signatures | E-sign integration for paperless execution |
| 9 | Provide AI-powered contract intelligence | RAG chat, risk scoring, clause extraction available on all contracts |
| 10 | Support invoice and financial tracking | Invoice lifecycle linked to contracts for margin visibility |

---

## 3. Scope

### 3.1 In Scope

- Contract CRUD (create, read, update, delete)
- Contract templates and clause library
- AI-powered review, risk scoring, and recommendations
- RAG chatbot with contract-scoped and global Q&A
- OCR for scanned/image PDFs (GPT-4o Vision)
- Version control with redline/diff comparison
- Multi-level approval workflows with execution gates
- Contract collaboration (viewer, editor, reviewer roles)
- Obligation tracking and deadline management
- Tag management with presets and color coding
- Custom fields per contract (text, number, date, select)
- E-signature via Leegality with webhook verification
- Email notifications with per-user preferences
- Webhook integrations for external systems
- Calendar view for deadlines and renewals
- Kanban board for visual pipeline management
- Reports and analytics dashboard (executive, counterparty risk, health score)
- Counterparty management and history
- Invoice tracking with margin analysis
- Contract linking (parent-child, related, amendment)
- Contract party management (client, vendor, subcontractor)
- Shareable contract links with view/comment permissions
- Auto-renewal detection and tracking
- Bulk import/export (CSV)
- System backup and restore
- Audit log with configurable retention and cleanup
- User management with RBAC (4-tier)
- Password management with bcrypt hashing and legacy migration
- Full audit trail per contract and system-wide

### 3.2 Out of Scope

- Native mobile application
- Multi-tenant / multi-organization support
- Offline mode
- Built-in WYSIWYG document editor
- Payment processing
- Third-party CRM integration (Salesforce, HubSpot)

---

## 4. System Architecture

### 4.1 Technology Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **Frontend** | HTML5 / CSS3 / Vanilla JavaScript | Single-page application (SPA) |
| **Backend** | Python 3.x / Flask | REST API (serverless functions) |
| **Database** | Supabase PostgreSQL + pgvector | Primary data store with vector search |
| **Hosting** | Vercel | Serverless deployment (API + static) |
| **AI/ML** | OpenAI GPT-4o + text-embedding-3-small | Contract review, chat, OCR, embeddings |
| **Email** | Resend API | Transactional email notifications |
| **E-Sign** | Leegality API | Digital signature workflows |
| **Auth** | HMAC-SHA256 token signing + bcrypt | Token-based authentication with password hashing |

### 4.2 Architecture Diagram

```
+---------------------------------------------------------+
|                    FRONTEND (SPA)                        |
|              public/index.html (~3,600 lines)            |
|     19 pages . Modal tabs . Kanban . Calendar . Chat     |
+--------------------------+------------------------------+
                           | HTTPS / REST API
                           v
+---------------------------------------------------------+
|                 BACKEND (Flask on Vercel)                 |
|                                                          |
|  +----------+ +----------+ +--------+ +-----------+     |
|  | index.py | | auth.py  | | ai.py  | | helpers.py|     |
|  | (routes) | | (RBAC)   | | (RAG)  | | (workflow)|     |
|  +----------+ +----------+ +--------+ +-----------+     |
|        config.py (shared state) | constants.py           |
|             ~4,300 lines . 132 endpoints                 |
+---+----------+--------------+--------------+------------+
    |          |              |              |
    v          v              v              v
+--------+ +--------+  +----------+  +-----------+
|Supabase| |OpenAI  |  | Resend   |  | Leegality |
|  (DB)  | |(GPT-4o)|  | (Email)  |  | (E-Sign)  |
+--------+ +--------+  +----------+  +-----------+
```

### 4.3 Module Structure

| Module | Lines | Responsibility |
|--------|-------|---------------|
| `api/index.py` | ~3,430 | All route definitions (132 endpoints) |
| `api/config.py` | ~87 | Configuration, DB init, rate limiter, CORS, RBAC hierarchy |
| `api/auth.py` | ~163 | HMAC token auth, bcrypt passwords, RBAC decorators, input sanitization |
| `api/ai.py` | ~251 | OpenAI chat/streaming/embeddings, RAG chunking, hybrid search, OCR |
| `api/helpers.py` | ~263 | Activity logging, webhooks, notifications, email, workflow engine, redline diff |
| `api/constants.py` | ~71 | Named constants for thresholds, limits, validation sets, colors |
| `public/index.html` | ~3,600 | Complete frontend (HTML + CSS + JS, ~195 functions) |

---

## 5. User Roles & Access Control

### 5.1 Role Hierarchy

| Role | Level | Description |
|------|-------|-------------|
| **Viewer** | 0 | Read-only access to contracts and dashboards |
| **Editor** | 1 | Create/edit contracts, add comments, manage tags |
| **Manager** | 2 | Approve contracts, manage workflows, calendar access |
| **Admin** | 3 | Full system access: user management, webhooks, backup/restore |

### 5.2 Permission Matrix

| Action | Viewer | Editor | Manager | Admin |
|--------|--------|--------|---------|-------|
| View contracts & dashboard | Yes | Yes | Yes | Yes |
| Search & chat with AI | Yes | Yes | Yes | Yes |
| Create/edit contracts | No | Yes | Yes | Yes |
| Add comments & tags | No | Yes | Yes | Yes |
| Manage custom fields | No | Yes | Yes | Yes |
| Upload PDFs | No | Yes | Yes | Yes |
| Manage invoices | No | Yes | Yes | Yes |
| Manage approvals | No | No | Yes | Yes |
| Manage workflows | No | No | Yes | Yes |
| View calendar | No | No | Yes | Yes |
| Delete templates | No | No | Yes | Yes |
| Manage users | No | No | No | Yes |
| Configure webhooks | No | No | No | Yes |
| Backup/restore system | No | No | No | Yes |
| Delete contracts | No | No | No | Yes |
| Bulk import/operations | No | No | No | Yes |
| Audit log cleanup | No | No | No | Yes |

### 5.3 Contract Collaborator Roles

Collaborators can be assigned per contract with specific roles:

| Role | Capabilities |
|------|-------------|
| **Viewer** | Read contract details and activity |
| **Editor** | Edit contract content and metadata |
| **Reviewer** | Review and provide feedback on contract |

---

## 6. Functional Requirements

### 6.1 Contract Management (FR-100)

| ID | Requirement | Priority | Status |
|----|------------|----------|--------|
| FR-101 | Create contracts with name, party, type (client/vendor), status, value, dates, department, description, and content | Must Have | Done |
| FR-102 | Edit all contract fields with version tracking | Must Have | Done |
| FR-103 | Delete contracts (admin only, with cascade) | Must Have | Done |
| FR-104 | Clone existing contracts to create new ones | Should Have | Done |
| FR-105 | Search contracts by keyword across all fields | Must Have | Done |
| FR-106 | Filter contracts by status, type, department | Must Have | Done |
| FR-107 | Bulk import contracts via CSV upload | Should Have | Done |
| FR-108 | Export contracts to CSV | Should Have | Done |
| FR-109 | PDF generation and download per contract | Should Have | Done |
| FR-110 | Contract linking (parent-child, related, amendment) | Should Have | Done |
| FR-111 | Contract party management (client, vendor, subcontractor) | Should Have | Done |
| FR-112 | Shareable contract links with view/comment permissions | Should Have | Done |
| FR-113 | Auto-renewal detection and tracking | Should Have | Done |

### 6.2 Contract Status Lifecycle (FR-200)

| ID | Requirement | Priority | Status |
|----|------------|----------|--------|
| FR-201 | Enforce state machine transitions between statuses | Must Have | Done |
| FR-202 | Block execution if pending approvals exist | Must Have | Done |
| FR-203 | Log all status changes in activity trail | Must Have | Done |
| FR-204 | Trigger notifications on status changes | Must Have | Done |
| FR-205 | Execute workflow rules on status transitions | Should Have | Done |
| FR-206 | Bulk status transitions with validation | Should Have | Done |

**State Machine:**

```
                    +----------+
           +-------|  DRAFT   |<--------------+
           |       +--+---+---+               |
           |          |   |                    |
           v          v   v                    |
     +---------+  +-----------+         +----------+
     | PENDING |--| IN_REVIEW |-------->| REJECTED |
     +----+----+  +-----+-----+         +----------+
          |             |                      ^
          |             v                      |
          |       +----------+                 |
          +------>| EXECUTED |----------------+
                  +----------+
```

**Valid Transitions:**
- Draft -> Pending, In Review
- Pending -> In Review, Rejected, Draft
- In Review -> Executed, Rejected, Pending
- Executed -> Rejected
- Rejected -> Draft

### 6.3 AI-Powered Features (FR-300)

| ID | Requirement | Priority | Status |
|----|------------|----------|--------|
| FR-301 | AI contract review with risk scoring (1-10 scale) | Must Have | Done |
| FR-302 | Identify key clauses, obligations, and deadlines from content | Must Have | Done |
| FR-303 | Generate improvement recommendations | Should Have | Done |
| FR-304 | RAG-based chatbot with contract-scoped queries | Must Have | Done |
| FR-305 | Hybrid search (semantic + keyword) across contract chunks | Must Have | Done |
| FR-306 | Auto-embed contract content into vector store on creation | Should Have | Done |
| FR-307 | Contract comparison with AI-generated analysis | Should Have | Done |
| FR-308 | Parse uploaded PDF content into structured contract data | Should Have | Done |
| FR-309 | OCR for scanned/image PDFs via GPT-4o Vision | Should Have | Done |
| FR-310 | Smart legal-aware chunking (clause boundaries, annexures, signatures) | Should Have | Done |
| FR-311 | Streaming chat responses for real-time UX | Should Have | Done |

### 6.4 Templates & Clause Library (FR-400)

| ID | Requirement | Priority | Status |
|----|------------|----------|--------|
| FR-401 | Create, edit, delete contract templates | Must Have | Done |
| FR-402 | Templates include name, category, type, description, content | Must Have | Done |
| FR-403 | Create contracts from templates (pre-filled content) | Must Have | Done |
| FR-404 | Maintain reusable clause library | Should Have | Done |
| FR-405 | Track clause usage count | Nice to Have | Done |

### 6.5 Version Control & Redline (FR-500)

| ID | Requirement | Priority | Status |
|----|------------|----------|--------|
| FR-501 | Auto-save contract versions on every edit | Must Have | Done |
| FR-502 | View version history with timestamps and authors | Must Have | Done |
| FR-503 | Restore any previous version | Must Have | Done |
| FR-504 | Word-level diff (redline) between versions | Must Have | Done |
| FR-505 | Line-level unified diff view | Should Have | Done |
| FR-506 | Visual markup: red strikethrough (deletions), green highlight (additions) | Must Have | Done |

### 6.6 Approvals (FR-600)

| ID | Requirement | Priority | Status |
|----|------------|----------|--------|
| FR-601 | Request approval from named approvers | Must Have | Done |
| FR-602 | Approve or reject with comments | Must Have | Done |
| FR-603 | Block contract execution until all approvals complete | Must Have | Done |
| FR-604 | Track approval history with timestamps | Must Have | Done |
| FR-605 | Notify approvers via email and in-app notification | Should Have | Done |
| FR-606 | Approval SLA tracking (days pending) | Should Have | Done |

### 6.7 Collaboration (FR-700)

| ID | Requirement | Priority | Status |
|----|------------|----------|--------|
| FR-701 | Add collaborators to contracts with roles (viewer/editor/reviewer) | Must Have | Done |
| FR-702 | Change collaborator roles inline | Must Have | Done |
| FR-703 | Remove collaborators from contracts | Must Have | Done |
| FR-704 | Add threaded comments on contracts | Must Have | Done |
| FR-705 | Track all collaboration activity in audit trail | Must Have | Done |

### 6.8 Obligations & Compliance (FR-800)

| ID | Requirement | Priority | Status |
|----|------------|----------|--------|
| FR-801 | Create obligations with title, description, deadline, assignee | Must Have | Done |
| FR-802 | Track obligation status (pending, in_progress, completed, overdue) | Must Have | Done |
| FR-803 | Calendar view showing all deadlines and renewals | Should Have | Done |
| FR-804 | Renewal tracking with auto-identification | Should Have | Done |
| FR-805 | Obligation escalation support | Nice to Have | Done |

### 6.9 E-Signature (FR-900)

| ID | Requirement | Priority | Status |
|----|------------|----------|--------|
| FR-901 | Internal signature capture (name + timestamp) | Must Have | Done |
| FR-902 | External e-signature via Leegality integration | Should Have | Done |
| FR-903 | Webhook-based signature status updates | Should Have | Done |
| FR-904 | HMAC verification for webhook security | Must Have | Done |

### 6.10 Notifications & Email (FR-1000)

| ID | Requirement | Priority | Status |
|----|------------|----------|--------|
| FR-1001 | In-app notification center with unread count | Must Have | Done |
| FR-1002 | Mark notifications as read / clear all | Must Have | Done |
| FR-1003 | Email notifications via Resend API with HTML templates | Should Have | Done |
| FR-1004 | Per-user email preferences (enable/disable by event type) | Should Have | Done |
| FR-1005 | Event types: status change, approval, comment, expiry, workflow | Must Have | Done |
| FR-1006 | Retry logic for email delivery (2 attempts) | Should Have | Done |
| FR-1007 | Recipient cap per event (max 10) | Should Have | Done |

### 6.11 Workflows & Automation (FR-1100)

| ID | Requirement | Priority | Status |
|----|------------|----------|--------|
| FR-1101 | Create workflow rules with trigger events and conditions | Should Have | Done |
| FR-1102 | Triggers: status_change, contract_created, approval_completed, obligation_overdue, contract_expiring | Should Have | Done |
| FR-1103 | Actions: add_tag, auto_approve, change_status, create_obligation, notify_webhook | Should Have | Done |
| FR-1104 | Conditional execution based on status, value, contract type | Should Have | Done |
| FR-1105 | Workflow execution log with details | Should Have | Done |
| FR-1106 | Priority-based rule ordering | Nice to Have | Done |

### 6.12 Reporting & Analytics (FR-1200)

| ID | Requirement | Priority | Status |
|----|------------|----------|--------|
| FR-1201 | Executive dashboard with contract counts by status | Must Have | Done |
| FR-1202 | Total and average contract value metrics | Must Have | Done |
| FR-1203 | Status distribution charts | Should Have | Done |
| FR-1204 | Type breakdown (client vs vendor) | Should Have | Done |
| FR-1205 | Department-wise contract distribution | Should Have | Done |
| FR-1206 | Monthly contract creation trends (12-month) | Should Have | Done |
| FR-1207 | Upcoming renewal alerts (30/60/90 day bands) | Should Have | Done |
| FR-1208 | Counterparty risk analysis with risk scoring | Should Have | Done |
| FR-1209 | Contract health score with risk indicators | Should Have | Done |
| FR-1210 | Margin analysis and financial reporting | Should Have | Done |

### 6.13 Kanban Board (FR-1300)

| ID | Requirement | Priority | Status |
|----|------------|----------|--------|
| FR-1301 | Visual kanban board with columns per contract status | Should Have | Done |
| FR-1302 | Contract cards showing key metadata | Should Have | Done |
| FR-1303 | Click-through to contract detail from kanban card | Should Have | Done |

### 6.14 Audit Trail (FR-1400)

| ID | Requirement | Priority | Status |
|----|------------|----------|--------|
| FR-1401 | Log every contract action with user, timestamp, details | Must Have | Done |
| FR-1402 | System-wide audit log with filtering | Must Have | Done |
| FR-1403 | Per-contract audit trail page with summary cards | Must Have | Done |
| FR-1404 | Color-coded timeline with action-type icons | Should Have | Done |
| FR-1405 | Export audit trail data | Should Have | Done |
| FR-1406 | Audit log retention management with configurable cleanup | Should Have | Done |
| FR-1407 | Minimum 30-day retention enforcement | Must Have | Done |

### 6.15 Tags & Custom Fields (FR-1500)

| ID | Requirement | Priority | Status |
|----|------------|----------|--------|
| FR-1501 | Add/remove tags with custom colors per contract | Should Have | Done |
| FR-1502 | Tag presets for quick application | Nice to Have | Done |
| FR-1503 | Define custom fields (text, number, date, select types) | Should Have | Done |
| FR-1504 | Set custom field values per contract | Should Have | Done |

### 6.16 Invoice Management (FR-1600)

| ID | Requirement | Priority | Status |
|----|------------|----------|--------|
| FR-1601 | Create invoices linked to contracts | Should Have | Done |
| FR-1602 | Track invoice status and amounts | Should Have | Done |
| FR-1603 | Delete invoices (editor role required) | Should Have | Done |
| FR-1604 | Margin calculation (contract value vs invoice totals) | Should Have | Done |

### 6.17 Data Management (FR-1700)

| ID | Requirement | Priority | Status |
|----|------------|----------|--------|
| FR-1701 | Full system backup (JSON export of all tables) | Must Have | Done |
| FR-1702 | System restore from backup file | Must Have | Done |
| FR-1703 | Bulk CSV import with validation and preview | Should Have | Done |
| FR-1704 | CSV export with configurable fields | Should Have | Done |
| FR-1705 | Bulk status transitions with error reporting | Should Have | Done |

---

## 7. Non-Functional Requirements

### 7.1 Performance

| ID | Requirement | Target |
|----|------------|--------|
| NFR-01 | API response time (non-AI endpoints) | < 500ms (p95) |
| NFR-02 | AI review response time | < 60 seconds |
| NFR-03 | Chat response time (streaming) | First token < 3s |
| NFR-04 | Dashboard load time (cached) | < 2 seconds |
| NFR-05 | Contract list pagination | Default 50, max 200 per page |
| NFR-06 | Concurrent users | Support 50+ simultaneous users |
| NFR-07 | Dashboard cache TTL | 60 seconds |

### 7.2 Security

| ID | Requirement |
|----|------------|
| NFR-08 | HMAC-SHA256 token-based authentication |
| NFR-09 | Token expiry: 24-hour access tokens |
| NFR-10 | Token revocation on logout |
| NFR-11 | Bcrypt password hashing with legacy SHA-256 migration |
| NFR-12 | Input sanitization on all user-provided fields (XSS prevention) |
| NFR-13 | Email format validation for user and collaborator fields |
| NFR-14 | HMAC verification for external webhook payloads |
| NFR-15 | Row-level security (RLS) on all database tables |
| NFR-16 | CORS origin validation for mutating requests |
| NFR-17 | API keys stored as environment variables, never in code |
| NFR-18 | Rate limiting: 120 requests/minute per IP (sliding window) |
| NFR-19 | Security headers (X-Content-Type-Options, X-Frame-Options, X-XSS-Protection) |
| NFR-20 | Standardized error responses via `err()` helper (no information leakage) |

### 7.3 Reliability & Availability

| ID | Requirement |
|----|------------|
| NFR-21 | Health check endpoint for monitoring |
| NFR-22 | Retry logic on OpenAI API calls (2 retries with backoff) |
| NFR-23 | Retry logic on email delivery (2 attempts) |
| NFR-24 | Graceful degradation when external APIs unavailable |
| NFR-25 | Database connection validation on startup |
| NFR-26 | Specific exception handling (no bare except clauses) |

### 7.4 Scalability

| ID | Requirement |
|----|------------|
| NFR-27 | Serverless deployment (auto-scales with Vercel) |
| NFR-28 | Database indexes on frequently queried columns |
| NFR-29 | Chunked embedding processing (batches of 20) |
| NFR-30 | Email recipient cap (max 10 per event) |
| NFR-31 | PDF upload limit: 50 MB per file, 10 files per bulk upload |
| NFR-32 | OCR page limit: 50 pages per document |
| NFR-33 | Max request body: 16 MB (Flask), 52 MB (Vercel) |

### 7.5 Maintainability

| ID | Requirement |
|----|------------|
| NFR-34 | Modular backend architecture (6 Python modules) |
| NFR-35 | Automated test suite: 514 tests across 8 test files |
| NFR-36 | Named constants centralized in constants.py (no magic numbers) |
| NFR-37 | Standardized error format: `{"error": {"message": "...", "code": N}}` |
| NFR-38 | Sequential database migrations (v2 through v15) |
| NFR-39 | Environment-based configuration |
| NFR-40 | Descriptive function and variable names throughout codebase |

---

## 8. Database Schema

### 8.1 Tables Overview

| # | Table | Purpose | Key Fields |
|---|-------|---------|------------|
| 1 | `contracts` | Core contract records | id, name, party, type, status, value, start_date, end_date, department, content, jurisdiction, governing_law, auto_renew |
| 2 | `contract_templates` | Reusable contract templates | id, name, category, type, description, content |
| 3 | `contract_versions` | Version history per contract | id, contract_id, version_number, content, changed_by |
| 4 | `contract_chunks` | RAG vector chunks | id, contract_id, chunk_index, chunk_text, section_title, embedding (vector) |
| 5 | `contract_tags` | Tags applied to contracts | id, contract_id, tag_name, tag_color, created_by |
| 6 | `tag_presets` | Predefined tag options | id, name, color |
| 7 | `contract_collaborators` | Per-contract team members | id, contract_id, user_email, user_name, role, added_by |
| 8 | `contract_activity` | Audit trail events | id, contract_id, action, user_name, details, created_at |
| 9 | `contract_comments` | Discussion threads | id, contract_id, user_name, comment, created_at |
| 10 | `contract_obligations` | Tracked obligations/deadlines | id, contract_id, title, description, deadline, assigned_to, status, escalation_email |
| 11 | `contract_approvals` | Approval workflow records | id, contract_id, approver_name, status, comments |
| 12 | `contract_signatures` | Signature records | id, contract_id, signer_name, signed_at |
| 13 | `contract_parties` | Multi-party management | id, contract_id, party_name, party_type, contact_email |
| 14 | `contract_links` | Contract relationships | id, source_id, target_id, link_type (parent, related, amendment) |
| 15 | `share_links` | Shareable access links | id, contract_id, token, permission (view/comment), expires_at |
| 16 | `clause_library` | Reusable clause bank | id, title, content, category, usage_count |
| 17 | `workflow_rules` | Automation rule definitions | id, name, trigger_event, trigger_condition, action_type, action_config, priority, is_active |
| 18 | `workflow_log` | Workflow execution history | id, rule_id, rule_name, contract_id, trigger_event, action_taken, details |
| 19 | `clm_users` | User accounts | id, name, email, role, department, is_active, password_hash |
| 20 | `custom_field_defs` | Custom field definitions | id, field_name, field_type, options |
| 21 | `custom_field_values` | Custom field values per contract | id, contract_id, field_id, value |
| 22 | `email_preferences` | Per-user email settings | id, user_email, enabled, on_status_change, on_approval, on_comment, on_expiry, on_workflow |
| 23 | `notifications` | In-app notifications | id, title, message, type, contract_id, user_email, is_read, link |
| 24 | `webhook_configs` | External webhook URLs | id, url, event_type, active |
| 25 | `invoices` | Invoice records per contract | id, contract_id, invoice_number, amount, status, due_date |
| 26 | `settings` | System-level settings | id, key, value |

### 8.2 Migration History

| Order | File | Description |
|-------|------|-------------|
| 1 | `migration_v2.sql` | Core schema (contracts, templates, versions, chunks, activity) |
| 2 | `migration_v3_users.sql` | User management (clm_users, revoked_tokens) |
| 3 | `migration_v4_features.sql` | Extended features (comments, obligations, approvals, signatures, clauses) |
| 4 | `migration_v5_tags_workflow.sql` | Tags, presets, workflow rules and log |
| 5 | `migration_v6_customfields_notifications.sql` | Custom fields, notifications, webhooks |
| 6 | `migration_v7_email_preferences.sql` | Email notification preferences |
| 7 | `migration_v8_production.sql` | Production constraints and indexes |
| 8 | `migration_v9_collaborators.sql` | Contract collaborators |
| 9 | `migration_v10_linking.sql` | Contract linking (parent-child, related, amendment) |
| 10 | `migration_v11_parties.sql` | Contract party management |
| 11 | `migration_v12_obligation_escalation.sql` | Obligation escalation support |
| 12 | `migration_v13_share_links.sql` | Shareable contract links |
| 13 | `migration_v14_invoices_settings.sql` | Invoice tracking and system settings |
| 14 | `migration_v15_rls.sql` | Row-level security policy fixes |

---

## 9. API Specification

### 9.1 API Summary

- **Base URL:** `https://contract-cli-six.vercel.app/api`
- **Authentication:** Bearer token (HMAC-SHA256) in `Authorization` header
- **Content Type:** `application/json`
- **Total Endpoints:** 132
- **Error Format:** `{"error": {"message": "...", "code": N}}`

### 9.2 Endpoint Groups

| Group | Endpoints | Auth Required | Min Role |
|-------|-----------|--------------|----------|
| **Health** | 2 | No | -- |
| **Auth** | 5 | Partial | -- |
| **Contracts CRUD** | 8 | Yes | Editor (create/edit), Admin (delete) |
| **Contract Status** | 1 | Yes | Editor |
| **Contract Linking** | 3 | Yes | Editor |
| **Contract Parties** | 3 | Yes | Editor |
| **Share Links** | 3 | Yes | Editor |
| **AI Review & Chat** | 4 | Yes | Viewer |
| **OCR & Parse** | 2 | Yes | Editor |
| **Comments** | 2 | Yes | Editor |
| **Obligations** | 3 | Yes | Editor |
| **Approvals** | 3 | Yes | Manager |
| **Approval SLA** | 1 | Yes | Manager |
| **Collaborators** | 4 | Yes | Editor |
| **Signatures & E-Sign** | 4 | Yes | Editor |
| **Versions & Redline** | 5 | Yes | Editor |
| **Tags** | 5 | Yes | Editor |
| **Templates** | 5 | Yes | Editor (CRUD), Manager (delete) |
| **Clauses** | 4 | Yes | Editor |
| **Custom Fields** | 5 | Yes | Editor |
| **Workflows** | 5 | Yes | Manager |
| **Users** | 4 | Yes | Admin |
| **Webhooks** | 3 | Yes | Admin |
| **Notifications** | 3 | Yes | Viewer |
| **Email Preferences** | 4 | Yes | Viewer |
| **Invoices** | 3 | Yes | Editor |
| **Reports & Analytics** | 4 | Yes | Viewer |
| **Calendar** | 1 | Yes | Manager |
| **Renewals** | 2 | Yes | Viewer |
| **Auto-Renew** | 1 | Yes | Editor |
| **Counterparty** | 2 | Yes | Viewer |
| **Audit Log** | 2 | Yes | Viewer (view), Admin (cleanup) |
| **Data Management** | 7 | Yes | Various |
| **Bulk Operations** | 2 | Yes | Admin |
| **Slack Integration** | 2 | Yes | Admin |

### 9.3 Key Endpoints Detail

#### Authentication
```
POST /api/auth/login          -- Login with password, returns token
GET  /api/auth/verify         -- Verify current token
POST /api/auth/refresh        -- Refresh access token
POST /api/auth/logout         -- Revoke current token
POST /api/auth/reset-password -- Reset user password (admin)
POST /api/auth/validate-password -- Validate password strength
```

#### Contract Lifecycle
```
GET    /api/contracts              -- List all contracts (paginated, filterable)
POST   /api/contracts              -- Create new contract
GET    /api/contracts/<id>         -- Get contract details
PUT    /api/contracts/<id>         -- Update contract
DELETE /api/contracts/<id>         -- Delete contract (admin only)
POST   /api/contracts/<id>/status  -- Change status (enforced state machine)
POST   /api/contracts/<id>/clone   -- Clone contract
POST   /api/contracts/<id>/review  -- AI-powered review
POST   /api/contracts/<id>/auto-renew -- Auto-renew contract
```

#### AI & Intelligence
```
POST /api/chat                     -- RAG chatbot (streaming response)
POST /api/contracts/compare        -- AI contract comparison
POST /api/parse                    -- Parse PDF into structured data (with OCR)
POST /api/contracts/<id>/embed     -- Generate vector embeddings
```

#### Invoices
```
GET    /api/contracts/<id>/invoices -- List invoices for contract
POST   /api/contracts/<id>/invoices -- Create invoice
DELETE /api/invoices/<id>           -- Delete invoice
```

#### Share Links
```
POST   /api/contracts/<id>/share   -- Create share link
GET    /api/shared/<token>         -- Access shared contract
DELETE /api/share-links/<id>       -- Delete share link
```

#### Analytics
```
GET /api/dashboard                 -- Executive dashboard (cached)
GET /api/reports                   -- Detailed analytics and charts
GET /api/counterparty-risk         -- Counterparty risk analysis
GET /api/approval-sla              -- Approval SLA tracking
```

---

## 10. Integration Points

### 10.1 OpenAI (AI Engine)

| Aspect | Detail |
|--------|--------|
| **Purpose** | Contract review, risk analysis, chatbot, embeddings, OCR |
| **Models** | GPT-4o (chat, review, OCR), GPT-4o-mini (metadata extraction), text-embedding-3-small (embeddings) |
| **API** | `https://api.openai.com/v1` |
| **Features** | Streaming responses, retry logic (2 retries), batch embedding (20 per batch), smart legal-aware chunking |
| **Timeouts** | 55s (standard), 120s (streaming) |
| **Config** | `OPENAI_API_KEY` environment variable |

### 10.2 Supabase (Database)

| Aspect | Detail |
|--------|--------|
| **Purpose** | Primary data store, vector search, RLS |
| **Type** | PostgreSQL with pgvector extension |
| **Features** | Row-level security, RPC functions, parameterized queries |
| **Config** | `SUPABASE_URL`, `SUPABASE_KEY` environment variables |
| **RPC** | `match_chunks` -- cosine similarity vector search |

### 10.3 Resend (Email)

| Aspect | Detail |
|--------|--------|
| **Purpose** | Transactional email notifications |
| **API** | `https://api.resend.com/emails` |
| **Features** | HTML email templates, per-user preferences, retry logic (2 attempts) |
| **Config** | `RESEND_API_KEY`, `EMAIL_FROM` environment variables |
| **Limit** | Max 10 recipients per event |

### 10.4 Leegality (E-Signature)

| Aspect | Detail |
|--------|--------|
| **Purpose** | Legally binding digital signatures |
| **Features** | Document signing, webhook status updates, HMAC verification |
| **Config** | `LEEGALITY_API_KEY`, `LEEGALITY_PRIVATE_SALT` environment variables |
| **Webhook** | `POST /api/leegality/webhook` (HMAC-verified) |

### 10.5 Slack (Notifications)

| Aspect | Detail |
|--------|--------|
| **Purpose** | Team notifications via Slack webhook |
| **Features** | Send contract alerts and workflow notifications |
| **Config** | Webhook URL stored in system settings |

---

## 11. Security Requirements

### 11.1 Authentication

- HMAC-SHA256 token signing using `APP_SECRET`
- Access token validity: 24 hours (configurable via `TOKEN_EXPIRY_SECONDS`)
- Token revocation on logout (stored in memory)
- Bcrypt password hashing with automatic legacy SHA-256 migration
- Password-based login with configurable `APP_PASSWORD`

### 11.2 Authorization

- 4-tier RBAC: viewer (0) -> editor (1) -> manager (2) -> admin (3)
- Role enforcement via `@role_required()` decorator on all protected endpoints
- Per-contract collaborator roles for granular access
- Share links with view/comment permissions and expiry

### 11.3 Input Security

- `_sanitize()` strips script tags, event handlers, and javascript: URIs from all inputs
- `_sanitize_html()` removes all HTML tags for plain-text fields
- `_sanitize_dict()` applies sanitization across all string fields in request payloads
- `_valid_email()` validates email format via regex
- Parameterized queries via Supabase client (no raw SQL)
- Content length validation with field-specific limits (email: 254, name: 500, URL: 2000, content: 500K)

### 11.4 API Security

- CORS origin validation for mutating requests (POST/PUT/DELETE)
- Rate limiting: 120 requests/minute per IP (sliding window)
- Security headers: X-Content-Type-Options, X-Frame-Options, X-XSS-Protection
- HMAC signature verification on Leegality webhook payloads
- All API keys stored as environment variables
- Standardized error responses (no stack traces or internal details exposed)
- Health endpoint for monitoring without auth

### 11.5 Data Security

- Row-Level Security (RLS) enabled on all Supabase tables
- Database access only via Supabase client with anon key
- Backup/restore restricted to admin role
- Audit trail with configurable retention (minimum 30 days enforced)
- Audit log cleanup requires explicit confirmation flag

---

## 12. Deployment & Infrastructure

### 12.1 Deployment Pipeline

```
Development -> Staging -> Production
    |            |           |
    |   deploy-staging.sh    |
    |   (tests first)        |
    |                  deploy-production.sh
    |                  (tests + confirmation)
    +-- Local development with Flask dev server
```

### 12.2 Vercel Configuration

| Setting | Value |
|---------|-------|
| **Runtime** | Python (serverless functions) |
| **Entry Point** | `api/index.py` |
| **Max Duration** | 300 seconds (5 minutes) |
| **Max Body Size** | 52 MB |
| **Static Files** | `public/` directory |
| **Routing** | `/api/*` -> serverless, `/*` -> static |

### 12.3 Environment Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `SUPABASE_URL` | Yes | Supabase project URL |
| `SUPABASE_KEY` | Yes | Supabase anonymous key |
| `OPENAI_API_KEY` | Yes | OpenAI API key |
| `APP_SECRET` | Yes | HMAC token signing secret |
| `APP_PASSWORD` | Yes | Admin login password |
| `RESEND_API_KEY` | Optional | Email notification API key |
| `LEEGALITY_API_KEY` | Optional | E-signature API key |
| `LEEGALITY_PRIVATE_SALT` | Optional | Leegality webhook HMAC salt |
| `LEEGALITY_BASE_URL` | Optional | Leegality API base URL |
| `EMAIL_FROM` | Optional | Sender email address |
| `RATE_LIMIT` | Optional | Requests per minute per IP (default: 120) |

### 12.4 Frontend Pages

| # | Page | Description |
|---|------|-------------|
| 1 | Dashboard | Overview metrics, status cards, quick actions |
| 2 | Contracts | Searchable/filterable table with audit buttons |
| 3 | Create Contract | Full-page form for new contracts |
| 4 | Templates | Template library with CRUD |
| 5 | Template Form | Full-page template editor |
| 6 | Clauses | Reusable clause library |
| 7 | AI Chat | RAG-powered contract Q&A with streaming |
| 8 | Users | User management (admin) |
| 9 | Webhooks | Webhook configuration (admin) |
| 10 | Email Settings | Per-user notification preferences |
| 11 | Compare | Side-by-side contract comparison with AI analysis |
| 12 | Calendar | Deadline and renewal calendar |
| 13 | Workflows | Automation rule builder |
| 14 | Reports | Analytics, charts, and health scoring |
| 15 | Kanban | Visual pipeline board |
| 16 | Counterparty | Party-specific contract history and risk |
| 17 | Renewals | Upcoming renewal tracker with 30/60/90 day bands |
| 18 | Audit Log | System-wide audit trail with filtering |
| 19 | Contract Audit | Per-contract detailed audit page |

---

## 13. Quality Assurance

### 13.1 Test Suite Overview

| Metric | Value |
|--------|-------|
| **Framework** | pytest |
| **Total Tests** | 514 |
| **Test Files** | 8 |
| **Pass Rate** | 100% |
| **Run Command** | `python -m pytest tests/ -v` |

### 13.2 Test Coverage by Area

| Test File | Tests | Coverage Area |
|-----------|-------|--------------|
| `test_deep_endpoints.py` | 186 | All endpoint paths, edge cases, role enforcement |
| `test_coverage_gaps.py` | 122 | Rate limiting, CORS, workflows, OCR, invoices, share links, bulk ops |
| `test_audit.py` | 101 | Audit trail, token auth, activity logging, user management |
| `test_contracts.py` | 26 | Contract CRUD, validation, state machine |
| `test_security.py` | 23 | XSS prevention, sanitization, injection, auth bypass |
| `test_new_features.py` | 23 | Approvals, RBAC, signatures, obligations |
| `test_auth.py` | 18 | Login, token verify/refresh, logout, rate limiting |
| `test_api.py` | 15 | Health check, dashboard, templates, error handling |

### 13.3 Test Infrastructure

| Component | Purpose |
|-----------|---------|
| `conftest.py` | Fixtures: app, client, auth_token, mock_sb, rate limiter reset |
| `test_helpers.py` | `make_mock_response()` and `mock_chain()` for Supabase mocking |
| Autouse fixtures | Rate limiter and revoked token cleanup between tests |

### 13.4 Code Quality Measures

- Zero bare `except:` clauses -- all exceptions use specific types
- Named constants centralized in `constants.py` -- no magic numbers
- Standardized error responses via `err()` helper
- Descriptive function names (`make_token`, `check_token`, `_hmac_sign`)
- Input sanitization on all user-facing endpoints
- Frontend `eMsg()` helper for backward-compatible error display
- Unused imports removed across all modules
- Section headers and docstrings on all public functions

---

## 14. Glossary

| Term | Definition |
|------|-----------|
| **CLM** | Contract Lifecycle Management |
| **EMB** | Expand My Business (brand name) |
| **RAG** | Retrieval-Augmented Generation -- AI technique combining search with LLM |
| **RLS** | Row-Level Security -- database-level access control |
| **RBAC** | Role-Based Access Control |
| **SPA** | Single Page Application |
| **HMAC** | Hash-based Message Authentication Code |
| **pgvector** | PostgreSQL extension for vector similarity search |
| **Redline** | Track changes showing additions and deletions between versions |
| **Obligation** | Contractual duty with a deadline and assignee |
| **Counterparty** | The other party in a contract |
| **OCR** | Optical Character Recognition -- extracting text from images |
| **SLA** | Service Level Agreement -- in this context, approval response time |
| **Hybrid Search** | Combining semantic (vector) and keyword search for better recall |

---

*Document generated for EMB CLM -- Contract Lifecycle Management Platform*
*Version 2.0 | April 13, 2026*
