"""Contract Lifecycle Management API — Full CLM with AI review, approvals, signatures, webhooks."""

import os, sys, re, io, csv, json as J, time, hmac, hashlib, logging, difflib, secrets, html as html_mod
import bcrypt
from datetime import datetime, timedelta
from functools import wraps
import requests as http
from flask import Flask, request, jsonify, Response, send_from_directory
from supabase import create_client
import fitz

# Ensure api/ directory is in path for module imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def _escape_like(s):
    """Escape SQL LIKE/ILIKE wildcards in user input."""
    return s.replace("%", "\\%").replace("_", "\\_")

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
    _hmac_sign, make_token, check_token,
    auth, role_required, need_db,
)
from ai import (
    oai_h, oai_chat, oai_stream, oai_emb,
    chunk_text, embed_contract, hybrid_search, build_prompt,
    ocr_pdf_pages, classify_query, generate_followups,
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

@app.errorhandler(415)
def unsupported_media(e):
    return jsonify({"error": {"message": "Content-Type must be application/json", "code": 415}}), 415

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
                    return jsonify({"token": make_token(email), "user": {"id": user["id"], "name": user["name"], "email": user["email"], "role": user["role"], "department": user.get("department","")}})
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

@app.route("/api/auth/verify")
def verify():
    if not PASSWORD: return jsonify({"valid": False, "auth_enabled": True, "error": "APP_PASSWORD not configured"}), 503
    h = request.headers.get("Authorization", "")
    if h.startswith("Bearer "):
        valid, email = check_token(h[7:])
        if valid:
            return jsonify({"valid": True, "auth_enabled": True})
    return jsonify({"valid": False, "auth_enabled": True}), 401

@app.route("/api/auth/refresh", methods=["POST"])
def refresh_token():
    h = request.headers.get("Authorization", "")
    if not h.startswith("Bearer "):
        return err("Auth required", 401)
    valid, email = check_token(h[7:])
    if not valid:
        return err("Invalid or expired token", 401)
    return jsonify({"token": make_token(email)})

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
    contracts = sb.table("contracts").select("id,status,contract_type,end_date,value").limit(5000).execute().data
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

@app.route("/api/executive-dashboard")
@auth
@need_db
def executive_dashboard():
    """Executive-level dashboard with TCV, at-risk, renewals, pending approvals"""
    contracts = sb.table("contracts").select("id,name,party_name,contract_type,status,start_date,end_date,value,department").limit(5000).execute().data or []
    today = datetime.now()
    # Total contract value
    total_client_value = 0; total_vendor_value = 0
    by_dept = {}; at_risk = []; renewals_30 = []; renewals_60 = []; renewals_90 = []
    for c in contracts:
        cv = _parse_currency(c.get("value", ""))
        dept = c.get("department", "Unassigned") or "Unassigned"
        if dept not in by_dept: by_dept[dept] = {"revenue": 0, "cost": 0, "count": 0}
        by_dept[dept]["count"] += 1
        if c.get("contract_type") == "client":
            total_client_value += cv; by_dept[dept]["revenue"] += cv
        else:
            total_vendor_value += cv; by_dept[dept]["cost"] += cv
        if c.get("end_date"):
            try:
                days = (datetime.strptime(c["end_date"], "%Y-%m-%d") - today).days
                if days < 0: at_risk.append({**c, "days_left": days, "risk": "Expired"})
                elif days <= 30: renewals_30.append({**c, "days_left": days})
                elif days <= 60: renewals_60.append({**c, "days_left": days})
                elif days <= 90: renewals_90.append({**c, "days_left": days})
            except (ValueError, TypeError): pass
    # Pending approvals
    approvals = sb.table("contract_approvals").select("id,contract_id,approver_name,created_at").eq("status", "pending").execute().data or []
    # Overdue obligations
    overdue_obs = sb.table("contract_obligations").select("id,contract_id,title,deadline").eq("status", "pending").lt("deadline", today.strftime("%Y-%m-%d")).execute().data or []
    at_risk_cids = {r["id"] for r in at_risk}
    for o in overdue_obs:
        cid = o.get("contract_id")
        if cid and cid not in at_risk_cids:
            # Find the contract in our list and add to at_risk
            match = next((c for c in contracts if c["id"] == cid), None)
            if match:
                at_risk.append({**match, "days_left": None, "risk": "Overdue Obligation"})
                at_risk_cids.add(cid)
    # Department summary
    dept_summary = [{"department": k, **v, "margin": v["revenue"] - v["cost"]} for k, v in sorted(by_dept.items(), key=lambda x: -x[1]["revenue"])]
    return jsonify({
        "tcv": total_client_value + total_vendor_value,
        "total_client_value": total_client_value, "total_vendor_value": total_vendor_value,
        "net_margin": total_client_value - total_vendor_value,
        "total_contracts": len(contracts),
        "at_risk_count": len(at_risk), "at_risk": at_risk[:10],
        "renewals_30": len(renewals_30), "renewals_60": len(renewals_60), "renewals_90": len(renewals_90),
        "renewal_contracts": (renewals_30 + renewals_60 + renewals_90)[:15],
        "pending_approvals": len(approvals), "approval_list": approvals[:10],
        "overdue_obligations": len(overdue_obs),
        "departments": dept_summary
    })

@app.route("/api/counterparty-risk", methods=["GET"])
@auth
@need_db
def counterparty_risk_aggregation():
    """Aggregate exposure across all contracts with each counterparty"""
    contracts = sb.table("contracts").select("id,name,party_name,contract_type,status,value,end_date,department").limit(5000).execute().data or []
    today = datetime.now()
    parties = {}
    for c in contracts:
        pn = c.get("party_name", "Unknown")
        if pn not in parties:
            parties[pn] = {"party_name": pn, "contracts": [], "total_value": 0,
                          "client_value": 0, "vendor_value": 0, "contract_count": 0,
                          "active_count": 0, "expiring_count": 0, "expired_count": 0, "risk_score": 0}
        p = parties[pn]
        cv = _parse_currency(c.get("value", ""))
        p["contracts"].append({"id": c["id"], "name": c["name"], "type": c["contract_type"],
                               "status": c["status"], "value": c.get("value", ""), "end_date": c.get("end_date")})
        p["total_value"] += cv; p["contract_count"] += 1
        if c["contract_type"] == "client": p["client_value"] += cv
        else: p["vendor_value"] += cv
        if c.get("status") in ("executed", "pending", "in_review"): p["active_count"] += 1
        if c.get("end_date"):
            try:
                days = (datetime.strptime(c["end_date"], "%Y-%m-%d") - today).days
                if days < 0: p["expired_count"] += 1; p["risk_score"] += 3
                elif days <= 30: p["expiring_count"] += 1; p["risk_score"] += 2
                elif days <= 60: p["expiring_count"] += 1; p["risk_score"] += 1
            except (ValueError, TypeError): pass
    result = sorted(parties.values(), key=lambda x: -x["total_value"])
    # Limit contracts in response to top 5 per party
    for p in result: p["contracts"] = p["contracts"][:5]
    return jsonify({"parties": result, "total_parties": len(result)})

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
    if not r.data: return err("Not found", 404)
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
        return err("Not found", 404)
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
        return err("Not found", 404)
    sb.table("contract_templates").delete().eq("id", tid).execute()
    return jsonify({"message": "Template deleted"})

# ─── Contracts CRUD ────────────────────────────────────────────────────────
@app.route("/api/contracts", methods=["GET"])
@auth
@need_db
def list_contracts():
    ctype = request.args.get("type")
    status = request.args.get("status")
    try: page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError): page = 1
    try: per = min(50, max(1, int(request.args.get("per_page", 20))))
    except (ValueError, TypeError): per = 20
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
        if not str(d.get(f) or "").strip(): return err(f"Missing: {f}", 400)
    if d["contract_type"] not in ("client", "vendor"):
        return err("Type must be client or vendor", 400)
    row = {
        "name": d["name"][:500], "party_name": d["party_name"][:500],
        "contract_type": d["contract_type"], "content": d["content"][:500000],
        "content_html": d.get("content_html", ""),
        "start_date": d.get("start_date") or None, "end_date": d.get("end_date") or None,
        "value": str(d.get("value", "") or "")[:100] or None, "notes": str(d.get("notes", "") or "")[:1000],
        "status": "draft", "department": d.get("department", ""),
        "jurisdiction": d.get("jurisdiction", ""), "governing_law": d.get("governing_law", ""),
        "template_id": d.get("template_id"), "created_by": d.get("created_by", "User"),
        "added_on": datetime.now().isoformat(),
    }
    r = sb.table("contracts").insert(row).execute()
    if not r.data: return err("Failed to create contract", 500)
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
    if not r.data: return err("Not found", 404)
    return jsonify(r.data[0])

@app.route("/api/contracts/<int:cid>", methods=["PUT"])
@auth
@role_required("editor")
@need_db
def update_contract(cid):
    chk = sb.table("contracts").select("id,content,content_html,name,status,updated_at").eq("id", cid).execute()
    if not chk.data: return err("Not found", 404)
    if chk.data[0].get("status") == "executed":
        return err("Executed contracts cannot be modified. Create an amendment instead.", 400)
    d = _sanitize_dict(request.json or {})
    # Optimistic locking
    if d.get("updated_at"):
        db_updated = chk.data[0].get("updated_at", "")
        if db_updated and d["updated_at"] != db_updated:
            return jsonify({"error": {"message": "This contract was modified by another user. Please reload and try again.", "code": 409}}), 409
    if "contract_type" in d and d["contract_type"] not in ("client", "vendor"):
        return err("contract_type must be 'client' or 'vendor'", 400)
    u = {}
    for f in ["name","party_name","contract_type","start_date","end_date","value","notes","content",
              "content_html","department","jurisdiction","governing_law"]:
        if f in d: u[f] = d[f]
    if not u: return err("Nothing to update", 400)
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
    if not chk.data: return err("Not found", 404)
    # Cascade delete all related records
    related_tables = [
        "contract_activity", "contract_comments", "contract_approvals",
        "contract_obligations", "contract_signatures", "contract_collaborators",
        "contract_versions", "contract_chunks", "contract_tags",
        "contract_links", "contract_parties", "share_links",
        "custom_field_values", "invoices",
    ]
    for table in related_tables:
        try:
            sb.table(table).delete().eq("contract_id", cid).execute()
        except Exception:
            pass  # Table may not exist in all deployments
    # Also delete reverse links (where this contract is the target)
    try:
        sb.table("contract_links").delete().eq("target_id", cid).execute()
    except Exception:
        pass
    sb.table("contracts").delete().eq("id", cid).execute()
    log_activity(None, "contract_deleted", request.user_email, f"Contract #{cid} deleted with all related data")
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
    if not oai_h(): return err("AI not configured", 500)
    r = sb.table("contracts").select("content,name,contract_type,party_name,value,start_date,end_date").eq("id", cid).execute()
    if not r.data: return err("Not found", 404)
    c = r.data[0]
    contract_type = c.get("contract_type", "client")

    # Type-specific review criteria
    type_context = ""
    if contract_type == "vendor":
        type_context = """
VENDOR CONTRACT PRIORITIES:
- Ensure EMB has right to audit vendor performance
- Check for adequate SLA commitments with measurable KPIs
- Verify vendor liability is not capped too low
- Look for adequate IP assignment to EMB/client
- Check subcontracting restrictions
- Ensure adequate insurance coverage from vendor
- Look for performance guarantees and penalty clauses"""
    else:
        type_context = """
CLIENT CONTRACT PRIORITIES:
- Ensure payment terms protect EMB cash flow (Net 30 preferred)
- Check scope is clearly defined to prevent scope creep
- Verify limitation of liability protects EMB (capped at contract value)
- Look for reasonable acceptance/sign-off criteria
- Check change request process is defined
- Ensure EMB retains IP for reusable components
- Look for auto-renewal terms favourable to EMB"""

    try:
        reply = oai_chat([
            {"role": "system", "content": f"""You are a senior contract review AI for EMB (Expand My Business), a technology services broker in India.
EMB operates as a broker: clients pay EMB, EMB pays vendors. Margin = client value - vendor cost.

Analyze this {contract_type} contract clause by clause against Indian business law and IT industry best practices.
{type_context}

For each key clause found (or missing), return a JSON object with this EXACT structure:
{{
  "clauses": [
    {{
      "clause_name": "Clause Name",
      "status": "aligned|partially_aligned|not_aligned|missing",
      "criteria": "What best practice requires (be specific)",
      "review": "Your detailed finding with exact quotes from the contract",
      "recommendation": "Specific actionable change to make",
      "risk_level": "low|medium|high|critical",
      "section_ref": "Exact section number (e.g., '4.2') or 'N/A' if missing",
      "priority": 1
    }}
  ],
  "overall_risk_score": "low|medium|high|critical",
  "executive_summary": "2-3 sentence summary of the contract's overall health",
  "top_actions": ["Action 1", "Action 2", "Action 3"]
}}

MUST analyze these clauses (minimum 15):
1. Confidentiality/NDA  2. Payment Terms & Schedule  3. Termination Rights
4. Intellectual Property  5. Indemnification  6. Limitation of Liability
7. Governing Law & Jurisdiction  8. Force Majeure  9. Non-Compete/Non-Solicitation
10. Data Protection/Privacy  11. Dispute Resolution/Arbitration  12. Insurance
13. Compliance with Laws  14. Assignment & Subcontracting  15. Warranty/SLA
16. Scope of Work (if applicable)  17. Change Management  18. Acceptance Criteria

RISK LEVELS:
- critical: Missing essential clause OR terms that could cause significant financial/legal harm
- high: Clause present but significantly below market standard
- medium: Clause needs improvement but workable
- low: Clause meets or exceeds standard practice

Set "priority" 1-5 (1=most urgent fix needed, 5=minor/informational).
Return ONLY valid JSON, no markdown wrapping."""},
            {"role": "user", "content": f"Contract: {c['name']}\nParty: {c.get('party_name','N/A')}\nType: {contract_type}\nValue: {c.get('value','N/A')}\nPeriod: {c.get('start_date','N/A')} to {c.get('end_date','N/A')}\n\n{c['content'][:15000]}"}
        ], model="gpt-4o", max_tok=4096, temperature=0.2)
        reply = reply.strip()
        if reply.startswith("```"): reply = reply.split("\n",1)[1].rsplit("```",1)[0].strip()
        parsed = J.loads(reply)

        # Handle both old format (array) and new format (object with clauses key)
        if isinstance(parsed, list):
            review = parsed
            overall_risk = "medium"
            executive_summary = ""
            top_actions = []
        else:
            review = parsed.get("clauses", parsed.get("review", []))
            overall_risk = parsed.get("overall_risk_score", "medium")
            executive_summary = parsed.get("executive_summary", "")
            top_actions = parsed.get("top_actions", [])

        # Sort by priority (most urgent first)
        review.sort(key=lambda x: x.get("priority", 3))

        aligned = sum(1 for r in review if r.get("status") == "aligned")
        partial = sum(1 for r in review if r.get("status") == "partially_aligned")
        not_aligned = sum(1 for r in review if r.get("status") in ("not_aligned", "missing"))
        critical = sum(1 for r in review if r.get("risk_level") == "critical")
        high = sum(1 for r in review if r.get("risk_level") == "high")

        log_activity(cid, "ai_review", "AI",
            f"Review: {aligned} aligned, {partial} partial, {not_aligned} issues ({critical} critical, {high} high risk)")
        return jsonify({
            "review": review,
            "summary": {"aligned": aligned, "partial": partial, "issues": not_aligned,
                         "critical": critical, "high_risk": high},
            "overall_risk": overall_risk,
            "executive_summary": executive_summary,
            "top_actions": top_actions[:5]
        })
    except Exception as e:
        log.error(f"Internal error: {e}")
        return err("Internal server error", 500)

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
    if not d.get("content"): return err("Content required", 400)
    row = {"contract_id": cid, "user_name": str(d.get("user_name") or "User")[:200],
           "content": str(d["content"])[:2000], "clause_ref": str(d.get("clause_ref") or "")[:200],
           "created_at": datetime.now().isoformat()}
    r = sb.table("contract_comments").insert(row).execute()
    log_activity(cid, "comment_added", row["user_name"], str(d["content"])[:100])
    create_notification(f"New Comment on Contract #{cid}", str(d["content"])[:100], "comment", cid)
    return jsonify(r.data[0] if r.data else {"message": "Created"}), 201

# ─── Obligations ───────────────────────────────────────────────────────────
@app.route("/api/contracts/<int:cid>/obligations", methods=["GET"])
@auth
@need_db
def list_obligations(cid):
    r = sb.table("contract_obligations").select("*").eq("contract_id", cid).order("deadline").limit(100).execute()
    return jsonify(r.data)

@app.route("/api/contracts/<int:cid>/obligations", methods=["POST"])
@auth
@role_required("editor")
@need_db
def add_obligation(cid):
    d = request.json or {}
    if not d.get("title"): return err("Title required", 400)
    row = {"contract_id": cid, "title": str(d["title"])[:500], "description": str(d.get("description") or "")[:2000],
           "deadline": d.get("deadline") or None, "status": "pending",
           "assigned_to": str(d.get("assigned_to") or "")[:200], "created_at": datetime.now().isoformat()}
    r = sb.table("contract_obligations").insert(row).execute()
    log_activity(cid, "obligation_added", "User", str(d["title"])[:100])
    return jsonify(r.data[0] if r.data else {"message": "Created"}), 201

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
    if "description" in d: u["description"] = d["description"]
    if "escalated" in d: u["escalated"] = d["escalated"]
    if "escalated_to" in d: u["escalated_to"] = d["escalated_to"]
    if not u: return err("Nothing to update", 400)
    sb.table("contract_obligations").update(u).eq("id", oid).execute()
    return jsonify({"message": "Updated"})

@app.route("/api/obligations/overdue", methods=["GET"])
@auth
@need_db
def get_overdue_obligations():
    """Get all overdue pending obligations"""
    today = datetime.now().strftime("%Y-%m-%d")
    rows = sb.table("contract_obligations").select("*").eq("status", "pending").lt("deadline", today).order("deadline").execute().data or []
    # Enrich with contract names
    cids = list(set(o["contract_id"] for o in rows if o.get("contract_id")))
    cmap = {}
    if cids:
        cdata = sb.table("contracts").select("id,name,party_name,department").in_("id", cids).execute().data or []
        cmap = {c["id"]: c for c in cdata}
    for o in rows:
        c = cmap.get(o.get("contract_id"), {})
        o["contract_name"] = c.get("name", "Unknown")
        o["party_name"] = c.get("party_name", "")
        o["department"] = c.get("department", "")
        days_overdue = (datetime.now() - datetime.strptime(o["deadline"], "%Y-%m-%d")).days if o.get("deadline") else 0
        o["days_overdue"] = days_overdue
    return jsonify(rows)

@app.route("/api/obligations/escalate", methods=["POST"])
@auth
@role_required("manager")
@need_db
def escalate_obligations():
    """Escalate overdue obligations — create notifications and mark as escalated"""
    d = request.json or {}
    escalate_to = d.get("escalate_to", "").strip()
    obligation_ids = d.get("obligation_ids", [])
    if not obligation_ids: return err("No obligations selected", 400)
    escalated = 0
    for oid in obligation_ids:
        ob = sb.table("contract_obligations").select("*").eq("id", oid).execute()
        if not ob.data: continue
        o = ob.data[0]
        sb.table("contract_obligations").update({
            "escalated": True, "escalated_to": escalate_to or "manager",
            "escalated_at": datetime.now().isoformat()
        }).eq("id", oid).execute()
        # Get contract name for notification
        cname = ""
        if o.get("contract_id"):
            cn = sb.table("contracts").select("name").eq("id", o["contract_id"]).execute()
            cname = cn.data[0]["name"] if cn.data else ""
        create_notification(
            f"Escalated: {o['title']}",
            f"Overdue obligation on '{cname}' has been escalated. Deadline was {o.get('deadline', 'N/A')}. Assigned to: {o.get('assigned_to', 'Unassigned')}",
            "warning", o.get("contract_id"), escalate_to or None
        )
        log_activity(o.get("contract_id"), "obligation_escalated", request.user_email,
                     f"Obligation '{o['title']}' escalated to {escalate_to or 'manager'}")
        escalated += 1
    return jsonify({"message": f"{escalated} obligation(s) escalated", "escalated": escalated})

@app.route("/api/obligations/auto-escalate", methods=["POST"])
@auth
@role_required("admin")
@need_db
def auto_escalate_obligations():
    """Auto-escalate obligations overdue by more than X days"""
    d = request.json or {}
    try: threshold_days = int(d.get("threshold_days", 3))
    except (ValueError, TypeError): threshold_days = 3
    escalate_to = d.get("escalate_to", "manager")
    cutoff = (datetime.now() - timedelta(days=threshold_days)).strftime("%Y-%m-%d")
    rows = sb.table("contract_obligations").select("*").eq("status", "pending").lt("deadline", cutoff).execute().data or []
    # Filter out already escalated
    to_escalate = [o for o in rows if not o.get("escalated")]
    escalated = 0
    for o in to_escalate:
        sb.table("contract_obligations").update({
            "escalated": True, "escalated_to": escalate_to,
            "escalated_at": datetime.now().isoformat()
        }).eq("id", o["id"]).execute()
        cname = ""
        if o.get("contract_id"):
            cn = sb.table("contracts").select("name").eq("id", o["contract_id"]).execute()
            cname = cn.data[0]["name"] if cn.data else ""
        create_notification(
            f"Auto-Escalated: {o['title']}",
            f"Obligation on '{cname}' is {threshold_days}+ days overdue. Deadline: {o.get('deadline', 'N/A')}",
            "warning", o.get("contract_id"), escalate_to
        )
        escalated += 1
    return jsonify({"message": f"{escalated} obligation(s) auto-escalated", "escalated": escalated, "total_overdue": len(rows)})

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
        return err("Contract not found", 404)
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
        return err("Not found", 404)
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
@role_required("editor")
@need_db
def request_approval(cid):
    d = request.json or {}
    if not d.get("approver_name"): return err("Approver name required", 400)
    row = {"contract_id": cid, "approver_name": d["approver_name"],
           "status": "pending", "comments": d.get("comments", ""),
           "created_at": datetime.now().isoformat(), "updated_at": datetime.now().isoformat()}
    # Check contract exists and validate status transition
    contract = sb.table("contracts").select("id,status").eq("id", cid).execute()
    if not contract.data: return err("Contract not found", 404)
    cur_status = contract.data[0].get("status", "draft")
    if cur_status not in ("draft", "in_review"):
        return err(f"Cannot request approval when contract is '{cur_status}'", 400)
    # Prevent duplicate pending approval from same approver
    existing = sb.table("contract_approvals").select("id").eq("contract_id", cid).eq("approver_name", d["approver_name"]).eq("status", "pending").execute()
    if existing.data:
        return err(f"Pending approval from {d['approver_name']} already exists", 409)
    r = sb.table("contract_approvals").insert(row).execute()
    log_activity(cid, "approval_requested", request.user_email, f"Approval requested from {d['approver_name']}")
    create_notification(f"Approval Requested", f"{d['approver_name']} needs to review Contract #{cid}", "approval", cid)
    sb.table("contracts").update({"status": "pending"}).eq("id", cid).execute()
    return jsonify(r.data[0]), 201

@app.route("/api/approvals/<int:aid>", methods=["PUT"])
@auth
@role_required("manager")
@need_db
def respond_approval(aid):
    d = request.json or {}
    action = d.get("action")
    if action not in ("approved", "rejected"): return err("Action must be approved or rejected", 400)
    appr = sb.table("contract_approvals").select("*").eq("id", aid).execute()
    if not appr.data: return err("Not found", 404)
    sb.table("contract_approvals").update({
        "status": action, "comments": d.get("comments", ""), "updated_at": datetime.now().isoformat()
    }).eq("id", aid).execute()
    cid = appr.data[0]["contract_id"]
    new_status = "in_review" if action == "approved" else "rejected"
    log_activity(cid, f"approval_{action}", appr.data[0]["approver_name"], d.get("comments", ""))
    fire_webhooks(f"contract.{action}", {"contract_id": cid})
    # Use state machine for status transition
    chk = sb.table("contracts").select("status").eq("id", cid).execute()
    old_status = chk.data[0]["status"] if chk.data else ""
    transition_applied = False
    if new_status in VALID_TRANSITIONS.get(old_status, set()):
        _transition_status(cid, new_status, appr.data[0]["approver_name"])
        transition_applied = True
    return jsonify({"message": f"Approval {action}", "status_updated": transition_applied})

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
        return err("Signer name and signature required", 400)
    # Validate contract status — only pending/in_review can be signed
    contract = sb.table("contracts").select("id,status").eq("id", cid).execute()
    if not contract.data: return err("Contract not found", 404)
    cur_status = contract.data[0].get("status", "draft")
    if cur_status not in ("pending", "in_review", "executed"):
        return err(f"Cannot sign a contract in '{cur_status}' status. Submit for approval first.", 400)
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
        return err("Leegality API not configured. Set LEEGALITY_API_KEY env var.", 503)
    c = sb.table("contracts").select("id,name,content,content_html,party_name").eq("id", cid).execute()
    if not c.data: return err("Not found", 404)
    contract = c.data[0]
    d = request.json or {}
    signers = d.get("signers", [])
    if not signers or not all(s.get("name") and s.get("email") for s in signers):
        return err("Signers required — each needs name and email", 400)
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
            return err(f"Leegality error: {err_resp.get('message', r.text)}", r.status_code)

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
        return err("Leegality API timeout", 504)
    except Exception as e:
        log.error(f"Leegality error: {e}")
        return err("Internal server error", 500)

@app.route("/api/leegality/webhook", methods=["POST"])
def leegality_webhook():
    """Receive Leegality webhook callbacks for signature status updates"""
    d = request.json or {}
    # Verify webhook MAC using Private Salt
    if not LEEGALITY_SALT:
        log.warning("Leegality webhook received but LEEGALITY_PRIVATE_SALT not configured")
        return err("Webhook not configured", 503)
    mac = d.get("mac", "")
    verify_data = {k: v for k, v in d.items() if k != "mac"}
    expected = hmac.new(LEEGALITY_SALT.encode(), J.dumps(verify_data, separators=(',', ':'), sort_keys=True).encode(), hashlib.sha256).hexdigest()
    if not mac or not hmac.compare_digest(mac, expected):
        log.warning("Leegality webhook MAC verification failed")
        return err("MAC verification failed", 403)
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
    try: limit = min(int(request.args.get("limit", 200)), 500)
    except (ValueError, TypeError): limit = 200
    r = sb.table("contract_activity").select("*").eq("contract_id", cid).order("created_at", desc=True).limit(limit).execute()
    return jsonify(r.data)

# ─── PDF Upload (single) ──────────────────────────────────────────────────
@app.route("/api/upload-pdf", methods=["POST"])
@auth
def upload_pdf():
    if "file" not in request.files: return err("No file", 400)
    f = request.files["file"]
    if not f.filename.lower().endswith(".pdf"): return err("PDF only", 400)
    try:
        b = f.read()
        if len(b) > 50*1024*1024: return err("Max 50MB per file", 400)
        if not b[:4] == b'%PDF':
            return err("Invalid PDF file", 400)
        doc = fitz.open(stream=b, filetype="pdf")
        pc = len(doc)
        txt = "".join(p.get_text() + "\n" for p in doc)
        doc.close()

        # If text extraction worked, return it (normal text-based PDF)
        if txt.strip():
            return jsonify({"content": txt.strip(), "pages": pc, "method": "text"})

        # Scanned/image PDF — use GPT-4o Vision OCR
        if not oai_h():
            return err("Scanned PDF detected but AI (OpenAI) is not configured for OCR", 400)
        log.info(f"Scanned PDF detected ({pc} pages), running OCR via GPT-4o Vision...")
        ocr_text, total_pages, ocr_pages = ocr_pdf_pages(b, max_pages=50)
        if not ocr_text.strip():
            return err("OCR could not extract text from this scanned PDF", 400)
        result = {"content": ocr_text.strip(), "pages": total_pages, "method": "ocr", "ocr_pages": ocr_pages}
        if total_pages > ocr_pages:
            result["warning"] = f"Only first {ocr_pages} of {total_pages} pages were OCR'd"
        return jsonify(result)
    except Exception as e:
        log.error(f"Internal error: {e}")
        return err("Internal server error", 500)

# ─── Bulk PDF Upload (multiple files → multiple contracts) ────────────────
@app.route("/api/upload-pdfs-bulk", methods=["POST"])
@auth
@role_required("editor")
@need_db
def upload_pdfs_bulk():
    """Process multiple PDFs at once. Each PDF becomes a separate contract via AI parse."""
    files = request.files.getlist("files")
    if not files or len(files) == 0:
        return err("No files uploaded", 400)
    if len(files) > 10:
        return err("Max 10 PDFs at a time", 400)

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
                except (Exception) as e:
                    log.debug(f"AI metadata extraction failed: {e}")

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
    q = _sanitize_html(request.args.get("q", "").strip(), max_len=200)
    if not q: return jsonify([])
    # Escape special PostgREST chars
    safe = q.replace("%", "").replace("*", "").replace(",", "").replace(".", " ").replace("_", "")
    t = f"%{safe}%"
    r = sb.table("contracts").select(
        "id,name,party_name,contract_type,status,start_date,end_date,value"
    ).or_(f"name.ilike.{t},party_name.ilike.{t}").limit(20).execute()
    return jsonify(r.data)

# ─── Parse (AI auto-fill) ─────────────────────────────────────────────────
@app.route("/api/parse", methods=["POST"])
@auth
def parse():
    if not oai_h(): return err("AI not configured", 500)
    d = request.json or {}
    content = d.get("content", "")
    if not content: return err("No content", 400)
    try:
        reply = oai_chat([
            {"role": "system", "content": """You are a contract metadata extraction engine. Extract key fields from the contract text.

Return ONLY valid JSON with these fields:
{
  "name": "Short descriptive contract name (e.g., 'Cloud Services MSA - Acme Corp')",
  "party_name": "The other party's company/person name (not EMB)",
  "contract_type": "client|vendor (client=they pay EMB, vendor=EMB pays them)",
  "start_date": "YYYY-MM-DD or null if not found",
  "end_date": "YYYY-MM-DD or null if not found",
  "value": "Currency + Amount (e.g., 'INR 25,00,000' or 'USD 50,000') or null",
  "notes": "One-line summary of what this contract covers",
  "department": "Likely department (Engineering/Sales/HR/Finance/Operations/Legal)",
  "jurisdiction": "City/State mentioned for disputes (e.g., 'Mumbai, Maharashtra')",
  "governing_law": "Country/State law governing the contract (e.g., 'India')"
}

RULES:
- For Indian amounts, preserve lakh/crore formatting (e.g., 'INR 25,00,000')
- If contract_type is ambiguous, check who provides services vs who pays
- Extract the most specific jurisdiction mentioned (city > state > country)
- For name, create a meaningful title, not just 'Agreement' or 'Contract'"""},
            {"role": "user", "content": content[:5000]}
        ], model="gpt-4o-mini", max_tok=500, temperature=0.1).strip()
        if reply.startswith("```"): reply = reply.split("\n",1)[1].rsplit("```",1)[0].strip()
        return jsonify(J.loads(reply))
    except Exception as e:
        log.error(f"Internal error: {e}")
        return err("Internal server error", 500)

# ─── Chat (RAG + Streaming) ───────────────────────────────────────────────
@app.route("/api/chat", methods=["POST"])
@auth
@need_db
def chat():
    if not oai_h(): return err("AI not configured", 500)
    d = request.json or {}
    msg = d.get("message", "").strip()
    history = d.get("history", [])[-20:]
    cids = d.get("contract_ids")
    if cids and isinstance(cids, list):
        cids = cids[:50]  # Cap to prevent DoS
    stream = d.get("stream", False)
    if not msg: return err("No message", 400)

    # Classify query for optimized retrieval and prompt
    query_types = classify_query(msg)

    chunks = []
    try: chunks = hybrid_search(msg, cids, 30)
    except Exception as e: log.debug(f"chat: {e}")

    ref_ids = list(set(c["contract_id"] for c in chunks)) if chunks else []
    meta = sb.table("contracts").select("id,name,party_name,contract_type,start_date,end_date,value").in_("id", ref_ids).execute().data if ref_ids else []
    ml = {c["id"]: c for c in meta}

    # For scoped queries (1-3 contracts): use full contract text as primary context
    # This ensures NO section is missed (annexures, payment terms, signatures, etc.)
    if cids and len(cids) <= 3:
        full_data = sb.table("contracts").select("id,name,party_name,contract_type,content,start_date,end_date,value,status").in_("id", cids).execute().data or []
        if full_data:
            # Limit per-contract text to avoid exceeding token limits (especially with 2-3 contracts)
            max_per = 120000 // max(len(full_data), 1)
            ctx_parts = []
            for c in full_data:
                header = f"=== CONTRACT: {c['name']} ({c['party_name']}) ==="
                meta_line = f"Type: {c.get('contract_type','N/A')} | Value: {c.get('value','N/A')} | Status: {c.get('status','N/A')} | Start: {c.get('start_date','N/A')} | End: {c.get('end_date','N/A')}"
                ctx_parts.append(f"{header}\n{meta_line}\n{(c.get('content') or '')[:max_per]}")
            ctx = "\n\n".join(ctx_parts)
            summ = "\n".join(f"- {c['name']} ({c['party_name']}, {c['contract_type']}, Value: {c.get('value','N/A')})" for c in full_data)
            meta = full_data
        elif chunks:
            parts = [f"[{ml.get(c['contract_id'],{}).get('name','?')} | {c.get('section_title','?')}]\n{c['chunk_text']}" for c in chunks]
            ctx = "\n---\n".join(parts)
            summ = "\n".join(f"- {c['name']} ({c['party_name']}, {c['contract_type']})" for c in meta)
        else:
            ctx = "No contracts found."
            summ = "None."
    elif chunks:
        parts = [f"[{ml.get(c['contract_id'],{}).get('name','?')} | {c.get('section_title','?')} | Relevance:{round(c.get('similarity',0)*100)}%]\n{c['chunk_text']}" for c in chunks]
        ctx = "\n---\n".join(parts)
        summ = "\n".join(f"- {c['name']} ({c['party_name']}, {c['contract_type']}, Value: {c.get('value','N/A')})" for c in meta)
    else:
        # Fallback: load contract summaries, not full text (prevents token overflow)
        q = sb.table("contracts").select("id,name,party_name,contract_type,content,value,status")
        if cids: q = q.in_("id", cids)
        r = q.limit(20).execute()
        if r.data:
            # Truncate each contract to stay within token budget
            budget_per = 100000 // max(len(r.data), 1)
            ctx = "\n\n".join(f"--- {c['name']} (Value: {c.get('value','N/A')}) ---\n{(c.get('content') or '')[:budget_per]}" for c in r.data)
            summ = "\n".join(f"- {c['name']} ({c['party_name']}, {c['contract_type']})" for c in r.data)
            meta = r.data
        else:
            ctx = "No contracts found in the system."
            summ = "None."

    # Fetch past learnings from feedback to improve response quality
    learnings = ""
    try:
        fb_query = sb.table("chat_feedback").select("query,response_snippet,rating").eq("rating", "down").order("created_at", desc=True).limit(10)
        if cids:
            fb_query = fb_query.contains("contract_ids", cids[:3])
        neg_fb = fb_query.execute().data or []
        if neg_fb:
            learnings = "\n\nPAST FEEDBACK (avoid these patterns):\n"
            for fb in neg_fb[:5]:
                learnings += f"- User asked: \"{fb['query'][:100]}\" — Response was rated poorly. Avoid similar approach.\n"

        # Also get positive patterns
        pos_query = sb.table("chat_feedback").select("query,response_snippet,rating").eq("rating", "up").order("created_at", desc=True).limit(10)
        if cids:
            pos_query = pos_query.contains("contract_ids", cids[:3])
        pos_fb = pos_query.execute().data or []
        if pos_fb:
            learnings += "\nPOSITIVE FEEDBACK (users liked this style):\n"
            for fb in pos_fb[:5]:
                learnings += f"- User asked: \"{fb['query'][:100]}\" — Response was well received.\n"
    except Exception:
        pass  # Learning context is optional — don't fail the request

    sys_prompt = build_prompt(summ, ctx, query_types, learnings)
    msgs = [{"role": "system", "content": sys_prompt}] + history + [{"role": "user", "content": msg}]
    sources = [{"id": c["id"], "name": c.get("name",""), "party": c.get("party_name","")} for c in meta]
    n_chunks = len(chunks)

    # Generate follow-up suggestions
    contract_names = [c.get("name","") for c in meta] if meta else []

    if stream:
        def gen():
            full_response = []
            try:
                for tok in oai_stream(msgs):
                    full_response.append(tok)
                    yield f"data: {J.dumps({'c': tok})}\n\n"
                # Generate follow-up suggestions based on response
                response_text = "".join(full_response)
                followups = generate_followups(msg, response_text, contract_names)
                yield f"data: {J.dumps({'done': True, 'sources': sources, 'chunks_used': n_chunks, 'followups': followups, 'query_types': query_types})}\n\n"
            except Exception as e:
                log.error(f"Chat stream error: {e}")
                yield f"data: {J.dumps({'error': 'AI service temporarily unavailable. Please try again.'})}\n\n"
        return Response(gen(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    try:
        reply = oai_chat(msgs)
        followups = generate_followups(msg, reply, contract_names)
        return jsonify({"reply": reply, "sources": sources, "chunks_used": n_chunks, "followups": followups, "query_types": query_types})
    except Exception as e:
        log.error(f"Internal error: {e}")
        return err("Internal server error", 500)

# ─── Chat Feedback (Learning) ────────────────────────────────────────────
@app.route("/api/chat/feedback", methods=["POST"])
@auth
@need_db
def chat_feedback():
    """Store thumbs up/down feedback on AI responses for learning."""
    d = request.json or {}
    query = _sanitize(d.get("query", ""))[:500]
    response_snippet = _sanitize(d.get("response_snippet", ""))[:1000]
    rating = d.get("rating", "")
    if rating not in ("up", "down"): return err("Rating must be 'up' or 'down'", 400)
    if not query: return err("Query is required", 400)
    try:
        row = {
            "user_email": request.user_email,
            "contract_ids": d.get("contract_ids", [])[:50],
            "query": query,
            "response_snippet": response_snippet,
            "rating": rating,
            "comment": _sanitize(d.get("comment", ""))[:500],
            "query_types": d.get("query_types", [])[:10],
        }
        sb.table("chat_feedback").insert(row).execute()
        return jsonify({"ok": True})
    except Exception as e:
        log.error(f"Chat feedback error: {e}")
        return err("Internal server error", 500)

@app.route("/api/chat/feedback/stats", methods=["GET"])
@auth
@need_db
def chat_feedback_stats():
    """Get feedback stats for admin dashboard."""
    try:
        all_fb = sb.table("chat_feedback").select("rating,query_types,created_at").execute().data or []
        total = len(all_fb)
        up = sum(1 for f in all_fb if f["rating"] == "up")
        down = total - up
        return jsonify({"total": total, "positive": up, "negative": down,
                        "satisfaction_rate": round(up / total * 100, 1) if total else 0})
    except Exception as e:
        log.error(f"Feedback stats error: {e}")
        return err("Internal server error", 500)

# ─── Chat Sessions (Persistence) ────────────────────────────────────────
@app.route("/api/chat/sessions", methods=["GET"])
@auth
@need_db
def list_chat_sessions():
    """List user's saved chat sessions."""
    try:
        sessions = sb.table("chat_sessions").select("id,scope_label,contract_ids,updated_at,messages") \
            .eq("user_email", request.user_email) \
            .order("updated_at", desc=True).limit(20).execute().data or []
        # Return message count instead of full messages for listing
        result = []
        for s in sessions:
            msgs = s.get("messages", [])
            preview = ""
            if msgs:
                first_user = next((m["content"] for m in msgs if m.get("role") == "user"), "")
                preview = first_user[:80]
            result.append({
                "id": s["id"],
                "scope_label": s.get("scope_label", "All Contracts"),
                "contract_ids": s.get("contract_ids", []),
                "message_count": len(msgs),
                "preview": preview,
                "updated_at": s["updated_at"],
            })
        return jsonify(result)
    except Exception as e:
        log.error(f"Chat sessions list error: {e}")
        return err("Internal server error", 500)

@app.route("/api/chat/sessions", methods=["POST"])
@auth
@need_db
def save_chat_session():
    """Save or update a chat session."""
    d = request.json or {}
    messages = d.get("messages", [])
    if not messages: return err("No messages to save", 400)
    # Sanitize messages — only keep role, content, sources
    clean_msgs = []
    for m in messages[:100]:  # Cap at 100 messages
        clean = {"role": m.get("role", "user"), "content": _sanitize(m.get("content", ""))[:10000]}
        if m.get("sources"):
            clean["sources"] = m["sources"][:20]
        clean_msgs.append(clean)

    session_id = d.get("session_id")
    scope_label = _sanitize(d.get("scope_label", "All Contracts"))[:200]
    contract_ids = d.get("contract_ids", [])[:50]

    try:
        if session_id:
            # Update existing
            sb.table("chat_sessions").update({
                "messages": clean_msgs,
                "scope_label": scope_label,
                "contract_ids": contract_ids,
                "updated_at": datetime.utcnow().isoformat(),
            }).eq("id", int(session_id)).eq("user_email", request.user_email).execute()
            return jsonify({"id": int(session_id), "ok": True})
        else:
            # Create new
            r = sb.table("chat_sessions").insert({
                "user_email": request.user_email,
                "messages": clean_msgs,
                "scope_label": scope_label,
                "contract_ids": contract_ids,
            }).execute()
            new_id = r.data[0]["id"] if r.data else None
            return jsonify({"id": new_id, "ok": True})
    except Exception as e:
        log.error(f"Chat session save error: {e}")
        return err("Internal server error", 500)

@app.route("/api/chat/sessions/<int:sid>", methods=["GET"])
@auth
@need_db
def get_chat_session(sid):
    """Load a specific chat session."""
    try:
        r = sb.table("chat_sessions").select("*").eq("id", sid).eq("user_email", request.user_email).execute()
        if not r.data: return err("Session not found", 404)
        return jsonify(r.data[0])
    except Exception as e:
        log.error(f"Chat session get error: {e}")
        return err("Internal server error", 500)

@app.route("/api/chat/sessions/<int:sid>", methods=["DELETE"])
@auth
@need_db
def delete_chat_session(sid):
    """Delete a chat session."""
    try:
        sb.table("chat_sessions").delete().eq("id", sid).eq("user_email", request.user_email).execute()
        return jsonify({"ok": True})
    except Exception as e:
        log.error(f"Chat session delete error: {e}")
        return err("Internal server error", 500)

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
        return err("URL and event_type required", 400)
    r = sb.table("webhook_configs").insert({
        "event_type": d["event_type"], "url": d["url"], "active": True,
        "created_at": datetime.now().isoformat()
    }).execute()
    return jsonify(r.data[0]), 201

@app.route("/api/webhooks/<int:wid>", methods=["DELETE"])
@auth
@role_required("admin")
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
    if not r.data: return err("Not found", 404)
    return jsonify(r.data[0])

@app.route("/api/contracts/<int:cid>/versions/<int:vid>/restore", methods=["POST"])
@auth
@need_db
def restore_version(cid, vid):
    v = sb.table("contract_versions").select("*").eq("id", vid).eq("contract_id", cid).execute()
    if not v.data: return err("Not found", 404)
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
    if not cur.data: return err("Not found", 404)
    current_text = cur.data[0].get("content", "")

    # Get comparison text
    if vid:
        try:
            vid = int(vid)
        except (ValueError, TypeError):
            return err("version_id must be an integer", 400)
        ver = sb.table("contract_versions").select("*").eq("id", vid).eq("contract_id", cid).execute()
        if not ver.data: return err("Version not found", 404)
        old_text = ver.data[0].get("content", "")
        old_label = f"Version {ver.data[0]['version_number']}"
    else:
        # Get latest version (previous content)
        vers = sb.table("contract_versions").select("*").eq("contract_id", cid).order("version_number", desc=True).limit(1).execute()
        if not vers.data:
            return err("No previous version available. Edit the contract at least once to see redline.", 404)
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

    result = {
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
    }

    # AI-powered change summary (if requested)
    if request.args.get("ai_summary") == "true" and oai_h() and (additions + deletions) > 0:
        try:
            # Build a concise diff for AI analysis
            changes = []
            for chunk in word_diff:
                if chunk["type"] != "equal":
                    changes.append(f"[{chunk['type'].upper()}]: {chunk['text'][:200]}")
            change_text = "\n".join(changes[:50])  # Cap to prevent token overflow

            ai_resp = oai_chat([
                {"role": "system", "content": """Analyze these contract changes and return JSON:
{
  "change_summary": "2-3 sentence plain-English summary of what changed",
  "impact_level": "low|medium|high|critical",
  "material_changes": ["List 2-4 material/significant changes"],
  "risk_implications": "Any new risks introduced by these changes (1-2 sentences, or 'No significant risk changes')",
  "action_needed": "What the contract manager should do about these changes (1 sentence)"
}"""},
                {"role": "user", "content": f"Contract: {cur.data[0]['name']}\nTotal additions: {additions}, deletions: {deletions}\n\nChanges:\n{change_text}"}
            ], model="gpt-4o-mini", max_tok=800, temperature=0.2)
            ai_resp = ai_resp.strip()
            if ai_resp.startswith("```"): ai_resp = ai_resp.split("\n",1)[1].rsplit("```",1)[0].strip()
            result["ai_change_summary"] = J.loads(ai_resp)
        except Exception as e:
            log.debug(f"Redline AI summary failed: {e}")
            result["ai_change_summary"] = None

    return jsonify(result)

@app.route("/api/contracts/<int:cid>/diff", methods=["GET"])
@auth
@need_db
def contract_diff(cid):
    """Compare two specific versions"""
    v1 = request.args.get("v1")
    v2 = request.args.get("v2")
    if not v1 or not v2:
        return err("v1 and v2 version IDs required", 400)
    try: v1, v2 = int(v1), int(v2)
    except (ValueError, TypeError): return err("v1 and v2 must be integers", 400)

    ver1 = sb.table("contract_versions").select("*").eq("id", v1).eq("contract_id", cid).execute()
    ver2 = sb.table("contract_versions").select("*").eq("id", int(v2)).eq("contract_id", cid).execute()

    if not ver1.data or not ver2.data:
        return err("Version not found", 404)

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
        return err("Title, category, and content required", 400)
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
    if not u: return err("Nothing to update", 400)
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
    c = sb.table("clause_library").select("usage_count").eq("id", cid).execute()
    if not c.data: return err("Clause not found", 404)
    sb.table("clause_library").update({"usage_count": (c.data[0].get("usage_count") or 0) + 1}).eq("id", cid).execute()
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
        if not d.get(f, "").strip(): return err(f"Missing: {f}", 400)
    email = d["email"].strip().lower()
    if not _valid_email(email):
        return err("Invalid email format", 400)
    # Check duplicate
    existing = sb.table("clm_users").select("id").eq("email", email).execute()
    if existing.data: return err("Email already exists", 409)
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
    new_id = r.data[0]["id"]

    # Send welcome email invite if Resend is configured
    email_sent = False
    email_error = None
    if RESEND_API_KEY:
        try:
            invite_html = f"""<!DOCTYPE html><html><body style="font-family:'Helvetica',Arial,sans-serif;background:#f5f7fa;margin:0;padding:0">
<div style="max-width:600px;margin:0 auto;padding:20px">
<div style="background:#0f172a;padding:20px 30px;border-radius:12px 12px 0 0">
<h1 style="margin:0;color:#fff;font-size:18px">EMB CLM</h1>
<p style="margin:4px 0 0;color:#64748b;font-size:12px">Contract Lifecycle Management</p>
</div>
<div style="background:#fff;padding:30px;border:1px solid #e2e8f0;border-top:none">
<div style="display:inline-block;padding:4px 12px;border-radius:20px;font-size:11px;font-weight:600;color:#fff;background:#2563eb;margin-bottom:16px">INVITE</div>
<h2 style="margin:0 0 12px;color:#0f172a;font-size:18px;font-weight:600">Welcome to EMB CLM!</h2>
<p style="margin:0 0 16px;color:#334155;font-size:14px;line-height:1.6">Hi <strong>{_sanitize(d['name'], 100)}</strong>, you have been invited to EMB CLM — Contract Lifecycle Management platform.</p>
<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:16px 20px;margin:0 0 20px">
<p style="margin:0 0 12px;font-weight:700;color:#0f172a;font-size:14px">Your Login Credentials</p>
<table style="border-collapse:collapse;width:100%;font-size:13px">
<tr><td style="padding:6px 0;color:#64748b;width:80px">Email</td><td style="padding:6px 0;font-family:monospace;color:#0f172a;font-weight:600">{email}</td></tr>
<tr><td style="padding:6px 0;color:#64748b">Password</td><td style="padding:6px 0;font-family:monospace;color:#0f172a;font-weight:600">{_sanitize(d['password'], 100)}</td></tr>
<tr><td style="padding:6px 0;color:#64748b">Role</td><td style="padding:6px 0;color:#0f172a;text-transform:capitalize">{role}</td></tr>
</table></div>
<p style="background:#fef2f2;color:#dc2626;padding:10px 16px;border-radius:6px;font-size:13px;font-weight:600;margin:0 0 20px">&#x26A0; Please change your password after your first login.</p>
<a href="https://contract-cli-six.vercel.app" style="display:inline-block;padding:10px 24px;background:#2563eb;color:#fff;border-radius:8px;text-decoration:none;font-size:13px;font-weight:600">Login to EMB CLM</a>
</div>
<div style="padding:16px 30px;background:#f8fafc;border:1px solid #e2e8f0;border-top:none;border-radius:0 0 12px 12px;text-align:center">
<p style="margin:0;color:#94a3b8;font-size:11px">EMB CLM &mdash; Expand My Business | Mantarav Private Limited</p>
</div></div></body></html>"""
            resp = http.post("https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
                json={"from": EMAIL_FROM, "to": [email],
                      "subject": f"Welcome to EMB CLM — Your login details inside",
                      "html": invite_html}, timeout=15)
            if resp.status_code in (200, 201):
                email_sent = True
                log.info(f"Welcome email sent to {email}")
            else:
                email_error = resp.json().get("message", resp.text[:200])
                log.warning(f"Resend rejected invite email for {email}: {resp.status_code} — {email_error}")
        except Exception as e:
            email_error = str(e)
            log.warning(f"Failed to send invite email to {email}: {e}")

    msg = f"User {email} created"
    if email_sent:
        msg += " — welcome email sent"
    elif RESEND_API_KEY:
        msg += f" — email failed: {email_error or 'unknown error'}"
    else:
        msg += " — no email (RESEND_API_KEY not configured)"

    return jsonify({"id": new_id, "message": msg, "email_sent": email_sent}), 201

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
    if not u: return err("Nothing to update", 400)
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

    elif rtype == "health":
        # Contract health score: based on completeness, obligations, expiry
        obligations = sb.table("contract_obligations").select("contract_id,status,deadline").execute().data or []
        ob_map = {}
        for o in obligations:
            cid = o.get("contract_id")
            if cid not in ob_map: ob_map[cid] = {"total": 0, "overdue": 0, "completed": 0}
            ob_map[cid]["total"] += 1
            if o.get("status") == "completed": ob_map[cid]["completed"] += 1
            elif o.get("deadline") and o["deadline"] < today.strftime("%Y-%m-%d"): ob_map[cid]["overdue"] += 1
        results = []
        for c in contracts:
            score = 100
            risks = []
            # Deduct for missing fields
            if not c.get("end_date"): score -= 10; risks.append("No end date")
            if not c.get("start_date"): score -= 5; risks.append("No start date")
            if not c.get("value"): score -= 5; risks.append("No value specified")
            if not c.get("department"): score -= 5; risks.append("No department")
            # Deduct for expiry proximity
            if c.get("end_date"):
                try:
                    days = (datetime.strptime(c["end_date"], "%Y-%m-%d") - today).days
                    if days < 0: score -= 25; risks.append(f"Expired {abs(days)}d ago")
                    elif days <= 30: score -= 15; risks.append(f"Expires in {days}d")
                    elif days <= 60: score -= 5; risks.append(f"Expires in {days}d")
                except (ValueError, TypeError): pass
            # Deduct for overdue obligations
            obs = ob_map.get(c["id"], {"total": 0, "overdue": 0, "completed": 0})
            if obs["overdue"] > 0: score -= min(obs["overdue"] * 10, 30); risks.append(f"{obs['overdue']} overdue obligations")
            # Status penalties
            if c.get("status") == "rejected": score -= 20; risks.append("Rejected")
            elif c.get("status") == "draft": score -= 5
            score = max(0, min(100, score))
            health = "healthy" if score >= 80 else "warning" if score >= 50 else "critical"
            results.append({**c, "health_score": score, "health": health, "risks": risks,
                           "obligations": obs})
        results.sort(key=lambda x: x["health_score"])
        # Summary
        healthy = sum(1 for r in results if r["health"] == "healthy")
        warning = sum(1 for r in results if r["health"] == "warning")
        critical = sum(1 for r in results if r["health"] == "critical")
        avg_score = round(sum(r["health_score"] for r in results) / max(len(results), 1))
        return jsonify({"contracts": results, "summary": {
            "healthy": healthy, "warning": warning, "critical": critical,
            "avg_score": avg_score, "total": len(results)}})

    elif rtype == "at_risk":
        # At-risk contracts: expired, expiring soon, overdue obligations
        obligations = sb.table("contract_obligations").select("contract_id,status,deadline,title").execute().data or []
        overdue_map = {}
        for o in obligations:
            if o.get("status") == "pending" and o.get("deadline") and o["deadline"] < today.strftime("%Y-%m-%d"):
                cid = o.get("contract_id")
                if cid not in overdue_map: overdue_map[cid] = []
                overdue_map[cid].append(o["title"])
        at_risk = []
        for c in contracts:
            risk_reasons = []
            risk_level = 0
            if c.get("end_date"):
                try:
                    days = (datetime.strptime(c["end_date"], "%Y-%m-%d") - today).days
                    if days < 0: risk_reasons.append(f"Expired {abs(days)} days ago"); risk_level += 3
                    elif days <= 30: risk_reasons.append(f"Expiring in {days} days"); risk_level += 2
                    elif days <= 60: risk_reasons.append(f"Expiring in {days} days"); risk_level += 1
                except (ValueError, TypeError): pass
            if c["id"] in overdue_map:
                risk_reasons.append(f"{len(overdue_map[c['id']])} overdue obligations")
                risk_level += 2
            if c.get("status") == "rejected": risk_reasons.append("Rejected"); risk_level += 1
            if risk_reasons:
                at_risk.append({**c, "risk_reasons": risk_reasons, "risk_level": risk_level})
        at_risk.sort(key=lambda x: -x["risk_level"])
        return jsonify({"contracts": at_risk, "total_at_risk": len(at_risk), "total_contracts": len(contracts)})

    elif rtype == "dept_spend":
        # Department-wise spend vs revenue analysis
        links = sb.table("contract_links").select("client_contract_id,vendor_contract_id").execute().data or []
        link_map = {}
        for l in links:
            cid = l["client_contract_id"]
            if cid not in link_map: link_map[cid] = []
            link_map[cid].append(l["vendor_contract_id"])
        vendor_map = {c["id"]: c for c in contracts if c.get("contract_type") == "vendor"}
        depts = {}
        for c in contracts:
            dept = c.get("department", "Unassigned") or "Unassigned"
            if dept not in depts: depts[dept] = {"department": dept, "revenue": 0, "cost": 0, "client_count": 0, "vendor_count": 0, "contracts": 0}
            depts[dept]["contracts"] += 1
            cv = _parse_currency(c.get("value", ""))
            if c.get("contract_type") == "client":
                depts[dept]["revenue"] += cv
                depts[dept]["client_count"] += 1
            else:
                depts[dept]["cost"] += cv
                depts[dept]["vendor_count"] += 1
        dept_list = []
        for d in sorted(depts.values(), key=lambda x: -x["revenue"]):
            d["margin"] = d["revenue"] - d["cost"]
            d["margin_pct"] = round((d["margin"] / d["revenue"] * 100), 1) if d["revenue"] > 0 else 0
            dept_list.append(d)
        total_rev = sum(d["revenue"] for d in dept_list)
        total_cost = sum(d["cost"] for d in dept_list)
        return jsonify({"departments": dept_list, "summary": {
            "total_revenue": total_rev, "total_cost": total_cost,
            "total_margin": total_rev - total_cost,
            "margin_pct": round(((total_rev - total_cost) / total_rev * 100), 1) if total_rev > 0 else 0
        }})

    return err("Unknown report type", 400)

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
    if action: q = q.ilike("action", f"%{_escape_like(action)}%")
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

@app.route("/api/audit-log/cleanup", methods=["POST"])
@auth
@role_required("admin")
@need_db
def audit_log_cleanup():
    """Delete audit log entries older than specified days (default 365)."""
    d = request.json or {}
    if not d.get("confirm"):
        return err("Include '\"confirm\": true' to proceed with cleanup.", 400)
    try: days = max(min(int(d.get("retention_days", 365)), 3650), 30)  # 30 days min, 10 years max
    except (ValueError, TypeError): days = 365
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    # Count before delete
    count_q = sb.table("contract_activity").select("id", count="exact").lt("created_at", cutoff).execute()
    count = count_q.count if hasattr(count_q, 'count') and count_q.count else len(count_q.data or [])
    if count == 0:
        return jsonify({"message": "No records older than the retention period", "deleted": 0, "retention_days": days})
    sb.table("contract_activity").delete().lt("created_at", cutoff).execute()
    log_activity(None, "audit_log_cleanup", request.user_email, f"Deleted {count} records older than {days} days")
    return jsonify({"message": f"Deleted {count} audit log entries older than {days} days", "deleted": count, "retention_days": days, "cutoff_date": cutoff[:10]})

# ─── Bulk Import ──────────────────────────────────────────────────────────
@app.route("/api/bulk-import", methods=["POST"])
@auth
@role_required("editor")
@need_db
def bulk_import():
    """Bulk import contracts from CSV."""
    if "file" not in request.files:
        return err("No file uploaded", 400)
    f = request.files["file"]
    if not f.filename.lower().endswith(".csv"):
        return err("CSV file required", 400)
    try:
        raw = f.read().decode("utf-8-sig")
        MAX_CSV_ROWS = 5000
        reader = csv.DictReader(io.StringIO(raw))
        imported, skipped, errors = 0, 0, []
        required = {"name", "party_name", "contract_type", "content"}
        for i, row in enumerate(reader):
            if i >= MAX_CSV_ROWS:
                errors.append(f"Row limit reached ({MAX_CSV_ROWS}). Remaining rows skipped.")
                break
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
        return err(f"Import failed: {str(e)}", 500)

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
    if not tag: return err("Tag name required", 400)
    # Check if already tagged
    existing = sb.table("contract_tags").select("id").eq("contract_id", cid).eq("tag_name", tag).execute()
    if existing.data: return err("Tag already exists on this contract", 400)
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
    if not name: return err("Name required", 400)
    try:
        r = sb.table("tag_presets").insert({"name": name, "color": color, "description": d.get("description", "")}).execute()
        return jsonify(r.data[0]), 201
    except Exception as e:
        return err("Tag preset already exists", 400)

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
    if not name: return err("Name required", 400)
    if trigger not in valid_triggers: return err(f"Trigger must be one of: {', '.join(valid_triggers)}", 400)
    if action not in valid_actions: return err(f"Action must be one of: {', '.join(valid_actions)}", 400)
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
    if not u: return err("Nothing to update", 400)
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
    if not name: return err("Field name required", 400)
    if ftype not in ("text", "number", "date", "select", "url", "email"):
        return err("Invalid field type", 400)
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
    # Note: broadcast notifications (user_email is null) are NOT deleted here
    # They are shared across all users and should expire naturally
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
        return err("Email required — login with email to set preferences", 400)
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
        log.error(f"Internal error: {e}")
        return err("Internal server error", 500)

@app.route("/api/email-preferences/test", methods=["POST"])
@auth
@need_db
def test_email():
    """Send a test email to verify configuration"""
    email = getattr(request, 'user_email', '')
    if not email:
        return err("Login with email to test", 400)
    if not RESEND_API_KEY:
        return err("RESEND_API_KEY not configured. Add it to Vercel environment variables.", 400)
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
            return err(f"Resend API error: {r.text}", 400)
    except Exception as e:
        log.error(f"Internal error: {e}")
        return err("Internal server error", 500)

@app.route("/api/email-status")
@auth
def email_status():
    """Check if email service is configured"""
    return jsonify({
        "configured": bool(RESEND_API_KEY),
        "provider": "Resend" if RESEND_API_KEY else None,
        "from_address": EMAIL_FROM if RESEND_API_KEY else None
    })

# ─── Contract Linking (Client ↔ Vendor) ──────────────────────────────────
@app.route("/api/contracts/<int:cid>/links", methods=["GET"])
@auth
@need_db
def get_contract_links(cid):
    """Get all contracts linked to this contract"""
    c = sb.table("contracts").select("id,contract_type").eq("id", cid).execute()
    if not c.data: return err("Not found", 404)
    ctype = c.data[0]["contract_type"]

    links = []
    if ctype == "client":
        rows = sb.table("contract_links").select("*").eq("client_contract_id", cid).execute().data or []
        linked_ids = [r["vendor_contract_id"] for r in rows]
    else:
        rows = sb.table("contract_links").select("*").eq("vendor_contract_id", cid).execute().data or []
        linked_ids = [r["client_contract_id"] for r in rows]

    if linked_ids:
        linked = sb.table("contracts").select("id,name,party_name,contract_type,status,value,start_date,end_date").in_("id", linked_ids).execute().data or []
        link_map = {r["vendor_contract_id" if ctype == "client" else "client_contract_id"]: r for r in rows}
        for lc in linked:
            link_row = link_map.get(lc["id"], {})
            links.append({**lc, "link_id": link_row.get("id"), "link_notes": link_row.get("notes", ""), "linked_at": link_row.get("created_at")})

    return jsonify({"contract_id": cid, "contract_type": ctype, "links": links})

@app.route("/api/contracts/<int:cid>/links", methods=["POST"])
@auth
@role_required("editor")
@need_db
def add_contract_link(cid):
    """Link a client contract to a vendor contract (or vice versa)"""
    d = request.json or {}
    target_id = d.get("linked_contract_id")
    if not target_id: return err("Missing linked_contract_id", 400)

    # Get both contracts
    contracts = sb.table("contracts").select("id,contract_type,name").in_("id", [cid, int(target_id)]).execute().data or []
    if len(contracts) < 2: return err("One or both contracts not found", 404)

    by_id = {c["id"]: c for c in contracts}
    c1, c2 = by_id.get(cid), by_id.get(int(target_id))
    if not c1 or not c2: return err("Contract not found", 404)

    # Determine which is client and which is vendor
    if c1["contract_type"] == c2["contract_type"]:
        return err("Can only link a client contract to a vendor contract", 400)

    client_id = cid if c1["contract_type"] == "client" else int(target_id)
    vendor_id = int(target_id) if c1["contract_type"] == "client" else cid

    try:
        row = {"client_contract_id": client_id, "vendor_contract_id": vendor_id,
               "notes": _sanitize(d.get("notes", ""), max_len=1000),
               "created_by": getattr(request, 'user_email', 'User')}
        r = sb.table("contract_links").insert(row).execute()
        log_activity(cid, "linked", row["created_by"], f"Linked to contract #{target_id}")
        return jsonify({"message": "Linked", "link_id": r.data[0]["id"]}), 201
    except Exception as e:
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            return err("These contracts are already linked", 409)
        raise

@app.route("/api/contract-links/<int:link_id>", methods=["DELETE"])
@auth
@role_required("editor")
@need_db
def delete_contract_link(link_id):
    """Remove a contract link"""
    link = sb.table("contract_links").select("*").eq("id", link_id).execute()
    if not link.data: return err("Link not found", 404)
    sb.table("contract_links").delete().eq("id", link_id).execute()
    log_activity(link.data[0]["client_contract_id"], "unlinked", getattr(request, 'user_email', 'User'),
                 f"Unlinked from contract #{link.data[0]['vendor_contract_id']}")
    return jsonify({"message": "Unlinked"})

@app.route("/api/contract-links", methods=["GET"])
@auth
@need_db
def list_all_links():
    """Get all contract links with contract names"""
    rows = sb.table("contract_links").select("*").execute().data or []
    if not rows: return jsonify([])
    all_ids = list(set([r["client_contract_id"] for r in rows] + [r["vendor_contract_id"] for r in rows]))
    contracts = sb.table("contracts").select("id,name,party_name").in_("id", all_ids).execute().data or []
    cmap = {c["id"]: c for c in contracts}
    result = []
    for r in rows:
        cl = cmap.get(r["client_contract_id"], {})
        vn = cmap.get(r["vendor_contract_id"], {})
        result.append({"id": r["id"], "client_contract_id": r["client_contract_id"], "vendor_contract_id": r["vendor_contract_id"],
                        "client_name": cl.get("name", ""), "vendor_name": vn.get("name", ""),
                        "notes": r.get("notes", ""), "created_at": r.get("created_at")})
    return jsonify(result)

@app.route("/api/contracts/linkable", methods=["GET"])
@auth
@need_db
def get_linkable_contracts():
    """Get contracts that can be linked to a given contract (opposite type, not already linked)"""
    cid = request.args.get("contract_id")
    if not cid: return err("Provide contract_id", 400)
    c = sb.table("contracts").select("id,contract_type").eq("id", int(cid)).execute()
    if not c.data: return err("Not found", 404)

    opposite = "vendor" if c.data[0]["contract_type"] == "client" else "client"
    # Get already linked IDs
    if c.data[0]["contract_type"] == "client":
        existing = sb.table("contract_links").select("vendor_contract_id").eq("client_contract_id", int(cid)).execute().data or []
        linked_ids = [r["vendor_contract_id"] for r in existing]
    else:
        existing = sb.table("contract_links").select("client_contract_id").eq("vendor_contract_id", int(cid)).execute().data or []
        linked_ids = [r["client_contract_id"] for r in existing]

    q = sb.table("contracts").select("id,name,party_name,status,value").eq("contract_type", opposite)
    if linked_ids:
        q = q.not_.in_("id", linked_ids)
    available = q.execute().data or []
    return jsonify({"contract_type": opposite, "contracts": available})

# ─── Contract Parties (Multi-vendor) ──────────────────────────────────────
@app.route("/api/contracts/<int:cid>/parties", methods=["GET"])
@auth
@need_db
def get_contract_parties(cid):
    rows = sb.table("contract_parties").select("*").eq("contract_id", cid).order("created_at").execute().data or []
    return jsonify(rows)

@app.route("/api/contracts/<int:cid>/parties", methods=["POST"])
@auth
@role_required("editor")
@need_db
def add_contract_party(cid):
    chk = sb.table("contracts").select("id").eq("id", cid).execute()
    if not chk.data: return err("Contract not found", 404)
    d = _sanitize_dict(request.json or {})
    if not d.get("party_name", "").strip(): return err("party_name required", 400)
    if d.get("party_type") not in ("client", "vendor", "subcontractor"):
        return err("party_type must be client, vendor, or subcontractor", 400)
    row = {
        "contract_id": cid,
        "party_name": d["party_name"][:500],
        "party_type": d["party_type"],
        "role": str(d.get("role", "") or "")[:200],
        "party_value": str(d.get("party_value", "") or "")[:100],
        "scope": str(d.get("scope", "") or "")[:1000],
        "status": d.get("status", "active"),
        "contact_name": str(d.get("contact_name", "") or "")[:200],
        "contact_email": str(d.get("contact_email", "") or "")[:200],
        "notes": str(d.get("notes", "") or "")[:1000],
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }
    r = sb.table("contract_parties").insert(row).execute()
    log_activity(cid, "party_added", request.user_email, f"Party '{row['party_name']}' ({row['party_type']}) added")
    return jsonify(r.data[0] if r.data else {"message": "Added"}), 201

@app.route("/api/contract-parties/<int:pid>", methods=["PUT"])
@auth
@role_required("editor")
@need_db
def update_contract_party(pid):
    chk = sb.table("contract_parties").select("*").eq("id", pid).execute()
    if not chk.data: return err("Party not found", 404)
    d = _sanitize_dict(request.json or {})
    u = {}
    for f in ["party_name","party_type","role","party_value","scope","status","contact_name","contact_email","notes"]:
        if f in d: u[f] = d[f]
    if not u: return err("Nothing to update", 400)
    if "party_type" in u and u["party_type"] not in ("client", "vendor", "subcontractor"):
        return err("party_type must be client, vendor, or subcontractor", 400)
    u["updated_at"] = datetime.now().isoformat()
    sb.table("contract_parties").update(u).eq("id", pid).execute()
    cid = chk.data[0]["contract_id"]
    log_activity(cid, "party_updated", request.user_email, f"Party '{chk.data[0]['party_name']}' updated")
    return jsonify({"message": "Updated"})

@app.route("/api/contract-parties/<int:pid>", methods=["DELETE"])
@auth
@role_required("editor")
@need_db
def delete_contract_party(pid):
    chk = sb.table("contract_parties").select("*").eq("id", pid).execute()
    if not chk.data: return err("Party not found", 404)
    cid = chk.data[0]["contract_id"]
    sb.table("contract_parties").delete().eq("id", pid).execute()
    log_activity(cid, "party_removed", request.user_email, f"Party '{chk.data[0]['party_name']}' removed")
    return jsonify({"message": "Deleted"})

# ─── AI Executive Summary ────────────────────────────────────────────────
@app.route("/api/contracts/<int:cid>/ai-summary", methods=["POST"])
@auth
@need_db
def ai_summary(cid):
    """Generate a concise executive summary of a contract."""
    if not oai_h(): return err("AI not configured", 500)
    r = sb.table("contracts").select("content,name,contract_type,party_name,value,start_date,end_date,status").eq("id", cid).execute()
    if not r.data: return err("Not found", 404)
    c = r.data[0]
    try:
        reply = oai_chat([
            {"role": "system", "content": """Generate a concise executive summary of this contract for business stakeholders.

Return JSON with this structure:
{
  "one_liner": "Single sentence describing what this contract is about",
  "key_terms": {
    "parties": "Who is involved",
    "value": "Total contract value",
    "duration": "Start to end date and total duration",
    "type": "Type of agreement"
  },
  "obligations": ["Top 3-5 key obligations for EMB"],
  "risks": ["Top 2-3 risks to flag"],
  "key_dates": [{"date": "YYYY-MM-DD", "description": "What happens on this date"}],
  "recommendation": "1-2 sentence recommendation for management"
}

Be specific — use actual numbers, dates, and party names from the contract."""},
            {"role": "user", "content": f"Contract: {c['name']}\nParty: {c.get('party_name','N/A')}\nType: {c.get('contract_type','N/A')}\nValue: {c.get('value','N/A')}\nStatus: {c.get('status','N/A')}\n\n{(c.get('content') or '')[:12000]}"}
        ], model="gpt-4o", max_tok=1500, temperature=0.2)
        reply = reply.strip()
        if reply.startswith("```"): reply = reply.split("\n",1)[1].rsplit("```",1)[0].strip()
        summary = J.loads(reply)
        log_activity(cid, "ai_summary", "AI", "Executive summary generated")
        return jsonify(summary)
    except Exception as e:
        log.error(f"Internal error: {e}")
        return err("Internal server error", 500)

# ─── AI Obligation Extraction ────────────────────────────────────────────
@app.route("/api/contracts/<int:cid>/extract-obligations", methods=["POST"])
@auth
@role_required("editor")
@need_db
def extract_obligations(cid):
    """AI-powered extraction of obligations, deadlines, and deliverables from contract text."""
    if not oai_h(): return err("AI not configured", 500)
    r = sb.table("contracts").select("content,name,contract_type,party_name").eq("id", cid).execute()
    if not r.data: return err("Not found", 404)
    c = r.data[0]
    auto_save = (request.json or {}).get("auto_save", False)
    try:
        reply = oai_chat([
            {"role": "system", "content": """Extract all obligations, deliverables, milestones, and deadlines from this contract.

Return JSON:
{
  "obligations": [
    {
      "title": "Short descriptive title (e.g., 'Monthly Progress Report')",
      "description": "What needs to be done — specific and actionable",
      "responsible_party": "EMB|counterparty|both",
      "due_date": "YYYY-MM-DD or null if recurring/not specified",
      "frequency": "one-time|weekly|monthly|quarterly|annually|ongoing|as-needed",
      "priority": "high|medium|low",
      "section_ref": "Section number where this obligation appears",
      "category": "deliverable|payment|reporting|compliance|notification|milestone"
    }
  ],
  "key_deadlines": [
    {"date": "YYYY-MM-DD", "description": "What happens on this date", "critical": true}
  ],
  "total_found": 0
}

RULES:
- Extract ALL obligations, not just major ones
- Include payment obligations (invoicing deadlines, payment terms)
- Include reporting obligations (progress reports, audits)
- Include compliance obligations (insurance, certifications)
- Include notification obligations (notice periods, escalation)
- Mark obligations as high priority if they have financial penalties for non-compliance"""},
            {"role": "user", "content": f"Contract: {c['name']}\nParty: {c.get('party_name','N/A')}\nType: {c.get('contract_type','N/A')}\n\n{(c.get('content') or '')[:15000]}"}
        ], model="gpt-4o", max_tok=3000, temperature=0.2)
        reply = reply.strip()
        if reply.startswith("```"): reply = reply.split("\n",1)[1].rsplit("```",1)[0].strip()
        parsed = J.loads(reply)
        obligations = parsed.get("obligations", [])

        # Auto-save obligations to database if requested
        saved = 0
        if auto_save and obligations:
            for ob in obligations:
                try:
                    row = {
                        "contract_id": cid,
                        "title": _sanitize(ob.get("title", "Untitled"))[:200],
                        "description": _sanitize(ob.get("description", ""))[:2000],
                        "due_date": ob.get("due_date"),
                        "status": "pending",
                        "priority": ob.get("priority", "medium"),
                    }
                    if row["due_date"] and not re.match(r'^\d{4}-\d{2}-\d{2}$', row["due_date"]):
                        row["due_date"] = None
                    sb.table("contract_obligations").insert(row).execute()
                    saved += 1
                except Exception:
                    pass

        log_activity(cid, "ai_extract_obligations", "AI",
            f"Extracted {len(obligations)} obligations" + (f", saved {saved}" if auto_save else ""))

        return jsonify({
            "obligations": obligations,
            "key_deadlines": parsed.get("key_deadlines", []),
            "total_found": len(obligations),
            "saved": saved if auto_save else None
        })
    except Exception as e:
        log.error(f"Internal error: {e}")
        return err("Internal server error", 500)

# ─── AI Contract Plain-English Explainer ─────────────────────────────────
@app.route("/api/contracts/<int:cid>/explain", methods=["POST"])
@auth
@need_db
def explain_contract(cid):
    """Explain contract terms in simple, non-legal language."""
    if not oai_h(): return err("AI not configured", 500)
    r = sb.table("contracts").select("content,name,contract_type,party_name").eq("id", cid).execute()
    if not r.data: return err("Not found", 404)
    c = r.data[0]
    section = (request.json or {}).get("section", "")  # Optional: explain a specific section
    content = section if section else (c.get("content") or "")[:12000]

    try:
        reply = oai_chat([
            {"role": "system", "content": """You are a contract simplifier. Explain this contract (or section) in plain English that a non-lawyer business person can understand.

Return JSON:
{
  "plain_english": "A clear, simple explanation of what this contract says and means for the parties involved. Use everyday language. 3-5 paragraphs.",
  "what_you_must_do": ["Simple list of what EMB must do under this contract"],
  "what_they_must_do": ["Simple list of what the other party must do"],
  "watch_out_for": ["Things to be careful about, explained simply"],
  "in_one_sentence": "The entire contract summarized in one plain sentence"
}

RULES:
- NO legal jargon — write as if explaining to someone with no legal background
- Use 'you' for EMB and the party name for the counterparty
- Highlight anything that could cost money if missed
- Be honest about unfavourable terms"""},
            {"role": "user", "content": f"Contract: {c['name']} with {c.get('party_name','the other party')}\nType: {c.get('contract_type','N/A')}\n\n{content}"}
        ], model="gpt-4o", max_tok=2000, temperature=0.3)
        reply = reply.strip()
        if reply.startswith("```"): reply = reply.split("\n",1)[1].rsplit("```",1)[0].strip()
        return jsonify(J.loads(reply))
    except Exception as e:
        log.error(f"Internal error: {e}")
        return err("Internal server error", 500)

# ─── Margin Tracking ─────────────────────────────────────────────────────
@app.route("/api/contracts/<int:cid>/margin", methods=["GET"])
@auth
@need_db
def get_contract_margin(cid):
    """Get margin data for a client contract — its value vs total linked vendor costs"""
    c = sb.table("contracts").select("id,name,party_name,contract_type,value").eq("id", cid).execute()
    if not c.data: return err("Not found", 404)
    contract = c.data[0]
    if contract["contract_type"] != "client":
        return err("Margin tracking is only for client contracts", 400)
    # Get linked vendor contracts
    links = sb.table("contract_links").select("vendor_contract_id").eq("client_contract_id", cid).execute().data or []
    vendor_ids = [l["vendor_contract_id"] for l in links]
    vendors = []
    total_vendor_cost = 0
    if vendor_ids:
        vdata = sb.table("contracts").select("id,name,party_name,value,status").in_("id", vendor_ids).execute().data or []
        for v in vdata:
            parsed = _parse_currency(v.get("value", ""))
            vendors.append({**v, "parsed_value": parsed})
            total_vendor_cost += parsed
    # Also include party-level values from contract_parties
    parties = sb.table("contract_parties").select("*").eq("contract_id", cid).execute().data or []
    client_value = _parse_currency(contract.get("value", ""))
    margin = client_value - total_vendor_cost
    margin_pct = round((margin / client_value * 100), 1) if client_value > 0 else 0
    return jsonify({
        "contract": contract, "client_value": client_value,
        "vendors": vendors, "total_vendor_cost": total_vendor_cost,
        "margin": margin, "margin_pct": margin_pct, "parties": parties
    })

@app.route("/api/margins", methods=["GET"])
@auth
@need_db
def get_all_margins():
    """Get margin overview across all client contracts with linked vendors"""
    clients = sb.table("contracts").select("id,name,party_name,value,status,department").eq("contract_type", "client").execute().data or []
    links = sb.table("contract_links").select("client_contract_id,vendor_contract_id").execute().data or []
    vendor_ids = list(set(l["vendor_contract_id"] for l in links))
    vendor_map = {}
    if vendor_ids:
        vdata = sb.table("contracts").select("id,name,party_name,value").in_("id", vendor_ids).execute().data or []
        vendor_map = {v["id"]: v for v in vdata}
    # Build link map: client_id -> [vendor contracts]
    link_map = {}
    for l in links:
        cid = l["client_contract_id"]
        vid = l["vendor_contract_id"]
        if cid not in link_map: link_map[cid] = []
        if vid in vendor_map: link_map[cid].append(vendor_map[vid])
    results = []
    total_revenue = 0
    total_cost = 0
    for c in clients:
        cv = _parse_currency(c.get("value", ""))
        vendors = link_map.get(c["id"], [])
        vcost = sum(_parse_currency(v.get("value", "")) for v in vendors)
        margin = cv - vcost
        margin_pct = round((margin / cv * 100), 1) if cv > 0 else 0
        total_revenue += cv
        total_cost += vcost
        results.append({
            "id": c["id"], "name": c["name"], "party_name": c["party_name"],
            "status": c["status"], "department": c.get("department", ""),
            "client_value": cv, "vendor_cost": vcost,
            "margin": margin, "margin_pct": margin_pct,
            "vendor_count": len(vendors)
        })
    total_margin = total_revenue - total_cost
    total_margin_pct = round((total_margin / total_revenue * 100), 1) if total_revenue > 0 else 0
    return jsonify({
        "contracts": results, "summary": {
            "total_revenue": total_revenue, "total_cost": total_cost,
            "total_margin": total_margin, "total_margin_pct": total_margin_pct,
            "contract_count": len(results)
        }
    })

def _parse_currency(val):
    """Parse currency string like '₹25,00,000' or '$48,000' to float."""
    if not val: return 0.0
    try:
        parsed = float(re.sub(r'[^\d.]', '', str(val)))
        return min(parsed, 1_000_000_000_000)  # Cap at 1 trillion
    except (ValueError, TypeError): return 0.0

# ─── AI Clause Suggestions ───────────────────────────────────────────────
@app.route("/api/ai/suggest-clauses", methods=["POST"])
@auth
@need_db
def suggest_clauses():
    """Suggest relevant clauses while drafting a contract"""
    if not oai_h(): return err("OpenAI not configured", 400)
    d = request.json or {}
    contract_type = d.get("contract_type", "client")
    context = d.get("context", "")[:2000]
    department = d.get("department", "")
    # Get existing clauses from library
    clauses = sb.table("clause_library").select("title,content,category").limit(50).execute().data or []
    clause_list = "\n".join(f"- {c['title']} ({c.get('category','general')}): {c['content'][:100]}" for c in clauses[:20])
    prompt = f"""You are a legal contract expert. Suggest 3-5 relevant clauses for a {contract_type} contract.
Context: {context or 'General contract'}
Department: {department or 'Not specified'}

Existing clause library for reference:
{clause_list or 'No existing clauses'}

For each suggestion, provide:
1. Title - short clause title
2. Content - the full clause text (2-4 sentences)
3. Reason - why this clause is important for this contract type

Return as JSON array: [{{"title":"...","content":"...","reason":"..."}}]"""
    try:
        r = http.post(f"{OAI_URL}/chat/completions", headers=oai_h(),
            json={"model": "gpt-4o-mini", "max_tokens": 2000,
                  "messages": [{"role": "system", "content": "You are a legal contract clause expert. Always return valid JSON."},
                               {"role": "user", "content": prompt}],
                  "response_format": {"type": "json_object"}}, timeout=30)
        data = r.json()["choices"][0]["message"]["content"]
        parsed = J.loads(data)
        suggestions = parsed.get("suggestions", parsed.get("clauses", parsed if isinstance(parsed, list) else []))
        return jsonify({"suggestions": suggestions})
    except Exception as e:
        log.error(f"Internal error: {e}")
        return err("Internal server error", 500)

# ─── Renewal Autopilot ───────────────────────────────────────────────────
@app.route("/api/contracts/<int:cid>/auto-renew", methods=["POST"])
@auth
@role_required("editor")
@need_db
def auto_renew_contract(cid):
    """Auto-draft a renewal from an expiring contract"""
    c = sb.table("contracts").select("*").eq("id", cid).execute()
    if not c.data: return err("Not found", 404)
    orig = c.data[0]
    # Idempotency: check if a renewal draft already exists for this contract
    existing_renewal = sb.table("contracts").select("id,name").ilike("name", f"%Renewal of {_escape_like(orig['name'])}%").eq("status", "draft").execute()
    if existing_renewal.data:
        return jsonify({"error": {"message": f"A renewal draft already exists (#{existing_renewal.data[0]['id']})", "code": 409}, "id": existing_renewal.data[0]["id"]}), 409
    # Calculate new dates
    duration_days = 365
    if orig.get("start_date") and orig.get("end_date"):
        try:
            sd = datetime.strptime(orig["start_date"], "%Y-%m-%d")
            ed = datetime.strptime(orig["end_date"], "%Y-%m-%d")
            duration_days = (ed - sd).days
        except (ValueError, TypeError): pass
    new_start = orig.get("end_date") or datetime.now().strftime("%Y-%m-%d")
    try:
        ns = datetime.strptime(new_start, "%Y-%m-%d")
        new_end = (ns + timedelta(days=duration_days)).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        new_end = (datetime.now() + timedelta(days=365)).strftime("%Y-%m-%d")
    row = {
        "name": f"{orig['name']} — Renewal",
        "party_name": orig["party_name"], "contract_type": orig["contract_type"],
        "content": orig.get("content", ""), "content_html": orig.get("content_html", ""),
        "start_date": new_start, "end_date": new_end,
        "value": orig.get("value"), "notes": f"Auto-renewed from contract #{cid}",
        "department": orig.get("department", ""), "jurisdiction": orig.get("jurisdiction", ""),
        "governing_law": orig.get("governing_law", ""), "status": "draft",
        "created_by": request.user_email, "added_on": datetime.now().isoformat()
    }
    r = sb.table("contracts").insert(row).execute()
    new_id = r.data[0]["id"]
    log_activity(new_id, "created", request.user_email, f"Auto-renewed from contract #{cid}")
    log_activity(cid, "renewed", request.user_email, f"Renewal created as contract #{new_id}")
    # Copy obligations
    obs = sb.table("contract_obligations").select("title,description,assigned_to").eq("contract_id", cid).execute().data or []
    for o in obs:
        sb.table("contract_obligations").insert({
            "contract_id": new_id, "title": o["title"],
            "description": o.get("description", ""), "assigned_to": o.get("assigned_to", ""),
            "status": "pending", "created_at": datetime.now().isoformat()
        }).execute()
    create_notification(f"Contract renewed: {orig['name']}", f"Renewal draft created from contract #{cid}", "info", new_id)
    return jsonify({"id": new_id, "message": f"Renewal draft created (#{new_id})"}), 201

# ─── Approval SLA Tracking ──────────────────────────────────────────────
@app.route("/api/approvals/sla", methods=["GET"])
@auth
@need_db
def approval_sla():
    """Get approval SLA data — flag approvals stuck beyond threshold"""
    try: threshold_days = int(request.args.get("threshold", 3))
    except (ValueError, TypeError): threshold_days = 3
    approvals = sb.table("contract_approvals").select("*").eq("status", "pending").execute().data or []
    today = datetime.now()
    results = []
    overdue_count = 0
    # Batch-fetch contract names to avoid N+1 queries
    cids = list({a["contract_id"] for a in approvals if a.get("contract_id")})
    contracts_map = {}
    if cids:
        contracts_data = sb.table("contracts").select("id,name,party_name").in_("id", cids).execute().data or []
        contracts_map = {c["id"]: c for c in contracts_data}
    for a in approvals:
        created = a.get("created_at", "")
        days_pending = 0
        if created:
            try:
                ct = datetime.fromisoformat(created.replace("Z", "+00:00")).replace(tzinfo=None)
                days_pending = (today - ct).days
            except (ValueError, TypeError): pass
        a["days_pending"] = days_pending
        a["is_overdue"] = days_pending > threshold_days
        if a["is_overdue"]: overdue_count += 1
        # Get contract name from batch lookup
        cn = contracts_map.get(a.get("contract_id"))
        if cn:
            a["contract_name"] = cn["name"]
            a["party_name"] = cn.get("party_name", "")
        results.append(a)
    results.sort(key=lambda x: -x["days_pending"])
    return jsonify({"approvals": results, "total": len(results), "overdue": overdue_count, "threshold_days": threshold_days})

# ─── Contract PO/Invoice Linkage ─────────────────────────────────────────
@app.route("/api/contracts/<int:cid>/invoices", methods=["GET"])
@auth
@need_db
def get_contract_invoices(cid):
    rows = sb.table("contract_invoices").select("*").eq("contract_id", cid).order("invoice_date", desc=True).execute().data or []
    return jsonify(rows)

@app.route("/api/contracts/<int:cid>/invoices", methods=["POST"])
@auth
@role_required("editor")
@need_db
def add_contract_invoice(cid):
    chk = sb.table("contracts").select("id").eq("id", cid).execute()
    if not chk.data: return err("Contract not found", 404)
    d = _sanitize_dict(request.json or {})
    if not d.get("invoice_number", "").strip(): return err("Invoice number required", 400)
    row = {
        "contract_id": cid,
        "invoice_number": str(d["invoice_number"])[:100],
        "po_number": str(d.get("po_number", "") or "")[:100],
        "amount": str(d.get("amount", "") or "")[:100],
        "invoice_date": d.get("invoice_date") or None,
        "due_date": d.get("due_date") or None,
        "status": d.get("status", "pending") if d.get("status") in ("pending", "paid", "overdue", "cancelled") else "pending",
        "notes": str(d.get("notes", "") or "")[:500],
        "created_by": request.user_email,
        "created_at": datetime.now().isoformat()
    }
    r = sb.table("contract_invoices").insert(row).execute()
    log_activity(cid, "invoice_added", request.user_email, f"Invoice {row['invoice_number']} added")
    return jsonify(r.data[0] if r.data else {"message": "Added"}), 201

@app.route("/api/contract-invoices/<int:iid>", methods=["PUT"])
@auth
@role_required("editor")
@need_db
def update_contract_invoice(iid):
    chk = sb.table("contract_invoices").select("*").eq("id", iid).execute()
    if not chk.data: return err("Not found", 404)
    d = _sanitize_dict(request.json or {})
    u = {}
    for f in ["invoice_number","po_number","amount","invoice_date","due_date","status","notes"]:
        if f in d: u[f] = d[f]
    if not u: return err("Nothing to update", 400)
    u["updated_at"] = datetime.now().isoformat()
    sb.table("contract_invoices").update(u).eq("id", iid).execute()
    return jsonify({"message": "Updated"})

@app.route("/api/contract-invoices/<int:iid>", methods=["DELETE"])
@auth
@role_required("editor")
@need_db
def delete_contract_invoice(iid):
    chk = sb.table("contract_invoices").select("*").eq("id", iid).execute()
    if not chk.data: return err("Not found", 404)
    sb.table("contract_invoices").delete().eq("id", iid).execute()
    return jsonify({"message": "Deleted"})

# ─── Slack/Teams Webhook Notifications ───────────────────────────────────
@app.route("/api/settings/slack-webhook", methods=["GET"])
@auth
@role_required("admin")
@need_db
def get_slack_webhook():
    r = sb.table("app_settings").select("*").eq("key", "slack_webhook_url").execute()
    url = r.data[0]["value"] if r.data else ""
    return jsonify({"url": url})

@app.route("/api/settings/slack-webhook", methods=["POST"])
@auth
@role_required("admin")
@need_db
def set_slack_webhook():
    d = request.json or {}
    url = str(d.get("url", ""))[:500]
    existing = sb.table("app_settings").select("id").eq("key", "slack_webhook_url").execute()
    if existing.data:
        sb.table("app_settings").update({"value": url}).eq("key", "slack_webhook_url").execute()
    else:
        sb.table("app_settings").insert({"key": "slack_webhook_url", "value": url}).execute()
    return jsonify({"message": "Slack webhook saved"})

@app.route("/api/settings/slack-test", methods=["POST"])
@auth
@role_required("admin")
@need_db
def test_slack_webhook():
    r = sb.table("app_settings").select("*").eq("key", "slack_webhook_url").execute()
    if not r.data or not r.data[0].get("value"):
        return err("No Slack webhook URL configured", 400)
    url = r.data[0]["value"]
    try:
        resp = http.post(url, json={"text": "EMB CLM test notification — Slack integration is working!"}, timeout=10)
        if resp.status_code == 200:
            return jsonify({"message": "Test message sent successfully"})
        return err(f"Slack returned {resp.status_code}", 400)
    except Exception as e:
        log.error(f"Internal error: {e}")
        return err("Internal server error", 500)

# ─── Shareable Review Links ──────────────────────────────────────────────
@app.route("/api/contracts/<int:cid>/share-links", methods=["GET"])
@auth
@need_db
def get_share_links(cid):
    rows = sb.table("contract_share_links").select("*").eq("contract_id", cid).order("created_at", desc=True).execute().data or []
    return jsonify(rows)

@app.route("/api/contracts/<int:cid>/share-links", methods=["POST"])
@auth
@role_required("editor")
@need_db
def create_share_link(cid):
    chk = sb.table("contracts").select("id").eq("id", cid).execute()
    if not chk.data: return err("Contract not found", 404)
    d = request.json or {}
    try: expires_hours = min(int(d.get("expires_hours", 72)), 720)  # max 30 days
    except (ValueError, TypeError): expires_hours = 72
    token = secrets.token_urlsafe(32)
    row = {
        "contract_id": cid, "token": token, "created_by": request.user_email,
        "recipient_name": str(d.get("recipient_name", "") or "")[:200],
        "recipient_email": str(d.get("recipient_email", "") or "")[:200],
        "permissions": d.get("permissions", "view") if d.get("permissions") in ("view", "comment") else "view",
        "expires_at": (datetime.now() + timedelta(hours=expires_hours)).isoformat(),
        "is_active": True, "created_at": datetime.now().isoformat()
    }
    r = sb.table("contract_share_links").insert(row).execute()
    log_activity(cid, "share_link_created", request.user_email,
                 f"Share link created for {row['recipient_name'] or 'external user'} ({row['permissions']}, {expires_hours}h)")
    return jsonify({"token": token, "link": r.data[0] if r.data else row, "expires_hours": expires_hours}), 201

@app.route("/api/share-links/<int:lid>/revoke", methods=["POST"])
@auth
@role_required("editor")
@need_db
def revoke_share_link(lid):
    chk = sb.table("contract_share_links").select("*").eq("id", lid).execute()
    if not chk.data: return err("Not found", 404)
    sb.table("contract_share_links").update({"is_active": False}).eq("id", lid).execute()
    log_activity(chk.data[0]["contract_id"], "share_link_revoked", request.user_email, "Share link revoked")
    return jsonify({"message": "Link revoked"})

@app.route("/api/shared/<token>", methods=["GET"])
def view_shared_contract(token):
    """Public endpoint — no auth required. View a shared contract via token."""
    if not sb: return err("Service unavailable", 503)
    link = sb.table("contract_share_links").select("*").eq("token", token).eq("is_active", True).execute()
    if not link.data: return err("Invalid or expired link", 404)
    sl = link.data[0]
    if datetime.fromisoformat(sl["expires_at"].replace("Z", "+00:00")).replace(tzinfo=None) < datetime.now():
        return err("This link has expired", 410)
    # Update access count
    sb.table("contract_share_links").update({
        "accessed_count": (sl.get("accessed_count", 0) or 0) + 1,
        "last_accessed_at": datetime.now().isoformat()
    }).eq("id", sl["id"]).execute()
    # Get contract (limited fields for security)
    c = sb.table("contracts").select("id,name,party_name,contract_type,status,content,content_html,start_date,end_date,value,department,jurisdiction").eq("id", sl["contract_id"]).execute()
    if not c.data: return err("Contract not found", 404)
    return jsonify({
        "contract": c.data[0], "permissions": sl["permissions"],
        "recipient_name": sl.get("recipient_name", ""),
        "expires_at": sl["expires_at"]
    })

@app.route("/api/shared/<token>/comments", methods=["POST"])
def add_shared_comment(token):
    """Public endpoint — add comment on a shared contract (if permission allows)"""
    if not sb: return err("Service unavailable", 503)
    link = sb.table("contract_share_links").select("*").eq("token", token).eq("is_active", True).execute()
    if not link.data: return err("Invalid or expired link", 404)
    sl = link.data[0]
    if sl["permissions"] != "comment": return err("View-only access", 403)
    if datetime.fromisoformat(sl["expires_at"].replace("Z", "+00:00")).replace(tzinfo=None) < datetime.now():
        return err("This link has expired", 410)
    d = request.json or {}
    if not d.get("text", "").strip(): return err("Comment text required", 400)
    commenter = sl.get("recipient_name") or "External Reviewer"
    row = {
        "contract_id": sl["contract_id"], "user_name": commenter,
        "content": _sanitize(str(d["text"]), 2000), "created_at": datetime.now().isoformat()
    }
    sb.table("contract_comments").insert(row).execute()
    create_notification(
        f"External comment on contract",
        f"{commenter} commented via shared link: {str(d['text'])[:100]}",
        "comment", sl["contract_id"]
    )
    return jsonify({"message": "Comment added"}), 201

# ─── Contract Comparison ──────────────────────────────────────────────────
@app.route("/api/contracts/compare")
@auth
@need_db
def compare_contracts():
    id1 = request.args.get("id1")
    id2 = request.args.get("id2")
    if not id1 or not id2:
        return err("Provide id1 and id2 parameters", 400)
    try:
        c1 = sb.table("contracts").select("*").eq("id", int(id1)).execute()
        c2 = sb.table("contracts").select("*").eq("id", int(id2)).execute()
        if not c1.data or not c2.data:
            return err("One or both contracts not found", 404)

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

        # Word-level diff (HTML-escaped to prevent XSS)
        diff_html = []
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            seg1 = html_mod.escape(" ".join(words1[i1:i2]))
            seg2 = html_mod.escape(" ".join(words2[j1:j2]))
            if op == "equal":
                diff_html.append(seg1)
            elif op == "delete":
                diff_html.append(f'<span class="rl-del">{seg1}</span>')
            elif op == "insert":
                diff_html.append(f'<span class="rl-ins">{seg2}</span>')
            elif op == "replace":
                diff_html.append(f'<span class="rl-del">{seg1}</span>')
                diff_html.append(f'<span class="rl-ins">{seg2}</span>')

        result = {
            "contract_1": {"id": a["id"], "name": a["name"], "party": a["party_name"],
                          "word_count": len(words1), "content": text1[:5000]},
            "contract_2": {"id": b["id"], "name": b["name"], "party": b["party_name"],
                          "word_count": len(words2), "content": text2[:5000]},
            "similarity": similarity,
            "field_diffs": field_diffs,
            "diff_html": " ".join(diff_html),
            "match_count": sum(1 for d in field_diffs if d["match"]),
            "total_fields": len(field_diffs)
        }

        # AI-powered comparison analysis (if requested and OpenAI available)
        if request.args.get("ai_analysis") == "true" and oai_h():
            try:
                ai_resp = oai_chat([
                    {"role": "system", "content": """You are a contract comparison analyst for EMB (a tech services broker).
Compare these two contracts and return JSON:
{
  "key_differences": ["Top 3-5 material differences between the contracts"],
  "which_is_better": "Which contract is more favourable for EMB and why (1-2 sentences)",
  "risk_comparison": "Which contract carries more risk and in what areas (1-2 sentences)",
  "financial_comparison": "Compare financial terms — values, payment terms, penalties (1-2 sentences)",
  "recommendation": "1-2 sentence actionable recommendation"
}
Be specific — reference actual terms, values, and clause names."""},
                    {"role": "user", "content": f"CONTRACT 1: {a['name']} ({a.get('party_name','?')}, {a.get('contract_type','?')}, Value: {a.get('value','N/A')})\n{text1[:6000]}\n\nCONTRACT 2: {b['name']} ({b.get('party_name','?')}, {b.get('contract_type','?')}, Value: {b.get('value','N/A')})\n{text2[:6000]}"}
                ], model="gpt-4o", max_tok=1500, temperature=0.2)
                ai_resp = ai_resp.strip()
                if ai_resp.startswith("```"): ai_resp = ai_resp.split("\n",1)[1].rsplit("```",1)[0].strip()
                result["ai_analysis"] = J.loads(ai_resp)
            except Exception as e:
                log.debug(f"Compare AI analysis failed: {e}")
                result["ai_analysis"] = None

        return jsonify(result)
    except Exception as e:
        log.error(f"Internal error: {e}")
        return err("Internal server error", 500)

# ─── Contract Clone ───────────────────────────────────────────────────────
@app.route("/api/contracts/<int:cid>/clone", methods=["POST"])
@auth
@role_required("editor")
@need_db
def clone_contract(cid):
    r = sb.table("contracts").select("*").eq("id", cid).execute()
    if not r.data: return err("Not found", 404)
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
    r = sb.table("contracts").select("id,name,party_name,contract_type,status,value,start_date,end_date,department").ilike("party_name", f"%{_escape_like(name)}%").order("added_on", desc=True).execute()
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
        return err("Provide ids and action", 400)
    if len(ids) > 50:
        return err("Max 50 contracts per batch", 400)

    results = {"success": 0, "failed": 0, "errors": []}

    if action == "change_status":
        new_status = d.get("status", "")
        valid = ["draft", "pending", "in_review", "executed", "rejected"]
        if new_status not in valid:
            return err(f"Invalid status", 400)
        for cid in ids:
            try:
                resp, code = _transition_status(int(cid), new_status, "Bulk Action")
                if code == 200:
                    results["success"] += 1
                else:
                    results["failed"] += 1
                    try: results["errors"].append(f"#{cid}: {resp.get_json().get('error',{}).get('message','Failed')}")
                    except (AttributeError, TypeError): results["errors"].append(f"#{cid}: Transition failed")
            except Exception as e:
                log.debug(f"Bulk status change error: {e}")
                results["failed"] += 1

    elif action == "add_tag":
        tag_name = d.get("tag_name", "").strip()
        tag_color = d.get("tag_color", "#2563eb")
        if not tag_name:
            return err("Tag name required", 400)
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
            return err("Tag name required", 400)
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
        return err(f"Unknown action: {action}", 400)

    return jsonify({"message": f"Bulk {action.replace('_', ' ').title()}: {results['success']} succeeded, {results['failed']} failed", **results})

# ─── Password Reset ──────────────────────────────────────────────────────
@app.route("/api/auth/reset-password", methods=["POST"])
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

    # Find user
    u = sb.table("clm_users").select("id,email,name").eq("email", email).execute()
    if not u.data:
        return err("User not found", 404)

    pw_hash = _hash_password(new_password)
    sb.table("clm_users").update({"password_hash": pw_hash, "updated_at": datetime.now().isoformat()}).eq("email", email).execute()
    return jsonify({"message": f"Password reset for {u.data[0]['name']}"})

# ─── Renewal Tracking ────────────────────────────────────────────────────
@app.route("/api/renewals")
@auth
@need_db
def renewal_tracker():
    try: days = int(request.args.get("days", 90))
    except (ValueError, TypeError): days = 90
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
    if not r.data: return err("Not found", 404)
    c = r.data[0]

    # Get obligations
    obls = []
    try:
        obr = sb.table("contract_obligations").select("*").eq("contract_id", cid).execute()
        obls = obr.data or []
    except Exception as e: log.debug(f"generate_pdf: {e}")

    # Get signatures
    sigs = []
    try:
        sgr = sb.table("contract_signatures").select("*").eq("contract_id", cid).execute()
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

    # Build HTML for PDF — escape all user content to prevent XSS
    _e = html_mod.escape
    name = _e(c.get("name", "Contract"))
    party = _e(c.get("party_name", ""))
    ctype = _e(c.get("contract_type", ""))
    status = _e(c.get("status", "draft"))
    value = _e(c.get("value", ""))
    dept = _e(c.get("department", ""))
    start = _e(c.get("start_date", ""))
    end = _e(c.get("end_date", ""))
    jurisdiction = _e(c.get("jurisdiction", ""))
    governing = _e(c.get("governing_law", ""))
    content = _e(c.get("content", ""))
    created = _e(c.get("created_at", "")[:10]) if c.get("created_at") else ""

    tag_html = ""
    if tags:
        tag_html = '<div style="margin-bottom:20px"><strong>Tags: </strong>' + ", ".join(_e(t["tag_name"]) for t in tags) + "</div>"

    cf_html = ""
    if cfields:
        cf_rows = ""
        for cf in cfields:
            fname = _e(cf.get("custom_field_defs", {}).get("field_name", "Field") if isinstance(cf.get("custom_field_defs"), dict) else "Field")
            fval = _e(cf.get("field_value", "---") or "---")
            cf_rows += f"<tr><td style='padding:8px 12px;border:1px solid #e2e8f0;font-weight:600;background:#f8fafc;width:40%'>{fname}</td><td style='padding:8px 12px;border:1px solid #e2e8f0'>{fval}</td></tr>"
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
@role_required("admin")
@need_db
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
@role_required("admin")
@need_db
def restore_data():
    """Restore data from a backup JSON (admin only)."""
    body = request.get_json(silent=True) or {}
    if not body.get("confirm"):
        return err("Safety check: include '\"confirm\": true' in the request body to proceed.", 400)
    tbl_data = body.get("tables")
    if not tbl_data or not isinstance(tbl_data, dict):
        return err("Invalid backup format: missing 'tables' object.", 400)

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
    if not oai_h(): return err("AI not configured", 500)
    r = sb.table("contracts").select("id,name,content").eq("id", cid).execute()
    if not r.data: return err("Not found", 404)
    c = r.data[0]
    try:
        n = embed_contract(c["id"], c["content"], c["name"])
        return jsonify({"chunks": n})
    except Exception as e:
        log.error(f"Internal error: {e}")
        return err("Internal server error", 500)
