"""Catalog routes: templates, clauses, tag-presets, workflows, workflow-log,
custom-fields (defs), renewals, parties (unique list), counterparty."""

import re
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify

from config import sb, log
from auth import (
    err, _sanitize, _sanitize_dict,
    auth, role_required, need_db,
)
from helpers import log_activity, fire_webhooks

bp = Blueprint("catalog", __name__)


def _escape_like(s):
    return s.replace("%", "\\%").replace("_", "\\_")


# ─── Templates ─────────────────────────────────────────────────────────────

@bp.route("/api/templates")
@auth
@need_db
def list_templates():
    r = sb.table("contract_templates").select("id,name,category,contract_type,description").execute()
    return jsonify(r.data)


@bp.route("/api/templates/<int:tid>")
@auth
@need_db
def get_template(tid):
    r = sb.table("contract_templates").select("*").eq("id", tid).execute()
    if not r.data:
        return err("Not found", 404)
    return jsonify(r.data[0])


@bp.route("/api/templates", methods=["POST"])
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


@bp.route("/api/templates/<int:tid>", methods=["PUT"])
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
        if len(name) < 3:
            return err("Template name must be at least 3 characters", 400)
        upd["name"] = name
    if "category" in d:
        upd["category"] = _sanitize(d["category"], 100) or "other"
    if "contract_type" in d:
        if d["contract_type"] not in ("client", "vendor"):
            return err("Type must be 'client' or 'vendor'", 400)
        upd["contract_type"] = d["contract_type"]
    if "description" in d:
        upd["description"] = _sanitize(d["description"], 2000)
    if "content" in d:
        content = _sanitize(d["content"], 50000)
        if len(content) < 10:
            return err("Template content must be at least 10 characters", 400)
        upd["content"] = content
    if "clauses" in d:
        upd["clauses"] = d["clauses"] if isinstance(d["clauses"], list) else []
    if not upd:
        return err("No fields to update", 400)
    sb.table("contract_templates").update(upd).eq("id", tid).execute()
    r = sb.table("contract_templates").select("*").eq("id", tid).execute()
    return jsonify(r.data[0] if r.data else {"message": "Updated"})


@bp.route("/api/templates/<int:tid>", methods=["DELETE"])
@auth
@role_required("manager")
@need_db
def delete_template(tid):
    chk = sb.table("contract_templates").select("id").eq("id", tid).execute()
    if not chk.data:
        return err("Not found", 404)
    sb.table("contract_templates").delete().eq("id", tid).execute()
    return jsonify({"message": "Template deleted"})


# ─── Clause Library ──────────────────────────────────────────────────────

@bp.route("/api/clauses", methods=["GET"])
@auth
@need_db
def list_clauses():
    cat = request.args.get("category")
    q = sb.table("clause_library").select("*").order("usage_count", desc=True)
    if cat:
        q = q.eq("category", cat)
    return jsonify(q.limit(200).execute().data)


@bp.route("/api/clauses", methods=["POST"])
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


@bp.route("/api/clauses/<int:cid>", methods=["PUT"])
@auth
@need_db
def update_clause(cid):
    d = request.json or {}
    u = {}
    for f in ["title", "category", "content", "tags"]:
        if f in d:
            u[f] = d[f]
    if not u:
        return err("Nothing to update", 400)
    sb.table("clause_library").update(u).eq("id", cid).execute()
    return jsonify({"message": "Updated"})


@bp.route("/api/clauses/<int:cid>", methods=["DELETE"])
@auth
@need_db
def delete_clause(cid):
    sb.table("clause_library").delete().eq("id", cid).execute()
    return jsonify({"message": "Deleted"})


@bp.route("/api/clauses/<int:cid>/use", methods=["POST"])
@auth
@need_db
def use_clause(cid):
    c = sb.table("clause_library").select("usage_count").eq("id", cid).execute()
    if not c.data:
        return err("Clause not found", 404)
    sb.table("clause_library").update({"usage_count": (c.data[0].get("usage_count") or 0) + 1}).eq("id", cid).execute()
    return jsonify({"message": "Usage tracked"})


# ─── Tag Presets ──────────────────────────────────────────────────────────

@bp.route("/api/tag-presets")
@auth
@need_db
def list_tag_presets():
    r = sb.table("tag_presets").select("*").order("name").execute()
    return jsonify(r.data)


@bp.route("/api/tag-presets", methods=["POST"])
@auth
@role_required("manager")
@need_db
def create_tag_preset():
    d = request.json or {}
    name = _sanitize(d.get("name", "")).strip()
    color = d.get("color", "#2563eb")
    if not name:
        return err("Name required", 400)
    try:
        r = sb.table("tag_presets").insert({"name": name, "color": color, "description": d.get("description", "")}).execute()
        return jsonify(r.data[0]), 201
    except Exception as e:
        return err("Tag preset already exists", 400)


@bp.route("/api/tag-presets/<int:tid>", methods=["DELETE"])
@auth
@role_required("admin")
@need_db
def delete_tag_preset(tid):
    sb.table("tag_presets").delete().eq("id", tid).execute()
    return jsonify({"message": "Deleted"})


# ─── Workflow Rules ───────────────────────────────────────────────────────

@bp.route("/api/workflows")
@auth
@role_required("manager")
@need_db
def list_workflows():
    r = sb.table("workflow_rules").select("*").order("priority", desc=True).execute()
    return jsonify(r.data)


@bp.route("/api/workflows", methods=["POST"])
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
    if not name:
        return err("Name required", 400)
    if trigger not in valid_triggers:
        return err(f"Trigger must be one of: {', '.join(valid_triggers)}", 400)
    if action not in valid_actions:
        return err(f"Action must be one of: {', '.join(valid_actions)}", 400)
    r = sb.table("workflow_rules").insert({
        "name": name, "trigger_event": trigger,
        "trigger_condition": d.get("trigger_condition", {}),
        "action_type": action, "action_config": d.get("action_config", {}),
        "is_active": d.get("is_active", True), "priority": d.get("priority", 0),
        "created_by": getattr(request, 'user_email', 'System')
    }).execute()
    return jsonify(r.data[0]), 201


@bp.route("/api/workflows/<int:wid>", methods=["PUT"])
@auth
@role_required("admin")
@need_db
def update_workflow(wid):
    d = request.json or {}
    u = {}
    for f in ["name", "trigger_event", "trigger_condition", "action_type", "action_config", "is_active", "priority"]:
        if f in d:
            u[f] = d[f]
    if not u:
        return err("Nothing to update", 400)
    u["updated_at"] = datetime.now().isoformat()
    sb.table("workflow_rules").update(u).eq("id", wid).execute()
    return jsonify({"message": "Updated"})


@bp.route("/api/workflows/<int:wid>", methods=["DELETE"])
@auth
@role_required("admin")
@need_db
def delete_workflow(wid):
    sb.table("workflow_rules").delete().eq("id", wid).execute()
    return jsonify({"message": "Deleted"})


@bp.route("/api/workflow-log")
@auth
@role_required("manager")
@need_db
def get_workflow_log():
    r = sb.table("workflow_log").select("*").order("executed_at", desc=True).limit(100).execute()
    return jsonify(r.data)


# ─── Custom Field Definitions ─────────────────────────────────────────────

@bp.route("/api/custom-fields")
@auth
@need_db
def list_custom_fields():
    r = sb.table("custom_field_defs").select("*").order("display_order").execute()
    return jsonify(r.data)


@bp.route("/api/custom-fields", methods=["POST"])
@auth
@role_required("admin")
@need_db
def create_custom_field():
    d = request.json or {}
    name = _sanitize(d.get("field_name", "")).strip()
    ftype = d.get("field_type", "text")
    if not name:
        return err("Field name required", 400)
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


@bp.route("/api/custom-fields/<int:fid>", methods=["DELETE"])
@auth
@role_required("admin")
@need_db
def delete_custom_field(fid):
    sb.table("custom_field_values").delete().eq("field_id", fid).execute()
    sb.table("custom_field_defs").delete().eq("id", fid).execute()
    return jsonify({"message": "Deleted"})


# ─── Renewal Tracking ────────────────────────────────────────────────────

@bp.route("/api/renewals")
@auth
@need_db
def renewal_tracker():
    try:
        days = int(request.args.get("days", 90))
    except (ValueError, TypeError):
        days = 90
    today = datetime.now()
    future = (today + timedelta(days=days)).strftime("%Y-%m-%d")
    today_str = today.strftime("%Y-%m-%d")

    r = sb.table("contracts").select("id,name,party_name,contract_type,status,value,end_date,department").lte("end_date", future).gte("end_date", today_str).neq("status", "rejected").order("end_date").execute()

    contracts = r.data or []
    for c in contracts:
        try:
            end = datetime.strptime(c["end_date"], "%Y-%m-%d")
            c["days_left"] = (end - today).days
            if c["days_left"] <= 30:
                c["urgency"] = "critical"
            elif c["days_left"] <= 60:
                c["urgency"] = "warning"
            else:
                c["urgency"] = "normal"
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

@bp.route("/api/parties")
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


# ─── Counterparty View ───────────────────────────────────────────────────

@bp.route("/api/counterparty/<party_name>")
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
