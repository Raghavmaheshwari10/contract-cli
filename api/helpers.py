"""Activity logger, webhooks, notifications, email, workflows, and utility helpers for CLM API."""

import os, sys, re, time, difflib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json
import requests as http
from datetime import datetime
from flask import jsonify

from config import (
    sb, log, RESEND_API_KEY, EMAIL_FROM, VALID_TRANSITIONS,
)
from constants import (
    DEFAULT_TAG_COLOR, NOTIFICATION_COLORS, VALID_STATUSES,
    MAX_EMAIL_RECIPIENTS,
)

# ─── Activity Logger ────────────────────────────────────────────────────
def log_activity(cid, action, user="System", details=None):
    try:
        sb.table("contract_activity").insert({
            "contract_id": cid, "action": action, "user_name": user,
            "details": details, "created_at": datetime.now().isoformat()
        }).execute()
    except Exception as e: log.debug(f"log_activity: {e}")

# ─── Webhook Trigger ────────────────────────────────────────────────────
def fire_webhooks(event, payload):
    try:
        hooks = sb.table("webhook_configs").select("url").eq("event_type", event).eq("active", True).execute().data
        for h in hooks:
            try: http.post(h["url"], json={"event": event, **payload}, timeout=5)
            except Exception as e: log.debug(f"fire_webhooks: {e}")
    except Exception as e: log.debug(f"fire_webhooks: {e}")

# ─── Notifications ──────────────────────────────────────────────────────
def create_notification(title, message="", ntype="info", contract_id=None, user_email=None):
    """Helper to create a notification"""
    if not sb: return
    try:
        sb.table("notifications").insert({
            "title": title, "message": message, "type": ntype,
            "contract_id": contract_id, "user_email": user_email,
            "link": str(contract_id) if contract_id else ""
        }).execute()
    except Exception as e: log.debug(f"create_notification: {e}")
    # Also send email notification
    try:
        send_email_notification(title, message, ntype, contract_id, user_email)
    except Exception as e: log.debug(f"create_notification: {e}")

# ─── Email Notifications ───────────────────────────────────────────────
def send_email_notification(title, message="", ntype="info", contract_id=None, user_email=None):
    """Send email via Resend API if configured"""
    if not RESEND_API_KEY: return
    if not sb: return
    try:
        # Get recipients: either specific user or all users with email notifications enabled
        recipients = []
        if user_email:
            # Check if this user has email notifications enabled
            pref = sb.table("email_preferences").select("*").eq("user_email", user_email).execute()
            if pref.data and pref.data[0].get("enabled"):
                # Check event type preference
                p = pref.data[0]
                if _should_email(p, ntype):
                    recipients.append(user_email)
        else:
            # Broadcast: send to all users with email notifications enabled
            prefs = sb.table("email_preferences").select("*").eq("enabled", True).execute()
            for p in (prefs.data or []):
                if _should_email(p, ntype):
                    recipients.append(p["user_email"])

        if not recipients: return

        # Build email HTML
        color = NOTIFICATION_COLORS.get(ntype, DEFAULT_TAG_COLOR)

        html = f"""<!DOCTYPE html><html><body style="font-family:'Helvetica',Arial,sans-serif;background:#f5f7fa;margin:0;padding:0">
<div style="max-width:600px;margin:0 auto;padding:20px">
<div style="background:#0f172a;padding:20px 30px;border-radius:12px 12px 0 0">
<h1 style="margin:0;color:#fff;font-size:18px">EMB CLM</h1>
<p style="margin:4px 0 0;color:#64748b;font-size:12px">Contract Lifecycle Management</p>
</div>
<div style="background:#fff;padding:30px;border:1px solid #e2e8f0;border-top:none">
<div style="display:inline-block;padding:4px 12px;border-radius:20px;font-size:11px;font-weight:600;color:#fff;background:{color};margin-bottom:16px">{ntype.upper()}</div>
<h2 style="margin:0 0 12px;color:#0f172a;font-size:18px;font-weight:600">{title}</h2>
<p style="margin:0 0 20px;color:#334155;font-size:14px;line-height:1.6">{message}</p>
{f'<a href="https://contract-cli-six.vercel.app" style="display:inline-block;padding:10px 24px;background:#2563eb;color:#fff;border-radius:8px;text-decoration:none;font-size:13px;font-weight:600">View in CLM</a>' if contract_id else ''}
</div>
<div style="padding:16px 30px;background:#f8fafc;border:1px solid #e2e8f0;border-top:none;border-radius:0 0 12px 12px;text-align:center">
<p style="margin:0;color:#94a3b8;font-size:11px">You're receiving this because you have email notifications enabled in EMB CLM.<br>
<a href="https://contract-cli-six.vercel.app" style="color:#64748b">Manage preferences</a></p>
</div></div></body></html>"""

        for email in recipients[:MAX_EMAIL_RECIPIENTS]:
            for attempt in range(2):
                try:
                    r = http.post("https://api.resend.com/emails",
                        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
                        json={"from": EMAIL_FROM, "to": [email], "subject": f"[EMB CLM] {title}", "html": html},
                        timeout=10)
                    if r.status_code in (200, 201): break
                except Exception as e:
                    if attempt == 0: time.sleep(1)
                    else: log.warning(f"Email send failed after retry: {e}")
    except Exception as e:
        log.warning(f"Email notification failed: {e}")

def _should_email(pref, ntype):
    """Check if notification type matches user preferences"""
    mapping = {
        "info": "on_status_change",
        "approval": "on_approval",
        "comment": "on_comment",
        "expiry": "on_expiry",
        "success": "on_status_change",
        "workflow": "on_workflow"
    }
    field = mapping.get(ntype, "on_status_change")
    return pref.get(field, True)

# ─── Workflow Engine ────────────────────────────────────────────────────
def run_workflows(event, contract_id, context=None):
    """Execute matching workflow rules for a given event"""
    if not sb: return
    context = context or {}
    try:
        rules = sb.table("workflow_rules").select("*").eq("trigger_event", event).eq("is_active", True).order("priority", desc=True).execute().data
        for rule in rules:
            cond = rule.get("trigger_condition", {}) or {}
            conf = rule.get("action_config", {}) or {}

            # Check conditions
            if event == "status_change":
                if cond.get("to_status") and cond["to_status"] != context.get("to_status"): continue
                if cond.get("from_status") and cond["from_status"] != context.get("from_status"): continue
            elif event == "contract_created":
                if cond.get("min_value"):
                    val = context.get("value", "")
                    nums = re.findall(r'[\d]+', str(val).replace(",", ""))
                    num_val = float(nums[0]) if nums else 0
                    if num_val < float(cond["min_value"]): continue
                if cond.get("contract_type") and cond["contract_type"] != context.get("contract_type"): continue

            # Execute action
            action_detail = ""
            try:
                if rule["action_type"] == "add_tag":
                    tag = conf.get("tag", "")
                    color = conf.get("color", DEFAULT_TAG_COLOR)
                    if tag:
                        existing = sb.table("contract_tags").select("id").eq("contract_id", contract_id).eq("tag_name", tag).execute()
                        if not existing.data:
                            sb.table("contract_tags").insert({"contract_id": contract_id, "tag_name": tag, "tag_color": color, "created_by": "Workflow"}).execute()
                        action_detail = f"Added tag '{tag}'"

                elif rule["action_type"] == "auto_approve":
                    approver = conf.get("approver", "Auto")
                    comments = conf.get("comments", "Auto-generated approval request")
                    sb.table("contract_approvals").insert({
                        "contract_id": contract_id, "approver_name": approver,
                        "status": "pending", "comments": comments
                    }).execute()
                    action_detail = f"Approval requested from '{approver}'"

                elif rule["action_type"] == "change_status":
                    new_s = conf.get("status", "")
                    if new_s in VALID_STATUSES:
                        upd = {"status": new_s}
                        if new_s == "executed": upd["executed_at"] = datetime.now().isoformat()
                        sb.table("contracts").update(upd).eq("id", contract_id).execute()
                        action_detail = f"Status changed to '{new_s}'"

                elif rule["action_type"] == "create_obligation":
                    title = conf.get("title", "Auto-generated obligation")
                    deadline = conf.get("deadline", "")
                    sb.table("contract_obligations").insert({
                        "contract_id": contract_id, "title": title,
                        "deadline": deadline or None, "assigned_to": conf.get("assigned_to", ""),
                        "status": "pending"
                    }).execute()
                    action_detail = f"Obligation created: '{title}'"

                elif rule["action_type"] == "notify_webhook":
                    msg = conf.get("message", "Workflow notification")
                    cname = context.get("name", f"Contract #{contract_id}")
                    fire_webhooks(f"workflow.{event}", {"contract_id": contract_id, "name": cname, "message": msg, "rule": rule["name"]})
                    action_detail = f"Webhook notified: '{msg}'"

            except Exception as ex:
                action_detail = f"Error: {str(ex)[:100]}"

            # Log execution
            if action_detail:
                try:
                    sb.table("workflow_log").insert({
                        "rule_id": rule["id"], "rule_name": rule["name"],
                        "contract_id": contract_id, "trigger_event": event,
                        "action_taken": rule["action_type"], "details": action_detail
                    }).execute()
                except Exception as e: log.debug(f"run_workflows: {e}")
                log_activity(contract_id, "workflow_executed", "Automation", f"{rule['name']}: {action_detail}")
    except Exception as e:
        log.error(f"Workflow engine error: {e}")

# ─── Redline / Track Changes ───────────────────────────────────────────
def _word_diff(old_text, new_text):
    """Generate word-level diff with inline markup"""
    old_words = old_text.split()
    new_words = new_text.split()
    sm = difflib.SequenceMatcher(None, old_words, new_words)
    result = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            result.append({"type": "equal", "text": " ".join(old_words[i1:i2])})
        elif op == "delete":
            result.append({"type": "delete", "text": " ".join(old_words[i1:i2])})
        elif op == "insert":
            result.append({"type": "insert", "text": " ".join(new_words[j1:j2])})
        elif op == "replace":
            result.append({"type": "delete", "text": " ".join(old_words[i1:i2])})
            result.append({"type": "insert", "text": " ".join(new_words[j1:j2])})
    return result

def _line_diff(old_text, new_text):
    """Generate line-level diff for structured view"""
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    diff = list(difflib.unified_diff(old_lines, new_lines, fromfile="Previous", tofile="Current", lineterm=""))
    additions = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    deletions = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
    return diff, additions, deletions

# ─── Status Transition ──────────────────────────────────────────────────
def _transition_status(cid, new_status, user="User"):
    """Enforce state machine transitions. Returns (success_response, status_code)."""
    from auth import err
    if new_status not in VALID_STATUSES:
        return err(f"Status must be one of: {', '.join(VALID_STATUSES)}", 400)
    contract = sb.table("contracts").select("id,status,name").eq("id", cid).execute()
    if not contract.data:
        return err("Contract not found", 404)
    old = contract.data[0]["status"]
    allowed = VALID_TRANSITIONS.get(old, set())
    if new_status not in allowed:
        return err(f"Cannot transition from '{old}' to '{new_status}'. Allowed: {', '.join(allowed)}", 400)
    # Approval gate for execution
    if new_status == "executed":
        pending_approvals = sb.table("contract_approvals").select("id").eq("contract_id", cid).eq("status", "pending").execute()
        if pending_approvals.data:
            return err(f"All approvals must be completed before execution. {len(pending_approvals.data)} pending.", 400)
    update_data = {"status": new_status}
    if new_status == "executed":
        update_data["executed_at"] = datetime.now().isoformat()
    sb.table("contracts").update(update_data).eq("id", cid).execute()
    name = contract.data[0]["name"]
    log_activity(cid, "status_changed", user, f"{old} -> {new_status}")
    fire_webhooks(f"contract.{new_status}", {"contract_id": cid, "name": name})
    create_notification(f"Status Changed: {name}", f"{old} -> {new_status}", "info", cid)
    run_workflows("status_change", cid, {"from_status": old, "to_status": new_status, "name": name})
    return jsonify({"message": f"Status changed from {old} to {new_status}"}), 200
