"""Admin routes: users, webhooks, settings/*, email-preferences, notifications,
backup, restore, bulk-import, export, email-status."""

import io
import csv
from datetime import datetime
from flask import Blueprint, request, jsonify, Response

from config import sb, log, BACKUP_TABLES, RESEND_API_KEY, EMAIL_FROM
from auth import (
    err, _hash_password, _valid_email,
    _sanitize, _sanitize_dict,
    auth, role_required, need_db,
)
from helpers import log_activity, create_notification
import requests as http

bp = Blueprint("admin", __name__)


# ─── User Management ─────────────────────────────────────────────────────

@bp.route("/api/users", methods=["GET"])
@auth
@role_required("admin")
@need_db
def list_users():
    r = sb.table("clm_users").select("id,email,name,role,department,designation,phone,is_active,last_login,created_at").order("created_at", desc=True).execute()
    return jsonify(r.data)


@bp.route("/api/users", methods=["POST"])
@auth
@role_required("admin")
@need_db
def create_user():
    d = request.json or {}
    for f in ["email", "name", "password"]:
        if not d.get(f, "").strip():
            return err(f"Missing: {f}", 400)
    email = d["email"].strip().lower()
    if not _valid_email(email):
        return err("Invalid email format", 400)
    existing = sb.table("clm_users").select("id").eq("email", email).execute()
    if existing.data:
        return err("Email already exists", 409)
    pw_hash = _hash_password(d["password"])
    role = d.get("role", "viewer")
    if role not in ("admin", "manager", "editor", "viewer"):
        role = "viewer"
    row = {
        "email": email, "name": d["name"][:200], "password_hash": pw_hash,
        "role": role, "department": d.get("department", ""),
        "designation": d.get("designation", ""), "phone": d.get("phone", ""),
        "is_active": True, "created_at": datetime.now().isoformat(), "updated_at": datetime.now().isoformat()
    }
    r = sb.table("clm_users").insert(row).execute()
    new_id = r.data[0]["id"]

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


@bp.route("/api/users/<int:uid>", methods=["PUT"])
@auth
@role_required("admin")
@need_db
def update_user(uid):
    d = request.json or {}
    u = {}
    for f in ["name", "email", "role", "department", "designation", "phone", "is_active"]:
        if f in d:
            u[f] = d[f]
    if "password" in d and d["password"]:
        u["password_hash"] = _hash_password(d["password"])
    if not u:
        return err("Nothing to update", 400)
    u["updated_at"] = datetime.now().isoformat()
    sb.table("clm_users").update(u).eq("id", uid).execute()
    return jsonify({"message": "User updated"})


@bp.route("/api/users/<int:uid>", methods=["DELETE"])
@auth
@role_required("admin")
@need_db
def delete_user(uid):
    sb.table("clm_users").delete().eq("id", uid).execute()
    return jsonify({"message": "User deleted"})


# ─── Webhooks Config ───────────────────────────────────────────────────────

@bp.route("/api/webhooks", methods=["GET"])
@auth
@need_db
def list_webhooks():
    r = sb.table("webhook_configs").select("*").order("created_at", desc=True).limit(50).execute()
    return jsonify(r.data)


@bp.route("/api/webhooks", methods=["POST"])
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


@bp.route("/api/webhooks/<int:wid>", methods=["DELETE"])
@auth
@role_required("admin")
@need_db
def delete_webhook(wid):
    sb.table("webhook_configs").delete().eq("id", wid).execute()
    return jsonify({"message": "Deleted"})


# ─── Settings: Slack Webhook ───────────────────────────────────────────────

@bp.route("/api/settings/slack-webhook", methods=["GET"])
@auth
@role_required("admin")
@need_db
def get_slack_webhook():
    r = sb.table("app_settings").select("*").eq("key", "slack_webhook_url").execute()
    url = r.data[0]["value"] if r.data else ""
    return jsonify({"url": url})


@bp.route("/api/settings/slack-webhook", methods=["POST"])
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


@bp.route("/api/settings/slack-test", methods=["POST"])
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


# ─── Email Preferences ────────────────────────────────────────────────────

@bp.route("/api/email-preferences")
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


@bp.route("/api/email-preferences", methods=["POST"])
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


@bp.route("/api/email-preferences/test", methods=["POST"])
@auth
@need_db
def test_email():
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


@bp.route("/api/email-status")
@auth
def email_status():
    return jsonify({
        "configured": bool(RESEND_API_KEY),
        "provider": "Resend" if RESEND_API_KEY else None,
        "from_address": EMAIL_FROM if RESEND_API_KEY else None
    })


# ─── Notifications ────────────────────────────────────────────────────────

@bp.route("/api/notifications")
@auth
@need_db
def list_notifications():
    email = getattr(request, 'user_email', '')
    q = sb.table("notifications").select("*").order("created_at", desc=True).limit(50)
    if email:
        r1 = q.eq("user_email", email).execute()
        r2 = sb.table("notifications").select("*").is_("user_email", "null").order("created_at", desc=True).limit(20).execute()
        data = sorted(r1.data + r2.data, key=lambda x: x.get("created_at", ""), reverse=True)[:50]
    else:
        data = q.execute().data
    unread = sum(1 for n in data if not n.get("is_read"))
    return jsonify({"notifications": data, "unread": unread})


@bp.route("/api/notifications/read", methods=["POST"])
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


@bp.route("/api/notifications/clear", methods=["POST"])
@auth
@need_db
def clear_notifications():
    email = getattr(request, 'user_email', '')
    if email:
        sb.table("notifications").delete().eq("user_email", email).execute()
    return jsonify({"message": "Cleared"})


# ─── Backup / Restore ─────────────────────────────────────────────────────

@bp.route("/api/backup")
@auth
@role_required("admin")
@need_db
def backup_data():
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


@bp.route("/api/restore", methods=["POST"])
@auth
@role_required("admin")
@need_db
def restore_data():
    body = request.get_json(silent=True) or {}
    if not body.get("confirm"):
        return err("Safety check: include '\"confirm\": true' in the request body to proceed.", 400)
    tbl_data = body.get("tables")
    if not tbl_data or not isinstance(tbl_data, dict):
        return err("Invalid backup format: missing 'tables' object.", 400)

    summary = {}
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
                if tbl == "contracts":
                    key = (row.get("name", ""), row.get("party_name", ""))
                    if key in existing_contracts:
                        skipped += 1
                        continue
                rid = row.get("id")
                if rid is not None:
                    sb.table(tbl).upsert(row).execute()
                    updated += 1
                else:
                    sb.table(tbl).insert(row).execute()
                    inserted += 1
                if tbl == "contracts":
                    existing_contracts.add((row.get("name", ""), row.get("party_name", "")))
            except Exception:
                errors += 1
        summary[tbl] = {"inserted": inserted, "updated": updated, "skipped": skipped, "errors": errors}

    log_activity(0, "restore", "Admin", f"Restore completed: {len(tbl_data)} tables processed")
    return jsonify({"message": "Restore completed", "summary": summary})


# ─── Bulk Import ──────────────────────────────────────────────────────────

@bp.route("/api/bulk-import", methods=["POST"])
@auth
@role_required("editor")
@need_db
def bulk_import():
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
            if ctype not in ("client", "vendor"):
                ctype = "client"
            rec = {
                "name": row["name"][:500], "party_name": row["party_name"][:500],
                "contract_type": ctype, "content": row.get("content", "")[:500000],
                "start_date": row.get("start_date") or None, "end_date": row.get("end_date") or None,
                "value": row.get("value", "")[:100] or None, "notes": row.get("notes", "")[:1000],
                "department": row.get("department", ""), "jurisdiction": row.get("jurisdiction", ""),
                "status": row.get("status", "executed") if row.get("status") in ("draft", "pending", "in_review", "executed", "rejected") else "executed",
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


@bp.route("/api/bulk-import/template")
@auth
def bulk_template():
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["name", "party_name", "contract_type", "content", "start_date", "end_date", "value", "notes", "department", "jurisdiction", "status"])
    w.writerow(["NDA - Acme Corp", "Acme Corp Pvt Ltd", "client", "Full contract text here...", "2024-01-01", "2025-12-31", "INR 50,00,000", "Standard NDA", "Legal", "Mumbai", "executed"])
    out.seek(0)
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=bulk_import_template.csv"})


# ─── Export ────────────────────────────────────────────────────────────────

@bp.route("/api/export")
@auth
@need_db
def export():
    r = sb.table("contracts").select(
        "id,name,party_name,contract_type,status,start_date,end_date,value,added_on,department,notes"
    ).order("added_on", desc=True).execute()
    fmt = request.args.get("format", "csv")
    if fmt == "json":
        return jsonify(r.data)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["ID", "Name", "Party", "Type", "Status", "Start", "End", "Value", "Department", "Added", "Notes"])
    for c in r.data:
        w.writerow([c["id"], c["name"], c["party_name"], c["contract_type"], c.get("status", ""),
                    c.get("start_date", ""), c.get("end_date", ""), c.get("value", ""),
                    c.get("department", ""), c.get("added_on", ""), c.get("notes", "")])
    out.seek(0)
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=contracts_{datetime.now():%Y%m%d}.csv"})
