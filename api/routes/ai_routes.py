"""AI routes: chat, chat/feedback, chat/sessions, parse, upload-pdf, upload-pdfs-bulk,
review, ai-summary, extract-obligations, explain, suggest-clauses, search, leegality."""

import re
import json as J
from datetime import datetime
from flask import Blueprint, request, jsonify, Response

from config import sb, log, OAI_URL, LEEGALITY_KEY
from auth import (
    err, _sanitize, _sanitize_html,
    auth, role_required, need_db,
)
from ai import (
    oai_h, oai_chat, oai_stream,
    embed_contract, hybrid_search, build_prompt,
    ocr_pdf_pages, classify_query, generate_followups,
)
from helpers import log_activity, fire_webhooks
import requests as http
import fitz

bp = Blueprint("ai_routes", __name__)


def _escape_like(s):
    return s.replace("%", "\\%").replace("_", "\\_")


# ─── Search ────────────────────────────────────────────────────────────────

@bp.route("/api/search")
@auth
@need_db
def search():
    q = _sanitize_html(request.args.get("q", "").strip(), max_len=200)
    if not q:
        return jsonify([])
    safe = q.replace("%", "").replace("*", "").replace(",", "").replace(".", " ").replace("_", "")
    t = f"%{safe}%"
    r = sb.table("contracts").select(
        "id,name,party_name,contract_type,status,start_date,end_date,value"
    ).or_(f"name.ilike.{t},party_name.ilike.{t}").limit(20).execute()
    return jsonify(r.data)


# ─── Parse (AI auto-fill) ─────────────────────────────────────────────────

@bp.route("/api/parse", methods=["POST"])
@auth
def parse():
    if not oai_h():
        return err("AI not configured", 500)
    d = request.json or {}
    content = d.get("content", "")
    if not content:
        return err("No content", 400)
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
        if reply.startswith("```"):
            reply = reply.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return jsonify(J.loads(reply))
    except Exception as e:
        log.error(f"Internal error: {e}")
        return err("Internal server error", 500)


# ─── Upload PDF (single) ──────────────────────────────────────────────────

@bp.route("/api/upload-pdf", methods=["POST"])
@auth
def upload_pdf():
    if "file" not in request.files:
        return err("No file", 400)
    f = request.files["file"]
    if not f.filename.lower().endswith(".pdf"):
        return err("PDF only", 400)
    try:
        b = f.read()
        if len(b) > 50 * 1024 * 1024:
            return err("Max 50MB per file", 400)
        if not b[:4] == b'%PDF':
            return err("Invalid PDF file", 400)
        doc = fitz.open(stream=b, filetype="pdf")
        pc = len(doc)
        txt = "".join(p.get_text() + "\n" for p in doc)
        doc.close()

        if txt.strip():
            return jsonify({"content": txt.strip(), "pages": pc, "method": "text"})

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


# ─── Bulk PDF Upload ──────────────────────────────────────────────────────

@bp.route("/api/upload-pdfs-bulk", methods=["POST"])
@auth
@role_required("editor")
@need_db
def upload_pdfs_bulk():
    files = request.files.getlist("files")
    if not files or len(files) == 0:
        return err("No files uploaded", 400)
    if len(files) > 10:
        return err("Max 10 PDFs at a time", 400)

    type_override = request.form.get("contract_type", "auto").strip().lower()
    if type_override not in ("client", "vendor", "auto"):
        type_override = "auto"
    link_to_id = request.form.get("link_to_contract_id", "").strip()
    link_to_id = int(link_to_id) if link_to_id.isdigit() else None
    tags_raw = request.form.get("tags", "").strip()
    tag_names = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []
    user_email = getattr(request, "user_email", "system")

    link_target = None
    if link_to_id:
        lr = sb.table("contracts").select("id,name,contract_type").eq("id", link_to_id).execute()
        link_target = lr.data[0] if lr.data else None
        if not link_target:
            return err(f"Link target contract #{link_to_id} not found", 404)

    results = []
    for f in files:
        fname = f.filename or "unknown.pdf"
        if not fname.lower().endswith(".pdf"):
            results.append({"file": fname, "status": "skipped", "error": "Not a PDF"})
            continue
        try:
            b = f.read()
            if len(b) > 50 * 1024 * 1024:
                results.append({"file": fname, "status": "error", "error": "Exceeds 50MB"})
                continue
            if not b[:4] == b'%PDF':
                results.append({"file": fname, "status": "error", "error": "Invalid PDF"})
                continue

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

            meta = {}
            if oai_h():
                try:
                    reply = oai_chat([
                        {"role": "system", "content": """Extract contract metadata. Return ONLY JSON:
{"name":"","party_name":"","contract_type":"client|vendor","start_date":"YYYY-MM-DD|null","end_date":"YYYY-MM-DD|null","value":"USD X|null","notes":"","department":"","jurisdiction":"","governing_law":""}"""},
                        {"role": "user", "content": txt[:4000]}
                    ], model="gpt-4o-mini", max_tok=400)
                    if reply.startswith("```"):
                        reply = reply.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                    meta = J.loads(reply)
                except Exception as e:
                    log.debug(f"AI metadata extraction failed: {e}")

            if type_override in ("client", "vendor"):
                ctype = type_override
            else:
                ctype = meta.get("contract_type", "client") if meta.get("contract_type") in ("client", "vendor") else "client"
                if link_target and type_override == "auto":
                    ctype = "vendor" if link_target["contract_type"] == "client" else "client"

            from auth import _sanitize as san
            contract = {
                "name": san(meta.get("name") or fname.replace(".pdf", "").replace("_", " ").title()),
                "party_name": san(meta.get("party_name", "")),
                "contract_type": ctype,
                "status": "draft",
                "content": txt.strip(),
                "start_date": meta.get("start_date") if meta.get("start_date") and meta.get("start_date") != "null" else None,
                "end_date": meta.get("end_date") if meta.get("end_date") and meta.get("end_date") != "null" else None,
                "value": san(meta.get("value", "")) if meta.get("value") and meta.get("value") != "null" else "",
                "department": san(meta.get("department", "")),
                "notes": san(meta.get("notes", "")),
                "created_by": user_email,
            }
            r = sb.table("contracts").insert(contract).execute()
            cid = r.data[0]["id"] if r.data else None

            if cid:
                log_activity(cid, "created", user_email, f"Bulk uploaded from {fname} ({pc} pages, {method})")

            tag_ids_applied = []
            if cid and tag_names:
                try:
                    for tname in tag_names:
                        tr = sb.table("contract_tags").select("id").eq("name", tname).execute()
                        if tr.data:
                            tid = tr.data[0]["id"]
                        else:
                            tnr = sb.table("contract_tags").insert({"name": tname, "created_by": user_email}).execute()
                            tid = tnr.data[0]["id"] if tnr.data else None
                        if tid:
                            sb.table("contract_tag_map").insert({"contract_id": cid, "tag_id": tid}).execute()
                            tag_ids_applied.append(tid)
                except Exception as te:
                    log.warning(f"Tag apply failed for contract {cid}: {te}")

            link_created = False
            if cid and link_target:
                try:
                    if ctype == "vendor" and link_target["contract_type"] == "client":
                        link_row = {"client_contract_id": link_to_id, "vendor_contract_id": cid, "created_by": user_email}
                    elif ctype == "client" and link_target["contract_type"] == "vendor":
                        link_row = {"client_contract_id": cid, "vendor_contract_id": link_to_id, "created_by": user_email}
                    else:
                        link_row = None
                        log.warning(f"Cannot link contracts of same type: {ctype} <-> {link_target['contract_type']}")

                    if link_row:
                        sb.table("contract_links").insert(link_row).execute()
                        log_activity(cid, "linked", user_email, f"Linked to '{link_target['name']}' (#{link_to_id}) during upload")
                        link_created = True
                except Exception as le:
                    log.warning(f"Link creation failed for contract {cid}: {le}")

            results.append({
                "file": fname,
                "status": "created",
                "contract_id": cid,
                "contract_name": contract["name"],
                "pages": pc,
                "method": method,
                "party": contract["party_name"],
                "type": contract["contract_type"],
                "tags_applied": len(tag_ids_applied),
                "linked": link_created,
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


# ─── AI Contract Review ───────────────────────────────────────────────────

@bp.route("/api/contracts/<int:cid>/review", methods=["POST"])
@auth
@need_db
def ai_review(cid):
    if not oai_h():
        return err("AI not configured", 500)
    r = sb.table("contracts").select("content,name,contract_type,party_name,value,start_date,end_date").eq("id", cid).execute()
    if not r.data:
        return err("Not found", 404)
    c = r.data[0]
    contract_type = c.get("contract_type", "client")

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
        if reply.startswith("```"):
            reply = reply.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        parsed = J.loads(reply)

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


# ─── AI Executive Summary ────────────────────────────────────────────────

@bp.route("/api/contracts/<int:cid>/ai-summary", methods=["POST"])
@auth
@need_db
def ai_summary(cid):
    if not oai_h():
        return err("AI not configured", 500)
    r = sb.table("contracts").select("content,name,contract_type,party_name,value,start_date,end_date,status").eq("id", cid).execute()
    if not r.data:
        return err("Not found", 404)
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
        if reply.startswith("```"):
            reply = reply.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        summary = J.loads(reply)
        log_activity(cid, "ai_summary", "AI", "Executive summary generated")
        return jsonify(summary)
    except Exception as e:
        log.error(f"Internal error: {e}")
        return err("Internal server error", 500)


# ─── AI Obligation Extraction ────────────────────────────────────────────

@bp.route("/api/contracts/<int:cid>/extract-obligations", methods=["POST"])
@auth
@role_required("editor")
@need_db
def extract_obligations(cid):
    if not oai_h():
        return err("AI not configured", 500)
    r = sb.table("contracts").select("content,name,contract_type,party_name").eq("id", cid).execute()
    if not r.data:
        return err("Not found", 404)
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
        if reply.startswith("```"):
            reply = reply.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        parsed = J.loads(reply)
        obligations = parsed.get("obligations", [])

        saved = 0
        if auto_save and obligations:
            for ob in obligations:
                try:
                    from auth import _sanitize as san
                    row = {
                        "contract_id": cid,
                        "title": san(ob.get("title", "Untitled"))[:200],
                        "description": san(ob.get("description", ""))[:2000],
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

@bp.route("/api/contracts/<int:cid>/explain", methods=["POST"])
@auth
@need_db
def explain_contract(cid):
    if not oai_h():
        return err("AI not configured", 500)
    r = sb.table("contracts").select("content,name,contract_type,party_name").eq("id", cid).execute()
    if not r.data:
        return err("Not found", 404)
    c = r.data[0]
    section = (request.json or {}).get("section", "")
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
        if reply.startswith("```"):
            reply = reply.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return jsonify(J.loads(reply))
    except Exception as e:
        log.error(f"Internal error: {e}")
        return err("Internal server error", 500)


# ─── AI Clause Suggestions ───────────────────────────────────────────────

@bp.route("/api/ai/suggest-clauses", methods=["POST"])
@auth
@need_db
def suggest_clauses():
    if not oai_h():
        return err("OpenAI not configured", 400)
    d = request.json or {}
    contract_type = d.get("contract_type", "client")
    context = d.get("context", "")[:2000]
    department = d.get("department", "")
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


# ─── Chat (RAG + Streaming) ───────────────────────────────────────────────

@bp.route("/api/chat", methods=["POST"])
@auth
@need_db
def chat():
    if not oai_h():
        return err("AI not configured", 500)
    d = request.json or {}
    msg = d.get("message", "").strip()
    history = d.get("history", [])[-20:]
    cids = d.get("contract_ids")
    if cids and isinstance(cids, list):
        cids = cids[:50]
    stream = d.get("stream", False)
    if not msg:
        return err("No message", 400)

    query_types = classify_query(msg)

    chunks = []
    try:
        chunks = hybrid_search(msg, cids, 30)
    except Exception as e:
        log.debug(f"chat: {e}")

    ref_ids = list(set(c["contract_id"] for c in chunks)) if chunks else []
    meta = sb.table("contracts").select("id,name,party_name,contract_type,start_date,end_date,value").in_("id", ref_ids).execute().data if ref_ids else []
    ml = {c["id"]: c for c in meta}

    if cids and len(cids) <= 3:
        full_data = sb.table("contracts").select("id,name,party_name,contract_type,content,start_date,end_date,value,status").in_("id", cids).execute().data or []
        if full_data:
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
        q = sb.table("contracts").select("id,name,party_name,contract_type,content,value,status")
        if cids:
            q = q.in_("id", cids)
        r = q.limit(20).execute()
        if r.data:
            budget_per = 100000 // max(len(r.data), 1)
            ctx = "\n\n".join(f"--- {c['name']} (Value: {c.get('value','N/A')}) ---\n{(c.get('content') or '')[:budget_per]}" for c in r.data)
            summ = "\n".join(f"- {c['name']} ({c['party_name']}, {c['contract_type']})" for c in r.data)
            meta = r.data
        else:
            ctx = "No contracts found in the system."
            summ = "None."

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

        pos_query = sb.table("chat_feedback").select("query,response_snippet,rating").eq("rating", "up").order("created_at", desc=True).limit(10)
        if cids:
            pos_query = pos_query.contains("contract_ids", cids[:3])
        pos_fb = pos_query.execute().data or []
        if pos_fb:
            learnings += "\nPOSITIVE FEEDBACK (users liked this style):\n"
            for fb in pos_fb[:5]:
                learnings += f"- User asked: \"{fb['query'][:100]}\" — Response was well received.\n"
    except Exception:
        pass

    sys_prompt = build_prompt(summ, ctx, query_types, learnings)
    msgs = [{"role": "system", "content": sys_prompt}] + history + [{"role": "user", "content": msg}]
    sources = [{"id": c["id"], "name": c.get("name", ""), "party": c.get("party_name", "")} for c in meta]
    n_chunks = len(chunks)
    contract_names = [c.get("name", "") for c in meta] if meta else []

    if stream:
        def gen():
            full_response = []
            try:
                for tok in oai_stream(msgs):
                    full_response.append(tok)
                    yield f"data: {J.dumps({'c': tok})}\n\n"
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


# ─── Chat Feedback ────────────────────────────────────────────────────────

@bp.route("/api/chat/feedback", methods=["POST"])
@auth
@need_db
def chat_feedback():
    d = request.json or {}
    query = _sanitize(d.get("query", ""))[:500]
    response_snippet = _sanitize(d.get("response_snippet", ""))[:1000]
    rating = d.get("rating", "")
    if rating not in ("up", "down"):
        return err("Rating must be 'up' or 'down'", 400)
    if not query:
        return err("Query is required", 400)
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


@bp.route("/api/chat/feedback/stats", methods=["GET"])
@auth
@need_db
def chat_feedback_stats():
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


# ─── Chat Sessions ────────────────────────────────────────────────────────

@bp.route("/api/chat/sessions", methods=["GET"])
@auth
@need_db
def list_chat_sessions():
    try:
        sessions = sb.table("chat_sessions").select("id,scope_label,contract_ids,updated_at,messages") \
            .eq("user_email", request.user_email) \
            .order("updated_at", desc=True).limit(20).execute().data or []
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


@bp.route("/api/chat/sessions", methods=["POST"])
@auth
@need_db
def save_chat_session():
    d = request.json or {}
    messages = d.get("messages", [])
    if not messages:
        return err("No messages to save", 400)
    clean_msgs = []
    for m in messages[:100]:
        clean = {"role": m.get("role", "user"), "content": _sanitize(m.get("content", ""))[:10000]}
        if m.get("sources"):
            clean["sources"] = m["sources"][:20]
        clean_msgs.append(clean)

    session_id = d.get("session_id")
    scope_label = _sanitize(d.get("scope_label", "All Contracts"))[:200]
    contract_ids = d.get("contract_ids", [])[:50]

    try:
        if session_id:
            sb.table("chat_sessions").update({
                "messages": clean_msgs,
                "scope_label": scope_label,
                "contract_ids": contract_ids,
                "updated_at": datetime.utcnow().isoformat(),
            }).eq("id", int(session_id)).eq("user_email", request.user_email).execute()
            return jsonify({"id": int(session_id), "ok": True})
        else:
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


@bp.route("/api/chat/sessions/<int:sid>", methods=["GET"])
@auth
@need_db
def get_chat_session(sid):
    try:
        r = sb.table("chat_sessions").select("*").eq("id", sid).eq("user_email", request.user_email).execute()
        if not r.data:
            return err("Session not found", 404)
        return jsonify(r.data[0])
    except Exception as e:
        log.error(f"Chat session get error: {e}")
        return err("Internal server error", 500)


@bp.route("/api/chat/sessions/<int:sid>", methods=["DELETE"])
@auth
@need_db
def delete_chat_session(sid):
    try:
        sb.table("chat_sessions").delete().eq("id", sid).eq("user_email", request.user_email).execute()
        return jsonify({"ok": True})
    except Exception as e:
        log.error(f"Chat session delete error: {e}")
        return err("Internal server error", 500)


# ─── Leegality Status ────────────────────────────────────────────────────

@bp.route("/api/leegality/status")
@auth
def leegality_status():
    return jsonify({"configured": bool(LEEGALITY_KEY), "provider": "Leegality"})
