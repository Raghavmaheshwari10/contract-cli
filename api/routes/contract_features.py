"""Contract feature routes: comments, obligations, collaborators, approvals, signatures,
activity, parties, invoices, custom-fields, tags, links, share-links, auto-renew."""

import secrets
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify

from config import sb, log, VALID_TRANSITIONS, LEEGALITY_KEY, LEEGALITY_SALT, LEEGALITY_URL, RESEND_API_KEY, EMAIL_FROM
from auth import (
    err, _sanitize, _sanitize_dict, _valid_email,
    auth, role_required, need_db,
)
from helpers import log_activity, fire_webhooks, create_notification, _transition_status
import requests as http
import hmac
import hashlib
import json as J

bp = Blueprint("contract_features", __name__)


def _escape_like(s):
    return s.replace("%", "\\%").replace("_", "\\_")


# ─── Comments ──────────────────────────────────────────────────────────────

@bp.route("/api/contracts/<int:cid>/comments", methods=["GET"])
@auth
@need_db
def list_comments(cid):
    r = sb.table("contract_comments").select("*").eq("contract_id", cid).order("created_at", desc=True).limit(100).execute()
    return jsonify(r.data)


@bp.route("/api/contracts/<int:cid>/comments", methods=["POST"])
@auth
@need_db
def add_comment(cid):
    d = request.json or {}
    if not d.get("content"):
        return err("Content required", 400)
    row = {"contract_id": cid, "user_name": str(d.get("user_name") or "User")[:200],
           "content": str(d["content"])[:2000], "clause_ref": str(d.get("clause_ref") or "")[:200],
           "created_at": datetime.now().isoformat()}
    r = sb.table("contract_comments").insert(row).execute()
    log_activity(cid, "comment_added", row["user_name"], str(d["content"])[:100])
    create_notification(f"New Comment on Contract #{cid}", str(d["content"])[:100], "comment", cid)
    return jsonify(r.data[0] if r.data else {"message": "Created"}), 201


# ─── Obligations ───────────────────────────────────────────────────────────

@bp.route("/api/contracts/<int:cid>/obligations", methods=["GET"])
@auth
@need_db
def list_obligations(cid):
    r = sb.table("contract_obligations").select("*").eq("contract_id", cid).order("deadline").limit(100).execute()
    return jsonify(r.data)


@bp.route("/api/contracts/<int:cid>/obligations", methods=["POST"])
@auth
@role_required("editor")
@need_db
def add_obligation(cid):
    d = request.json or {}
    if not d.get("title"):
        return err("Title required", 400)
    row = {"contract_id": cid, "title": str(d["title"])[:500], "description": str(d.get("description") or "")[:2000],
           "deadline": d.get("deadline") or None, "status": "pending",
           "assigned_to": str(d.get("assigned_to") or "")[:200], "created_at": datetime.now().isoformat()}
    r = sb.table("contract_obligations").insert(row).execute()
    log_activity(cid, "obligation_added", "User", str(d["title"])[:100])
    return jsonify(r.data[0] if r.data else {"message": "Created"}), 201


@bp.route("/api/obligations/<int:oid>", methods=["PUT"])
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
    if not u:
        return err("Nothing to update", 400)
    sb.table("contract_obligations").update(u).eq("id", oid).execute()
    return jsonify({"message": "Updated"})


@bp.route("/api/obligations/overdue", methods=["GET"])
@auth
@need_db
def get_overdue_obligations():
    today = datetime.now().strftime("%Y-%m-%d")
    rows = sb.table("contract_obligations").select("*").eq("status", "pending").lt("deadline", today).order("deadline").execute().data or []
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


@bp.route("/api/obligations/escalate", methods=["POST"])
@auth
@role_required("manager")
@need_db
def escalate_obligations():
    d = request.json or {}
    escalate_to = d.get("escalate_to", "").strip()
    obligation_ids = d.get("obligation_ids", [])
    if not obligation_ids:
        return err("No obligations selected", 400)
    escalated = 0
    for oid in obligation_ids:
        ob = sb.table("contract_obligations").select("*").eq("id", oid).execute()
        if not ob.data:
            continue
        o = ob.data[0]
        sb.table("contract_obligations").update({
            "escalated": True, "escalated_to": escalate_to or "manager",
            "escalated_at": datetime.now().isoformat()
        }).eq("id", oid).execute()
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


@bp.route("/api/obligations/auto-escalate", methods=["POST"])
@auth
@role_required("admin")
@need_db
def auto_escalate_obligations():
    d = request.json or {}
    try:
        threshold_days = int(d.get("threshold_days", 3))
    except (ValueError, TypeError):
        threshold_days = 3
    escalate_to = d.get("escalate_to", "manager")
    cutoff = (datetime.now() - timedelta(days=threshold_days)).strftime("%Y-%m-%d")
    rows = sb.table("contract_obligations").select("*").eq("status", "pending").lt("deadline", cutoff).execute().data or []
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

@bp.route("/api/contracts/<int:cid>/collaborators", methods=["GET"])
@auth
@need_db
def list_collaborators(cid):
    r = sb.table("contract_collaborators").select("*").eq("contract_id", cid).order("created_at", desc=True).execute()
    return jsonify(r.data)


@bp.route("/api/contracts/<int:cid>/collaborators", methods=["POST"])
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
    chk = sb.table("contracts").select("id,name").eq("id", cid).execute()
    if not chk.data:
        return err("Contract not found", 404)
    user = sb.table("clm_users").select("name,email").eq("email", email).execute()
    user_name = user.data[0]["name"] if user.data else d.get("user_name", email)
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


@bp.route("/api/contracts/<int:cid>/collaborators/<int:collab_id>", methods=["PUT"])
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


@bp.route("/api/contracts/<int:cid>/collaborators/<int:collab_id>", methods=["DELETE"])
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

@bp.route("/api/contracts/<int:cid>/approvals", methods=["GET"])
@auth
@need_db
def list_approvals(cid):
    r = sb.table("contract_approvals").select("*").eq("contract_id", cid).order("created_at", desc=True).limit(100).execute()
    return jsonify(r.data)


@bp.route("/api/contracts/<int:cid>/approvals", methods=["POST"])
@auth
@role_required("editor")
@need_db
def request_approval(cid):
    d = request.json or {}
    if not d.get("approver_name"):
        return err("Approver name required", 400)
    row = {"contract_id": cid, "approver_name": d["approver_name"],
           "status": "pending", "comments": d.get("comments", ""),
           "created_at": datetime.now().isoformat(), "updated_at": datetime.now().isoformat()}
    contract = sb.table("contracts").select("id,status").eq("id", cid).execute()
    if not contract.data:
        return err("Contract not found", 404)
    cur_status = contract.data[0].get("status", "draft")
    if cur_status not in ("draft", "in_review"):
        return err(f"Cannot request approval when contract is '{cur_status}'", 400)
    existing = sb.table("contract_approvals").select("id").eq("contract_id", cid).eq("approver_name", d["approver_name"]).eq("status", "pending").execute()
    if existing.data:
        return err(f"Pending approval from {d['approver_name']} already exists", 409)
    r = sb.table("contract_approvals").insert(row).execute()
    log_activity(cid, "approval_requested", request.user_email, f"Approval requested from {d['approver_name']}")
    create_notification(f"Approval Requested", f"{d['approver_name']} needs to review Contract #{cid}", "approval", cid)
    sb.table("contracts").update({"status": "pending"}).eq("id", cid).execute()
    return jsonify(r.data[0]), 201


@bp.route("/api/approvals/<int:aid>", methods=["PUT"])
@auth
@role_required("manager")
@need_db
def respond_approval(aid):
    d = request.json or {}
    action = d.get("action")
    if action not in ("approved", "rejected"):
        return err("Action must be approved or rejected", 400)
    appr = sb.table("contract_approvals").select("*").eq("id", aid).execute()
    if not appr.data:
        return err("Not found", 404)
    sb.table("contract_approvals").update({
        "status": action, "comments": d.get("comments", ""), "updated_at": datetime.now().isoformat()
    }).eq("id", aid).execute()
    cid = appr.data[0]["contract_id"]
    new_status = "in_review" if action == "approved" else "rejected"
    log_activity(cid, f"approval_{action}", appr.data[0]["approver_name"], d.get("comments", ""))
    fire_webhooks(f"contract.{action}", {"contract_id": cid})
    chk = sb.table("contracts").select("status").eq("id", cid).execute()
    old_status = chk.data[0]["status"] if chk.data else ""
    transition_applied = False
    if new_status in VALID_TRANSITIONS.get(old_status, set()):
        _transition_status(cid, new_status, appr.data[0]["approver_name"])
        transition_applied = True
    return jsonify({"message": f"Approval {action}", "status_updated": transition_applied})


# ─── Signatures ────────────────────────────────────────────────────────────

@bp.route("/api/contracts/<int:cid>/signatures", methods=["GET"])
@auth
@need_db
def list_signatures(cid):
    r = sb.table("contract_signatures").select("*").eq("contract_id", cid).order("signed_at").limit(100).execute()
    return jsonify(r.data)


@bp.route("/api/contracts/<int:cid>/sign", methods=["POST"])
@auth
@role_required("manager")
@need_db
def sign_contract(cid):
    d = request.json or {}
    if not d.get("signer_name") or not d.get("signature_data"):
        return err("Signer name and signature required", 400)
    contract = sb.table("contracts").select("id,status").eq("id", cid).execute()
    if not contract.data:
        return err("Contract not found", 404)
    cur_status = contract.data[0].get("status", "draft")
    if cur_status not in ("pending", "in_review", "executed"):
        return err(f"Cannot sign a contract in '{cur_status}' status. Submit for approval first.", 400)
    row = {"contract_id": cid, "signer_name": d["signer_name"],
           "signer_email": d.get("signer_email", ""), "signer_designation": d.get("signer_designation", ""),
           "signature_data": d["signature_data"], "ip_address": request.remote_addr,
           "signed_at": datetime.now().isoformat()}
    r = sb.table("contract_signatures").insert(row).execute()
    log_activity(cid, "signed", d["signer_name"], f"Contract signed by {d['signer_name']}")
    sb.table("contracts").update({"status": "executed", "executed_at": datetime.now().isoformat()}).eq("id", cid).execute()
    fire_webhooks("contract.executed", {"contract_id": cid, "signer": d["signer_name"]})
    return jsonify(r.data[0]), 201


# ─── Leegality E-Sign Integration ────────────────────────────────────────

@bp.route("/api/contracts/<int:cid>/esign", methods=["POST"])
@auth
@need_db
def leegality_esign(cid):
    if not LEEGALITY_KEY:
        return err("Leegality API not configured. Set LEEGALITY_API_KEY env var.", 503)
    c = sb.table("contracts").select("id,name,content,content_html,party_name").eq("id", cid).execute()
    if not c.data:
        return err("Not found", 404)
    contract = c.data[0]
    d = request.json or {}
    signers = d.get("signers", [])
    if not signers or not all(s.get("name") and s.get("email") for s in signers):
        return err("Signers required — each needs name and email", 400)
    try:
        leeg_headers = {
            "X-Auth-Token": LEEGALITY_KEY,
            "Content-Type": "application/json"
        }
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
            err_resp = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"message": r.text}
            return err(f"Leegality error: {err_resp.get('message', r.text)}", r.status_code)

        result = r.json()
        doc_id = result.get("data", {}).get("documentId") or result.get("documentId", "")
        signing_url = result.get("data", {}).get("signingUrl") or result.get("signingUrl", "")

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


# ─── Activity Timeline ────────────────────────────────────────────────────

@bp.route("/api/contracts/<int:cid>/activity", methods=["GET"])
@auth
@need_db
def get_activity(cid):
    try:
        limit = min(int(request.args.get("limit", 200)), 500)
    except (ValueError, TypeError):
        limit = 200
    r = sb.table("contract_activity").select("*").eq("contract_id", cid).order("created_at", desc=True).limit(limit).execute()
    return jsonify(r.data)


# ─── Contract Parties (Multi-vendor) ──────────────────────────────────────

@bp.route("/api/contracts/<int:cid>/parties", methods=["GET"])
@auth
@need_db
def get_contract_parties(cid):
    rows = sb.table("contract_parties").select("*").eq("contract_id", cid).order("created_at").execute().data or []
    return jsonify(rows)


@bp.route("/api/contracts/<int:cid>/parties", methods=["POST"])
@auth
@role_required("editor")
@need_db
def add_contract_party(cid):
    chk = sb.table("contracts").select("id").eq("id", cid).execute()
    if not chk.data:
        return err("Contract not found", 404)
    d = _sanitize_dict(request.json or {})
    if not d.get("party_name", "").strip():
        return err("party_name required", 400)
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


@bp.route("/api/contract-parties/<int:pid>", methods=["PUT"])
@auth
@role_required("editor")
@need_db
def update_contract_party(pid):
    chk = sb.table("contract_parties").select("*").eq("id", pid).execute()
    if not chk.data:
        return err("Party not found", 404)
    d = _sanitize_dict(request.json or {})
    u = {}
    for f in ["party_name", "party_type", "role", "party_value", "scope", "status", "contact_name", "contact_email", "notes"]:
        if f in d:
            u[f] = d[f]
    if not u:
        return err("Nothing to update", 400)
    if "party_type" in u and u["party_type"] not in ("client", "vendor", "subcontractor"):
        return err("party_type must be client, vendor, or subcontractor", 400)
    u["updated_at"] = datetime.now().isoformat()
    sb.table("contract_parties").update(u).eq("id", pid).execute()
    cid = chk.data[0]["contract_id"]
    log_activity(cid, "party_updated", request.user_email, f"Party '{chk.data[0]['party_name']}' updated")
    return jsonify({"message": "Updated"})


@bp.route("/api/contract-parties/<int:pid>", methods=["DELETE"])
@auth
@role_required("editor")
@need_db
def delete_contract_party(pid):
    chk = sb.table("contract_parties").select("*").eq("id", pid).execute()
    if not chk.data:
        return err("Party not found", 404)
    cid = chk.data[0]["contract_id"]
    sb.table("contract_parties").delete().eq("id", pid).execute()
    log_activity(cid, "party_removed", request.user_email, f"Party '{chk.data[0]['party_name']}' removed")
    return jsonify({"message": "Deleted"})


# ─── PO/Invoice Linkage ─────────────────────────────────────────────────

@bp.route("/api/contracts/<int:cid>/invoices", methods=["GET"])
@auth
@need_db
def get_contract_invoices(cid):
    rows = sb.table("contract_invoices").select("*").eq("contract_id", cid).order("invoice_date", desc=True).execute().data or []
    return jsonify(rows)


@bp.route("/api/contracts/<int:cid>/invoices", methods=["POST"])
@auth
@role_required("editor")
@need_db
def add_contract_invoice(cid):
    chk = sb.table("contracts").select("id").eq("id", cid).execute()
    if not chk.data:
        return err("Contract not found", 404)
    d = _sanitize_dict(request.json or {})
    if not d.get("invoice_number", "").strip():
        return err("Invoice number required", 400)
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


@bp.route("/api/contract-invoices/<int:iid>", methods=["PUT"])
@auth
@role_required("editor")
@need_db
def update_contract_invoice(iid):
    chk = sb.table("contract_invoices").select("*").eq("id", iid).execute()
    if not chk.data:
        return err("Not found", 404)
    d = _sanitize_dict(request.json or {})
    u = {}
    for f in ["invoice_number", "po_number", "amount", "invoice_date", "due_date", "status", "notes"]:
        if f in d:
            u[f] = d[f]
    if not u:
        return err("Nothing to update", 400)
    u["updated_at"] = datetime.now().isoformat()
    sb.table("contract_invoices").update(u).eq("id", iid).execute()
    return jsonify({"message": "Updated"})


@bp.route("/api/contract-invoices/<int:iid>", methods=["DELETE"])
@auth
@role_required("editor")
@need_db
def delete_contract_invoice(iid):
    chk = sb.table("contract_invoices").select("*").eq("id", iid).execute()
    if not chk.data:
        return err("Not found", 404)
    sb.table("contract_invoices").delete().eq("id", iid).execute()
    return jsonify({"message": "Deleted"})


# ─── Custom Fields (per-contract) ────────────────────────────────────────

@bp.route("/api/contracts/<int:cid>/custom-fields")
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


@bp.route("/api/contracts/<int:cid>/custom-fields", methods=["POST"])
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
        if not fid:
            continue
        try:
            existing = sb.table("custom_field_values").select("id").eq("contract_id", cid).eq("field_id", fid).execute()
            if existing.data:
                sb.table("custom_field_values").update({"field_value": val, "updated_by": getattr(request, 'user_email', 'User'), "updated_at": datetime.now().isoformat()}).eq("id", existing.data[0]["id"]).execute()
            else:
                sb.table("custom_field_values").insert({"contract_id": cid, "field_id": fid, "field_value": val, "updated_by": getattr(request, 'user_email', 'User')}).execute()
            saved += 1
        except Exception as e:
            log.debug(f"save_contract_custom_fields: {e}")
    log_activity(cid, "custom_fields_updated", getattr(request, 'user_email', 'User'), f"{saved} custom fields updated")
    return jsonify({"saved": saved, "message": f"{saved} fields saved"})


# ─── Contract Tags ────────────────────────────────────────────────────────

@bp.route("/api/contracts/<int:cid>/tags")
@auth
@need_db
def get_contract_tags(cid):
    r = sb.table("contract_tags").select("*").eq("contract_id", cid).order("created_at").execute()
    return jsonify(r.data)


@bp.route("/api/contracts/<int:cid>/tags", methods=["POST"])
@auth
@role_required("editor")
@need_db
def add_contract_tag(cid):
    d = request.json or {}
    tag = _sanitize(d.get("tag_name", "")).strip()
    color = d.get("tag_color", "#2563eb")
    if not tag:
        return err("Tag name required", 400)
    existing = sb.table("contract_tags").select("id").eq("contract_id", cid).eq("tag_name", tag).execute()
    if existing.data:
        return err("Tag already exists on this contract", 400)
    r = sb.table("contract_tags").insert({
        "contract_id": cid, "tag_name": tag, "tag_color": color,
        "created_by": getattr(request, 'user_email', 'User')
    }).execute()
    log_activity(cid, "tag_added", getattr(request, 'user_email', 'User'), f"Tag '{tag}' added")
    return jsonify(r.data[0]), 201


@bp.route("/api/contracts/<int:cid>/tags/<int:tid>", methods=["DELETE"])
@auth
@role_required("editor")
@need_db
def remove_contract_tag(cid, tid):
    tag = sb.table("contract_tags").select("tag_name").eq("id", tid).eq("contract_id", cid).execute()
    sb.table("contract_tags").delete().eq("id", tid).eq("contract_id", cid).execute()
    if tag.data:
        log_activity(cid, "tag_removed", getattr(request, 'user_email', 'User'), f"Tag '{tag.data[0]['tag_name']}' removed")
    return jsonify({"message": "Tag removed"})


# ─── Contract Linking (Client ↔ Vendor) ──────────────────────────────────

@bp.route("/api/contracts/<int:cid>/links", methods=["GET"])
@auth
@need_db
def get_contract_links(cid):
    c = sb.table("contracts").select("id,contract_type").eq("id", cid).execute()
    if not c.data:
        return err("Not found", 404)
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


@bp.route("/api/contracts/<int:cid>/links", methods=["POST"])
@auth
@role_required("editor")
@need_db
def add_contract_link(cid):
    d = request.json or {}
    target_id = d.get("linked_contract_id")
    if not target_id:
        return err("Missing linked_contract_id", 400)

    contracts = sb.table("contracts").select("id,contract_type,name").in_("id", [cid, int(target_id)]).execute().data or []
    if len(contracts) < 2:
        return err("One or both contracts not found", 404)

    by_id = {c["id"]: c for c in contracts}
    c1, c2 = by_id.get(cid), by_id.get(int(target_id))
    if not c1 or not c2:
        return err("Contract not found", 404)

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


@bp.route("/api/contract-links/<int:link_id>", methods=["DELETE"])
@auth
@role_required("editor")
@need_db
def delete_contract_link(link_id):
    link = sb.table("contract_links").select("*").eq("id", link_id).execute()
    if not link.data:
        return err("Link not found", 404)
    sb.table("contract_links").delete().eq("id", link_id).execute()
    log_activity(link.data[0]["client_contract_id"], "unlinked", getattr(request, 'user_email', 'User'),
                 f"Unlinked from contract #{link.data[0]['vendor_contract_id']}")
    return jsonify({"message": "Unlinked"})


@bp.route("/api/contract-links", methods=["GET"])
@auth
@need_db
def list_all_links():
    rows = sb.table("contract_links").select("*").execute().data or []
    if not rows:
        return jsonify([])
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


# ─── Shareable Review Links ──────────────────────────────────────────────

@bp.route("/api/contracts/<int:cid>/share-links", methods=["GET"])
@auth
@need_db
def get_share_links(cid):
    rows = sb.table("contract_share_links").select("*").eq("contract_id", cid).order("created_at", desc=True).execute().data or []
    return jsonify(rows)


@bp.route("/api/contracts/<int:cid>/share-links", methods=["POST"])
@auth
@role_required("editor")
@need_db
def create_share_link(cid):
    chk = sb.table("contracts").select("id").eq("id", cid).execute()
    if not chk.data:
        return err("Contract not found", 404)
    d = request.json or {}
    try:
        expires_hours = min(int(d.get("expires_hours", 72)), 720)
    except (ValueError, TypeError):
        expires_hours = 72
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


@bp.route("/api/share-links/<int:lid>/revoke", methods=["POST"])
@auth
@role_required("editor")
@need_db
def revoke_share_link(lid):
    chk = sb.table("contract_share_links").select("*").eq("id", lid).execute()
    if not chk.data:
        return err("Not found", 404)
    sb.table("contract_share_links").update({"is_active": False}).eq("id", lid).execute()
    log_activity(chk.data[0]["contract_id"], "share_link_revoked", request.user_email, "Share link revoked")
    return jsonify({"message": "Link revoked"})


@bp.route("/api/shared/<token>", methods=["GET"])
def view_shared_contract(token):
    """Public endpoint — no auth required. View a shared contract via token."""
    if not sb:
        return err("Service unavailable", 503)
    link = sb.table("contract_share_links").select("*").eq("token", token).eq("is_active", True).execute()
    if not link.data:
        return err("Invalid or expired link", 404)
    sl = link.data[0]
    if datetime.fromisoformat(sl["expires_at"].replace("Z", "+00:00")).replace(tzinfo=None) < datetime.now():
        return err("This link has expired", 410)
    sb.table("contract_share_links").update({
        "accessed_count": (sl.get("accessed_count", 0) or 0) + 1,
        "last_accessed_at": datetime.now().isoformat()
    }).eq("id", sl["id"]).execute()
    c = sb.table("contracts").select("id,name,party_name,contract_type,status,content,content_html,start_date,end_date,value,department,jurisdiction").eq("id", sl["contract_id"]).execute()
    if not c.data:
        return err("Contract not found", 404)
    return jsonify({
        "contract": c.data[0], "permissions": sl["permissions"],
        "recipient_name": sl.get("recipient_name", ""),
        "expires_at": sl["expires_at"]
    })


@bp.route("/api/shared/<token>/comments", methods=["POST"])
def add_shared_comment(token):
    """Public endpoint — add comment on a shared contract (if permission allows)"""
    if not sb:
        return err("Service unavailable", 503)
    link = sb.table("contract_share_links").select("*").eq("token", token).eq("is_active", True).execute()
    if not link.data:
        return err("Invalid or expired link", 404)
    sl = link.data[0]
    if sl["permissions"] != "comment":
        return err("View-only access", 403)
    if datetime.fromisoformat(sl["expires_at"].replace("Z", "+00:00")).replace(tzinfo=None) < datetime.now():
        return err("This link has expired", 410)
    d = request.json or {}
    if not d.get("text", "").strip():
        return err("Comment text required", 400)
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


# ─── Auto-Renew ───────────────────────────────────────────────────────────

@bp.route("/api/contracts/<int:cid>/auto-renew", methods=["POST"])
@auth
@role_required("editor")
@need_db
def auto_renew_contract(cid):
    c = sb.table("contracts").select("*").eq("id", cid).execute()
    if not c.data:
        return err("Not found", 404)
    orig = c.data[0]
    existing_renewal = sb.table("contracts").select("id,name").ilike("name", f"%Renewal of {_escape_like(orig['name'])}%").eq("status", "draft").execute()
    if existing_renewal.data:
        return jsonify({"error": {"message": f"A renewal draft already exists (#{existing_renewal.data[0]['id']})", "code": 409}, "id": existing_renewal.data[0]["id"]}), 409
    duration_days = 365
    if orig.get("start_date") and orig.get("end_date"):
        try:
            sd = datetime.strptime(orig["start_date"], "%Y-%m-%d")
            ed = datetime.strptime(orig["end_date"], "%Y-%m-%d")
            duration_days = (ed - sd).days
        except (ValueError, TypeError):
            pass
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
    obs = sb.table("contract_obligations").select("title,description,assigned_to").eq("contract_id", cid).execute().data or []
    for o in obs:
        sb.table("contract_obligations").insert({
            "contract_id": new_id, "title": o["title"],
            "description": o.get("description", ""), "assigned_to": o.get("assigned_to", ""),
            "status": "pending", "created_at": datetime.now().isoformat()
        }).execute()
    create_notification(f"Contract renewed: {orig['name']}", f"Renewal draft created from contract #{cid}", "info", new_id)
    return jsonify({"id": new_id, "message": f"Renewal draft created (#{new_id})"}), 201
