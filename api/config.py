"""Configuration, DB initialization, and shared state for CLM API."""

import os, re, time, logging
from flask import request
from supabase import create_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("clm")

# ─── Config ──────────────────────────────────────────────────────────────
SB_URL = os.environ.get("SUPABASE_URL", "")
SB_KEY = os.environ.get("SUPABASE_KEY", "")
OAI_URL = "https://api.openai.com/v1"
EMB_MODEL = "text-embedding-3-small"
SECRET = os.environ.get("APP_SECRET", "dev-secret")
PASSWORD = os.environ.get("APP_PASSWORD", "")
CHUNK_SZ, CHUNK_OV = 1500, 200
LEEGALITY_KEY = os.environ.get("LEEGALITY_API_KEY", "")
LEEGALITY_SALT = os.environ.get("LEEGALITY_PRIVATE_SALT", "")
LEEGALITY_URL = os.environ.get("LEEGALITY_BASE_URL", "https://app.leegality.com")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "").strip()
EMAIL_FROM = os.environ.get("EMAIL_FROM", "EMB CLM <onboarding@resend.dev>")

sb = create_client(SB_URL, SB_KEY) if SB_URL and SB_KEY else None

# ─── Rate Limiter ────────────────────────────────────────────────────────
_rate_store = {}  # {ip: [(timestamp, ...)] }
RATE_LIMIT = int(os.environ.get("RATE_LIMIT", "120"))  # requests per minute
RATE_WINDOW = 60  # seconds

MAX_RATE_STORE_IPS = 10000  # Prevent unbounded memory growth

def _check_rate_limit():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
    now = time.time()
    # Evict stale IPs if store grows too large
    if len(_rate_store) > MAX_RATE_STORE_IPS:
        stale = [k for k, v in _rate_store.items() if not v or now - v[-1] > RATE_WINDOW]
        for k in stale:
            del _rate_store[k]
    if ip not in _rate_store:
        _rate_store[ip] = []
    _rate_store[ip] = [t for t in _rate_store[ip] if now - t < RATE_WINDOW]
    if len(_rate_store[ip]) >= RATE_LIMIT:
        return False
    _rate_store[ip].append(now)
    return True

# ─── Revoked Tokens ──────────────────────────────────────────────────────
_revoked_tokens = set()

# ─── Dashboard Cache ─────────────────────────────────────────────────────
_dashboard_cache = {"data": None, "ts": 0}

# ─── CORS / Origin Check ────────────────────────────────────────────────
ALLOWED_ORIGINS = {
    "https://contract-cli-six.vercel.app",
    "http://localhost:3000",
    "http://localhost:5000",
}

def _check_origin():
    """Verify request origin for mutating requests"""
    if request.method in ("GET", "HEAD", "OPTIONS"): return True
    origin = request.headers.get("Origin", "")
    referer = request.headers.get("Referer", "")
    # Allow if no origin header (server-to-server, curl, etc.)
    if not origin and not referer: return True
    # Check origin
    if origin and origin in ALLOWED_ORIGINS: return True
    # Check referer starts with allowed origin
    for ao in ALLOWED_ORIGINS:
        if referer.startswith(ao): return True
    return False

# ─── RBAC ────────────────────────────────────────────────────────────────
ROLE_HIERARCHY = {"viewer": 0, "editor": 1, "manager": 2, "admin": 3}

# ─── Status Transitions ─────────────────────────────────────────────────
VALID_TRANSITIONS = {
    "draft": {"pending", "in_review"},
    "pending": {"in_review", "rejected", "draft"},
    "in_review": {"executed", "rejected", "pending"},
    "executed": {"rejected"},
    "rejected": {"draft"},
}

# ─── Backup Tables ──────────────────────────────────────────────────────
BACKUP_TABLES = [
    "contracts", "contract_versions", "contract_chunks", "contract_tags",
    "contract_comments", "contract_approvals", "contract_obligations",
    "contract_signatures", "contract_collaborators", "contract_parties",
    "contract_links", "contract_activity", "contract_templates",
    "clause_library", "tag_presets", "share_links", "invoices",
    "clm_users", "workflow_rules", "workflow_log",
    "custom_field_defs", "custom_field_values",
    "notifications", "webhook_configs", "email_preferences",
    "chat_sessions", "chat_feedback",
]
