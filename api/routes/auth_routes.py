"""Auth routes: login, verify, refresh, logout, reset-password, config."""

import hmac
from datetime import datetime
from flask import Blueprint, request, jsonify

from config import sb, log, SB_URL, SB_KEY, SECRET, PASSWORD, _revoked_tokens
from auth import (
    err, _hash_password, _verify_password,
    _valid_email, _sanitize,
    make_token, check_token,
    auth, role_required, need_db,
)

bp = Blueprint("auth", __name__)


@bp.route("/api/auth/login", methods=["POST"])
def login():
    d = request.json or {}
    email = d.get("email", "").strip().lower()
    password = d.get("password", "")
    if email and not _valid_email(email):
        return err("Invalid email format", 400)
    # Multi-user login: try user table first
    if sb and email:
        try:
            u = sb.table("clm_users").select("*").eq("email", email).eq("is_active", True).execute()
            if u.data:
                stored = u.data[0].get("password_hash", "")
                valid, needs_upgrade = _verify_password(password, stored)
                if valid:
                    user = u.data[0]
                    upd = {"last_login": datetime.now().isoformat()}
                    if needs_upgrade:
                        upd["password_hash"] = _hash_password(password)
                    sb.table("clm_users").update(upd).eq("id", user["id"]).execute()
                    return jsonify({"token": make_token(email), "user": {"id": user["id"], "name": user["name"], "email": user["email"], "role": user["role"], "department": user.get("department", "")}})
                else:
                    return err("Invalid password", 401)
        except Exception as e:
            log.error(f"Login error: {e}")
    # Fallback: simple password auth (admin)
    if not PASSWORD:
        return err("APP_PASSWORD not configured. Set it in environment variables.", 503)
    if not hmac.compare_digest(password, PASSWORD):
        return err("Invalid password", 401)
    return jsonify({"token": make_token("raghav.maheshwari@emb.global"), "user": {"name": "Raghav Maheshwari", "email": "raghav.maheshwari@emb.global", "role": "admin"}})


@bp.route("/api/auth/verify")
def verify():
    if not PASSWORD:
        return jsonify({"valid": False, "auth_enabled": True, "error": "APP_PASSWORD not configured"}), 503
    h = request.headers.get("Authorization", "")
    if h.startswith("Bearer "):
        valid, email = check_token(h[7:])
        if valid:
            return jsonify({"valid": True, "auth_enabled": True})
    return jsonify({"valid": False, "auth_enabled": True}), 401


@bp.route("/api/auth/refresh", methods=["POST"])
def refresh_token():
    h = request.headers.get("Authorization", "")
    if not h.startswith("Bearer "):
        return err("Auth required", 401)
    valid, email = check_token(h[7:])
    if not valid:
        return err("Invalid or expired token", 401)
    return jsonify({"token": make_token(email)})


@bp.route("/api/auth/logout", methods=["POST"])
def logout():
    h = request.headers.get("Authorization", "")
    if h.startswith("Bearer "):
        token = h[7:]
        sig = token.rsplit(":", 1)[-1] if ":" in token else token
        _revoked_tokens.add(sig)
    return jsonify({"message": "Logged out"})


@bp.route("/api/config")
@auth
def config():
    return jsonify({"supabase_url": SB_URL, "supabase_anon_key": SB_KEY})


@bp.route("/api/auth/reset-password", methods=["POST"])
@auth
@role_required("admin")
@need_db
def reset_password():
    d = request.json or {}
    email = d.get("email", "").strip().lower()
    new_password = d.get("new_password", "")

    if not email or not new_password:
        return err("Email and new password required", 400)
    if len(new_password) < 6:
        return err("Password must be at least 6 characters", 400)

    u = sb.table("clm_users").select("id,email,name").eq("email", email).execute()
    if not u.data:
        return err("User not found", 404)

    pw_hash = _hash_password(new_password)
    sb.table("clm_users").update({"password_hash": pw_hash, "updated_at": datetime.now().isoformat()}).eq("email", email).execute()
    return jsonify({"message": f"Password reset for {u.data[0]['name']}"})
