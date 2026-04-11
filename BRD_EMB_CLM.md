# Business Requirements Document (BRD)

## EMB CLM — Contract Lifecycle Management Platform

| Field | Detail |
|-------|--------|
| **Document Version** | 1.0 |
| **Date** | April 11, 2026 |
| **Organization** | EMB (Expand My Business) / Mantarav Private Limited |
| **Project** | EMB CLM — Contract Lifecycle Management |
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
13. [Glossary](#13-glossary)

---

## 1. Executive Summary

EMB CLM is a full-featured Contract Lifecycle Management platform built for EMB (Expand My Business / Mantarav Private Limited), a technology services broker operating across Cloud Resell (AWS/Azure/GCP), Resource Augmentation, and AI/Software Development verticals.

The platform manages the entire contract lifecycle — from creation and negotiation through review, approval, execution, and renewal — with AI-powered analysis, automated workflows, real-time collaboration, and comprehensive audit trails. It serves as the central system of record for all client and vendor contracts, enabling margin tracking, risk assessment, compliance monitoring, and operational efficiency.

**Key Capabilities:**
- End-to-end contract lifecycle management with enforced state machine
- AI-powered contract review and risk analysis (GPT-4o)
- RAG-based intelligent chatbot for contract Q&A
- Multi-party collaboration with role-based access
- Automated workflow engine with configurable triggers
- E-signature integration (Leegality)
- Email notification system with user preferences
- Redline/track changes with version history
- Kanban board, calendar view, and analytics dashboards
- Bulk import/export and backup/restore

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

---

## 3. Scope

### 3.1 In Scope

- Contract CRUD (create, read, update, delete)
- Contract templates and clause library
- AI-powered review, risk scoring, and recommendations
- RAG chatbot with contract-scoped and global Q&A
- Version control with redline/diff comparison
- Multi-level approval workflows
- Contract collaboration (viewer, editor, reviewer roles)
- Obligation tracking and deadline management
- Tag management with presets and color coding
- Custom fields per contract
- E-signature via Leegality
- Email notifications with per-user preferences
- Webhook integrations for external systems
- Calendar view for deadlines and renewals
- Kanban board for visual pipeline management
- Reports and analytics dashboard
- Counterparty management and history
- Bulk import/export (CSV)
- System backup and restore
- User management with RBAC (4-tier)
- Full audit trail per contract and system-wide

### 3.2 Out of Scope

- Native mobile application
- Multi-tenant / multi-organization support
- Offline mode
- OCR for scanned paper contracts
- Built-in document editor (WYSIWYG)
- Payment processing or invoicing
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
| **AI/ML** | OpenAI GPT-4o + text-embedding-3-small | Contract review, chat, embeddings |
| **Email** | Resend API | Transactional email notifications |
| **E-Sign** | Leegality API | Digital signature workflows |
| **Auth** | JWT (HS256) + HMAC | Token-based authentication |

### 4.2 Architecture Diagram

```
┌─────────────────────────────────────────────────────────┐
│                    FRONTEND (SPA)                        │
│              public/index.html (~2,700 lines)            │
│     19 pages · Modal tabs · Kanban · Calendar · Chat     │
└──────────────────────┬──────────────────────────────────┘
                       │ HTTPS / REST API
                       ▼
┌─────────────────────────────────────────────────────────┐
│                 BACKEND (Flask on Vercel)                 │
│                                                          │
│  ┌──────────┐ ┌──────────┐ ┌────────┐ ┌───────────┐     │
│  │ index.py │ │ auth.py  │ │ ai.py  │ │ helpers.py│     │
│  │ (routes) │ │ (RBAC)   │ │ (RAG)  │ │ (workflow)│     │
│  └──────────┘ └──────────┘ └────────┘ └───────────┘     │
│                  config.py (shared state)                 │
│                  ~2,300 lines · 99 endpoints              │
└───┬──────────┬──────────────┬──────────────┬────────────┘
    │          │              │              │
    ▼          ▼              ▼              ▼
┌────────┐ ┌────────┐  ┌──────────┐  ┌───────────┐
│Supabase│ │OpenAI  │  │ Resend   │  │ Leegality │
│  (DB)  │ │(GPT-4o)│  │ (Email)  │  │ (E-Sign)  │
└────────┘ └────────┘  └──────────┘  └───────────┘
```

### 4.3 Module Structure

| Module | Lines | Responsibility |
|--------|-------|---------------|
| `api/index.py` | ~2,300 | All route definitions (99 endpoints) |
| `api/config.py` | ~86 | Configuration, DB init, constants |
| `api/auth.py` | ~157 | JWT auth, RBAC decorators, input sanitization |
| `api/ai.py` | ~130 | OpenAI integration, RAG, chunking, embeddings |
| `api/helpers.py` | ~258 | Activity logging, webhooks, notifications, workflows |
| `public/index.html` | ~2,700 | Complete frontend (HTML + CSS + JS) |

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
| Manage approvals | No | No | Yes | Yes |
| Manage workflows | No | No | Yes | Yes |
| View calendar | No | No | Yes | Yes |
| Delete templates | No | No | Yes | Yes |
| Manage users | No | No | No | Yes |
| Configure webhooks | No | No | No | Yes |
| Backup/restore system | No | No | No | Yes |
| Delete contracts | No | No | No | Yes |
| Bulk import | No | No | No | Yes |

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

| ID | Requirement | Priority |
|----|------------|----------|
| FR-101 | Create contracts with name, party, type (client/vendor), status, value, dates, department, description, and content | Must Have |
| FR-102 | Edit all contract fields with version tracking | Must Have |
| FR-103 | Delete contracts (admin only, with cascade) | Must Have |
| FR-104 | Clone existing contracts to create new ones | Should Have |
| FR-105 | Search contracts by keyword across all fields | Must Have |
| FR-106 | Filter contracts by status, type, department | Must Have |
| FR-107 | Bulk import contracts via CSV upload | Should Have |
| FR-108 | Export contracts to CSV | Should Have |
| FR-109 | PDF generation and download per contract | Should Have |

### 6.2 Contract Status Lifecycle (FR-200)

| ID | Requirement | Priority |
|----|------------|----------|
| FR-201 | Enforce state machine transitions between statuses | Must Have |
| FR-202 | Block execution if pending approvals exist | Must Have |
| FR-203 | Log all status changes in activity trail | Must Have |
| FR-204 | Trigger notifications on status changes | Must Have |
| FR-205 | Execute workflow rules on status transitions | Should Have |

**State Machine:**

```
                    ┌──────────┐
           ┌───────│  DRAFT   │◄──────────────┐
           │       └──┬───┬───┘               │
           │          │   │                    │
           ▼          ▼   ▼                    │
     ┌─────────┐  ┌───────────┐         ┌──────────┐
     │ PENDING │──│ IN_REVIEW │────────►│ REJECTED │
     └────┬────┘  └─────┬─────┘         └──────────┘
          │             │                      ▲
          │             ▼                      │
          │       ┌──────────┐                 │
          └──────►│ EXECUTED │─────────────────┘
                  └──────────┘
```

**Valid Transitions:**
- Draft → Pending, In Review
- Pending → In Review, Rejected, Draft
- In Review → Executed, Rejected, Pending
- Executed → Rejected
- Rejected → Draft

### 6.3 AI-Powered Features (FR-300)

| ID | Requirement | Priority |
|----|------------|----------|
| FR-301 | AI contract review with risk scoring (1-10 scale) | Must Have |
| FR-302 | Identify key clauses, obligations, and deadlines from content | Must Have |
| FR-303 | Generate improvement recommendations | Should Have |
| FR-304 | RAG-based chatbot with contract-scoped queries | Must Have |
| FR-305 | Hybrid search (semantic + keyword) across contract chunks | Must Have |
| FR-306 | Auto-embed contract content into vector store on creation | Should Have |
| FR-307 | Contract comparison with AI-generated analysis | Should Have |
| FR-308 | Parse uploaded PDF content into structured contract data | Should Have |

### 6.4 Templates & Clause Library (FR-400)

| ID | Requirement | Priority |
|----|------------|----------|
| FR-401 | Create, edit, delete contract templates | Must Have |
| FR-402 | Templates include name, category, type, description, content | Must Have |
| FR-403 | Create contracts from templates (pre-filled content) | Must Have |
| FR-404 | Maintain reusable clause library | Should Have |
| FR-405 | Track clause usage count | Nice to Have |

### 6.5 Version Control & Redline (FR-500)

| ID | Requirement | Priority |
|----|------------|----------|
| FR-501 | Auto-save contract versions on every edit | Must Have |
| FR-502 | View version history with timestamps and authors | Must Have |
| FR-503 | Restore any previous version | Must Have |
| FR-504 | Word-level diff (redline) between versions | Must Have |
| FR-505 | Line-level unified diff view | Should Have |
| FR-506 | Visual markup: red strikethrough (deletions), green highlight (additions) | Must Have |

### 6.6 Approvals (FR-600)

| ID | Requirement | Priority |
|----|------------|----------|
| FR-601 | Request approval from named approvers | Must Have |
| FR-602 | Approve or reject with comments | Must Have |
| FR-603 | Block contract execution until all approvals complete | Must Have |
| FR-604 | Track approval history with timestamps | Must Have |
| FR-605 | Notify approvers via email and in-app notification | Should Have |

### 6.7 Collaboration (FR-700)

| ID | Requirement | Priority |
|----|------------|----------|
| FR-701 | Add collaborators to contracts with roles (viewer/editor/reviewer) | Must Have |
| FR-702 | Change collaborator roles inline | Must Have |
| FR-703 | Remove collaborators from contracts | Must Have |
| FR-704 | Add threaded comments on contracts | Must Have |
| FR-705 | Track all collaboration activity in audit trail | Must Have |

### 6.8 Obligations & Compliance (FR-800)

| ID | Requirement | Priority |
|----|------------|----------|
| FR-801 | Create obligations with title, description, deadline, assignee | Must Have |
| FR-802 | Track obligation status (pending, in_progress, completed, overdue) | Must Have |
| FR-803 | Calendar view showing all deadlines and renewals | Should Have |
| FR-804 | Renewal tracking with auto-identification | Should Have |

### 6.9 E-Signature (FR-900)

| ID | Requirement | Priority |
|----|------------|----------|
| FR-901 | Internal signature capture (name + timestamp) | Must Have |
| FR-902 | External e-signature via Leegality integration | Should Have |
| FR-903 | Webhook-based signature status updates | Should Have |
| FR-904 | HMAC verification for webhook security | Must Have |

### 6.10 Notifications & Email (FR-1000)

| ID | Requirement | Priority |
|----|------------|----------|
| FR-1001 | In-app notification center with unread count | Must Have |
| FR-1002 | Mark notifications as read / clear all | Must Have |
| FR-1003 | Email notifications via Resend API | Should Have |
| FR-1004 | Per-user email preferences (enable/disable by event type) | Should Have |
| FR-1005 | Event types: status change, approval, comment, expiry, workflow | Must Have |

### 6.11 Workflows & Automation (FR-1100)

| ID | Requirement | Priority |
|----|------------|----------|
| FR-1101 | Create workflow rules with trigger events and conditions | Should Have |
| FR-1102 | Supported triggers: status_change, contract_created | Should Have |
| FR-1103 | Actions: add_tag, auto_approve, change_status, create_obligation, notify_webhook | Should Have |
| FR-1104 | Conditional execution based on status, value, contract type | Should Have |
| FR-1105 | Workflow execution log with details | Should Have |
| FR-1106 | Priority-based rule ordering | Nice to Have |

### 6.12 Reporting & Analytics (FR-1200)

| ID | Requirement | Priority |
|----|------------|----------|
| FR-1201 | Dashboard with contract counts by status | Must Have |
| FR-1202 | Total and average contract value metrics | Must Have |
| FR-1203 | Status distribution charts | Should Have |
| FR-1204 | Type breakdown (client vs vendor) | Should Have |
| FR-1205 | Department-wise contract distribution | Should Have |
| FR-1206 | Monthly contract creation trends | Should Have |
| FR-1207 | Upcoming renewal alerts | Should Have |
| FR-1208 | Counterparty history and analytics | Should Have |

### 6.13 Kanban Board (FR-1300)

| ID | Requirement | Priority |
|----|------------|----------|
| FR-1301 | Visual kanban board with columns per contract status | Should Have |
| FR-1302 | Contract cards showing key metadata | Should Have |
| FR-1303 | Click-through to contract detail from kanban card | Should Have |

### 6.14 Audit Trail (FR-1400)

| ID | Requirement | Priority |
|----|------------|----------|
| FR-1401 | Log every contract action with user, timestamp, details | Must Have |
| FR-1402 | System-wide audit log with filtering | Must Have |
| FR-1403 | Per-contract audit trail page with summary cards | Must Have |
| FR-1404 | Color-coded timeline with action-type icons | Should Have |
| FR-1405 | Export audit trail data | Should Have |

### 6.15 Tags & Custom Fields (FR-1500)

| ID | Requirement | Priority |
|----|------------|----------|
| FR-1501 | Add/remove tags with custom colors per contract | Should Have |
| FR-1502 | Tag presets for quick application | Nice to Have |
| FR-1503 | Define custom fields (text, number, date, select types) | Should Have |
| FR-1504 | Set custom field values per contract | Should Have |

### 6.16 Data Management (FR-1600)

| ID | Requirement | Priority |
|----|------------|----------|
| FR-1601 | Full system backup (JSON export of all tables) | Must Have |
| FR-1602 | System restore from backup file | Must Have |
| FR-1603 | Bulk CSV import with validation and preview | Should Have |
| FR-1604 | CSV export with configurable fields | Should Have |

---

## 7. Non-Functional Requirements

### 7.1 Performance

| ID | Requirement | Target |
|----|------------|--------|
| NFR-01 | API response time (non-AI endpoints) | < 500ms (p95) |
| NFR-02 | AI review response time | < 60 seconds |
| NFR-03 | Chat response time (streaming) | First token < 3s |
| NFR-04 | Dashboard load time | < 2 seconds |
| NFR-05 | Contract list pagination | Default 50, max 200 per page |
| NFR-06 | Concurrent users | Support 50+ simultaneous users |

### 7.2 Security

| ID | Requirement |
|----|------------|
| NFR-07 | JWT-based authentication with HS256 signing |
| NFR-08 | Token expiry: 24-hour access tokens, 30-day refresh tokens |
| NFR-09 | Token revocation on logout |
| NFR-10 | Input sanitization on all user-provided fields |
| NFR-11 | Email validation for user and collaborator fields |
| NFR-12 | HMAC verification for webhook payloads |
| NFR-13 | Row-level security (RLS) on all database tables |
| NFR-14 | CORS headers configured for allowed origins |
| NFR-15 | API keys stored as environment variables, never in code |
| NFR-16 | Rate limiting on authentication endpoints |

### 7.3 Reliability & Availability

| ID | Requirement |
|----|------------|
| NFR-17 | Health check endpoint for monitoring |
| NFR-18 | Retry logic on OpenAI and email API calls (2 retries) |
| NFR-19 | Graceful degradation when external APIs unavailable |
| NFR-20 | Database connection validation on startup |

### 7.4 Scalability

| ID | Requirement |
|----|------------|
| NFR-21 | Serverless deployment (auto-scales with Vercel) |
| NFR-22 | Database indexes on frequently queried columns |
| NFR-23 | Chunked embedding processing (batches of 20) |
| NFR-24 | Email recipient cap (max 10 per event) |

### 7.5 Maintainability

| ID | Requirement |
|----|------------|
| NFR-25 | Modular backend architecture (5 Python modules) |
| NFR-26 | Automated test suite (82+ test cases) |
| NFR-27 | Sequential database migrations |
| NFR-28 | Environment-based configuration |

---

## 8. Database Schema

### 8.1 Tables Overview

| # | Table | Purpose | Key Fields |
|---|-------|---------|------------|
| 1 | `contracts` | Core contract records | id, name, party, type, status, value, start_date, end_date, department, content |
| 2 | `contract_templates` | Reusable contract templates | id, name, category, type, description, content |
| 3 | `contract_versions` | Version history per contract | id, contract_id, version_number, content, changed_by |
| 4 | `contract_chunks` | RAG vector chunks | id, contract_id, chunk_index, chunk_text, section_title, embedding (vector) |
| 5 | `contract_tags` | Tags applied to contracts | id, contract_id, tag_name, tag_color, created_by |
| 6 | `tag_presets` | Predefined tag options | id, name, color |
| 7 | `contract_collaborators` | Per-contract team members | id, contract_id, user_email, user_name, role, added_by |
| 8 | `contract_activity` | Audit trail events | id, contract_id, action, user_name, details, created_at |
| 9 | `contract_comments` | Discussion threads | id, contract_id, user_name, comment, created_at |
| 10 | `contract_obligations` | Tracked obligations/deadlines | id, contract_id, title, description, deadline, assigned_to, status |
| 11 | `contract_approvals` | Approval workflow records | id, contract_id, approver_name, status, comments |
| 12 | `contract_signatures` | Signature records | id, contract_id, signer_name, signed_at |
| 13 | `clause_library` | Reusable clause bank | id, title, content, category, usage_count |
| 14 | `workflow_rules` | Automation rule definitions | id, name, trigger_event, trigger_condition, action_type, action_config, priority, is_active |
| 15 | `workflow_log` | Workflow execution history | id, rule_id, rule_name, contract_id, trigger_event, action_taken, details |
| 16 | `clm_users` | User accounts | id, name, email, role, is_active |
| 17 | `custom_field_defs` | Custom field definitions | id, field_name, field_type, options |
| 18 | `custom_field_values` | Custom field values per contract | id, contract_id, field_id, value |
| 19 | `email_preferences` | Per-user email settings | id, user_email, enabled, on_status_change, on_approval, on_comment, on_expiry, on_workflow |
| 20 | `notifications` | In-app notifications | id, title, message, type, contract_id, user_email, is_read, link |
| 21 | `webhook_configs` | External webhook URLs | id, url, event_type, active |
| 22 | `revoked_tokens` | Invalidated JWT tokens | id, token, revoked_at |

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

---

## 9. API Specification

### 9.1 API Summary

- **Base URL:** `https://contract-cli-six.vercel.app/api`
- **Authentication:** Bearer token (JWT) in `Authorization` header
- **Content Type:** `application/json`
- **Total Endpoints:** 99

### 9.2 Endpoint Groups

| Group | Endpoints | Auth Required | Min Role |
|-------|-----------|--------------|----------|
| **Auth** | 5 | Partial | — |
| **Contracts CRUD** | 8 | Yes | Editor (create/edit), Admin (delete) |
| **Contract Status** | 1 | Yes | Editor |
| **AI Review & Chat** | 3 | Yes | Viewer |
| **Comments** | 2 | Yes | Editor |
| **Obligations** | 3 | Yes | Editor |
| **Approvals** | 3 | Yes | Manager |
| **Collaborators** | 4 | Yes | Editor |
| **Signatures & E-Sign** | 3 | Yes | Editor |
| **Versions & Redline** | 5 | Yes | Editor |
| **Tags** | 5 | Yes | Editor |
| **Templates** | 5 | Yes | Editor (CRUD), Manager (delete) |
| **Clauses** | 4 | Yes | Editor |
| **Custom Fields** | 5 | Yes | Editor |
| **Workflows** | 5 | Yes | Manager |
| **Users** | 4 | Yes | Admin |
| **Webhooks** | 3 | Yes | Admin |
| **Notifications** | 3 | Yes | Viewer |
| **Email** | 4 | Yes | Viewer |
| **Reports & Analytics** | 1 | Yes | Viewer |
| **Calendar** | 1 | Yes | Manager |
| **Data Management** | 7 | Yes | Various |
| **Health** | 2 | No | — |

### 9.3 Key Endpoints Detail

#### Authentication
```
POST /api/auth/login          — Login with password, returns JWT
GET  /api/auth/verify         — Verify current token
POST /api/auth/refresh        — Refresh access token
POST /api/auth/logout         — Revoke current token
POST /api/auth/reset-password — Reset password (admin)
```

#### Contract Lifecycle
```
GET    /api/contracts              — List all contracts (paginated, filterable)
POST   /api/contracts              — Create new contract
GET    /api/contracts/<id>         — Get contract details
PUT    /api/contracts/<id>         — Update contract
DELETE /api/contracts/<id>         — Delete contract (admin only)
POST   /api/contracts/<id>/status  — Change status (enforced state machine)
POST   /api/contracts/<id>/clone   — Clone contract
POST   /api/contracts/<id>/review  — AI-powered review
```

#### AI & Intelligence
```
POST /api/chat                     — RAG chatbot (streaming response)
POST /api/contracts/compare        — AI contract comparison
POST /api/parse                    — Parse PDF into structured data
POST /api/contracts/<id>/embed     — Generate vector embeddings
```

---

## 10. Integration Points

### 10.1 OpenAI (AI Engine)

| Aspect | Detail |
|--------|--------|
| **Purpose** | Contract review, risk analysis, chatbot, embeddings |
| **Models** | GPT-4o (chat), text-embedding-3-small (embeddings) |
| **API** | `https://api.openai.com/v1` |
| **Features** | Streaming responses, retry logic, batch embedding |
| **Config** | `OPENAI_API_KEY` environment variable |

### 10.2 Supabase (Database)

| Aspect | Detail |
|--------|--------|
| **Purpose** | Primary data store, vector search, RLS |
| **Type** | PostgreSQL with pgvector extension |
| **Features** | Row-level security, real-time subscriptions, RPC functions |
| **Config** | `SUPABASE_URL`, `SUPABASE_KEY` environment variables |
| **RPC** | `match_chunks` — cosine similarity vector search |

### 10.3 Resend (Email)

| Aspect | Detail |
|--------|--------|
| **Purpose** | Transactional email notifications |
| **API** | `https://api.resend.com/emails` |
| **Features** | HTML email templates, per-user preferences, retry logic |
| **Config** | `RESEND_API_KEY`, `EMAIL_FROM` environment variables |
| **Limit** | Max 10 recipients per event |

### 10.4 Leegality (E-Signature)

| Aspect | Detail |
|--------|--------|
| **Purpose** | Legally binding digital signatures |
| **Features** | Document signing, webhook status updates, HMAC verification |
| **Config** | `LEEGALITY_API_KEY` environment variable |
| **Webhook** | `POST /api/leegality/webhook` (MAC-verified) |

---

## 11. Security Requirements

### 11.1 Authentication

- JWT tokens with HS256 signing using `APP_SECRET`
- Access token validity: 24 hours
- Refresh token validity: 30 days
- Token revocation stored in `revoked_tokens` table
- Password-based login with configurable `APP_PASSWORD`

### 11.2 Authorization

- 4-tier RBAC: viewer → editor → manager → admin
- Role enforcement via `@role_required()` decorator on all protected endpoints
- Per-contract collaborator roles for granular access

### 11.3 Input Security

- `_sanitize()` function strips dangerous characters from all user inputs
- `_valid_email()` validates email format
- Parameterized queries via Supabase client (no raw SQL injection)
- Content length validation on contract fields

### 11.4 API Security

- CORS configuration for allowed origins
- HMAC signature verification on webhook payloads
- API keys stored as environment variables
- Health endpoint for monitoring without auth

### 11.5 Data Security

- Row-Level Security (RLS) enabled on all Supabase tables
- Database access only via Supabase client with anon key
- Backup/restore restricted to admin role
- Audit trail is append-only (no deletion of activity records)

---

## 12. Deployment & Infrastructure

### 12.1 Deployment Pipeline

```
Development → Staging → Production
    │            │           │
    │   deploy-staging.sh    │
    │   (tests first)        │
    │                  deploy-production.sh
    │                  (tests + confirmation)
    └── Local development with Flask dev server
```

### 12.2 Environment Configuration

| Environment | Variable | Description |
|-------------|----------|-------------|
| All | `SUPABASE_URL` | Supabase project URL |
| All | `SUPABASE_KEY` | Supabase anonymous key |
| All | `OPENAI_API_KEY` | OpenAI API key |
| All | `APP_SECRET` | JWT/HMAC signing secret |
| All | `APP_PASSWORD` | Admin login password |
| Optional | `RESEND_API_KEY` | Email notification API key |
| Optional | `LEEGALITY_API_KEY` | E-signature API key |
| Optional | `EMAIL_FROM` | Sender email address |

### 12.3 Testing

- **Framework:** pytest
- **Test Count:** 82+ test cases
- **Coverage:** API endpoints, auth, validation, state machine
- **Run:** `python -m pytest tests/ -v`

### 12.4 Frontend Pages

| # | Page | Description |
|---|------|-------------|
| 1 | Dashboard | Overview metrics, status cards, quick actions |
| 2 | Contracts | Searchable/filterable table with audit buttons |
| 3 | Create Contract | Full-page form for new contracts |
| 4 | Templates | Template library with CRUD |
| 5 | Template Form | Full-page template editor |
| 6 | Clauses | Reusable clause library |
| 7 | AI Chat | RAG-powered contract Q&A |
| 8 | Users | User management (admin) |
| 9 | Webhooks | Webhook configuration (admin) |
| 10 | Email Settings | Per-user notification preferences |
| 11 | Compare | Side-by-side contract comparison |
| 12 | Calendar | Deadline and renewal calendar |
| 13 | Workflows | Automation rule builder |
| 14 | Reports | Analytics and charts |
| 15 | Kanban | Visual pipeline board |
| 16 | Counterparty | Party-specific contract history |
| 17 | Renewals | Upcoming renewal tracker |
| 18 | Audit Log | System-wide audit trail |
| 19 | Contract Audit | Per-contract detailed audit page |

---

## 13. Glossary

| Term | Definition |
|------|-----------|
| **CLM** | Contract Lifecycle Management |
| **EMB** | Expand My Business (brand name) |
| **RAG** | Retrieval-Augmented Generation — AI technique combining search with LLM |
| **RLS** | Row-Level Security — database-level access control |
| **RBAC** | Role-Based Access Control |
| **SPA** | Single Page Application |
| **JWT** | JSON Web Token — authentication standard |
| **HMAC** | Hash-based Message Authentication Code |
| **pgvector** | PostgreSQL extension for vector similarity search |
| **Redline** | Track changes showing additions and deletions between versions |
| **Obligation** | Contractual duty with a deadline and assignee |
| **Counterparty** | The other party in a contract |

---

*Document generated for EMB CLM — Contract Lifecycle Management Platform*
*Version 1.0 | April 2026*
