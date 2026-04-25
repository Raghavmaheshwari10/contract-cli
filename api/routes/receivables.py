"""Receivables: standalone money-receivable tracker with CSV import + dashboard aggregation."""

import io
import csv
import re
from datetime import datetime, date, timedelta
from flask import Blueprint, request, jsonify

from config import sb, log
from auth import err, _sanitize_dict, auth, role_required, need_db

bp = Blueprint("receivables", __name__)

VALID_STATUSES = ("pending", "paid", "overdue", "cancelled", "disputed")


def _parse_amount(val):
    """Parse a string/number into a non-negative float, capped to a sane max."""
    if val is None or val == "":
        return None
    try:
        cleaned = re.sub(r"[^\d.\-]", "", str(val))
        if cleaned in ("", "-", "."):
            return None
        f = float(cleaned)
        if f < 0:
            return None
        return min(f, 1_000_000_000_000)
    except (ValueError, TypeError):
        return None


def _parse_date(val):
    """Accept YYYY-MM-DD; return ISO date string or None."""
    if not val:
        return None
    s = str(val).strip()[:10]
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return s
    except (ValueError, TypeError):
        return None


def _today_iso():
    return date.today().isoformat()


def _classify_overdue(rows):
    """For non-paid rows with due_date in the past, classify status as 'overdue' on read."""
    today = _today_iso()
    for r in rows:
        if r.get("status") not in ("paid", "cancelled") and r.get("due_date") and r["due_date"] < today:
            r["status"] = "overdue"
    return rows


# ─── List / Filter ────────────────────────────────────────────────────────
@bp.route("/api/receivables", methods=["GET"])
@auth
@need_db
def list_receivables():
    status = request.args.get("status")
    client = request.args.get("client")
    date_from = request.args.get("from")
    date_to = request.args.get("to")
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1
    try:
        per = min(100, max(1, int(request.args.get("per_page", 25))))
    except (ValueError, TypeError):
        per = 25

    q = sb.table("receivables").select("*", count="exact").order("created_at", desc=True)
    if status and status in VALID_STATUSES:
        q = q.eq("status", status)
    if client:
        q = q.ilike("client_name", f"%{client}%")
    if date_from:
        q = q.gte("invoice_date", date_from)
    if date_to:
        q = q.lte("invoice_date", date_to)
    off = (page - 1) * per
    r = q.range(off, off + per - 1).execute()
    total = r.count if r.count is not None else len(r.data or [])
    data = _classify_overdue(r.data or [])
    return jsonify({
        "data": data,
        "total": total,
        "page": page,
        "per_page": per,
        "pages": max(1, -(-total // per)),
    })


# ─── Create ───────────────────────────────────────────────────────────────
@bp.route("/api/receivables", methods=["POST"])
@auth
@role_required("editor")
@need_db
def create_receivable():
    d = _sanitize_dict(request.json or {})
    client_name = str(d.get("client_name") or "").strip()
    if not client_name:
        return err("Missing: client_name", 400)
    amount = _parse_amount(d.get("amount"))
    if amount is None:
        return err("Missing or invalid: amount", 400)
    status = d.get("status") or "pending"
    if status not in VALID_STATUSES:
        return err(f"status must be one of: {', '.join(VALID_STATUSES)}", 400)

    row = {
        "client_name": client_name[:500],
        "client_email": str(d.get("client_email") or "")[:254],
        "invoice_number": str(d.get("invoice_number") or "")[:100],
        "description": str(d.get("description") or "")[:1000],
        "amount": amount,
        "currency": (str(d.get("currency") or "INR")[:10]) or "INR",
        "invoice_date": _parse_date(d.get("invoice_date")),
        "due_date": _parse_date(d.get("due_date")),
        "paid_date": _parse_date(d.get("paid_date")),
        "status": status,
        "notes": str(d.get("notes") or "")[:2000],
        "created_by": getattr(request, "user_email", "") or "User",
    }
    if row["status"] == "paid" and not row["paid_date"]:
        row["paid_date"] = _today_iso()
    r = sb.table("receivables").insert(row).execute()
    if not r.data:
        return err("Failed to create receivable", 500)
    return jsonify(r.data[0]), 201


# ─── Get one ──────────────────────────────────────────────────────────────
@bp.route("/api/receivables/<int:rid>", methods=["GET"])
@auth
@need_db
def get_receivable(rid):
    r = sb.table("receivables").select("*").eq("id", rid).execute()
    if not r.data:
        return err("Not found", 404)
    return jsonify(_classify_overdue(r.data)[0])


# ─── Update ───────────────────────────────────────────────────────────────
@bp.route("/api/receivables/<int:rid>", methods=["PATCH"])
@auth
@role_required("editor")
@need_db
def update_receivable(rid):
    d = _sanitize_dict(request.json or {})
    update = {}
    if "client_name" in d:
        cn = str(d["client_name"] or "").strip()
        if not cn:
            return err("client_name cannot be empty", 400)
        update["client_name"] = cn[:500]
    if "client_email" in d:
        update["client_email"] = str(d["client_email"] or "")[:254]
    if "invoice_number" in d:
        update["invoice_number"] = str(d["invoice_number"] or "")[:100]
    if "description" in d:
        update["description"] = str(d["description"] or "")[:1000]
    if "amount" in d:
        amt = _parse_amount(d["amount"])
        if amt is None:
            return err("Invalid amount", 400)
        update["amount"] = amt
    if "currency" in d:
        update["currency"] = (str(d["currency"] or "INR")[:10]) or "INR"
    if "invoice_date" in d:
        update["invoice_date"] = _parse_date(d["invoice_date"])
    if "due_date" in d:
        update["due_date"] = _parse_date(d["due_date"])
    if "paid_date" in d:
        update["paid_date"] = _parse_date(d["paid_date"])
    if "status" in d:
        st = d["status"]
        if st not in VALID_STATUSES:
            return err(f"status must be one of: {', '.join(VALID_STATUSES)}", 400)
        update["status"] = st
        if st == "paid" and "paid_date" not in update:
            update["paid_date"] = _today_iso()
    if "notes" in d:
        update["notes"] = str(d["notes"] or "")[:2000]
    if not update:
        return err("Nothing to update", 400)
    update["updated_at"] = datetime.utcnow().isoformat()

    r = sb.table("receivables").update(update).eq("id", rid).execute()
    if not r.data:
        return err("Not found", 404)
    return jsonify(r.data[0])


# ─── Delete ───────────────────────────────────────────────────────────────
@bp.route("/api/receivables/<int:rid>", methods=["DELETE"])
@auth
@role_required("manager")
@need_db
def delete_receivable(rid):
    sb.table("receivables").delete().eq("id", rid).execute()
    return jsonify({"deleted": rid})


# ─── CSV Import ───────────────────────────────────────────────────────────
@bp.route("/api/receivables/import", methods=["POST"])
@auth
@role_required("editor")
@need_db
def import_receivables():
    if "file" not in request.files:
        return err("No file uploaded", 400)
    f = request.files["file"]
    if not f.filename.lower().endswith(".csv"):
        return err("CSV file required", 400)
    try:
        raw = f.read().decode("utf-8-sig")
    except Exception as e:
        return err(f"Could not read CSV: {e}", 400)

    MAX_CSV_ROWS = 5000
    reader = csv.DictReader(io.StringIO(raw))
    imported, skipped, errors = 0, 0, []
    created_by = getattr(request, "user_email", "") or "Bulk Import"

    for i, row in enumerate(reader):
        if i >= MAX_CSV_ROWS:
            errors.append(f"Row limit reached ({MAX_CSV_ROWS}). Remaining rows skipped.")
            break
        row = {k.strip().lower().replace(" ", "_"): (v.strip() if v else "") for k, v in row.items() if k}
        client_name = row.get("client_name", "")
        amount = _parse_amount(row.get("amount"))
        if not client_name or amount is None:
            skipped += 1
            errors.append(f"Row {i+2}: missing client_name or invalid amount")
            continue
        status = row.get("status", "pending").lower()
        if status not in VALID_STATUSES:
            status = "pending"
        rec = {
            "client_name": client_name[:500],
            "client_email": row.get("client_email", "")[:254],
            "invoice_number": row.get("invoice_number", "")[:100],
            "description": row.get("description", "")[:1000],
            "amount": amount,
            "currency": (row.get("currency") or "INR")[:10],
            "invoice_date": _parse_date(row.get("invoice_date")),
            "due_date": _parse_date(row.get("due_date")),
            "paid_date": _parse_date(row.get("paid_date")),
            "status": status,
            "notes": row.get("notes", "")[:2000],
            "created_by": created_by,
        }
        if rec["status"] == "paid" and not rec["paid_date"]:
            rec["paid_date"] = _today_iso()
        try:
            sb.table("receivables").insert(rec).execute()
            imported += 1
        except Exception as ex:
            skipped += 1
            errors.append(f"Row {i+2}: {str(ex)[:120]}")

    return jsonify({
        "imported": imported,
        "skipped": skipped,
        "total_rows": imported + skipped,
        "errors": errors[:25],
        "message": f"Imported {imported}, skipped {skipped}",
    })


# ─── Dashboard Aggregation ────────────────────────────────────────────────
@bp.route("/api/receivables/dashboard", methods=["GET"])
@auth
@need_db
def receivables_dashboard():
    rows = sb.table("receivables").select(
        "id,client_name,amount,currency,invoice_date,due_date,paid_date,status"
    ).limit(10000).execute().data or []
    rows = _classify_overdue(rows)
    today = date.today()
    today_iso = today.isoformat()
    month_start = today.replace(day=1).isoformat()

    total_outstanding = 0.0
    total_paid = 0.0
    total_overdue = 0.0
    paid_this_month = 0.0
    count_by_status = {s: 0 for s in VALID_STATUSES}
    aging = {"0_30": 0.0, "31_60": 0.0, "61_90": 0.0, "90_plus": 0.0}
    by_client = {}
    trend = {}  # YYYY-MM -> {paid, outstanding}
    active_clients = set()

    # Build last-12-months keys so chart has stable axis
    cur = today.replace(day=1)
    months = []
    for _ in range(12):
        months.append(cur.strftime("%Y-%m"))
        # step back 1 month
        if cur.month == 1:
            cur = cur.replace(year=cur.year - 1, month=12)
        else:
            cur = cur.replace(month=cur.month - 1)
    months.reverse()
    for m in months:
        trend[m] = {"month": m, "paid": 0.0, "outstanding": 0.0}

    for r in rows:
        amt = float(r.get("amount") or 0)
        st = r.get("status") or "pending"
        count_by_status[st] = count_by_status.get(st, 0) + 1
        client = r.get("client_name") or "Unknown"
        if st != "cancelled":
            active_clients.add(client)
        if st == "paid":
            total_paid += amt
            pd = r.get("paid_date") or ""
            if pd >= month_start:
                paid_this_month += amt
            month_key = (pd or r.get("invoice_date") or "")[:7]
            if month_key in trend:
                trend[month_key]["paid"] += amt
        else:
            total_outstanding += amt
            if client not in by_client:
                by_client[client] = {"client_name": client, "outstanding": 0.0, "count": 0}
            by_client[client]["outstanding"] += amt
            by_client[client]["count"] += 1
            month_key = (r.get("invoice_date") or "")[:7]
            if month_key in trend:
                trend[month_key]["outstanding"] += amt
            # Aging buckets (against due_date when present, else invoice_date)
            ref_date = r.get("due_date") or r.get("invoice_date")
            if ref_date and ref_date < today_iso:
                try:
                    ref = datetime.strptime(ref_date, "%Y-%m-%d").date()
                    days = (today - ref).days
                    if days <= 30:
                        aging["0_30"] += amt
                    elif days <= 60:
                        aging["31_60"] += amt
                    elif days <= 90:
                        aging["61_90"] += amt
                    else:
                        aging["90_plus"] += amt
                except (ValueError, TypeError):
                    pass
            elif ref_date and ref_date >= today_iso:
                # Not yet due — bucket as 0-30 by convention
                aging["0_30"] += amt
            if st == "overdue":
                total_overdue += amt

    top_clients = sorted(by_client.values(), key=lambda x: -x["outstanding"])[:10]
    trend_list = [trend[m] for m in months]

    return jsonify({
        "total_outstanding": round(total_outstanding, 2),
        "total_paid": round(total_paid, 2),
        "total_overdue": round(total_overdue, 2),
        "paid_this_month": round(paid_this_month, 2),
        "active_clients": len(active_clients),
        "count_by_status": count_by_status,
        "aging": {k: round(v, 2) for k, v in aging.items()},
        "top_clients": [{**c, "outstanding": round(c["outstanding"], 2)} for c in top_clients],
        "trend": [{"month": t["month"], "paid": round(t["paid"], 2), "outstanding": round(t["outstanding"], 2)} for t in trend_list],
    })
