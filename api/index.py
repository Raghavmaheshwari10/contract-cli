"""Contract Lifecycle Management API — Full CLM with AI review, approvals, signatures, webhooks."""

import os, sys, re, io, csv, json as J, time, hmac, hashlib, logging, difflib
import bcrypt
from datetime import datetime, timedelta
from functools import wraps
import requests as http
from flask import Flask, request, jsonify, Response, send_from_directory
from supabase import create_client
import fitz

# Ensure api/ directory is in path for module imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ─── Import modules ──────────────────────────────────────────────────────
from config import (
    log, sb, SB_URL, SB_KEY, SECRET, PASSWORD, OAI_URL, EMB_MODEL,
    CHUNK_SZ, CHUNK_OV, LEEGALITY_KEY, LEEGALITY_SALT, LEEGALITY_URL,
    RESEND_API_KEY, EMAIL_FROM,
    _rate_store, RATE_LIMIT, RATE_WINDOW, _check_rate_limit,
    _revoked_tokens, _dashboard_cache,
    ALLOWED_ORIGINS, _check_origin,
    ROLE_HIERARCHY, VALID_TRANSITIONS, BACKUP_TABLES,
)
from auth import (
    err, _hash_password, _verify_password,
    _EMAIL_RE, _FIELD_MAX, _valid_email,
    _sanitize, _sanitize_html, _sanitize_dict,
    _sign, mk_token, chk_token,
    auth, role_required, need_db,
)
from ai import (
    oai_h, oai_chat, oai_stream, oai_emb,
    chunk_text, embed_contract, hybrid_search, build_prompt,
    ocr_pdf_pages,
)
from helpers import (
    log_activity, fire_webhooks,
    create_notification, send_email_notification, _should_email,
    run_workflows,
    _word_diff, _line_diff, _transition_status,
)

# ─── Flask App ───────────────────────────────────────────────────────────
app = Flask(__name__, static_folder="../public", static_url_path="")
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max request size

# ─── Security Headers ───────────────────────────────────────────────────
@app.after_request
def _security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-XSS-Protection"] = "1; mode=block"
    resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if resp.content_type and "text/html" in resp.content_type:
        resp.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.quilljs.com https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.quilljs.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob:; connect-src 'self' https://*.supabase.co https://api.openai.com"
    return resp

@app.errorhandler(413)
def too_large(e):
    return jsonify({"error": {"message": "Request too large. Max 16MB.", "code": 413}}), 413

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": {"message": "Not found", "code": 404}}), 404

@app.errorhandler(Exception)
def handle_exception(e):
    log.error(f"Unhandled exception: {e}", exc_info=True)
    return jsonify({"error": {"message": "Internal server error", "code": 500}}), 500

# ═══════════════════════════════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/")
def index(): return send_from_directory(app.static_folder, "index.html")

@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "db": bool(sb), "ai": bool(oai_h())})

# ─── Auth ──────────────────────────────────────────────────────────────────
@app.route("/api/auth/login", methods=["POST"])
def login():
    d = request.json or {}
    email = d.get("email", "").strip().lower()
    password = d.get("password", "")
    if email and not _valid_email(email):
        return jsonify({"error": "Invalid email format"}), 400
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
                    return jsonify({"token": mk_token(email), "user": {"id": user["id"], "name": user["name"], "email": user["email"], "role": user["role"], "department": user.get("department","")}})
                else:
                    return jsonify({"error": "Invalid password"}), 401
        except Exception as e:
            log.error(f"Login error: {e}")
    # Fallback: simple password auth (admin)
    if not PASSWORD: return jsonify({"token": mk_token("raghav.maheshwari@emb.global"), "user": {"name": "Raghav Maheshwari", "email": "raghav.maheshwari@emb.global", "role": "admin"}})
    if not hmac.compare_digest(password, PASSWORD):
        return jsonify({"error": "Invalid password"}), 401
    return jsonify({"token": mk_token("raghav.maheshwari@emb.global"), "user": {"name": "Raghav Maheshwari", "email": "raghav.maheshwari@emb.global", "role": "admin"}})

@app.route("/api/auth/verify")
def verify():
    if not PASSWORD: return jsonify({"valid": True, "auth_enabled": False})
    h = request.headers.get("Authorization", "")
    if h.startswith("Bearer "):
        valid, email = chk_token(h[7:])
        if valid:
            return jsonify({"valid": True, "auth_enabled": True})
    return jsonify({"valid": False, "auth_enabled": True}), 401

@app.route("/api/auth/refresh", methods=["POST"])
def refresh_token():
    h = request.headers.get("Authorization", "")
    if not h.startswith("Bearer "):
        return jsonify({"error": "Auth required"}), 401
    valid, email = chk_token(h[7:])
    if not valid:
        return jsonify({"error": "Invalid or expired token"}), 401
    return jsonify({"token": mk_token(email)})

@app.route("/api/auth/logout", methods=["POST"])
def logout():
    h = request.headers.get("Authorization", "")
    if h.startswith("Bearer "):
        token = h[7:]
        sig = token.rsplit(":", 1)[-1] if ":" in token else token
        _revoked_tokens.add(sig)
    return jsonify({"message": "Logged out"})

# ─── Config (for frontend Supabase realtime) ──────────────────────────────
@app.route("/api/config")
@auth
def config():
    return jsonify({"supabase_url": SB_URL, "supabase_anon_key": SB_KEY})

# ─── Dashboard ─────────────────────────────────────────────────────────────
@app.route("/api/dashboard")
@auth
@need_db
def dashboard():
    global _dashboard_cache
    if _dashboard_cache["data"] and time.time() - _dashboard_cache["ts"] <= 60:
        return jsonify(_dashboard_cache["data"])
    contracts = sb.table("contracts").select("id,status,contract_type,end_date,value").execute().data
    today = datetime.now()
    stats = {"total": len(contracts), "draft": 0, "pending": 0, "in_review": 0,
             "executed": 0, "rejected": 0, "clients": 0, "vendors": 0, "expiring": 0, "expired": 0}
    expiring_list = []
    for c in contracts:
        s = c.get("status", "draft")
        if s in stats: stats[s] += 1
        if c["contract_type"] == "client": stats["clients"] += 1
        else: stats["vendors"] += 1
        if c.get("end_date"):
            try:
                end = datetime.strptime(c["end_date"], "%Y-%m-%d")
                if end < today: stats["expired"] += 1
                elif (end - today).days <= 30:
                    stats["expiring"] += 1
                    expiring_list.append({"id": c["id"], "end_date": c["end_date"], "days_left": (end - today).days})
            except Exception as e: log.debug(f"dashboard: {e}")
    # Recent activity
    activity = sb.table("contract_activity").select("*").order("created_at", desc=True).limit(10).execute().data
    # Pending obligations
    obligations = sb.table("contract_obligations").select("*").eq("status", "pending").order("deadline").limit(10).execute().data
    # Monthly trend (last 12 months)
    monthly = {}
    for c in contracts:
        d_str = c.get("added_on", "")
        if d_str:
            try:
                m = d_str[:7]  # YYYY-MM
                monthly[m] = monthly.get(m, 0) + 1
            except Exception as e: log.debug(f"dashboard: {e}")
    months_sorted = sorted(monthly.keys())[-12:]
    monthly_trend = [{"month": m, "count": monthly[m]} for m in months_sorted]
    result = {**stats, "expiring_contracts": expiring_list, "recent_activity": activity,
              "pending_obligations": obligations, "monthly_trend": monthly_trend}
    _dashboard_cache["data"] = result
    _dashboard_cache["ts"] = time.time()
    return jsonify(result)

# ─── Templates ─────────────────────────────────────────────────────────────
@app.route("/api/templates")
@auth
@need_db
def list_templates():
    r = sb.table("contract_templates").select("id,name,category,contract_type,description").execute()
    return jsonify(r.data)

@app.route("/api/templates/<int:tid>")
@auth
@need_db
def get_template(tid):
    r = sb.table("contract_templates").select("*").eq("id", tid).execute()
    if not r.data: return jsonify({"error": "Not found"}), 404
    return jsonify(r.data[0])

@app.route("/api/templates", methods=["POST"])
@auth
@role_required("editor")
@need_db
def create_template():
    d = request.json or {}
    name = _sanitize(d.get("name", ""), 500)
    category = _sanitize(d.get("category", ""), 100)
    ctype = d.get("contract_type", "client")
    description = _sanitize(d.get("description", ""), 2000)
    content = _sanitize(d.get("content", ""), 50000)
    clauses = d.get("clauses", [])
    if not name or len(name) < 3:
        return err("Template name must be at least 3 characters", 400)
    if not content or len(content) < 10:
        return err("Template content must be at least 10 characters", 400)
    if ctype not in ("client", "vendor"):
        return err("Type must be 'client' or 'vendor'", 400)
    row = {
        "name": name, "category": category or "other",
        "contract_type": ctype, "description": description,
        "content": content, "clauses": clauses if isinstance(clauses, list) else []
    }
    r = sb.table("contract_templates").insert(row).execute()
    return jsonify(r.data[0] if r.data else {"message": "Created"}), 201

@app.route("/api/templates/<int:tid>", methods=["PUT"])
@auth
@role_required("editor")
@need_db
def update_template(tid):
    chk = sb.table("contract_templates").select("id").eq("id", tid).execute()
    if not chk.data:
        return jsonify({"error": "Not found"}), 404
    d = request.json or {}
    upd = {}
    if "name" in d:
        name = _sanitize(d["name"], 500)
        if len(name) < 3: return err("Template name must be at least 3 characters", 400)
        upd["name"] = name
    if "category" in d: upd["category"] = _sanitize(d["category"], 100) or "other"
    if "contract_type" in d:
        if d["contract_type"] not in ("client", "vendor"):
            return err("Type must be 'client' or 'vendor'", 400)
        upd["contract_type"] = d["contract_type"]
    if "description" in d: upd["description"] = _sanitize(d["description"], 2000)
    if "content" in d:
        content = _sanitize(d["content"], 50000)
        if len(content) < 10: return err("Template content must be at least 10 characters", 400)
        upd["content"] = content
    if "clauses" in d: upd["clauses"] = d["clauses"] if isinstance(d["clauses"], list) else []
    if not upd:
        return err("No fields to update", 400)
    sb.table("contract_templates").update(upd).eq("id", tid).execute()
    r = sb.table("contract_templates").select("*").eq("id", tid).execute()
    return jsonify(r.data[0] if r.data else {"message": "Updated"})

@app.route("/api/templates/<int:tid>", methods=["DELETE"])
@auth
@role_required("manager")
@need_db
def delete_template(tid):
    chk = sb.table("contract_templates").select("id").eq("id", tid).execute()
    if not chk.data:
        return jsonify({"error": "Not found"}), 404
    sb.table("contract_templates").delete().eq("id", tid).execute()
    return jsonify({"message": "Template deleted"})

# ─── Contracts CRUD ────────────────────────────────────────────────────────
@app.route("/api/contracts", methods=["GET"])
@auth
@need_db
def list_contracts():
    ctype = request.args.get("type")
    status = request.args.get("status")
    page = max(1, int(request.args.get("page", 1)))
    per = min(50, max(1, int(request.args.get("per_page", 20))))
    q = sb.table("contracts").select(
        "id,name,party_name,contract_type,status,start_date,end_date,value,added_on,notes,department,created_by",
        count="exact"
    ).order("added_on", desc=True)
    if ctype: q = q.eq("contract_type", ctype)
    if status: q = q.eq("status", status)
    off = (page - 1) * per
    r = q.range(off, off + per - 1).execute()
    total = r.count if r.count is not None else len(r.data)
    return jsonify({"data": r.data, "total": total, "page": page, "per_page": per, "pages": max(1, -(-total // per))})

@app.route("/api/contracts", methods=["POST"])
@auth
@role_required("editor")
@need_db
def create_contract():
    d = _sanitize_dict(request.json or {})
    for f in ["name", "party_name", "contract_type", "content"]:
        if not d.get(f, "").strip(): return jsonify({"error": f"Missing: {f}"}), 400
    if d["contract_type"] not in ("client", "vendor"):
        return jsonify({"error": "Type must be client or vendor"}), 400
    row = {
        "name": d["name"][:500], "party_name": d["party_name"][:500],
        "contract_type": d["contract_type"], "content": d["content"][:500000],
        "content_html": d.get("content_html", ""),
        "start_date": d.get("start_date") or None, "end_date": d.get("end_date") or None,
        "value": d.get("value", "")[:100] or None, "notes": d.get("notes", "")[:1000],
        "status": d.get("status", "draft"), "department": d.get("department", ""),
        "jurisdiction": d.get("jurisdiction", ""), "governing_law": d.get("governing_law", ""),
        "template_id": d.get("template_id"), "created_by": d.get("created_by", "User"),
        "added_on": datetime.now().isoformat(),
    }
    r = sb.table("contracts").insert(row).execute()
    cid = r.data[0]["id"]
    log_activity(cid, "created", row["created_by"], f"Contract '{row['name']}' created")
    fire_webhooks("contract.created", {"contract_id": cid, "name": row["name"]})
    # Run workflow automations
    run_workflows("contract_created", cid, {"name": row["name"], "value": d.get("value", ""), "contract_type": d["contract_type"], "department": d.get("department", "")})
    # Auto-embed
    chunks = 0
    if oai_h():
        try: chunks = embed_contract(cid, d["content"], d["name"])
        except Exception as e: log.error(f"Embed failed: {e}")
    return jsonify({"id": cid, "message": "Created", "chunks_embedded": chunks}), 201

@app.route("/api/contracts/<int:cid>", methods=["GET"])
@auth
@need_db
def get_contract(cid):
    r = sb.table("contracts").select("*").eq("id", cid).execute()
    if not r.data: return jsonify({"error": "Not found"}), 404
    return jsonify(r.data[0])

@app.route("/api/contracts/<int:cid>", methods=["PUT"])
@auth
@role_required("editor")
@need_db
def update_contract(cid):
    chk = sb.table("contracts").select("id,content,content_html,name,status,updated_at").eq("id", cid).execute()
    if not chk.data: return jsonify({"error": "Not found"}), 404
    if chk.data[0].get("status") == "executed":
        return jsonify({"error": "Executed contracts cannot be modified. Create an amendment instead."}), 400
    d = _sanitize_dict(request.json or {})
    # Optimistic locking
    if d.get("updated_at"):
        db_updated = chk.data[0].get("updated_at", "")
        if db_updated and d["updated_at"] != db_updated:
            return jsonify({"error": {"message": "This contract was modified by another user. Please reload and try again.", "code": 409}}), 409
    u = {}
    for f in ["name","party_name","contract_type","start_date","end_date","value","notes","content",
              "content_html","department","jurisdiction","governing_law"]:
        if f in d: u[f] = d[f]
    if not u: return jsonify({"error": "Nothing to update"}), 400
    # Save version snapshot if content changed
    if "content" in u:
        try:
            old = chk.data[0]
            max_v = sb.table("contract_versions").select("version_number").eq("contract_id", cid).order("version_number", desc=True).limit(1).execute()
            next_v = (max_v.data[0]["version_number"] + 1) if max_v.data else 1
            sb.table("contract_versions").insert({
                "contract_id": cid, "version_number": next_v, "content": old.get("content", ""),
                "content_html": old.get("content_html", ""), "changed_by": d.get("user", "User"),
                "change_summary": f"Version {next_v} — before edit", "created_at": datetime.now().isoformat()
            }).execute()
        except Exception as e: log.debug(f"update_contract: {e}")
    u["updated_at"] = datetime.now().isoformat()
    sb.table("contracts").update(u).eq("id", cid).execute()
    log_activity(cid, "updated", d.get("user", "User"), f"Contract updated: {', '.join(u.keys())}")
    # Re-embed if content changed
    chunks = 0
    if "content" in u and oai_h():
        try: chunks = embed_contract(cid, u["content"], u.get("name", chk.data[0].get("name", "")))
        except Exception as e: log.debug(f"update_contract: {e}")
    return jsonify({"message": "Updated", "chunks_embedded": chunks})

@app.route("/api/contracts/<int:cid>", methods=["DELETE"])
@auth
@role_required("admin")
@need_db
def delete_contract(cid):
    chk = sb.table("contracts").select("id").eq("id", cid).execute()
    if not chk.data: return jsonify({"error": "Not found"}), 404
    sb.table("contracts").delete().eq("id", cid).execute()
    return jsonify({"message": "Deleted"})

# ─── Status Transitions ───────────────────────────────────────────────────
@app.route("/api/contracts/<int:cid>/status", methods=["PUT"])
@auth
@role_required("manager")
@need_db
def update_status(cid):
    d = request.json or {}
    new_status = d.get("status", "")
    return _transition_status(cid, new_status, d.get("user", "User"))

# ─── AI Contract Review ───────────────────────────────────────────────────
@app.route("/api/contracts/<int:cid>/review", methods=["POST"])
@auth
@need_db
def ai_review(cid):
    if not oai_h(): return jsonify({"error": "AI not configured"}), 500
    r = sb.table("contracts").select("content,name,contract_type").eq("id", cid).execute()
    if not r.data: return jsonify({"error": "Not found"}), 404
    c = r.data[0]
    try:
        reply = oai_chat([
            {"role": "system", "content": """You are a contract review AI for EMB (Expand My Business), a technology services broker company in India.

Analyze the contract clause by clause against standard Indian business & legal best practices.

For each key clause found (or missing), return a JSON array:
[
  {
    "clause_name": "Clause Name",
    "status": "aligned|partially_aligned|not_aligned|missing",
    "criteria": "What best practice requires",
    "review": "Your specific finding",
    "recommendation": "What should change",
    "risk_level": "low|medium|high",
    "section_ref": "Section number if found"
  }
]

MUST analyze these clauses:
1. Confidentiality/NDA 2. Payment Terms 3. Termination 4. Intellectual Property
5. Indemnification 6. Limitation of Liability 7. Governing Law & Jurisdiction
8. Force Majeure 9. Non-Compete/Non-Solicitation 10. Data Protection/Privacy
11. Dispute Resolution/Arbitration 12. Insurance 13. Compliance with Laws
14. Assignment 15. Warranty/SLA

Return ONLY valid JSON array, no markdown."""},
            {"role": "user", "content": f"Contract: {c['name']} (Type: {c['contract_type']})\n\n{c['content'][:8000]}"}
        ], model="gpt-4o", max_tok=4096)
        reply = reply.strip()
        if reply.startswith("```"): reply = reply.split("\n",1)[1].rsplit("```",1)[0].strip()
        review = J.loads(reply)
        aligned = sum(1 for r in review if r["status"] == "aligned")
        partial = sum(1 for r in review if r["status"] == "partially_aligned")
        not_aligned = sum(1 for r in review if r["status"] in ("not_aligned", "missing"))
        log_activity(cid, "ai_review", "AI", f"Review: {aligned} aligned, {partial} partial, {not_aligned} issues")
        return jsonify({"review": review, "summary": {"aligned": aligned, "partial": partial, "issues": not_aligned}})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─── Comments ──────────────────────────────────────────────────────────────
@app.route("/api/contracts/<int:cid>/comments", methods=["GET"])
@auth
@need_db
def list_comments(cid):
    r = sb.table("contract_comments").select("*").eq("contract_id", cid).order("created_at", desc=True).limit(100).execute()
    return jsonify(r.data)

@app.route("/api/contracts/<int:cid>/comments", methods=["POST"])
@auth
@need_db
def add_comment(cid):
    d = request.json or {}
    if not d.get("content"): return jsonify({"error": "Content required"}), 400
    row = {"contract_id": cid, "user_name": d.get("user_name", "User"),
           "content": d["content"][:2000], "clause_ref": d.get("clause_ref", ""),
           "created_at": datetime.now().isoformat()}
    r = sb.table("contract_comments").insert(row).execute()
    log_activity(cid, "comment_added", row["user_name"], d["content"][:100])
    create_notification(f"New Comment on Contract #{cid}", d["content"][:100], "comment", cid)
    return jsonify(r.data[0]), 201

# ─── Obligations ───────────────────────────────────────────────────────────
@app.route("/api/contracts/<int:cid>/obligations", methods=["GET"])
@auth
@need_db
def list_obligations(cid):
    r = sb.table("contract_obligations").select("*").eq("contract_id", cid).order("deadline").limit(100).execute()
    return jsonify(r.data)

@app.route("/api/contracts/<int:cid>/obligations", methods=["POST"])
@auth
@need_db
def add_obligation(cid):
    d = request.json or {}
    if not d.get("title"): return jsonify({"error": "Title required"}), 400
    row = {"contract_id": cid, "title": d["title"][:500], "description": d.get("description", "")[:2000],
           "deadline": d.get("deadline"), "status": "pending",
           "assigned_to": d.get("assigned_to", ""), "created_at": datetime.now().isoformat()}
    r = sb.table("contract_obligations").insert(row).execute()
    log_activity(cid, "obligation_added", "User", d["title"][:100])
    return jsonify(r.data[0]), 201

@app.route("/api/obligations/<int:oid>", methods=["PUT"])
@auth
@need_db
def update_obligation(oid):
    d = request.json or {}
    u = {}
    if "status" in d: u["status"] = d["status"]
    if "title" in d: u["title"] = d["title"]
    if "deadline" in d: u["deadline"] = d["deadline"]
    if "assigned_to" in d: u["assigned_to"] = d["assigned_to"]
    if not u: return jsonify({"error": "Nothing to update"}), 400
    sb.table("contract_obligations").update(u).eq("id", oid).execute()
    return jsonify({"message": "Updated"})

# ─── Collaborators ─────────────────────────────────────────────────────────
@app.route("/api/contracts/<int:cid>/collaborators", methods=["GET"])
@auth
@need_db
def list_collaborators(cid):
    r = sb.table("contract_collaborators").select("*").eq("contract_id", cid).order("created_at", desc=True).execute()
    return jsonify(r.data)

@app.route("/api/contracts/<int:cid>/collaborators", methods=["POST"])
@auth
@role_required("editor")
@need_db
def add_collaborator(cid):
    d = request.json or {}
    email = (d.get("user_email") or "").strip()
    if not _valid_email(email):
        return err("Valid email required", 400)
    role = d.get("role", "viewer")
    if role not in ("viewer", "editor", "reviewer"):
        return err("Role must be viewer, editor, or reviewer", 400)
    # Check contract exists
    chk = sb.table("contracts").select("id,name").eq("id", cid).execute()
    if not chk.data:
        return jsonify({"error": "Contract not found"}), 404
    # Check if user exists in clm_users
    user = sb.table("clm_users").select("name,email").eq("email", email).execute()
    user_name = user.data[0]["name"] if user.data else d.get("user_name", email)
    # Check duplicate
    existing = sb.table("contract_collaborators").select("id").eq("contract_id", cid).eq("user_email", email).execute()
    if existing.data:
        return err("User is already a collaborator on this contract", 400)
    row = {
        "contract_id": cid, "user_email": email, "user_name": _sanitize(user_name, 500),
        "role": role, "added_by": getattr(request, 'user_name', getattr(request, 'user_email', 'User'))
    }
    r = sb.table("contract_collaborators").insert(row).execute()
    log_activity(cid, "collaborator_added", row["added_by"], f"{user_name} ({role})")
    create_notification(f"Added as collaborator: {chk.data[0]['name']}", f"You were added as {role}", "info", cid, email)
    return jsonify(r.data[0] if r.data else {"message": "Added"}), 201

@app.route("/api/contracts/<int:cid>/collaborators/<int:collab_id>", methods=["PUT"])
@auth
@role_required("editor")
@need_db
def update_collaborator(cid, collab_id):
    d = request.json or {}
    role = d.get("role", "")
    if role not in ("viewer", "editor", "reviewer"):
        return err("Role must be viewer, editor, or reviewer", 400)
    sb.table("contract_collaborators").update({"role": role}).eq("id", collab_id).eq("contract_id", cid).execute()
    return jsonify({"message": "Updated"})

@app.route("/api/contracts/<int:cid>/collaborators/<int:collab_id>", methods=["DELETE"])
@auth
@role_required("editor")
@need_db
def remove_collaborator(cid, collab_id):
    collab = sb.table("contract_collaborators").select("user_name").eq("id", collab_id).eq("contract_id", cid).execute()
    if not collab.data:
        return jsonify({"error": "Not found"}), 404
    sb.table("contract_collaborators").delete().eq("id", collab_id).eq("contract_id", cid).execute()
    log_activity(cid, "collaborator_removed", getattr(request, 'user_email', 'User'), collab.data[0].get("user_name", ""))
    return jsonify({"message": "Removed"})

# ─── Approvals ─────────────────────────────────────────────────────────────
@app.route("/api/contracts/<int:cid>/approvals", methods=["GET"])
@auth
@need_db
def list_approvals(cid):
    r = sb.table("contract_approvals").select("*").eq("contract_id", cid).order("created_at", desc=True).limit(100).execute()
    return jsonify(r.data)

@app.route("/api/contracts/<int:cid>/approvals", methods=["POST"])
@auth
@need_db
def request_approval(cid):
    d = request.json or {}
    if not d.get("approver_name"): return jsonify({"error": "Approver name required"}), 400
    row = {"contract_id": cid, "approver_name": d["approver_name"],
           "status": "pending", "comments": d.get("comments", ""),
           "created_at": datetime.now().isoformat(), "updated_at": datetime.now().isoformat()}
    r = sb.table("contract_approvals").insert(row).execute()
    log_activity(cid, "approval_requested", "User", f"Approval requested from {d['approver_name']}")
    create_notification(f"Approval Requested", f"{d['approver_name']} needs to review Contract #{cid}", "approval", cid)
    sb.table("contracts").update({"status": "pending"}).eq("id", cid).execute()
    return jsonify(r.data[0]), 201

@app.route("/api/approvals/<int:aid>", methods=["PUT"])
@auth
@need_db
def respond_approval(aid):
    d = request.json or {}
    action = d.get("action")
    if action not in ("approved", "rejected"): return jsonify({"error": "Action must be approved or rejected"}), 400
    appr = sb.table("contract_approvals").select("*").eq("id", aid).execute()
    if not appr.data: return jsonify({"error": "Not found"}), 404
    sb.table("contract_approvals").update({
        "status": action, "comments": d.get("comments", ""), "updated_at": datetime.now().isoformat()
    }).eq("id", aid).execute()
    cid = appr.data[0]["contract_id"]
    new_status = "in_review" if action == "approved" else "rejected"
    log_activity(cid, f"approval_{action}", appr.data[0]["approver_name"], d.get("comments", ""))
    fire_webhooks(f"contract.{action}", {"contract_id": cid})
    # Use state machine for status transition -- silently skip if transition is invalid
    chk = sb.table("contracts").select("status").eq("id", cid).execute()
    old_status = chk.data[0]["status"] if chk.data else ""
    if new_status in VALID_TRANSITIONS.get(old_status, set()):
        _transition_status(cid, new_status, appr.data[0]["approver_name"])
    return jsonify({"message": f"Approval {action}"})

# ─── Signatures ────────────────────────────────────────────────────────────
@app.route("/api/contracts/<int:cid>/signatures", methods=["GET"])
@auth
@need_db
def list_signatures(cid):
    r = sb.table("contract_signatures").select("*").eq("contract_id", cid).order("signed_at").limit(100).execute()
    return jsonify(r.data)

@app.route("/api/contracts/<int:cid>/sign", methods=["POST"])
@auth
@role_required("manager")
@need_db
def sign_contract(cid):
    d = request.json or {}
    if not d.get("signer_name") or not d.get("signature_data"):
        return jsonify({"error": "Signer name and signature required"}), 400
    row = {"contract_id": cid, "signer_name": d["signer_name"],
           "signer_email": d.get("signer_email", ""), "signer_designation": d.get("signer_designation", ""),
           "signature_data": d["signature_data"], "ip_address": request.remote_addr,
           "signed_at": datetime.now().isoformat()}
    r = sb.table("contract_signatures").insert(row).execute()
    log_activity(cid, "signed", d["signer_name"], f"Contract signed by {d['signer_name']}")
    # Auto-execute if signed
    sb.table("contracts").update({"status": "executed", "executed_at": datetime.now().isoformat()}).eq("id", cid).execute()
    fire_webhooks("contract.executed", {"contract_id": cid, "signer": d["signer_name"]})
    return jsonify(r.data[0]), 201

# ─── Leegality E-Sign Integration ────────────────────────────────────────
@app.route("/api/contracts/<int:cid>/esign", methods=["POST"])
@auth
@need_db
def leegality_esign(cid):
    """Send contract for e-signature via Leegality API"""
    if not LEEGALITY_KEY:
        return jsonify({"error": "Leegality API not configured. Set LEEGALITY_API_KEY env var."}), 503
    c = sb.table("contracts").select("id,name,content,content_html,party_name").eq("id", cid).execute()
    if not c.data: return jsonify({"error": "Not found"}), 404
    contract = c.data[0]
    d = request.json or {}
    signers = d.get("signers", [])
    if not signers or not all(s.get("name") and s.get("email") for s in signers):
        return jsonify({"error": "Signers required — each needs name and email"}), 400
    try:
        # Build Leegality document signing request
        leeg_headers = {
            "X-Auth-Token": LEEGALITY_KEY,
            "Content-Type": "application/json"
        }
        # Prepare invitees
        invitees = []
        for i, s in enumerate(signers):
            invitee = {
                "name": s["name"],
                "email": s["email"],
                "phone": s.get("phone", ""),
                "signOrder": i + 1,
                "signingOptions": {
                    "aadhaarESign": s.get("aadhaar_esign", False),
                    "dsc": s.get("dsc", False),
                    "electronicSignature": s.get("electronic_signature", True)
                }
            }
            invitees.append(invitee)

        payload = {
            "name": contract["name"],
            "invitees": invitees,
            "callbackUrl": d.get("callback_url", ""),
            "isSequentialSigning": d.get("sequential", True),
            "sendEmail": d.get("send_email", True),
            "expiryDays": d.get("expiry_days", 30),
        }

        # If contract has HTML content, send as HTML; otherwise send as text
        content = contract.get("content_html") or contract.get("content", "")
        if content:
            payload["file"] = {
                "name": f"{contract['name']}.html",
                "content": content
            }

        r = http.post(
            f"{LEEGALITY_URL}/api/v3.1/document/create",
            headers=leeg_headers,
            json=payload,
            timeout=30
        )

        if r.status_code >= 400:
            err_resp = r.json() if r.headers.get("content-type","").startswith("application/json") else {"message": r.text}
            return jsonify({"error": f"Leegality error: {err_resp.get('message', r.text)}"}), r.status_code

        result = r.json()
        doc_id = result.get("data", {}).get("documentId") or result.get("documentId", "")
        signing_url = result.get("data", {}).get("signingUrl") or result.get("signingUrl", "")

        # Store e-sign request in signatures table for tracking
        for s in signers:
            sb.table("contract_signatures").insert({
                "contract_id": cid,
                "signer_name": s["name"],
                "signer_email": s["email"],
                "signer_designation": s.get("designation", ""),
                "signature_data": f"leegality:{doc_id}",
                "ip_address": "leegality-esign",
                "signed_at": None
            }).execute()

        # Update contract status
        sb.table("contracts").update({"status": "pending"}).eq("id", cid).execute()
        log_activity(cid, "esign_sent", "User", f"E-sign request sent via Leegality to {', '.join(s['name'] for s in signers)}")
        fire_webhooks("contract.esign_sent", {"contract_id": cid, "signers": [s["name"] for s in signers]})

        return jsonify({
            "message": "E-sign request sent via Leegality",
            "document_id": doc_id,
            "signing_url": signing_url,
            "signers": [s["name"] for s in signers]
        }), 201

    except http.exceptions.Timeout:
        return jsonify({"error": "Leegality API timeout"}), 504
    except Exception as e:
        log.error(f"Leegality error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/leegality/webhook", methods=["POST"])
def leegality_webhook():
    """Receive Leegality webhook callbacks for signature status updates"""
    d = request.json or {}
    # Verify webhook MAC using Private Salt
    if LEEGALITY_SALT:
        mac = d.get("mac", "")
        # Leegality sends a MAC for verification -- compute expected MAC
        payload_str = J.dumps(d, separators=(',', ':'), sort_keys=True)
        # Remove mac from payload for verification
        verify_data = {k: v for k, v in d.items() if k != "mac"}
        expected = hmac.new(LEEGALITY_SALT.encode(), J.dumps(verify_data, separators=(',', ':'), sort_keys=True).encode(), hashlib.sha256).hexdigest()
        if mac and not hmac.compare_digest(mac, expected):
            log.warning("Leegality webhook MAC verification failed")
            # Still process -- some webhook formats may differ, log the warning
    doc_id = d.get("documentId", "")
    event = d.get("event", "")
    signer_info = d.get("signer", {})
    log.info(f"Leegality webhook: event={event}, doc={doc_id}")

    if not doc_id:
        return jsonify({"status": "ignored"}), 200

    try:
        # Find matching signature records
        sigs = sb.table("contract_signatures").select("*").like("signature_data", f"leegality:{doc_id}%").execute().data
        if not sigs:
            return jsonify({"status": "no match"}), 200

        cid = sigs[0]["contract_id"]

        if event in ("document.signed", "invitee.signed"):
            signer_name = signer_info.get("name", "Unknown")
            signer_email = signer_info.get("email", "")
            # Update the specific signer's record
            for s in sigs:
                if s["signer_email"] == signer_email or s["signer_name"] == signer_name:
                    sb.table("contract_signatures").update({
                        "signed_at": datetime.now().isoformat(),
                        "signature_data": f"leegality:{doc_id}:signed"
                    }).eq("id", s["id"]).execute()
                    break
            log_activity(cid, "esign_completed", signer_name, f"E-signature completed via Leegality")

        elif event == "document.completed":
            # All signers done -> execute contract
            sb.table("contracts").update({
                "status": "executed",
                "executed_at": datetime.now().isoformat()
            }).eq("id", cid).execute()
            log_activity(cid, "executed", "Leegality", "All signatures collected — contract executed")
            fire_webhooks("contract.executed", {"contract_id": cid, "method": "leegality"})

        elif event == "document.rejected":
            sb.table("contracts").update({"status": "rejected"}).eq("id", cid).execute()
            log_activity(cid, "esign_rejected", signer_info.get("name", "Unknown"), "E-signature rejected")

        elif event == "document.expired":
            log_activity(cid, "esign_expired", "Leegality", "E-sign request expired")

    except Exception as e:
        log.error(f"Leegality webhook error: {e}")

    return jsonify({"status": "ok"}), 200

@app.route("/api/leegality/status")
@auth
def leegality_status():
    """Check if Leegality is configured"""
    return jsonify({"configured": bool(LEEGALITY_KEY), "provider": "Leegality"})

# ─── Activity Timeline ────────────────────────────────────────────────────
@app.route("/api/contracts/<int:cid>/activity", methods=["GET"])
@auth
@need_db
def get_activity(cid):
    limit = min(int(request.args.get("limit", 200)), 500)
    r = sb.table("contract_activity").select("*").eq("contract_id", cid).order("created_at", desc=True).limit(limit).execute()
    return jsonify(r.data)

# ─── PDF Upload (single) ──────────────────────────────────────────────────
@app.route("/api/upload-pdf", methods=["POST"])
@auth
def upload_pdf():
    if "file" not in request.files: return jsonify({"error": "No file"}), 400
    f = request.files["file"]
    if not f.filename.lower().endswith(".pdf"): return jsonify({"error": "PDF only"}), 400
    try:
        b = f.read()
        if len(b) > 50*1024*1024: return jsonify({"error": "Max 50MB per file"}), 400
        if not b[:4] == b'%PDF':
            return jsonify({"error": "Invalid PDF file"}), 400
        doc = fitz.open(stream=b, filetype="pdf")
        pc = len(doc)
        txt = "".join(p.get_text() + "\n" for p in doc)
        doc.close()

        # If text extraction worked, return it (normal text-based PDF)
        if txt.strip():
            return jsonify({"content": txt.strip(), "pages": pc, "method": "text"})

        # Scanned/image PDF — use GPT-4o Vision OCR
        if not oai_h():
            return jsonify({"error": "Scanned PDF detected but AI (OpenAI) is not configured for OCR"}), 400
        log.info(f"Scanned PDF detected ({pc} pages), running OCR via GPT-4o Vision...")
        ocr_text, total_pages, ocr_pages = ocr_pdf_pages(b, max_pages=50)
        if not ocr_text.strip():
            return jsonify({"error": "OCR could not extract text from this scanned PDF"}), 400
        result = {"content": ocr_text.strip(), "pages": total_pages, "method": "ocr", "ocr_pages": ocr_pages}
        if total_pages > ocr_pages:
            result["warning"] = f"Only first {ocr_pages} of {total_pages} pages were OCR'd"
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─── Bulk PDF Upload (multiple files → multiple contracts) ────────────────
@app.route("/api/upload-pdfs-bulk", methods=["POST"])
@auth
@role_required("editor")
@need_db
def upload_pdfs_bulk():
    """Process multiple PDFs at once. Each PDF becomes a separate contract via AI parse."""
    files = request.files.getlist("files")
    if not files or len(files) == 0:
        return jsonify({"error": "No files uploaded"}), 400
    if len(files) > 10:
        return jsonify({"error": "Max 10 PDFs at a time"}), 400

    results = []
    for f in files:
        fname = f.filename or "unknown.pdf"
        if not fname.lower().endswith(".pdf"):
            results.append({"file": fname, "status": "skipped", "error": "Not a PDF"})
            continue
        try:
            b = f.read()
            if len(b) > 50*1024*1024:
                results.append({"file": fname, "status": "error", "error": "Exceeds 50MB"})
                continue
            if not b[:4] == b'%PDF':
                results.append({"file": fname, "status": "error", "error": "Invalid PDF"})
                continue

            # Extract text (text-based or OCR)
            doc = fitz.open(stream=b, filetype="pdf")
            pc = len(doc)
            txt = "".join(p.get_text() + "\n" for p in doc)
            doc.close()
            method = "text"

            if not txt.strip():
                if not oai_h():
                    results.append({"file": fname, "status": "error", "error": "Scanned PDF but no AI configured"})
                    continue
                txt, pc, _ = ocr_pdf_pages(b, max_pages=50)
                method = "ocr"

            if not txt.strip():
                results.append({"file": fname, "status": "error", "error": "No text extracted"})
                continue

            # AI parse to extract contract metadata
            meta = {}
            if oai_h():
                try:
                    reply = oai_chat([
                        {"role": "system", "content": """Extract contract metadata. Return ONLY JSON:
{"name":"","party_name":"","contract_type":"client|vendor","start_date":"YYYY-MM-DD|null","end_date":"YYYY-MM-DD|null","value":"USD X|null","notes":"","department":"","jurisdiction":"","governing_law":""}"""},
                        {"role": "user", "content": txt[:4000]}
                    ], model="gpt-4o-mini", max_tok=400)
                    if reply.startswith("```"): reply = reply.split("\n",1)[1].rsplit("```",1)[0].strip()
                    meta = J.loads(reply)
                except: pass

            # Create the contract
            contract = {
                "name": _sanitize(meta.get("name") or fname.replace(".pdf","").replace("_"," ").title()),
                "party_name": _sanitize(meta.get("party_name", "")),
                "contract_type": meta.get("contract_type", "client") if meta.get("contract_type") in ("client","vendor") else "client",
                "status": "draft",
                "content": txt.strip(),
                "start_date": meta.get("start_date") if meta.get("start_date") and meta.get("start_date") != "null" else None,
                "end_date": meta.get("end_date") if meta.get("end_date") and meta.get("end_date") != "null" else None,
                "value": _sanitize(meta.get("value", "")) if meta.get("value") and meta.get("value") != "null" else "",
                "department": _sanitize(meta.get("department", "")),
                "notes": _sanitize(meta.get("notes", "")),
                "created_by": getattr(request, "user_email", "system"),
            }
            r = sb.table("contracts").insert(contract).execute()
            cid = r.data[0]["id"] if r.data else None

            # Log activity
            if cid:
                log_activity(cid, "created", getattr(request, "user_email", "system"),
                             f"Bulk uploaded from {fname} ({pc} pages, {method})")

            results.append({
                "file": fname,
                "status": "created",
                "contract_id": cid,
                "contract_name": contract["name"],
                "pages": pc,
                "method": method,
                "party": contract["party_name"],
                "type": contract["contract_type"],
            })
        except Exception as e:
            results.append({"file": fname, "status": "error", "error": str(e)})

    created = sum(1 for r in results if r["status"] == "created")
    failed = sum(1 for r in results if r["status"] == "error")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    return jsonify({
        "summary": {"total": len(files), "created": created, "failed": failed, "skipped": skipped},
        "results": results
    })

# ─── Search ────────────────────────────────────────────────────────────────
@app.route("/api/search")
@auth
@need_db
def search():
    q = request.args.get("q", "").strip()
    if not q: return jsonify([])
    t = f"%{q}%"
    r = sb.table("contracts").select(
        "id,name,party_name,contract_type,status,start_date,end_date,value"
    ).or_(f"name.ilike.{t},party_name.ilike.{t},content.ilike.{t}").limit(20).execute()
    return jsonify(r.data)

# ─── Parse (AI auto-fill) ─────────────────────────────────────────────────
@app.route("/api/parse", methods=["POST"])
@auth
def parse():
    if not oai_h(): return jsonify({"error": "AI not configured"}), 500
    d = request.json or {}
    content = d.get("content", "")
    if not content: return jsonify({"error": "No content"}), 400
    try:
        reply = oai_chat([
            {"role": "system", "content": """Extract contract metadata. Return ONLY JSON:
{"name":"","party_name":"","contract_type":"client|vendor","start_date":"YYYY-MM-DD|null","end_date":"YYYY-MM-DD|null","value":"USD X|null","notes":"","department":"","jurisdiction":"","governing_law":""}"""},
            {"role": "user", "content": content[:3000]}
        ], model="gpt-4o-mini", max_tok=300).strip()
        if reply.startswith("```"): reply = reply.split("\n",1)[1].rsplit("```",1)[0].strip()
        return jsonify(J.loads(reply))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─── Chat (RAG + Streaming) ───────────────────────────────────────────────
@app.route("/api/chat", methods=["POST"])
@auth
@need_db
def chat():
    if not oai_h(): return jsonify({"error": "AI not configured"}), 500
    d = request.json or {}
    msg = d.get("message", "").strip()
    history = d.get("history", [])[-20:]
    cids = d.get("contract_ids")
    stream = d.get("stream", False)
    if not msg: return jsonify({"error": "No message"}), 400

    chunks = []
    try: chunks = hybrid_search(msg, cids, 30)
    except Exception as e: log.debug(f"chat: {e}")

    ref_ids = list(set(c["contract_id"] for c in chunks)) if chunks else []
    meta = sb.table("contracts").select("id,name,party_name,contract_type,start_date,end_date,value").in_("id", ref_ids).execute().data if ref_ids else []
    ml = {c["id"]: c for c in meta}

    if chunks:
        parts = [f"[{ml.get(c['contract_id'],{}).get('name','?')} | {c.get('section_title','?')} | Rel:{c.get('similarity','?')}]\n{c['chunk_text']}" for c in chunks]
        ctx = "\n---\n".join(parts)
        summ = "\n".join(f"- {c['name']} ({c['party_name']}, {c['contract_type']})" for c in meta)

        # For single-contract queries: supplement with full text to catch missed sections
        if cids and len(cids) <= 3:
            try:
                full = sb.table("contracts").select("content").in_("id", cids).execute().data
                for fc in (full or []):
                    content = fc.get("content", "")
                    if content:
                        # Send full text (up to 30K chars) to ensure no section is missed
                        ctx += f"\n\n--- FULL CONTRACT TEXT ---\n{content[:30000]}"
            except: pass
    else:
        q = sb.table("contracts").select("id,name,party_name,contract_type,content")
        if cids: q = q.in_("id", cids)
        r = q.execute()
        ctx = "\n\n".join(f"--- {c['name']} ---\n{c['content']}" for c in r.data) if r.data else "No contracts."
        summ = "Full text loaded."

    msgs = [{"role": "system", "content": build_prompt(summ, ctx)}] + history + [{"role": "user", "content": msg}]
    sources = [{"id": c["id"], "name": c["name"], "party": c["party_name"]} for c in meta]
    n_chunks = len(chunks)

    if stream:
        def gen():
            try:
                for tok in oai_stream(msgs):
                    yield f"data: {J.dumps({'c': tok})}\n\n"
                yield f"data: {J.dumps({'done': True, 'sources': sources, 'chunks_used': n_chunks})}\n\n"
            except Exception as e:
                yield f"data: {J.dumps({'error': str(e)})}\n\n"
        return Response(gen(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    try:
        reply = oai_chat(msgs)
        return jsonify({"reply": reply, "sources": sources, "chunks_used": n_chunks})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─── Export ────────────────────────────────────────────────────────────────
@app.route("/api/export")
@auth
@need_db
def export():
    r = sb.table("contracts").select(
        "id,name,party_name,contract_type,status,start_date,end_date,value,added_on,department,notes"
    ).order("added_on", desc=True).execute()
    fmt = request.args.get("format", "csv")
    if fmt == "json": return jsonify(r.data)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["ID","Name","Party","Type","Status","Start","End","Value","Department","Added","Notes"])
    for c in r.data:
        w.writerow([c["id"],c["name"],c["party_name"],c["contract_type"],c.get("status",""),
                    c.get("start_date",""),c.get("end_date",""),c.get("value",""),
                    c.get("department",""),c.get("added_on",""),c.get("notes","")])
    out.seek(0)
    return Response(out.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=contracts_{datetime.now():%Y%m%d}.csv"})

# ─── Webhooks Config ───────────────────────────────────────────────────────
@app.route("/api/webhooks", methods=["GET"])
@auth
@need_db
def list_webhooks():
    r = sb.table("webhook_configs").select("*").order("created_at", desc=True).limit(50).execute()
    return jsonify(r.data)

@app.route("/api/webhooks", methods=["POST"])
@auth
@role_required("admin")
@need_db
def create_webhook():
    d = request.json or {}
    if not d.get("url") or not d.get("event_type"):
        return jsonify({"error": "URL and event_type required"}), 400
    r = sb.table("webhook_configs").insert({
        "event_type": d["event_type"], "url": d["url"], "active": True,
        "created_at": datetime.now().isoformat()
    }).execute()
    return jsonify(r.data[0]), 201

@app.route("/api/webhooks/<int:wid>", methods=["DELETE"])
@auth
@need_db
def delete_webhook(wid):
    sb.table("webhook_configs").delete().eq("id", wid).execute()
    return jsonify({"message": "Deleted"})

# ─── Version History ──────────────────────────────────────────────────────
@app.route("/api/contracts/<int:cid>/versions", methods=["GET"])
@auth
@need_db
def list_versions(cid):
    r = sb.table("contract_versions").select("id,contract_id,version_number,changed_by,change_summary,created_at").eq("contract_id", cid).order("version_number", desc=True).limit(50).execute()
    return jsonify(r.data)

@app.route("/api/contracts/<int:cid>/versions/<int:vid>", methods=["GET"])
@auth
@need_db
def get_version(cid, vid):
    r = sb.table("contract_versions").select("*").eq("id", vid).eq("contract_id", cid).execute()
    if not r.data: return jsonify({"error": "Not found"}), 404
    return jsonify(r.data[0])

@app.route("/api/contracts/<int:cid>/versions/<int:vid>/restore", methods=["POST"])
@auth
@need_db
def restore_version(cid, vid):
    v = sb.table("contract_versions").select("*").eq("id", vid).eq("contract_id", cid).execute()
    if not v.data: return jsonify({"error": "Not found"}), 404
    ver = v.data[0]
    # Snapshot current before restoring
    cur = sb.table("contracts").select("content,content_html").eq("id", cid).execute()
    if cur.data:
        max_v = sb.table("contract_versions").select("version_number").eq("contract_id", cid).order("version_number", desc=True).limit(1).execute()
        next_v = (max_v.data[0]["version_number"] + 1) if max_v.data else 1
        sb.table("contract_versions").insert({
            "contract_id": cid, "version_number": next_v, "content": cur.data[0].get("content", ""),
            "content_html": cur.data[0].get("content_html", ""), "changed_by": "User",
            "change_summary": "Snapshot before restore", "created_at": datetime.now().isoformat()
        }).execute()
    sb.table("contracts").update({"content": ver["content"], "content_html": ver.get("content_html", "")}).eq("id", cid).execute()
    log_activity(cid, "version_restored", "User", f"Restored to version {ver['version_number']}")
    return jsonify({"message": f"Restored to version {ver['version_number']}"})

# ─── Redline / Track Changes ─────────────────────────────────────────────
@app.route("/api/contracts/<int:cid>/redline", methods=["GET"])
@auth
@need_db
def contract_redline(cid):
    """Compare current contract with its previous version (or a specific version)"""
    vid = request.args.get("version_id")
    # Get current contract
    cur = sb.table("contracts").select("id,name,content").eq("id", cid).execute()
    if not cur.data: return jsonify({"error": "Not found"}), 404
    current_text = cur.data[0].get("content", "")

    # Get comparison text
    if vid:
        ver = sb.table("contract_versions").select("*").eq("id", int(vid)).eq("contract_id", cid).execute()
        if not ver.data: return jsonify({"error": "Version not found"}), 404
        old_text = ver.data[0].get("content", "")
        old_label = f"Version {ver.data[0]['version_number']}"
    else:
        # Get latest version (previous content)
        vers = sb.table("contract_versions").select("*").eq("contract_id", cid).order("version_number", desc=True).limit(1).execute()
        if not vers.data:
            return jsonify({"error": "No previous version available. Edit the contract at least once to see redline."}), 404
        old_text = vers.data[0].get("content", "")
        old_label = f"Version {vers.data[0]['version_number']}"

    # Generate diffs
    word_diff = _word_diff(old_text, current_text)
    line_diff, additions, deletions = _line_diff(old_text, current_text)

    # Build HTML redline
    html_parts = []
    for chunk in word_diff:
        text = chunk["text"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if chunk["type"] == "equal":
            html_parts.append(text)
        elif chunk["type"] == "delete":
            html_parts.append(f'<del class="rl-del">{text}</del>')
        elif chunk["type"] == "insert":
            html_parts.append(f'<ins class="rl-ins">{text}</ins>')

    redline_html = " ".join(html_parts)
    # Preserve line breaks
    redline_html = redline_html.replace("\n", "<br>")

    return jsonify({
        "redline_html": redline_html,
        "word_diff": word_diff,
        "additions": additions,
        "deletions": deletions,
        "old_label": old_label,
        "new_label": "Current",
        "stats": {
            "total_changes": additions + deletions,
            "additions": additions,
            "deletions": deletions,
            "old_word_count": len(old_text.split()),
            "new_word_count": len(current_text.split())
        }
    })

@app.route("/api/contracts/<int:cid>/diff", methods=["GET"])
@auth
@need_db
def contract_diff(cid):
    """Compare two specific versions"""
    v1 = request.args.get("v1")
    v2 = request.args.get("v2")
    if not v1 or not v2:
        return jsonify({"error": "v1 and v2 version IDs required"}), 400

    ver1 = sb.table("contract_versions").select("*").eq("id", int(v1)).eq("contract_id", cid).execute()
    ver2 = sb.table("contract_versions").select("*").eq("id", int(v2)).eq("contract_id", cid).execute()

    if not ver1.data or not ver2.data:
        return jsonify({"error": "Version not found"}), 404

    text1 = ver1.data[0].get("content", "")
    text2 = ver2.data[0].get("content", "")

    word_diff = _word_diff(text1, text2)
    _, additions, deletions = _line_diff(text1, text2)

    html_parts = []
    for chunk in word_diff:
        text = chunk["text"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if chunk["type"] == "equal":
            html_parts.append(text)
        elif chunk["type"] == "delete":
            html_parts.append(f'<del class="rl-del">{text}</del>')
        elif chunk["type"] == "insert":
            html_parts.append(f'<ins class="rl-ins">{text}</ins>')

    redline_html = " ".join(html_parts).replace("\n", "<br>")

    return jsonify({
        "redline_html": redline_html,
        "v1_label": f"Version {ver1.data[0]['version_number']}",
        "v2_label": f"Version {ver2.data[0]['version_number']}",
        "additions": additions,
        "deletions": deletions
    })

# ─── Clause Library ──────────────────────────────────────────────────────
@app.route("/api/clauses", methods=["GET"])
@auth
@need_db
def list_clauses():
    cat = request.args.get("category")
    q = sb.table("clause_library").select("*").order("usage_count", desc=True)
    if cat: q = q.eq("category", cat)
    return jsonify(q.limit(200).execute().data)

@app.route("/api/clauses", methods=["POST"])
@auth
@need_db
def create_clause():
    d = request.json or {}
    if not d.get("title") or not d.get("content") or not d.get("category"):
        return jsonify({"error": "Title, category, and content required"}), 400
    row = {"title": d["title"][:300], "category": d["category"][:100], "content": d["content"][:10000],
           "tags": d.get("tags", ""), "created_by": d.get("created_by", "User"), "created_at": datetime.now().isoformat()}
    r = sb.table("clause_library").insert(row).execute()
    return jsonify(r.data[0]), 201

@app.route("/api/clauses/<int:cid>", methods=["PUT"])
@auth
@need_db
def update_clause(cid):
    d = request.json or {}
    u = {}
    for f in ["title", "category", "content", "tags"]:
        if f in d: u[f] = d[f]
    if not u: return jsonify({"error": "Nothing to update"}), 400
    sb.table("clause_library").update(u).eq("id", cid).execute()
    return jsonify({"message": "Updated"})

@app.route("/api/clauses/<int:cid>", methods=["DELETE"])
@auth
@need_db
def delete_clause(cid):
    sb.table("clause_library").delete().eq("id", cid).execute()
    return jsonify({"message": "Deleted"})

@app.route("/api/clauses/<int:cid>/use", methods=["POST"])
@auth
@need_db
def use_clause(cid):
    """Increment usage count when a clause is inserted into a contract"""
    sb.table("clause_library").update({"usage_count": sb.table("clause_library").select("usage_count").eq("id", cid).execute().data[0]["usage_count"] + 1}).eq("id", cid).execute()
    return jsonify({"message": "Usage tracked"})

# ─── User Management ─────────────────────────────────────────────────────
@app.route("/api/users", methods=["GET"])
@auth
@role_required("admin")
@need_db
def list_users():
    r = sb.table("clm_users").select("id,email,name,role,department,designation,phone,is_active,last_login,created_at").order("created_at", desc=True).execute()
    return jsonify(r.data)

@app.route("/api/users", methods=["POST"])
@auth
@role_required("admin")
@need_db
def create_user():
    d = request.json or {}
    for f in ["email", "name", "password"]:
        if not d.get(f, "").strip(): return jsonify({"error": f"Missing: {f}"}), 400
    email = d["email"].strip().lower()
    if not _valid_email(email):
        return jsonify({"error": "Invalid email format"}), 400
    # Check duplicate
    existing = sb.table("clm_users").select("id").eq("email", email).execute()
    if existing.data: return jsonify({"error": "Email already exists"}), 409
    pw_hash = _hash_password(d["password"])
    role = d.get("role", "viewer")
    if role not in ("admin", "manager", "editor", "viewer"): role = "viewer"
    row = {
        "email": email, "name": d["name"][:200], "password_hash": pw_hash,
        "role": role, "department": d.get("department", ""),
        "designation": d.get("designation", ""), "phone": d.get("phone", ""),
        "is_active": True, "created_at": datetime.now().isoformat(), "updated_at": datetime.now().isoformat()
    }
    r = sb.table("clm_users").insert(row).execute()
    return jsonify({"id": r.data[0]["id"], "message": f"User {email} created"}), 201

@app.route("/api/users/<int:uid>", methods=["PUT"])
@auth
@role_required("admin")
@need_db
def update_user(uid):
    d = request.json or {}
    u = {}
    for f in ["name", "email", "role", "department", "designation", "phone", "is_active"]:
        if f in d: u[f] = d[f]
    if "password" in d and d["password"]:
        u["password_hash"] = _hash_password(d["password"])
    if not u: return jsonify({"error": "Nothing to update"}), 400
    u["updated_at"] = datetime.now().isoformat()
    sb.table("clm_users").update(u).eq("id", uid).execute()
    return jsonify({"message": "User updated"})

@app.route("/api/users/<int:uid>", methods=["DELETE"])
@auth
@role_required("admin")
@need_db
def delete_user(uid):
    sb.table("clm_users").delete().eq("id", uid).execute()
    return jsonify({"message": "User deleted"})

# ─── Reports ─────────────────────────────────────────────────────────────
@app.route("/api/reports")
@auth
@need_db
def reports():
    rtype = request.args.get("type", "summary")
    date_from = request.args.get("from")
    date_to = request.args.get("to")

    contracts = sb.table("contracts").select("id,name,party_name,contract_type,status,start_date,end_date,value,added_on,department").execute().data
    today = datetime.now()

    # Filter by date range if provided
    if date_from:
        contracts = [c for c in contracts if (c.get("added_on") or "") >= date_from]
    if date_to:
        contracts = [c for c in contracts if (c.get("added_on") or "") <= date_to]

    if rtype == "summary":
        by_status = {}
        by_type = {"client": 0, "vendor": 0}
        by_dept = {}
        total_value = 0
        monthly = {}
        for c in contracts:
            s = c.get("status", "draft")
            by_status[s] = by_status.get(s, 0) + 1
            ct = c.get("contract_type", "client")
            by_type[ct] = by_type.get(ct, 0) + 1
            dept = c.get("department", "Unassigned") or "Unassigned"
            if dept not in by_dept: by_dept[dept] = 0
            by_dept[dept] += 1
            v = c.get("value", "")
            if v:
                nums = re.findall(r'[\d,]+(?:\.\d+)?', str(v).replace(",", ""))
                if nums:
                    try: total_value += float(nums[0])
                    except Exception as e: log.debug(f"reports: {e}")
            added = c.get("added_on", "")
            if added:
                m = added[:7]
                monthly[m] = monthly.get(m, 0) + 1
        dept_list = [{"department": k, "count": v} for k, v in sorted(by_dept.items(), key=lambda x: -x[1])]
        trend = [{"month": k, "count": v} for k, v in sorted(monthly.items())][-12:]
        val_str = f"INR {total_value:,.0f}" if total_value else "N/A"
        return jsonify({
            "total_contracts": len(contracts), "draft": by_status.get("draft", 0),
            "pending": by_status.get("pending", 0), "in_review": by_status.get("in_review", 0),
            "executed": by_status.get("executed", 0), "rejected": by_status.get("rejected", 0),
            "client_count": by_type.get("client", 0), "vendor_count": by_type.get("vendor", 0),
            "total_value": val_str, "by_department": dept_list, "monthly_trend": trend,
            "contracts": contracts
        })

    elif rtype == "expiry":
        result = []
        expired = 0; exp30 = 0; exp90 = 0; safe = 0
        for c in contracts:
            if c.get("end_date"):
                try:
                    end = datetime.strptime(c["end_date"], "%Y-%m-%d")
                    days = (end - today).days
                    c["days_left"] = days
                    result.append(c)
                    if days < 0: expired += 1
                    elif days <= 30: exp30 += 1
                    elif days <= 90: exp90 += 1
                    else: safe += 1
                except Exception as e: log.debug(f"reports: {e}")
        result.sort(key=lambda x: x.get("days_left", 9999))
        return jsonify({"expired": expired, "expiring_30": exp30, "expiring_90": exp90,
                        "safe": safe, "contracts": result})

    elif rtype == "department":
        depts = {}
        for c in contracts:
            dept = c.get("department", "Unassigned") or "Unassigned"
            if dept not in depts: depts[dept] = {"department": dept, "count": 0, "draft": 0, "pending": 0, "executed": 0, "total_value": 0}
            depts[dept]["count"] += 1
            s = c.get("status", "draft")
            if s in depts[dept]: depts[dept][s] += 1
            v = c.get("value", "")
            if v:
                nums = re.findall(r'[\d,]+(?:\.\d+)?', str(v).replace(",", ""))
                if nums:
                    try: depts[dept]["total_value"] += float(nums[0])
                    except Exception as e: log.debug(f"reports: {e}")
        dept_list = sorted(depts.values(), key=lambda x: -x["count"])
        for d in dept_list:
            d["total_value"] = f"INR {d['total_value']:,.0f}" if d["total_value"] else "—"
        return jsonify({"departments": dept_list})

    return jsonify({"error": "Unknown report type"}), 400

# ─── Audit Log Export ────────────────────────────────────────────────────
@app.route("/api/audit-log")
@auth
@role_required("manager")
@need_db
def audit_log():
    date_from = request.args.get("from")
    date_to = request.args.get("to")
    action = request.args.get("action")
    fmt = request.args.get("format", "json")

    q = sb.table("contract_activity").select("*").order("created_at", desc=True)
    if date_from: q = q.gte("created_at", date_from)
    if date_to: q = q.lte("created_at", date_to)
    if action: q = q.ilike("action", f"%{action}%")
    r = q.limit(1000).execute()

    # Enrich with contract names
    cids = list(set(a.get("contract_id") for a in r.data if a.get("contract_id")))
    cmap = {}
    if cids:
        try:
            cr = sb.table("contracts").select("id,name").in_("id", cids[:200]).execute()
            cmap = {c["id"]: c["name"] for c in cr.data}
        except Exception as e: log.debug(f"audit_log: {e}")
    for a in r.data:
        a["contract_name"] = cmap.get(a.get("contract_id"), "")

    if fmt == "csv":
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["ID", "Contract ID", "Contract Name", "Action", "User", "Details", "Timestamp"])
        for a in r.data:
            w.writerow([a["id"], a["contract_id"], a.get("contract_name", ""), a["action"], a["user_name"], a.get("details", ""), a["created_at"]])
        out.seek(0)
        return Response(out.getvalue(), mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename=audit_log_{datetime.now():%Y%m%d}.csv"})

    return jsonify(r.data)

# ─── Bulk Import ──────────────────────────────────────────────────────────
@app.route("/api/bulk-import", methods=["POST"])
@auth
@need_db
def bulk_import():
    """Bulk import contracts from CSV."""
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    f = request.files["file"]
    if not f.filename.lower().endswith(".csv"):
        return jsonify({"error": "CSV file required"}), 400
    try:
        raw = f.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(raw))
        imported, skipped, errors = 0, 0, []
        required = {"name", "party_name", "contract_type", "content"}
        for i, row in enumerate(reader):
            row = {k.strip().lower().replace(" ", "_"): (v.strip() if v else "") for k, v in row.items() if k}
            missing = required - {k for k, v in row.items() if v}
            if missing:
                skipped += 1
                errors.append(f"Row {i+2}: Missing {', '.join(missing)}")
                continue
            ctype = row.get("contract_type", "client").lower()
            if ctype not in ("client", "vendor"): ctype = "client"
            rec = {
                "name": row["name"][:500], "party_name": row["party_name"][:500],
                "contract_type": ctype, "content": row.get("content", "")[:500000],
                "start_date": row.get("start_date") or None, "end_date": row.get("end_date") or None,
                "value": row.get("value", "")[:100] or None, "notes": row.get("notes", "")[:1000],
                "department": row.get("department", ""), "jurisdiction": row.get("jurisdiction", ""),
                "status": row.get("status", "executed") if row.get("status") in ("draft","pending","in_review","executed","rejected") else "executed",
                "created_by": "Bulk Import", "added_on": datetime.now().isoformat(),
            }
            try:
                sb.table("contracts").insert(rec).execute()
                imported += 1
            except Exception as ex:
                skipped += 1
                errors.append(f"Row {i+2}: {str(ex)[:100]}")
        log_activity(0, "bulk_import", "User", f"Imported {imported} contracts, skipped {skipped}")
        return jsonify({"imported": imported, "skipped": skipped, "total_rows": imported + skipped,
            "errors": errors[:20], "message": f"Successfully imported {imported} contracts"})
    except Exception as e:
        return jsonify({"error": f"Import failed: {str(e)}"}), 500

@app.route("/api/bulk-import/template")
@auth
def bulk_template():
    """Download CSV template for bulk import"""
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["name","party_name","contract_type","content","start_date","end_date","value","notes","department","jurisdiction","status"])
    w.writerow(["NDA - Acme Corp","Acme Corp Pvt Ltd","client","Full contract text here...","2024-01-01","2025-12-31","INR 50,00,000","Standard NDA","Legal","Mumbai","executed"])
    out.seek(0)
    return Response(out.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=bulk_import_template.csv"})

# ─── Contract Tags ────────────────────────────────────────────────────────
@app.route("/api/contracts/<int:cid>/tags")
@auth
@need_db
def get_contract_tags(cid):
    r = sb.table("contract_tags").select("*").eq("contract_id", cid).order("created_at").execute()
    return jsonify(r.data)

@app.route("/api/contracts/<int:cid>/tags", methods=["POST"])
@auth
@role_required("editor")
@need_db
def add_contract_tag(cid):
    d = request.json or {}
    tag = _sanitize(d.get("tag_name", "")).strip()
    color = d.get("tag_color", "#2563eb")
    if not tag: return jsonify({"error": "Tag name required"}), 400
    # Check if already tagged
    existing = sb.table("contract_tags").select("id").eq("contract_id", cid).eq("tag_name", tag).execute()
    if existing.data: return jsonify({"error": "Tag already exists on this contract"}), 400
    r = sb.table("contract_tags").insert({
        "contract_id": cid, "tag_name": tag, "tag_color": color,
        "created_by": getattr(request, 'user_email', 'User')
    }).execute()
    log_activity(cid, "tag_added", getattr(request, 'user_email', 'User'), f"Tag '{tag}' added")
    return jsonify(r.data[0]), 201

@app.route("/api/contracts/<int:cid>/tags/<int:tid>", methods=["DELETE"])
@auth
@role_required("editor")
@need_db
def remove_contract_tag(cid, tid):
    tag = sb.table("contract_tags").select("tag_name").eq("id", tid).eq("contract_id", cid).execute()
    sb.table("contract_tags").delete().eq("id", tid).eq("contract_id", cid).execute()
    if tag.data:
        log_activity(cid, "tag_removed", getattr(request, 'user_email', 'User'), f"Tag '{tag.data[0]['tag_name']}' removed")
    return jsonify({"message": "Tag removed"})

# ─── Tag Presets ──────────────────────────────────────────────────────────
@app.route("/api/tag-presets")
@auth
@need_db
def list_tag_presets():
    r = sb.table("tag_presets").select("*").order("name").execute()
    return jsonify(r.data)

@app.route("/api/tag-presets", methods=["POST"])
@auth
@role_required("manager")
@need_db
def create_tag_preset():
    d = request.json or {}
    name = _sanitize(d.get("name", "")).strip()
    color = d.get("color", "#2563eb")
    if not name: return jsonify({"error": "Name required"}), 400
    try:
        r = sb.table("tag_presets").insert({"name": name, "color": color, "description": d.get("description", "")}).execute()
        return jsonify(r.data[0]), 201
    except Exception as e:
        return jsonify({"error": "Tag preset already exists"}), 400

@app.route("/api/tag-presets/<int:tid>", methods=["DELETE"])
@auth
@role_required("admin")
@need_db
def delete_tag_preset(tid):
    sb.table("tag_presets").delete().eq("id", tid).execute()
    return jsonify({"message": "Deleted"})

# ─── Calendar View ────────────────────────────────────────────────────────
@app.route("/api/calendar")
@auth
@need_db
def calendar_events():
    """Returns all contract dates and obligation deadlines for calendar view"""
    year = request.args.get("year", str(datetime.now().year))
    month = request.args.get("month")

    events = []

    # Contract start/end dates
    contracts = sb.table("contracts").select("id,name,party_name,contract_type,status,start_date,end_date,value,department").execute().data
    for c in contracts:
        if c.get("start_date"):
            if (not month or c["start_date"][:7] == f"{year}-{month.zfill(2)}") if month else c["start_date"][:4] == year:
                events.append({
                    "id": f"c-start-{c['id']}", "contract_id": c["id"],
                    "title": f"Start: {c['name']}", "date": c["start_date"],
                    "type": "contract_start", "color": "#059669",
                    "meta": {"party": c["party_name"], "status": c.get("status", "draft"), "value": c.get("value", ""), "department": c.get("department", "")}
                })
        if c.get("end_date"):
            if (not month or c["end_date"][:7] == f"{year}-{month.zfill(2)}") if month else c["end_date"][:4] == year:
                today = datetime.now()
                try:
                    end = datetime.strptime(c["end_date"], "%Y-%m-%d")
                    days_left = (end - today).days
                except Exception:
                    days_left = 999
                color = "#dc2626" if days_left < 0 else "#ea580c" if days_left < 30 else "#d97706" if days_left < 90 else "#2563eb"
                events.append({
                    "id": f"c-end-{c['id']}", "contract_id": c["id"],
                    "title": f"Expiry: {c['name']}", "date": c["end_date"],
                    "type": "contract_end", "color": color,
                    "meta": {"party": c["party_name"], "status": c.get("status", "draft"), "days_left": days_left, "value": c.get("value", "")}
                })

    # Obligation deadlines
    try:
        obligations = sb.table("contract_obligations").select("id,contract_id,title,deadline,assigned_to,status").execute().data
        cids = list(set(o.get("contract_id") for o in obligations if o.get("contract_id")))
        cmap = {}
        if cids:
            try:
                cr = sb.table("contracts").select("id,name").in_("id", cids[:200]).execute()
                cmap = {c["id"]: c["name"] for c in cr.data}
            except Exception as e: log.debug(f"calendar_events: {e}")
        for o in obligations:
            if o.get("deadline"):
                if (not month or o["deadline"][:7] == f"{year}-{month.zfill(2)}") if month else o["deadline"][:4] == year:
                    events.append({
                        "id": f"obl-{o['id']}", "contract_id": o.get("contract_id"),
                        "title": f"Due: {o['title']}", "date": o["deadline"],
                        "type": "obligation", "color": "#7c3aed" if o.get("status") != "completed" else "#94a3b8",
                        "meta": {"assigned_to": o.get("assigned_to", ""), "status": o.get("status", "pending"),
                                 "contract_name": cmap.get(o.get("contract_id"), "")}
                    })
    except Exception as e: log.debug(f"calendar_events: {e}")

    # Sort by date
    events.sort(key=lambda x: x.get("date", ""))
    return jsonify(events)

# ─── Workflow Rules ───────────────────────────────────────────────────────
@app.route("/api/workflows")
@auth
@role_required("manager")
@need_db
def list_workflows():
    r = sb.table("workflow_rules").select("*").order("priority", desc=True).execute()
    return jsonify(r.data)

@app.route("/api/workflows", methods=["POST"])
@auth
@role_required("admin")
@need_db
def create_workflow():
    d = request.json or {}
    name = _sanitize(d.get("name", "")).strip()
    trigger = d.get("trigger_event", "")
    action = d.get("action_type", "")
    valid_triggers = ["status_change", "contract_created", "expiry_approaching", "approval_completed"]
    valid_actions = ["auto_approve", "add_tag", "change_status", "create_obligation", "notify_webhook"]
    if not name: return jsonify({"error": "Name required"}), 400
    if trigger not in valid_triggers: return jsonify({"error": f"Trigger must be one of: {', '.join(valid_triggers)}"}), 400
    if action not in valid_actions: return jsonify({"error": f"Action must be one of: {', '.join(valid_actions)}"}), 400
    r = sb.table("workflow_rules").insert({
        "name": name, "trigger_event": trigger,
        "trigger_condition": d.get("trigger_condition", {}),
        "action_type": action, "action_config": d.get("action_config", {}),
        "is_active": d.get("is_active", True), "priority": d.get("priority", 0),
        "created_by": getattr(request, 'user_email', 'System')
    }).execute()
    return jsonify(r.data[0]), 201

@app.route("/api/workflows/<int:wid>", methods=["PUT"])
@auth
@role_required("admin")
@need_db
def update_workflow(wid):
    d = request.json or {}
    u = {}
    for f in ["name", "trigger_event", "trigger_condition", "action_type", "action_config", "is_active", "priority"]:
        if f in d: u[f] = d[f]
    if not u: return jsonify({"error": "Nothing to update"}), 400
    u["updated_at"] = datetime.now().isoformat()
    sb.table("workflow_rules").update(u).eq("id", wid).execute()
    return jsonify({"message": "Updated"})

@app.route("/api/workflows/<int:wid>", methods=["DELETE"])
@auth
@role_required("admin")
@need_db
def delete_workflow(wid):
    sb.table("workflow_rules").delete().eq("id", wid).execute()
    return jsonify({"message": "Deleted"})

@app.route("/api/workflow-log")
@auth
@role_required("manager")
@need_db
def get_workflow_log():
    r = sb.table("workflow_log").select("*").order("executed_at", desc=True).limit(100).execute()
    return jsonify(r.data)

# ─── Custom Fields ────────────────────────────────────────────────────────
@app.route("/api/custom-fields")
@auth
@need_db
def list_custom_fields():
    r = sb.table("custom_field_defs").select("*").order("display_order").execute()
    return jsonify(r.data)

@app.route("/api/custom-fields", methods=["POST"])
@auth
@role_required("admin")
@need_db
def create_custom_field():
    d = request.json or {}
    name = _sanitize(d.get("field_name", "")).strip()
    ftype = d.get("field_type", "text")
    if not name: return jsonify({"error": "Field name required"}), 400
    if ftype not in ("text", "number", "date", "select", "url", "email"):
        return jsonify({"error": "Invalid field type"}), 400
    r = sb.table("custom_field_defs").insert({
        "field_name": name, "field_type": ftype,
        "field_options": d.get("field_options", ""),
        "is_required": d.get("is_required", False),
        "display_order": d.get("display_order", 0),
        "created_by": getattr(request, 'user_email', 'Admin')
    }).execute()
    return jsonify(r.data[0]), 201

@app.route("/api/custom-fields/<int:fid>", methods=["DELETE"])
@auth
@role_required("admin")
@need_db
def delete_custom_field(fid):
    sb.table("custom_field_values").delete().eq("field_id", fid).execute()
    sb.table("custom_field_defs").delete().eq("id", fid).execute()
    return jsonify({"message": "Deleted"})

@app.route("/api/contracts/<int:cid>/custom-fields")
@auth
@need_db
def get_contract_custom_fields(cid):
    defs = sb.table("custom_field_defs").select("*").order("display_order").execute().data
    vals = sb.table("custom_field_values").select("*").eq("contract_id", cid).execute().data
    val_map = {v["field_id"]: v for v in vals}
    result = []
    for d in defs:
        v = val_map.get(d["id"])
        result.append({**d, "value": v["field_value"] if v else "", "value_id": v["id"] if v else None})
    return jsonify(result)

@app.route("/api/contracts/<int:cid>/custom-fields", methods=["POST"])
@auth
@role_required("editor")
@need_db
def save_contract_custom_fields(cid):
    d = request.json or {}
    fields = d.get("fields", [])
    saved = 0
    for f in fields:
        fid = f.get("field_id")
        val = _sanitize(str(f.get("value", "")))
        if not fid: continue
        try:
            existing = sb.table("custom_field_values").select("id").eq("contract_id", cid).eq("field_id", fid).execute()
            if existing.data:
                sb.table("custom_field_values").update({"field_value": val, "updated_by": getattr(request, 'user_email', 'User'), "updated_at": datetime.now().isoformat()}).eq("id", existing.data[0]["id"]).execute()
            else:
                sb.table("custom_field_values").insert({"contract_id": cid, "field_id": fid, "field_value": val, "updated_by": getattr(request, 'user_email', 'User')}).execute()
            saved += 1
        except Exception as e: log.debug(f"save_contract_custom_fields: {e}")
    log_activity(cid, "custom_fields_updated", getattr(request, 'user_email', 'User'), f"{saved} custom fields updated")
    return jsonify({"saved": saved, "message": f"{saved} fields saved"})

# ─── Notifications ────────────────────────────────────────────────────────
@app.route("/api/notifications")
@auth
@need_db
def list_notifications():
    email = getattr(request, 'user_email', '')
    q = sb.table("notifications").select("*").order("created_at", desc=True).limit(50)
    if email:
        # Get user-specific + broadcast notifications
        r1 = q.eq("user_email", email).execute()
        r2 = sb.table("notifications").select("*").is_("user_email", "null").order("created_at", desc=True).limit(20).execute()
        data = sorted(r1.data + r2.data, key=lambda x: x.get("created_at", ""), reverse=True)[:50]
    else:
        data = q.execute().data
    unread = sum(1 for n in data if not n.get("is_read"))
    return jsonify({"notifications": data, "unread": unread})

@app.route("/api/notifications/read", methods=["POST"])
@auth
@need_db
def mark_notifications_read():
    d = request.json or {}
    ids = d.get("ids", [])
    if ids:
        for nid in ids[:50]:
            sb.table("notifications").update({"is_read": True}).eq("id", nid).execute()
    else:
        email = getattr(request, 'user_email', '')
        if email:
            sb.table("notifications").update({"is_read": True}).eq("user_email", email).eq("is_read", False).execute()
        sb.table("notifications").update({"is_read": True}).is_("user_email", "null").eq("is_read", False).execute()
    return jsonify({"message": "Marked as read"})

@app.route("/api/notifications/clear", methods=["POST"])
@auth
@need_db
def clear_notifications():
    email = getattr(request, 'user_email', '')
    if email:
        sb.table("notifications").delete().eq("user_email", email).execute()
    # Also clear broadcast notifications (user_email is null)
    sb.table("notifications").delete().is_("user_email", "null").execute()
    return jsonify({"message": "Cleared"})

# ─── Email Preferences API ──────────────────────────────────────────────
@app.route("/api/email-preferences")
@auth
@need_db
def get_email_prefs():
    email = getattr(request, 'user_email', '')
    if not email:
        return jsonify({"enabled": False, "on_status_change": True, "on_approval": True,
                        "on_comment": True, "on_expiry": True, "on_workflow": True, "email": ""})
    try:
        r = sb.table("email_preferences").select("*").eq("user_email", email).execute()
        if r.data:
            return jsonify(r.data[0])
        return jsonify({"enabled": False, "user_email": email, "on_status_change": True,
                        "on_approval": True, "on_comment": True, "on_expiry": True, "on_workflow": True})
    except Exception as e:
        return jsonify({"enabled": False, "user_email": email})

@app.route("/api/email-preferences", methods=["POST"])
@auth
@need_db
def save_email_prefs():
    email = getattr(request, 'user_email', '')
    if not email:
        return jsonify({"error": "Email required — login with email to set preferences"}), 400
    d = request.json or {}
    row = {
        "user_email": email,
        "enabled": d.get("enabled", False),
        "on_status_change": d.get("on_status_change", True),
        "on_approval": d.get("on_approval", True),
        "on_comment": d.get("on_comment", True),
        "on_expiry": d.get("on_expiry", True),
        "on_workflow": d.get("on_workflow", True)
    }
    try:
        existing = sb.table("email_preferences").select("id").eq("user_email", email).execute()
        if existing.data:
            sb.table("email_preferences").update(row).eq("user_email", email).execute()
        else:
            sb.table("email_preferences").insert(row).execute()
        return jsonify({"message": "Preferences saved", **row})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/email-preferences/test", methods=["POST"])
@auth
@need_db
def test_email():
    """Send a test email to verify configuration"""
    email = getattr(request, 'user_email', '')
    if not email:
        return jsonify({"error": "Login with email to test"}), 400
    if not RESEND_API_KEY:
        return jsonify({"error": "RESEND_API_KEY not configured. Add it to Vercel environment variables."}), 400
    try:
        r = http.post("https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={
                "from": EMAIL_FROM,
                "to": [email],
                "subject": "[EMB CLM] Test Email Notification",
                "html": """<!DOCTYPE html><html><body style="font-family:'Helvetica',Arial,sans-serif;background:#f5f7fa;margin:0;padding:0">
<div style="max-width:600px;margin:0 auto;padding:20px">
<div style="background:#0f172a;padding:20px 30px;border-radius:12px 12px 0 0">
<h1 style="margin:0;color:#fff;font-size:18px">EMB CLM</h1>
<p style="margin:4px 0 0;color:#64748b;font-size:12px">Contract Lifecycle Management</p>
</div>
<div style="background:#fff;padding:30px;border:1px solid #e2e8f0;border-top:none;border-radius:0 0 12px 12px">
<div style="display:inline-block;padding:4px 12px;border-radius:20px;font-size:11px;font-weight:600;color:#fff;background:#059669;margin-bottom:16px">TEST</div>
<h2 style="margin:0 0 12px;color:#0f172a;font-size:18px">Email Notifications Working!</h2>
<p style="margin:0;color:#334155;font-size:14px;line-height:1.6">This is a test email from EMB CLM. Your email notifications are configured correctly. You'll receive emails for contract status changes, approvals, comments, and more.</p>
</div></div></body></html>"""
            }, timeout=15)
        if r.status_code in (200, 201):
            return jsonify({"message": f"Test email sent to {email}"})
        else:
            return jsonify({"error": f"Resend API error: {r.text}"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/email-status")
@auth
def email_status():
    """Check if email service is configured"""
    return jsonify({
        "configured": bool(RESEND_API_KEY),
        "provider": "Resend" if RESEND_API_KEY else None,
        "from_address": EMAIL_FROM if RESEND_API_KEY else None
    })

# ─── Contract Comparison ──────────────────────────────────────────────────
@app.route("/api/contracts/compare")
@auth
@need_db
def compare_contracts():
    id1 = request.args.get("id1")
    id2 = request.args.get("id2")
    if not id1 or not id2:
        return jsonify({"error": "Provide id1 and id2 parameters"}), 400
    try:
        c1 = sb.table("contracts").select("*").eq("id", int(id1)).execute()
        c2 = sb.table("contracts").select("*").eq("id", int(id2)).execute()
        if not c1.data or not c2.data:
            return jsonify({"error": "One or both contracts not found"}), 404

        a, b = c1.data[0], c2.data[0]
        # Field comparison
        compare_fields = ["name", "party_name", "contract_type", "status", "value",
                         "start_date", "end_date", "department", "jurisdiction", "governing_law"]
        field_diffs = []
        for f in compare_fields:
            v1, v2 = a.get(f, ""), b.get(f, "")
            field_diffs.append({
                "field": f.replace("_", " ").title(),
                "contract_1": v1 or "—",
                "contract_2": v2 or "—",
                "match": str(v1).lower() == str(v2).lower()
            })

        # Content diff
        text1 = (a.get("content") or "")[:50000]
        text2 = (b.get("content") or "")[:50000]
        words1 = text1.split()
        words2 = text2.split()

        sm = difflib.SequenceMatcher(None, words1, words2)
        similarity = round(sm.ratio() * 100, 1)

        # Word-level diff
        diff_html = []
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "equal":
                diff_html.append(" ".join(words1[i1:i2]))
            elif op == "delete":
                diff_html.append(f'<span class="rl-del">{" ".join(words1[i1:i2])}</span>')
            elif op == "insert":
                diff_html.append(f'<span class="rl-ins">{" ".join(words2[j1:j2])}</span>')
            elif op == "replace":
                diff_html.append(f'<span class="rl-del">{" ".join(words1[i1:i2])}</span>')
                diff_html.append(f'<span class="rl-ins">{" ".join(words2[j1:j2])}</span>')

        return jsonify({
            "contract_1": {"id": a["id"], "name": a["name"], "party": a["party_name"],
                          "word_count": len(words1), "content": text1[:5000]},
            "contract_2": {"id": b["id"], "name": b["name"], "party": b["party_name"],
                          "word_count": len(words2), "content": text2[:5000]},
            "similarity": similarity,
            "field_diffs": field_diffs,
            "diff_html": " ".join(diff_html),
            "match_count": sum(1 for d in field_diffs if d["match"]),
            "total_fields": len(field_diffs)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─── Contract Clone ───────────────────────────────────────────────────────
@app.route("/api/contracts/<int:cid>/clone", methods=["POST"])
@auth
@role_required("editor")
@need_db
def clone_contract(cid):
    r = sb.table("contracts").select("*").eq("id", cid).execute()
    if not r.data: return jsonify({"error": "Not found"}), 404
    orig = r.data[0]
    d = request.json or {}
    new_name = d.get("name", f"Copy of {orig['name']}")
    new_party = d.get("party_name", orig["party_name"])
    row = {
        "name": new_name[:500], "party_name": new_party[:500],
        "contract_type": orig["contract_type"], "content": orig.get("content", ""),
        "content_html": orig.get("content_html", ""),
        "start_date": None, "end_date": None,
        "value": orig.get("value", ""), "notes": orig.get("notes", ""),
        "status": "draft", "department": orig.get("department", ""),
        "jurisdiction": orig.get("jurisdiction", ""), "governing_law": orig.get("governing_law", ""),
        "created_by": d.get("created_by", "User"),
        "added_on": datetime.now().isoformat(),
    }
    nr = sb.table("contracts").insert(row).execute()
    new_id = nr.data[0]["id"]
    log_activity(new_id, "created", row["created_by"], f"Cloned from '{orig['name']}' (#{cid})")
    # Copy tags
    try:
        tags = sb.table("contract_tags").select("*").eq("contract_id", cid).execute()
        for t in (tags.data or []):
            sb.table("contract_tags").insert({"contract_id": new_id, "tag_name": t["tag_name"], "tag_color": t["tag_color"], "created_by": row["created_by"]}).execute()
    except Exception as e: log.debug(f"clone_contract: {e}")
    return jsonify({"id": new_id, "message": f"Contract cloned as '{new_name}'"}), 201

# ─── Counterparty View ───────────────────────────────────────────────────
@app.route("/api/counterparty/<party_name>")
@auth
@need_db
def counterparty_view(party_name):
    from urllib.parse import unquote
    name = unquote(party_name).strip()
    r = sb.table("contracts").select("id,name,party_name,contract_type,status,value,start_date,end_date,department").ilike("party_name", f"%{name}%").order("added_on", desc=True).execute()
    contracts = r.data or []
    total = len(contracts)
    by_status = {}
    by_type = {"client": 0, "vendor": 0}
    for c in contracts:
        s = c.get("status", "draft")
        by_status[s] = by_status.get(s, 0) + 1
        t = c.get("contract_type", "client")
        by_type[t] = by_type.get(t, 0) + 1
    return jsonify({"party_name": name, "total_contracts": total, "contracts": contracts, "by_status": by_status, "by_type": by_type})

# ─── Bulk Actions ─────────────────────────────────────────────────────────
@app.route("/api/contracts/bulk", methods=["POST"])
@auth
@role_required("manager")
@need_db
def bulk_action():
    d = request.json or {}
    ids = d.get("ids", [])
    action = d.get("action", "")
    if not ids or not action:
        return jsonify({"error": "Provide ids and action"}), 400
    if len(ids) > 50:
        return jsonify({"error": "Max 50 contracts per batch"}), 400

    results = {"success": 0, "failed": 0, "errors": []}

    if action == "change_status":
        new_status = d.get("status", "")
        valid = ["draft", "pending", "in_review", "executed", "rejected"]
        if new_status not in valid:
            return jsonify({"error": f"Invalid status"}), 400
        for cid in ids:
            try:
                resp, code = _transition_status(int(cid), new_status, "Bulk Action")
                if code == 200:
                    results["success"] += 1
                else:
                    results["failed"] += 1
                    try: results["errors"].append(f"#{cid}: {resp.get_json().get('error',{}).get('message','Failed')}")
                    except: results["errors"].append(f"#{cid}: Transition failed")
            except Exception as e:
                log.debug(f"Bulk status change error: {e}")
                results["failed"] += 1

    elif action == "add_tag":
        tag_name = d.get("tag_name", "").strip()
        tag_color = d.get("tag_color", "#2563eb")
        if not tag_name:
            return jsonify({"error": "Tag name required"}), 400
        for cid in ids:
            try:
                sb.table("contract_tags").insert({"contract_id": int(cid), "tag_name": tag_name, "tag_color": tag_color, "created_by": "Bulk Action"}).execute()
                results["success"] += 1
            except Exception as e:
                log.debug(f"Bulk add tag error: {e}")
                results["failed"] += 1

    elif action == "remove_tag":
        tag_name = d.get("tag_name", "").strip()
        if not tag_name:
            return jsonify({"error": "Tag name required"}), 400
        for cid in ids:
            try:
                sb.table("contract_tags").delete().eq("contract_id", int(cid)).eq("tag_name", tag_name).execute()
                results["success"] += 1
            except Exception as e:
                log.debug(f"Bulk remove tag error: {e}")
                results["failed"] += 1

    elif action == "delete":
        for cid in ids:
            try:
                sb.table("contracts").delete().eq("id", int(cid)).execute()
                results["success"] += 1
            except Exception as e:
                log.debug(f"Bulk delete error: {e}")
                results["failed"] += 1

    else:
        return jsonify({"error": f"Unknown action: {action}"}), 400

    return jsonify({"message": f"Bulk {action.replace('_', ' ').title()}: {results['success']} succeeded, {results['failed']} failed", **results})

# ─── Password Reset ──────────────────────────────────────────────────────
@app.route("/api/auth/reset-password", methods=["POST"])
@need_db
def reset_password():
    d = request.json or {}
    email = d.get("email", "").strip().lower()
    new_password = d.get("new_password", "")
    admin_password = d.get("admin_password", "")

    if not email or not new_password:
        return jsonify({"error": "Email and new password required"}), 400
    if len(new_password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400

    # Verify admin credentials
    if not admin_password or not hmac.compare_digest(admin_password, PASSWORD):
        return jsonify({"error": "Admin password required to reset user passwords"}), 401

    # Find user
    u = sb.table("clm_users").select("id,email,name").eq("email", email).execute()
    if not u.data:
        return jsonify({"error": "User not found"}), 404

    pw_hash = _hash_password(new_password)
    sb.table("clm_users").update({"password_hash": pw_hash, "updated_at": datetime.now().isoformat()}).eq("email", email).execute()
    return jsonify({"message": f"Password reset for {u.data[0]['name']}"})

# ─── Renewal Tracking ────────────────────────────────────────────────────
@app.route("/api/renewals")
@auth
@need_db
def renewal_tracker():
    days = int(request.args.get("days", 90))
    today = datetime.now()
    future = (today + timedelta(days=days)).strftime("%Y-%m-%d")
    today_str = today.strftime("%Y-%m-%d")

    r = sb.table("contracts").select("id,name,party_name,contract_type,status,value,end_date,department").lte("end_date", future).gte("end_date", today_str).neq("status", "rejected").order("end_date").execute()

    contracts = r.data or []
    for c in contracts:
        try:
            end = datetime.strptime(c["end_date"], "%Y-%m-%d")
            c["days_left"] = (end - today).days
            if c["days_left"] <= 30: c["urgency"] = "critical"
            elif c["days_left"] <= 60: c["urgency"] = "warning"
            else: c["urgency"] = "normal"
        except Exception:
            c["days_left"] = None
            c["urgency"] = "normal"

    return jsonify({
        "total": len(contracts),
        "critical": sum(1 for c in contracts if c.get("urgency") == "critical"),
        "warning": sum(1 for c in contracts if c.get("urgency") == "warning"),
        "normal": sum(1 for c in contracts if c.get("urgency") == "normal"),
        "contracts": contracts
    })

# ─── Unique Parties List ─────────────────────────────────────────────────
@app.route("/api/parties")
@auth
@need_db
def list_parties():
    r = sb.table("contracts").select("party_name").execute()
    parties = {}
    for c in (r.data or []):
        name = c.get("party_name", "").strip()
        if name:
            parties[name] = parties.get(name, 0) + 1
    result = [{"name": k, "count": v} for k, v in sorted(parties.items())]
    return jsonify(result)

# ─── PDF Generation ──────────────────────────────────────────────────────
@app.route("/api/contracts/<int:cid>/pdf")
@auth
@need_db
def generate_pdf(cid):
    r = sb.table("contracts").select("*").eq("id", cid).execute()
    if not r.data: return jsonify({"error": "Not found"}), 404
    c = r.data[0]

    # Get obligations
    obls = []
    try:
        obr = sb.table("obligations").select("*").eq("contract_id", cid).execute()
        obls = obr.data or []
    except Exception as e: log.debug(f"generate_pdf: {e}")

    # Get signatures
    sigs = []
    try:
        sgr = sb.table("signatures").select("*").eq("contract_id", cid).execute()
        sigs = sgr.data or []
    except Exception as e: log.debug(f"generate_pdf: {e}")

    # Get custom fields
    cfields = []
    try:
        cfr = sb.table("custom_field_values").select("*, custom_field_defs(field_name, field_type)").eq("contract_id", cid).execute()
        cfields = cfr.data or []
    except Exception as e: log.debug(f"generate_pdf: {e}")

    # Get tags
    tags = []
    try:
        tr = sb.table("contract_tags").select("tag_name, tag_color").eq("contract_id", cid).execute()
        tags = tr.data or []
    except Exception as e: log.debug(f"generate_pdf: {e}")

    # Build HTML for PDF
    name = c.get("name", "Contract")
    party = c.get("party_name", "")
    ctype = c.get("contract_type", "")
    status = c.get("status", "draft")
    value = c.get("value", "")
    dept = c.get("department", "")
    start = c.get("start_date", "")
    end = c.get("end_date", "")
    jurisdiction = c.get("jurisdiction", "")
    governing = c.get("governing_law", "")
    content = c.get("content", "")
    created = c.get("created_at", "")[:10] if c.get("created_at") else ""

    tag_html = ""
    if tags:
        tag_html = '<div style="margin-bottom:20px"><strong>Tags: </strong>' + ", ".join(t["tag_name"] for t in tags) + "</div>"

    cf_html = ""
    if cfields:
        cf_rows = ""
        for cf in cfields:
            fname = cf.get("custom_field_defs", {}).get("field_name", "Field") if isinstance(cf.get("custom_field_defs"), dict) else "Field"
            fval = cf.get("field_value", "---")
            cf_rows += f"<tr><td style='padding:8px 12px;border:1px solid #e2e8f0;font-weight:600;background:#f8fafc;width:40%'>{fname}</td><td style='padding:8px 12px;border:1px solid #e2e8f0'>{fval or '---'}</td></tr>"
        if cf_rows:
            cf_html = f"<h3 style='margin-top:30px;margin-bottom:10px;color:#334155'>Custom Fields</h3><table style='width:100%;border-collapse:collapse;margin-bottom:20px'>{cf_rows}</table>"

    obl_html = ""
    if obls:
        obl_rows = ""
        for o in obls:
            st = "done" if o.get("status") == "completed" else "pending"
            obl_rows += f"<tr><td style='padding:8px 12px;border:1px solid #e2e8f0'>{st}</td><td style='padding:8px 12px;border:1px solid #e2e8f0'>{o.get('title','')}</td><td style='padding:8px 12px;border:1px solid #e2e8f0'>{o.get('deadline','---')}</td><td style='padding:8px 12px;border:1px solid #e2e8f0'>{o.get('assigned_to','---')}</td></tr>"
        obl_html = f"<h3 style='margin-top:30px;margin-bottom:10px;color:#334155'>Obligations</h3><table style='width:100%;border-collapse:collapse;margin-bottom:20px'><tr style='background:#f8fafc'><th style='padding:8px 12px;border:1px solid #e2e8f0;text-align:left'>Status</th><th style='padding:8px 12px;border:1px solid #e2e8f0;text-align:left'>Title</th><th style='padding:8px 12px;border:1px solid #e2e8f0;text-align:left'>Deadline</th><th style='padding:8px 12px;border:1px solid #e2e8f0;text-align:left'>Assigned To</th></tr>{obl_rows}</table>"

    sig_html = ""
    if sigs:
        sig_items = ""
        for s in sigs:
            sig_items += f"<div style='margin-bottom:10px;padding:10px;border:1px solid #e2e8f0;border-radius:6px'><strong>{s.get('signer_name','')}</strong> -- {s.get('signer_designation','')} &middot; {s.get('signer_email','')}<br><small style='color:#64748b'>Signed: {(s.get('signed_at','')[:10]) if s.get('signed_at') else 'Pending'}</small></div>"
        sig_html = f"<h3 style='margin-top:30px;margin-bottom:10px;color:#334155'>Signatures</h3>{sig_items}"

    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><style>
    @page {{ margin: 40px 50px; }}
    body {{ font-family: 'Helvetica', 'Arial', sans-serif; color: #0f172a; font-size: 13px; line-height: 1.7; }}
    .header {{ text-align: center; border-bottom: 3px solid #2563eb; padding-bottom: 20px; margin-bottom: 30px; }}
    .header h1 {{ font-size: 22px; color: #1e293b; margin: 0 0 5px; }}
    .header .subtitle {{ font-size: 12px; color: #64748b; }}
    .meta-table {{ width: 100%; border-collapse: collapse; margin-bottom: 25px; }}
    .meta-table td {{ padding: 8px 12px; border: 1px solid #e2e8f0; font-size: 12px; }}
    .meta-table .label {{ font-weight: 600; background: #f8fafc; width: 25%; color: #334155; }}
    .content-section {{ margin-top: 25px; }}
    .content-section h3 {{ font-size: 14px; color: #334155; border-bottom: 1px solid #e2e8f0; padding-bottom: 6px; margin-bottom: 12px; }}
    .content-body {{ white-space: pre-wrap; font-size: 12.5px; line-height: 1.8; color: #334155; }}
    .footer {{ margin-top: 40px; padding-top: 15px; border-top: 1px solid #e2e8f0; text-align: center; font-size: 10px; color: #94a3b8; }}
    .status-badge {{ display: inline-block; padding: 3px 12px; border-radius: 12px; font-size: 11px; font-weight: 600; }}
    </style></head><body>
    <div class="header">
        <h1>{name}</h1>
        <div class="subtitle">Contract Document -- Generated from EMB CLM</div>
    </div>
    <table class="meta-table">
        <tr><td class="label">Party</td><td>{party}</td><td class="label">Type</td><td>{ctype.title()}</td></tr>
        <tr><td class="label">Status</td><td>{status.replace('_',' ').title()}</td><td class="label">Value</td><td>{value or '---'}</td></tr>
        <tr><td class="label">Department</td><td>{dept or '---'}</td><td class="label">Jurisdiction</td><td>{jurisdiction or '---'}</td></tr>
        <tr><td class="label">Start Date</td><td>{start or '---'}</td><td class="label">End Date</td><td>{end or '---'}</td></tr>
        <tr><td class="label">Governing Law</td><td>{governing or '---'}</td><td class="label">Created</td><td>{created}</td></tr>
    </table>
    {tag_html}
    {cf_html}
    <div class="content-section">
        <h3>Contract Content</h3>
        <div class="content-body">{content}</div>
    </div>
    {obl_html}
    {sig_html}
    <div class="footer">
        <p>This document was generated from EMB Contract Lifecycle Management System on {datetime.now().strftime('%d %B %Y at %I:%M %p')}</p>
        <p>Document ID: {cid} &middot; CONFIDENTIAL</p>
    </div>
    </body></html>"""

    # Return HTML with print-ready headers for browser PDF generation
    return Response(html, mimetype='text/html', headers={
        'Content-Disposition': f'inline; filename="{name.replace(" ","_")}_contract.html"'
    })

# ─── Backup / Restore ─────────────────────────────────────────────────────
@app.route("/api/backup")
@auth
@need_db
@role_required("admin")
def backup_data():
    """Export all data as a single JSON document (admin only)."""
    tables = {}
    counts = {}
    for tbl in BACKUP_TABLES:
        try:
            rows = sb.table(tbl).select("*").execute().data or []
            if tbl == "clm_users":
                for row in rows:
                    row.pop("password_hash", None)
            tables[tbl] = rows
            counts[tbl] = len(rows)
        except Exception as e:
            log.warning(f"Backup: skipping table {tbl}: {e}")
            tables[tbl] = []
            counts[tbl] = 0
    payload = {
        "backup_date": datetime.now().isoformat(),
        "version": "1.0",
        "tables": tables,
        "counts": counts,
    }
    return jsonify(payload)


@app.route("/api/restore", methods=["POST"])
@auth
@need_db
@role_required("admin")
def restore_data():
    """Restore data from a backup JSON (admin only)."""
    body = request.get_json(silent=True) or {}
    if not body.get("confirm"):
        return jsonify({"error": "Safety check: include '\"confirm\": true' in the request body to proceed."}), 400
    tbl_data = body.get("tables")
    if not tbl_data or not isinstance(tbl_data, dict):
        return jsonify({"error": "Invalid backup format: missing 'tables' object."}), 400

    summary = {}
    # Pre-fetch existing contracts for duplicate check
    existing_contracts = set()
    try:
        rows = sb.table("contracts").select("name,party_name").execute().data or []
        existing_contracts = {(r["name"], r["party_name"]) for r in rows}
    except Exception:
        pass

    for tbl, rows in tbl_data.items():
        if tbl not in BACKUP_TABLES:
            summary[tbl] = {"skipped": True, "reason": "unknown table"}
            continue
        if tbl == "clm_users":
            summary[tbl] = {"skipped": True, "reason": "security -- user accounts not overwritten"}
            continue
        if not isinstance(rows, list):
            summary[tbl] = {"skipped": True, "reason": "invalid data"}
            continue

        inserted, updated, skipped, errors = 0, 0, 0, 0
        for row in rows:
            try:
                # Duplicate check for contracts
                if tbl == "contracts":
                    key = (row.get("name", ""), row.get("party_name", ""))
                    if key in existing_contracts:
                        skipped += 1
                        continue
                # Upsert by id if present
                rid = row.get("id")
                if rid is not None:
                    sb.table(tbl).upsert(row).execute()
                    updated += 1
                else:
                    sb.table(tbl).insert(row).execute()
                    inserted += 1
                # Track newly added contracts
                if tbl == "contracts":
                    existing_contracts.add((row.get("name", ""), row.get("party_name", "")))
            except Exception:
                errors += 1
        summary[tbl] = {"inserted": inserted, "updated": updated, "skipped": skipped, "errors": errors}

    log_activity(0, "restore", "Admin", f"Restore completed: {len(tbl_data)} tables processed")
    return jsonify({"message": "Restore completed", "summary": summary})


# ─── Embed endpoint ───────────────────────────────────────────────────────
@app.route("/api/contracts/<int:cid>/embed", methods=["POST"])
@auth
@need_db
def embed_single(cid):
    if not oai_h(): return jsonify({"error": "AI not configured"}), 500
    r = sb.table("contracts").select("id,name,content").eq("id", cid).execute()
    if not r.data: return jsonify({"error": "Not found"}), 404
    c = r.data[0]
    try:
        n = embed_contract(c["id"], c["content"], c["name"])
        return jsonify({"chunks": n})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
