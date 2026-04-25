"""Dashboard routes: dashboard, executive-dashboard, counterparty-risk, reports, margins,
approval-sla, calendar, audit-log."""

import io
import re
import csv
import time
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, Response

from config import sb, log, _dashboard_cache
from auth import err, _sanitize_html, auth, role_required, need_db
from helpers import log_activity

bp = Blueprint("dashboard", __name__)


def _escape_like(s):
    return s.replace("%", "\\%").replace("_", "\\_")


def _parse_currency(val):
    if not val:
        return 0.0
    try:
        parsed = float(re.sub(r'[^\d.]', '', str(val)))
        return min(parsed, 1_000_000_000_000)
    except (ValueError, TypeError):
        return 0.0


# ─── Dashboard ─────────────────────────────────────────────────────────────

@bp.route("/api/dashboard")
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
        if s in stats:
            stats[s] += 1
        if c["contract_type"] == "client":
            stats["clients"] += 1
        else:
            stats["vendors"] += 1
        if c.get("end_date"):
            try:
                end = datetime.strptime(c["end_date"], "%Y-%m-%d")
                if end < today:
                    stats["expired"] += 1
                elif (end - today).days <= 30:
                    stats["expiring"] += 1
                    expiring_list.append({"id": c["id"], "end_date": c["end_date"], "days_left": (end - today).days})
            except Exception as e:
                log.debug(f"dashboard: {e}")
    activity = sb.table("contract_activity").select("*").order("created_at", desc=True).limit(10).execute().data
    obligations = sb.table("contract_obligations").select("*").eq("status", "pending").order("deadline").limit(10).execute().data
    monthly = {}
    for c in contracts:
        d_str = c.get("added_on", "")
        if d_str:
            try:
                m = d_str[:7]
                monthly[m] = monthly.get(m, 0) + 1
            except Exception as e:
                log.debug(f"dashboard: {e}")
    months_sorted = sorted(monthly.keys())[-12:]
    monthly_trend = [{"month": m, "count": monthly[m]} for m in months_sorted]
    result = {**stats, "expiring_contracts": expiring_list, "recent_activity": activity,
              "pending_obligations": obligations, "monthly_trend": monthly_trend}
    _dashboard_cache["data"] = result
    _dashboard_cache["ts"] = time.time()
    return jsonify(result)


# ─── Executive Dashboard ─────────────────────────────────────────────────

@bp.route("/api/executive-dashboard")
@auth
@need_db
def executive_dashboard():
    contracts = sb.table("contracts").select("id,name,party_name,contract_type,status,start_date,end_date,value,department").limit(5000).execute().data or []
    today = datetime.now()
    total_client_value = 0
    total_vendor_value = 0
    by_dept = {}
    at_risk = []
    renewals_30 = []
    renewals_60 = []
    renewals_90 = []
    for c in contracts:
        cv = _parse_currency(c.get("value", ""))
        dept = c.get("department", "Unassigned") or "Unassigned"
        if dept not in by_dept:
            by_dept[dept] = {"revenue": 0, "cost": 0, "count": 0}
        by_dept[dept]["count"] += 1
        if c.get("contract_type") == "client":
            total_client_value += cv
            by_dept[dept]["revenue"] += cv
        else:
            total_vendor_value += cv
            by_dept[dept]["cost"] += cv
        if c.get("end_date"):
            try:
                days = (datetime.strptime(c["end_date"], "%Y-%m-%d") - today).days
                if days < 0:
                    at_risk.append({**c, "days_left": days, "risk": "Expired"})
                elif days <= 30:
                    renewals_30.append({**c, "days_left": days})
                elif days <= 60:
                    renewals_60.append({**c, "days_left": days})
                elif days <= 90:
                    renewals_90.append({**c, "days_left": days})
            except (ValueError, TypeError):
                pass
    approvals = sb.table("contract_approvals").select("id,contract_id,approver_name,created_at").eq("status", "pending").execute().data or []
    overdue_obs = sb.table("contract_obligations").select("id,contract_id,title,deadline").eq("status", "pending").lt("deadline", today.strftime("%Y-%m-%d")).execute().data or []
    at_risk_cids = {r["id"] for r in at_risk}
    for o in overdue_obs:
        cid = o.get("contract_id")
        if cid and cid not in at_risk_cids:
            match = next((c for c in contracts if c["id"] == cid), None)
            if match:
                at_risk.append({**match, "days_left": None, "risk": "Overdue Obligation"})
                at_risk_cids.add(cid)
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


# ─── Counterparty Risk ───────────────────────────────────────────────────

@bp.route("/api/counterparty-risk", methods=["GET"])
@auth
@need_db
def counterparty_risk_aggregation():
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
        p["total_value"] += cv
        p["contract_count"] += 1
        if c["contract_type"] == "client":
            p["client_value"] += cv
        else:
            p["vendor_value"] += cv
        if c.get("status") in ("executed", "pending", "in_review"):
            p["active_count"] += 1
        if c.get("end_date"):
            try:
                days = (datetime.strptime(c["end_date"], "%Y-%m-%d") - today).days
                if days < 0:
                    p["expired_count"] += 1
                    p["risk_score"] += 3
                elif days <= 30:
                    p["expiring_count"] += 1
                    p["risk_score"] += 2
                elif days <= 60:
                    p["expiring_count"] += 1
                    p["risk_score"] += 1
            except (ValueError, TypeError):
                pass
    result = sorted(parties.values(), key=lambda x: -x["total_value"])
    for p in result:
        p["contracts"] = p["contracts"][:5]
    return jsonify({"parties": result, "total_parties": len(result)})


# ─── Reports ─────────────────────────────────────────────────────────────

@bp.route("/api/reports")
@auth
@need_db
def reports():
    rtype = request.args.get("type", "summary")
    date_from = request.args.get("from")
    date_to = request.args.get("to")

    contracts = sb.table("contracts").select("id,name,party_name,contract_type,status,start_date,end_date,value,added_on,department").execute().data
    today = datetime.now()

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
            if dept not in by_dept:
                by_dept[dept] = 0
            by_dept[dept] += 1
            v = c.get("value", "")
            if v:
                nums = re.findall(r'[\d,]+(?:\.\d+)?', str(v).replace(",", ""))
                if nums:
                    try:
                        total_value += float(nums[0])
                    except Exception as e:
                        log.debug(f"reports: {e}")
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
        expired = 0
        exp30 = 0
        exp90 = 0
        safe = 0
        for c in contracts:
            if c.get("end_date"):
                try:
                    end = datetime.strptime(c["end_date"], "%Y-%m-%d")
                    days = (end - today).days
                    c["days_left"] = days
                    result.append(c)
                    if days < 0:
                        expired += 1
                    elif days <= 30:
                        exp30 += 1
                    elif days <= 90:
                        exp90 += 1
                    else:
                        safe += 1
                except Exception as e:
                    log.debug(f"reports: {e}")
        result.sort(key=lambda x: x.get("days_left", 9999))
        return jsonify({"expired": expired, "expiring_30": exp30, "expiring_90": exp90,
                        "safe": safe, "contracts": result})

    elif rtype == "department":
        depts = {}
        for c in contracts:
            dept = c.get("department", "Unassigned") or "Unassigned"
            if dept not in depts:
                depts[dept] = {"department": dept, "count": 0, "draft": 0, "pending": 0, "executed": 0, "total_value": 0}
            depts[dept]["count"] += 1
            s = c.get("status", "draft")
            if s in depts[dept]:
                depts[dept][s] += 1
            v = c.get("value", "")
            if v:
                nums = re.findall(r'[\d,]+(?:\.\d+)?', str(v).replace(",", ""))
                if nums:
                    try:
                        depts[dept]["total_value"] += float(nums[0])
                    except Exception as e:
                        log.debug(f"reports: {e}")
        dept_list = sorted(depts.values(), key=lambda x: -x["count"])
        for d in dept_list:
            d["total_value"] = f"INR {d['total_value']:,.0f}" if d["total_value"] else "—"
        return jsonify({"departments": dept_list})

    elif rtype == "health":
        obligations = sb.table("contract_obligations").select("contract_id,status,deadline").execute().data or []
        ob_map = {}
        for o in obligations:
            cid = o.get("contract_id")
            if cid not in ob_map:
                ob_map[cid] = {"total": 0, "overdue": 0, "completed": 0}
            ob_map[cid]["total"] += 1
            if o.get("status") == "completed":
                ob_map[cid]["completed"] += 1
            elif o.get("deadline") and o["deadline"] < today.strftime("%Y-%m-%d"):
                ob_map[cid]["overdue"] += 1
        results = []
        for c in contracts:
            score = 100
            risks = []
            if not c.get("end_date"):
                score -= 10
                risks.append("No end date")
            if not c.get("start_date"):
                score -= 5
                risks.append("No start date")
            if not c.get("value"):
                score -= 5
                risks.append("No value specified")
            if not c.get("department"):
                score -= 5
                risks.append("No department")
            if c.get("end_date"):
                try:
                    days = (datetime.strptime(c["end_date"], "%Y-%m-%d") - today).days
                    if days < 0:
                        score -= 25
                        risks.append(f"Expired {abs(days)}d ago")
                    elif days <= 30:
                        score -= 15
                        risks.append(f"Expires in {days}d")
                    elif days <= 60:
                        score -= 5
                        risks.append(f"Expires in {days}d")
                except (ValueError, TypeError):
                    pass
            obs = ob_map.get(c["id"], {"total": 0, "overdue": 0, "completed": 0})
            if obs["overdue"] > 0:
                score -= min(obs["overdue"] * 10, 30)
                risks.append(f"{obs['overdue']} overdue obligations")
            if c.get("status") == "rejected":
                score -= 20
                risks.append("Rejected")
            elif c.get("status") == "draft":
                score -= 5
            score = max(0, min(100, score))
            health = "healthy" if score >= 80 else "warning" if score >= 50 else "critical"
            results.append({**c, "health_score": score, "health": health, "risks": risks, "obligations": obs})
        results.sort(key=lambda x: x["health_score"])
        healthy = sum(1 for r in results if r["health"] == "healthy")
        warning = sum(1 for r in results if r["health"] == "warning")
        critical = sum(1 for r in results if r["health"] == "critical")
        avg_score = round(sum(r["health_score"] for r in results) / max(len(results), 1))
        return jsonify({"contracts": results, "summary": {
            "healthy": healthy, "warning": warning, "critical": critical,
            "avg_score": avg_score, "total": len(results)}})

    elif rtype == "at_risk":
        obligations = sb.table("contract_obligations").select("contract_id,status,deadline,title").execute().data or []
        overdue_map = {}
        for o in obligations:
            if o.get("status") == "pending" and o.get("deadline") and o["deadline"] < today.strftime("%Y-%m-%d"):
                cid = o.get("contract_id")
                if cid not in overdue_map:
                    overdue_map[cid] = []
                overdue_map[cid].append(o["title"])
        at_risk = []
        for c in contracts:
            risk_reasons = []
            risk_level = 0
            if c.get("end_date"):
                try:
                    days = (datetime.strptime(c["end_date"], "%Y-%m-%d") - today).days
                    if days < 0:
                        risk_reasons.append(f"Expired {abs(days)} days ago")
                        risk_level += 3
                    elif days <= 30:
                        risk_reasons.append(f"Expiring in {days} days")
                        risk_level += 2
                    elif days <= 60:
                        risk_reasons.append(f"Expiring in {days} days")
                        risk_level += 1
                except (ValueError, TypeError):
                    pass
            if c["id"] in overdue_map:
                risk_reasons.append(f"{len(overdue_map[c['id']])} overdue obligations")
                risk_level += 2
            if c.get("status") == "rejected":
                risk_reasons.append("Rejected")
                risk_level += 1
            if risk_reasons:
                at_risk.append({**c, "risk_reasons": risk_reasons, "risk_level": risk_level})
        at_risk.sort(key=lambda x: -x["risk_level"])
        return jsonify({"contracts": at_risk, "total_at_risk": len(at_risk), "total_contracts": len(contracts)})

    elif rtype == "dept_spend":
        links = sb.table("contract_links").select("client_contract_id,vendor_contract_id").execute().data or []
        link_map = {}
        for l in links:
            cid = l["client_contract_id"]
            if cid not in link_map:
                link_map[cid] = []
            link_map[cid].append(l["vendor_contract_id"])
        depts = {}
        for c in contracts:
            dept = c.get("department", "Unassigned") or "Unassigned"
            if dept not in depts:
                depts[dept] = {"department": dept, "revenue": 0, "cost": 0, "client_count": 0, "vendor_count": 0, "contracts": 0}
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


# ─── Margins ─────────────────────────────────────────────────────────────

@bp.route("/api/contracts/<int:cid>/margin", methods=["GET"])
@auth
@need_db
def get_contract_margin(cid):
    c = sb.table("contracts").select("id,name,party_name,contract_type,value").eq("id", cid).execute()
    if not c.data:
        return err("Not found", 404)
    contract = c.data[0]
    if contract["contract_type"] != "client":
        return err("Margin tracking is only for client contracts", 400)
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
    parties = sb.table("contract_parties").select("*").eq("contract_id", cid).execute().data or []
    client_value = _parse_currency(contract.get("value", ""))
    margin = client_value - total_vendor_cost
    margin_pct = round((margin / client_value * 100), 1) if client_value > 0 else 0
    return jsonify({
        "contract": contract, "client_value": client_value,
        "vendors": vendors, "total_vendor_cost": total_vendor_cost,
        "margin": margin, "margin_pct": margin_pct, "parties": parties
    })


@bp.route("/api/margins", methods=["GET"])
@auth
@need_db
def get_all_margins():
    clients = sb.table("contracts").select("id,name,party_name,value,status,department").eq("contract_type", "client").execute().data or []
    links = sb.table("contract_links").select("client_contract_id,vendor_contract_id").execute().data or []
    vendor_ids = list(set(l["vendor_contract_id"] for l in links))
    vendor_map = {}
    if vendor_ids:
        vdata = sb.table("contracts").select("id,name,party_name,value").in_("id", vendor_ids).execute().data or []
        vendor_map = {v["id"]: v for v in vdata}
    link_map = {}
    for l in links:
        cid = l["client_contract_id"]
        vid = l["vendor_contract_id"]
        if cid not in link_map:
            link_map[cid] = []
        if vid in vendor_map:
            link_map[cid].append(vendor_map[vid])
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


# ─── Approval SLA ────────────────────────────────────────────────────────

@bp.route("/api/approvals/sla", methods=["GET"])
@auth
@need_db
def approval_sla():
    try:
        threshold_days = int(request.args.get("threshold", 3))
    except (ValueError, TypeError):
        threshold_days = 3
    approvals = sb.table("contract_approvals").select("*").eq("status", "pending").execute().data or []
    today = datetime.now()
    results = []
    overdue_count = 0
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
            except (ValueError, TypeError):
                pass
        a["days_pending"] = days_pending
        a["is_overdue"] = days_pending > threshold_days
        if a["is_overdue"]:
            overdue_count += 1
        cn = contracts_map.get(a.get("contract_id"))
        if cn:
            a["contract_name"] = cn["name"]
            a["party_name"] = cn.get("party_name", "")
        results.append(a)
    results.sort(key=lambda x: -x["days_pending"])
    return jsonify({"approvals": results, "total": len(results), "overdue": overdue_count, "threshold_days": threshold_days})


# ─── Calendar ────────────────────────────────────────────────────────────

@bp.route("/api/calendar")
@auth
@need_db
def calendar_events():
    year = request.args.get("year", str(datetime.now().year))
    month = request.args.get("month")

    events = []

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

    try:
        obligations = sb.table("contract_obligations").select("id,contract_id,title,deadline,assigned_to,status").execute().data
        cids = list(set(o.get("contract_id") for o in obligations if o.get("contract_id")))
        cmap = {}
        if cids:
            try:
                cr = sb.table("contracts").select("id,name").in_("id", cids[:200]).execute()
                cmap = {c["id"]: c["name"] for c in cr.data}
            except Exception as e:
                log.debug(f"calendar_events: {e}")
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
    except Exception as e:
        log.debug(f"calendar_events: {e}")

    events.sort(key=lambda x: x.get("date", ""))
    return jsonify(events)


# ─── Audit Log ────────────────────────────────────────────────────────────

@bp.route("/api/audit-log")
@auth
@role_required("manager")
@need_db
def audit_log():
    date_from = request.args.get("from")
    date_to = request.args.get("to")
    action = request.args.get("action")
    fmt = request.args.get("format", "json")

    q = sb.table("contract_activity").select("*").order("created_at", desc=True)
    if date_from:
        q = q.gte("created_at", date_from)
    if date_to:
        q = q.lte("created_at", date_to)
    if action:
        q = q.ilike("action", f"%{_escape_like(action)}%")
    r = q.limit(1000).execute()

    cids = list(set(a.get("contract_id") for a in r.data if a.get("contract_id")))
    cmap = {}
    if cids:
        try:
            cr = sb.table("contracts").select("id,name").in_("id", cids[:200]).execute()
            cmap = {c["id"]: c["name"] for c in cr.data}
        except Exception as e:
            log.debug(f"audit_log: {e}")
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


@bp.route("/api/audit-log/cleanup", methods=["POST"])
@auth
@role_required("admin")
@need_db
def audit_log_cleanup():
    d = request.json or {}
    if not d.get("confirm"):
        return err("Include '\"confirm\": true' to proceed with cleanup.", 400)
    try:
        days = max(min(int(d.get("retention_days", 365)), 3650), 30)
    except (ValueError, TypeError):
        days = 365
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    count_q = sb.table("contract_activity").select("id", count="exact").lt("created_at", cutoff).execute()
    count = count_q.count if hasattr(count_q, 'count') and count_q.count else len(count_q.data or [])
    if count == 0:
        return jsonify({"message": "No records older than the retention period", "deleted": 0, "retention_days": days})
    sb.table("contract_activity").delete().lt("created_at", cutoff).execute()
    log_activity(None, "audit_log_cleanup", request.user_email, f"Deleted {count} records older than {days} days")
    return jsonify({"message": f"Deleted {count} audit log entries older than {days} days", "deleted": count, "retention_days": days, "cutoff_date": cutoff[:10]})
