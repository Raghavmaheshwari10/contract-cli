"""Named constants for the CLM API.

Centralises magic numbers, thresholds, and repeated literals so every module
can import a readable name instead of a bare number.
"""

# ─── Request / Upload Limits ────────────────────────────────────────────
MAX_REQUEST_SIZE = 16 * 1024 * 1024       # 16 MB — Flask MAX_CONTENT_LENGTH
MAX_PDF_SIZE = 50 * 1024 * 1024           # 50 MB per uploaded PDF
MAX_BULK_PDFS = 10                        # max PDFs in a single bulk upload
MAX_CONTRACTS_PER_BATCH = 50              # bulk action batch ceiling
MAX_EMAIL_RECIPIENTS = 10                 # cap recipients per notification event
MAX_OCR_PAGES = 50                        # max pages to OCR from a scanned PDF

# ─── Token / Auth ───────────────────────────────────────────────────────
TOKEN_EXPIRY_SECONDS = 86400              # 24 hours

# ─── Date Format ────────────────────────────────────────────────────────
DATE_FMT = "%Y-%m-%d"                     # ISO date used throughout the app

# ─── Contract Expiry Thresholds (days) ──────────────────────────────────
EXPIRY_CRITICAL_DAYS = 30                 # red — needs immediate action
EXPIRY_WARNING_DAYS = 60                  # amber — renew soon
EXPIRY_SAFE_DAYS = 90                     # green — on radar

# ─── OpenAI ─────────────────────────────────────────────────────────────
OPENAI_TIMEOUT = 55                       # seconds per API call
OPENAI_STREAM_TIMEOUT = 120              # seconds for streaming responses
OPENAI_RETRIES = 2                        # retry count on transient failures
CONTEXT_TOKEN_LIMIT = 120000             # approx char budget for RAG context
EMBEDDING_BATCH_SIZE = 20                 # chunks per embedding API call

# ─── UI / Defaults ──────────────────────────────────────────────────────
DEFAULT_TAG_COLOR = "#2563eb"             # blue — default tag/workflow colour
DASHBOARD_CACHE_TTL = 60                  # seconds before dashboard re-fetches
MONTHLY_TREND_MONTHS = 12                 # last N months shown in trend chart

# ─── Audit Log ──────────────────────────────────────────────────────────
MIN_RETENTION_DAYS = 30                   # minimum days to keep audit records

# ─── Workflow Engine ────────────────────────────────────────────────────
VALID_WORKFLOW_TRIGGERS = [
    "status_change", "contract_created", "approval_completed",
    "obligation_overdue", "contract_expiring",
]
VALID_WORKFLOW_ACTIONS = [
    "add_tag", "change_status", "auto_approve",
    "create_obligation", "notify_webhook",
]
VALID_STATUSES = ["draft", "pending", "in_review", "executed", "rejected"]

# ─── Contract Field Validation ──────────────────────────────────────────
VALID_CONTRACT_TYPES = {"client", "vendor"}
VALID_COLLABORATOR_ROLES = {"viewer", "editor", "reviewer"}
VALID_PARTY_TYPES = {"client", "vendor", "subcontractor"}
VALID_SHARE_PERMISSIONS = {"view", "comment"}

# ─── Notification Type → Colour ─────────────────────────────────────────
NOTIFICATION_COLORS = {
    "info": "#2563eb", "approval": "#ea580c", "comment": "#0891b2",
    "expiry": "#dc2626", "success": "#059669", "workflow": "#7c3aed",
}

# ─── Search Stopwords (excluded from keyword search) ────────────────────
SEARCH_STOPWORDS = frozenset({
    "what", "which", "when", "where", "this", "that", "with", "from",
    "have", "does", "about", "many", "much", "there", "their", "they",
    "been", "will", "would", "could", "should", "contract", "agreement",
    "mentioned", "provide",
})
