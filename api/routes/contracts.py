"""Contract CRUD, status, versions, redline, diff, clone, bulk, compare, linkable, pdf, embed."""

import difflib
import html as html_mod
from datetime import datetime
from flask import Blueprint, request, jsonify, Response

from config import sb, log, VALID_TRANSITIONS
from auth import (
    err, _sanitize, _sanitize_dict,
    auth, role_required, need_db,
)
from ai import oai_h, oai_chat, embed_contract
from helpers import (
    log_activity, fire_webhooks, run_workflows,
    _word_diff, _line_diff, _transition_status,
)

import json as J

bp = Blueprint("contracts", __name__)


def _escape_like(s):
    """Escape SQL LIKE/ILIKE wildcards in user input."""
    return s.replace("%", "\\%").replace("_", "\\_")


def _parse_currency(val):
    """Parse currency string like '₹25,00,000' or '$48,000' to float."""
    import re
    if not val:
        return 0.0
    try:
        parsed = float(re.sub(r'[^\d.]', '', str(val)))
        return min(parsed, 1_000_000_000_000)
    except (ValueError, TypeError):
        return 0.0


# ─── Contracts CRUD ────────────────────────────────────────────────────────

@bp.route("/api/contracts", methods=["GET"])
@auth
@need_db
def list_contracts():
    ctype = request.args.get("type")
    status = request.args.get("status")
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1
    try:
        per = min(50, max(1, int(request.args.get("per_page", 20))))
    except (ValueError, TypeError):
        per = 20
    q = sb.table("contracts").select(
        "id,name,party_name,contract_type,status,start_date,end_date,value,added_on,notes,department,created_by",
        count="exact"
    ).order("added_on", desc=True)
    if ctype:
        q = q.eq("contract_type", ctype)
    if status:
        q = q.eq("status", status)
    off = (page - 1) * per
    r = q.range(off, off + per - 1).execute()
    total = r.count if r.count is not None else len(r.data)
    return jsonify({"data": r.data, "total": total, "page": page, "per_page": per, "pages": max(1, -(-total // per))})


@bp.route("/api/contracts", methods=["POST"])
@auth
@role_required("editor")
@need_db
def create_contract():
    d = _sanitize_dict(request.json or {})
    for f in ["name", "party_name", "contract_type", "content"]:
        if not str(d.get(f) or "").strip():
            return err(f"Missing: {f}", 400)
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
    if not r.data:
        return err("Failed to create contract", 500)
    cid = r.data[0]["id"]
    log_activity(cid, "created", row["created_by"], f"Contract '{row['name']}' created")
    fire_webhooks("contract.created", {"contract_id": cid, "name": row["name"]})
    run_workflows("contract_created", cid, {"name": row["name"], "value": d.get("value", ""), "contract_type": d["contract_type"], "department": d.get("department", "")})
    chunks = 0
    if oai_h():
        try:
            chunks = embed_contract(cid, d["content"], d["name"])
        except Exception as e:
            log.error(f"Embed failed: {e}")
    return jsonify({"id": cid, "message": "Created", "chunks_embedded": chunks}), 201


@bp.route("/api/contracts/<int:cid>", methods=["GET"])
@auth
@need_db
def get_contract(cid):
    r = sb.table("contracts").select("*").eq("id", cid).execute()
    if not r.data:
        return err("Not found", 404)
    return jsonify(r.data[0])


@bp.route("/api/contracts/<int:cid>", methods=["PUT"])
@auth
@role_required("editor")
@need_db
def update_contract(cid):
    chk = sb.table("contracts").select("id,content,content_html,name,status,updated_at").eq("id", cid).execute()
    if not chk.data:
        return err("Not found", 404)
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
    for f in ["name", "party_name", "contract_type", "start_date", "end_date", "value", "notes", "content",
              "content_html", "department", "jurisdiction", "governing_law"]:
        if f in d:
            u[f] = d[f]
    if not u:
        return err("Nothing to update", 400)
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
        except Exception as e:
            log.debug(f"update_contract: {e}")
    u["updated_at"] = datetime.now().isoformat()
    sb.table("contracts").update(u).eq("id", cid).execute()
    log_activity(cid, "updated", d.get("user", "User"), f"Contract updated: {', '.join(u.keys())}")
    chunks = 0
    if "content" in u and oai_h():
        try:
            chunks = embed_contract(cid, u["content"], u.get("name", chk.data[0].get("name", "")))
        except Exception as e:
            log.debug(f"update_contract: {e}")
    return jsonify({"message": "Updated", "chunks_embedded": chunks})


@bp.route("/api/contracts/<int:cid>", methods=["DELETE"])
@auth
@role_required("admin")
@need_db
def delete_contract(cid):
    chk = sb.table("contracts").select("id").eq("id", cid).execute()
    if not chk.data:
        return err("Not found", 404)
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
            pass
    try:
        sb.table("contract_links").delete().eq("target_id", cid).execute()
    except Exception:
        pass
    sb.table("contracts").delete().eq("id", cid).execute()
    log_activity(None, "contract_deleted", request.user_email, f"Contract #{cid} deleted with all related data")
    return jsonify({"message": "Deleted"})


# ─── Status Transitions ───────────────────────────────────────────────────

@bp.route("/api/contracts/<int:cid>/status", methods=["PUT"])
@auth
@role_required("manager")
@need_db
def update_status(cid):
    d = request.json or {}
    new_status = d.get("status", "")
    return _transition_status(cid, new_status, d.get("user", "User"))


# ─── Version History ──────────────────────────────────────────────────────

@bp.route("/api/contracts/<int:cid>/versions", methods=["GET"])
@auth
@need_db
def list_versions(cid):
    r = sb.table("contract_versions").select("id,contract_id,version_number,changed_by,change_summary,created_at").eq("contract_id", cid).order("version_number", desc=True).limit(50).execute()
    return jsonify(r.data)


@bp.route("/api/contracts/<int:cid>/versions/<int:vid>", methods=["GET"])
@auth
@need_db
def get_version(cid, vid):
    r = sb.table("contract_versions").select("*").eq("id", vid).eq("contract_id", cid).execute()
    if not r.data:
        return err("Not found", 404)
    return jsonify(r.data[0])


@bp.route("/api/contracts/<int:cid>/versions/<int:vid>/restore", methods=["POST"])
@auth
@need_db
def restore_version(cid, vid):
    v = sb.table("contract_versions").select("*").eq("id", vid).eq("contract_id", cid).execute()
    if not v.data:
        return err("Not found", 404)
    ver = v.data[0]
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

@bp.route("/api/contracts/<int:cid>/redline", methods=["GET"])
@auth
@need_db
def contract_redline(cid):
    """Compare current contract with its previous version (or a specific version)"""
    vid = request.args.get("version_id")
    cur = sb.table("contracts").select("id,name,content").eq("id", cid).execute()
    if not cur.data:
        return err("Not found", 404)
    current_text = cur.data[0].get("content", "")

    if vid:
        try:
            vid = int(vid)
        except (ValueError, TypeError):
            return err("version_id must be an integer", 400)
        ver = sb.table("contract_versions").select("*").eq("id", vid).eq("contract_id", cid).execute()
        if not ver.data:
            return err("Version not found", 404)
        old_text = ver.data[0].get("content", "")
        old_label = f"Version {ver.data[0]['version_number']}"
    else:
        vers = sb.table("contract_versions").select("*").eq("contract_id", cid).order("version_number", desc=True).limit(1).execute()
        if not vers.data:
            return err("No previous version available. Edit the contract at least once to see redline.", 404)
        old_text = vers.data[0].get("content", "")
        old_label = f"Version {vers.data[0]['version_number']}"

    word_diff = _word_diff(old_text, current_text)
    line_diff, additions, deletions = _line_diff(old_text, current_text)

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

    if request.args.get("ai_summary") == "true" and oai_h() and (additions + deletions) > 0:
        try:
            changes = []
            for chunk in word_diff:
                if chunk["type"] != "equal":
                    changes.append(f"[{chunk['type'].upper()}]: {chunk['text'][:200]}")
            change_text = "\n".join(changes[:50])

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
            if ai_resp.startswith("```"):
                ai_resp = ai_resp.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            result["ai_change_summary"] = J.loads(ai_resp)
        except Exception as e:
            log.debug(f"Redline AI summary failed: {e}")
            result["ai_change_summary"] = None

    return jsonify(result)


@bp.route("/api/contracts/<int:cid>/diff", methods=["GET"])
@auth
@need_db
def contract_diff(cid):
    """Compare two specific versions"""
    v1 = request.args.get("v1")
    v2 = request.args.get("v2")
    if not v1 or not v2:
        return err("v1 and v2 version IDs required", 400)
    try:
        v1, v2 = int(v1), int(v2)
    except (ValueError, TypeError):
        return err("v1 and v2 must be integers", 400)

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


# ─── Clone ───────────────────────────────────────────────────────────────

@bp.route("/api/contracts/<int:cid>/clone", methods=["POST"])
@auth
@role_required("editor")
@need_db
def clone_contract(cid):
    r = sb.table("contracts").select("*").eq("id", cid).execute()
    if not r.data:
        return err("Not found", 404)
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
    try:
        tags = sb.table("contract_tags").select("*").eq("contract_id", cid).execute()
        for t in (tags.data or []):
            sb.table("contract_tags").insert({"contract_id": new_id, "tag_name": t["tag_name"], "tag_color": t["tag_color"], "created_by": row["created_by"]}).execute()
    except Exception as e:
        log.debug(f"clone_contract: {e}")
    return jsonify({"id": new_id, "message": f"Contract cloned as '{new_name}'"}), 201


# ─── Bulk Actions ─────────────────────────────────────────────────────────

@bp.route("/api/contracts/bulk", methods=["POST"])
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
            return err("Invalid status", 400)
        for cid in ids:
            try:
                resp, code = _transition_status(int(cid), new_status, "Bulk Action")
                if code == 200:
                    results["success"] += 1
                else:
                    results["failed"] += 1
                    try:
                        results["errors"].append(f"#{cid}: {resp.get_json().get('error', {}).get('message', 'Failed')}")
                    except (AttributeError, TypeError):
                        results["errors"].append(f"#{cid}: Transition failed")
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


# ─── Compare ──────────────────────────────────────────────────────────────

@bp.route("/api/contracts/compare")
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

        text1 = (a.get("content") or "")[:50000]
        text2 = (b.get("content") or "")[:50000]
        words1 = text1.split()
        words2 = text2.split()

        sm = difflib.SequenceMatcher(None, words1, words2)
        similarity = round(sm.ratio() * 100, 1)

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
                if ai_resp.startswith("```"):
                    ai_resp = ai_resp.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                result["ai_analysis"] = J.loads(ai_resp)
            except Exception as e:
                log.debug(f"Compare AI analysis failed: {e}")
                result["ai_analysis"] = None

        return jsonify(result)
    except Exception as e:
        log.error(f"Internal error: {e}")
        return err("Internal server error", 500)


# ─── Linkable contracts ───────────────────────────────────────────────────

@bp.route("/api/contracts/linkable", methods=["GET"])
@auth
@need_db
def get_linkable_contracts():
    """Get contracts that can be linked to a given contract (opposite type, not already linked)"""
    cid = request.args.get("contract_id")
    if not cid:
        return err("Provide contract_id", 400)
    c = sb.table("contracts").select("id,contract_type").eq("id", int(cid)).execute()
    if not c.data:
        return err("Not found", 404)

    opposite = "vendor" if c.data[0]["contract_type"] == "client" else "client"
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


# ─── PDF Generation ──────────────────────────────────────────────────────

@bp.route("/api/contracts/<int:cid>/pdf")
@auth
@need_db
def generate_pdf(cid):
    r = sb.table("contracts").select("*").eq("id", cid).execute()
    if not r.data:
        return err("Not found", 404)
    c = r.data[0]

    obls = []
    try:
        obr = sb.table("contract_obligations").select("*").eq("contract_id", cid).execute()
        obls = obr.data or []
    except Exception as e:
        log.debug(f"generate_pdf: {e}")

    sigs = []
    try:
        sgr = sb.table("contract_signatures").select("*").eq("contract_id", cid).execute()
        sigs = sgr.data or []
    except Exception as e:
        log.debug(f"generate_pdf: {e}")

    cfields = []
    try:
        cfr = sb.table("custom_field_values").select("*, custom_field_defs(field_name, field_type)").eq("contract_id", cid).execute()
        cfields = cfr.data or []
    except Exception as e:
        log.debug(f"generate_pdf: {e}")

    tags = []
    try:
        tr = sb.table("contract_tags").select("tag_name, tag_color").eq("contract_id", cid).execute()
        tags = tr.data or []
    except Exception as e:
        log.debug(f"generate_pdf: {e}")

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

    return Response(html, mimetype='text/html', headers={
        'Content-Disposition': f'inline; filename="{name.replace(" ","_")}_contract.html"'
    })


# ─── Embed ───────────────────────────────────────────────────────────────

@bp.route("/api/contracts/<int:cid>/embed", methods=["POST"])
@auth
@need_db
def embed_single(cid):
    if not oai_h():
        return err("AI not configured", 500)
    r = sb.table("contracts").select("id,name,content").eq("id", cid).execute()
    if not r.data:
        return err("Not found", 404)
    c = r.data[0]
    try:
        n = embed_contract(c["id"], c["content"], c["name"])
        return jsonify({"chunks": n})
    except Exception as e:
        log.error(f"Internal error: {e}")
        return err("Internal server error", 500)
