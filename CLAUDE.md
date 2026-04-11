# EMB CLM -- Contract Lifecycle Management

## Architecture
- **Backend**: Python Flask API on Vercel serverless (`api/index.py` + modules)
- **Frontend**: Single-file HTML/CSS/JS (`public/index.html`)
- **Database**: Supabase PostgreSQL with pgvector
- **AI**: OpenAI GPT-4o for contract review, RAG chat
- **Email**: Resend API for notifications

## Development

### Setup
```bash
pip install -r api/requirements.txt
```

### Running Tests
```bash
python -m pytest tests/ -v
```

### Deploy
```bash
# Staging (runs tests first)
./scripts/deploy-staging.sh

# Production (runs tests + confirms)
./scripts/deploy-production.sh
```

### Environment Variables (Vercel)
- `SUPABASE_URL` -- Supabase project URL
- `SUPABASE_KEY` -- Supabase anon key
- `OPENAI_API_KEY` -- OpenAI API key
- `APP_SECRET` -- HMAC signing secret
- `APP_PASSWORD` -- Admin login password
- `RESEND_API_KEY` -- Resend email API key
- `LEEGALITY_API_KEY` -- Leegality e-sign key (optional)

### Database Migrations
Run in order in Supabase SQL Editor:
1. `migration_v2.sql` -- Core schema
2. `migration_v3_users.sql` -- User management
3. `migration_v4_features.sql` -- Extended features
4. `migration_v5_tags_workflow.sql` -- Tags & workflows
5. `migration_v6_customfields_notifications.sql` -- Custom fields
6. `migration_v7_email_preferences.sql` -- Email preferences
7. `migration_v8_production.sql` -- Production constraints & indexes

### API Module Structure
- `api/config.py` -- Configuration, DB init, shared state
- `api/auth.py` -- Authentication, RBAC, input sanitization
- `api/ai.py` -- OpenAI, RAG, embeddings
- `api/helpers.py` -- Activity logging, webhooks, notifications, workflows
- `api/index.py` -- Route definitions (entry point)

### Key Patterns
- `auth` decorator -- Validates Bearer token, sets `request.user_email` and `request.user_role`
- `role_required(min_role)` -- Enforces RBAC (viewer < editor < manager < admin)
- `need_db` -- Returns 503 if Supabase not configured
- `_transition_status()` -- Enforces contract state machine
- `tc()` (frontend) -- Title Case helper for display text
- `apiFetch()` (frontend) -- API wrapper with error handling
