"""Auth, RBAC, sanitization, and password helpers for CLM API."""

import os, sys, re, time, hmac, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bcrypt
from functools import wraps
from flask import request, jsonify

from config import (
    SECRET, PASSWORD, sb, log,
    _check_rate_limit, _check_origin, _revoked_tokens,
    ROLE_HIERARCHY,
)
from constants import TOKEN_EXPIRY_SECONDS

# ─── Error Helper ────────────────────────────────────────────────────────
def err(message, code, details=None):
    """Standard error response"""
    body = {"error": {"message": message, "code": code}}
    if details: body["error"]["details"] = details
    return jsonify(body), code

# ─── Password Helpers ────────────────────────────────────────────────────
def _hash_password(password):
    """Hash password with bcrypt"""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def _verify_password(password, stored_hash):
    """Verify password against bcrypt or legacy SHA-256 hash, returns (valid, needs_upgrade)"""
    if stored_hash.startswith("$2b$") or stored_hash.startswith("$2a$"):
        return bcrypt.checkpw(password.encode(), stored_hash.encode()), False
    # Legacy SHA-256 fallback
    legacy = hashlib.sha256(password.encode()).hexdigest()
    if hmac.compare_digest(legacy, stored_hash):
        return True, True  # valid but needs upgrade to bcrypt
    return False, False

# ─── Email Validation ────────────────────────────────────────────────────
_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
_FIELD_MAX = {"email": 254, "name": 500, "url": 2000}

def _valid_email(email):
    """Validate email format using regex"""
    if not email or not isinstance(email, str): return False
    return bool(_EMAIL_RE.match(email.strip())) and len(email.strip()) <= 254

# ─── Input Sanitization ─────────────────────────────────────────────────
def _sanitize(text, max_len=10000, field_type=None):
    """Strip dangerous HTML/script tags from user input"""
    if not text: return text
    if not isinstance(text, str): return text
    if field_type and field_type in _FIELD_MAX: max_len = min(max_len, _FIELD_MAX[field_type])
    text = text[:max_len]
    # Remove script tags and event handlers
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'\bon\w+\s*=', '', text, flags=re.IGNORECASE)
    text = re.sub(r'javascript\s*:', '', text, flags=re.IGNORECASE)
    return text

def _sanitize_html(text, max_len=10000):
    """Remove ALL HTML tags -- use for plain text fields"""
    if not text: return text
    if not isinstance(text, str): return text
    text = text[:max_len]
    return re.sub(r'<[^>]+>', '', text).strip()

# Fields that allow large content (contract text, HTML content, etc.)
_LARGE_FIELDS = {"content", "content_html", "description", "template_content"}

def _sanitize_dict(d, fields=None):
    """Sanitize all string values in a dict"""
    if not d: return d
    out = {}
    for k, v in d.items():
        if fields and k not in fields:
            out[k] = v
        elif isinstance(v, str):
            max_len = 500000 if k in _LARGE_FIELDS else 10000
            out[k] = _sanitize(v, max_len=max_len)
        else:
            out[k] = v
    return out

# ─── Token Auth ──────────────────────────────────────────────────────────
def _hmac_sign(payload): return hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()

def make_token(email=""):
    ts = str(int(time.time()))
    payload = f"{email}:{ts}" if email else ts
    return f"{payload}:{_hmac_sign(payload)}"

def check_token(t):
    """Returns (valid, email)"""
    try:
        parts = t.rsplit(":", 1)
        if len(parts) != 2: return False, ""
        payload, sig = parts
        if not hmac.compare_digest(sig, _hmac_sign(payload)): return False, ""
        if sig in _revoked_tokens: return False, ""
        # Extract timestamp -- payload is either "ts" or "email:ts"
        segments = payload.split(":")
        ts = int(segments[-1])
        if time.time() - ts > TOKEN_EXPIRY_SECONDS: return False, ""
        email = segments[0] if len(segments) > 1 else ""
        return True, email
    except Exception as e:
        log.debug(f"Token validation failed: {e}")
        return False, ""

# ─── Decorators ──────────────────────────────────────────────────────────
def auth(f):
    @wraps(f)
    def w(*a, **k):
        # Rate limit check
        if not _check_rate_limit():
            return err("Rate limit exceeded. Try again later.", 429)
        if not _check_origin():
            return err("Invalid request origin", 403)
        if not PASSWORD:
            request.user_email = ""
            request.user_role = "admin"
            return f(*a, **k)
        h = request.headers.get("Authorization", "")
        if not h.startswith("Bearer "):
            return err("Auth required", 401)
        valid, email = check_token(h[7:])
        if not valid:
            return err("Auth required", 401)
        # Look up user role
        request.user_email = email
        request.user_role = "admin"  # default for password-only login
        if email and sb:
            try:
                u = sb.table("clm_users").select("role,name,is_active").eq("email", email).execute()
                if u.data:
                    if not u.data[0].get("is_active", True):
                        return err("Account deactivated", 403)
                    request.user_role = u.data[0].get("role", "viewer")
                    request.user_name = u.data[0].get("name", email)
            except Exception as e: log.warning(f"User lookup failed for {email}: {e}")
        return f(*a, **k)
    return w

def role_required(min_role):
    """Decorator to enforce minimum role level"""
    def decorator(f):
        @wraps(f)
        def w(*a, **k):
            user_level = ROLE_HIERARCHY.get(getattr(request, 'user_role', 'viewer'), 0)
            required_level = ROLE_HIERARCHY.get(min_role, 0)
            if user_level < required_level:
                return err(f"Requires {min_role} role or higher", 403)
            return f(*a, **k)
        return w
    return decorator

def need_db(f):
    @wraps(f)
    def w(*a, **k):
        if not sb: return err("DB not configured", 503)
        return f(*a, **k)
    return w
