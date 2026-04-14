"""OpenAI helpers, RAG, chunking, OCR for CLM API."""

import os, sys, re, time, base64, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests as http

from config import sb, log, OAI_URL, EMB_MODEL, CHUNK_SZ, CHUNK_OV
from constants import (
    OPENAI_TIMEOUT, OPENAI_STREAM_TIMEOUT, OPENAI_RETRIES,
    EMBEDDING_BATCH_SIZE, SEARCH_STOPWORDS, MAX_OCR_PAGES,
)

# ─── OpenAI ──────────────────────────────────────────────────────────────
def oai_h():
    k = os.environ.get("OPENAI_API_KEY", "").strip()
    return {"Authorization": f"Bearer {k}", "Content-Type": "application/json"} if k else None

def oai_chat(msgs, model="gpt-4o", max_tok=4096, retries=OPENAI_RETRIES, temperature=0.3):
    """Send a chat completion request to OpenAI with retry logic.
    Temperature defaults to 0.3 for consistent, factual contract analysis."""
    headers = oai_h()
    if not headers: raise ValueError("OPENAI_API_KEY not set")
    for attempt in range(retries + 1):
        try:
            resp = http.post(f"{OAI_URL}/chat/completions", headers=headers,
                json={"model": model, "max_tokens": max_tok, "messages": msgs,
                      "temperature": temperature},
                timeout=OPENAI_TIMEOUT)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            if attempt < retries: time.sleep(1)
            else: raise

def oai_stream(msgs, model="gpt-4o", max_tok=4096, temperature=0.3):
    """Stream a chat completion from OpenAI, yielding content chunks."""
    headers = oai_h()
    if not headers: raise ValueError("OPENAI_API_KEY not set")
    resp = http.post(f"{OAI_URL}/chat/completions", headers=headers,
        json={"model": model, "max_tokens": max_tok, "messages": msgs,
              "stream": True, "temperature": temperature},
        timeout=OPENAI_STREAM_TIMEOUT, stream=True)
    resp.raise_for_status()
    for line in resp.iter_lines():
        if not line: continue
        decoded = line.decode("utf-8")
        if decoded.startswith("data: ") and decoded != "data: [DONE]":
            try:
                content = json.loads(decoded[6:])["choices"][0].get("delta", {}).get("content", "")
                if content: yield content
            except (json.JSONDecodeError, KeyError, IndexError):
                continue

def oai_emb(texts, retries=OPENAI_RETRIES):
    """Generate embeddings for a list of texts via OpenAI."""
    headers = oai_h()
    if not headers: raise ValueError("OPENAI_API_KEY not set")
    for attempt in range(retries + 1):
        try:
            resp = http.post(f"{OAI_URL}/embeddings", headers=headers,
                json={"model": EMB_MODEL, "input": texts}, timeout=OPENAI_TIMEOUT)
            resp.raise_for_status()
            return [item["embedding"] for item in resp.json()["data"]]
        except Exception as e:
            if attempt < retries: time.sleep(1)
            else: raise

# ─── Query Classification ──────────────────────────────────────────────
QUERY_TYPES = {
    "financial": re.compile(
        r'\b(payment|amount|value|cost|price|invoice|margin|revenue|fee|'
        r'penalty|compensation|rate|budget|billing|salary|remuneration)\b', re.I),
    "dates": re.compile(
        r'\b(date|deadline|expir|renew|terminat|start|end|duration|period|timeline|'
        r'effective|commence|month|year|days)\b', re.I),
    "legal": re.compile(
        r'\b(clause|liability|indemnit|warrant|governing law|jurisdiction|'
        r'arbitrat|dispute|force majeure|confidential|nda|compliance|'
        r'intellectual property|assignment|insurance|sla)\b', re.I),
    "comparison": re.compile(
        r'\b(compar|differ|same|similar|versus|vs\.?|between|contrast)\b', re.I),
    "risk": re.compile(
        r'\b(risk|danger|concern|issue|problem|flag|missing|gap|weak|'
        r'vulnerability|exposure|threat)\b', re.I),
    "summary": re.compile(
        r'\b(summar|overview|brief|highlight|key point|main point|outline|'
        r'what does|tell me about|explain)\b', re.I),
}

def classify_query(query):
    """Classify user query to optimize context retrieval and prompt."""
    types = []
    for qtype, pattern in QUERY_TYPES.items():
        if pattern.search(query):
            types.append(qtype)
    return types if types else ["general"]


# ─── Chunking & RAG ─────────────────────────────────────────────────────
def chunk_text(content):
    """Smart chunking for legal contracts. Splits on major clause boundaries
    while keeping sub-clauses together. Handles Annexures, Schedules, and
    signature blocks as separate priority chunks."""

    # Split on major section headers: "1. TITLE", "2. TITLE", "ANNEXURE", "SCHEDULE", "SIGNATURE"
    # Also split on patterns like "Clause 1", "Article 1", and standalone headers in ALL CAPS
    section_pattern = r'\n(?=(?:\d{1,2}[\.\)]\s+[A-Z]|ANNEXURE|SCHEDULE|SIGNATURE|Annexure|Schedule|Agreed and Accepted))'
    sections = re.split(section_pattern, content)

    chunks = []
    for sec in sections:
        sec = sec.strip()
        if not sec: continue

        # Extract title from first line
        first_line = sec.split('\n')[0].strip()[:120]
        title = first_line

        # Tag special sections for priority retrieval
        lower = sec.lower()
        if any(kw in lower for kw in ['annexure', 'schedule', 'appendix']):
            title = f"ANNEXURE: {first_line}"
        elif any(kw in lower for kw in ['agreed and accepted', 'signature', 'signed by', 'executed by']):
            title = f"SIGNATURES: {first_line}"
        elif any(kw in lower for kw in ['payment', 'fee', 'compensation', 'billing', 'invoice']):
            title = f"FINANCIAL: {first_line}"
        elif any(kw in lower for kw in ['confidential', 'nda', 'non-disclosure']):
            title = f"CONFIDENTIALITY: {first_line}"
        elif any(kw in lower for kw in ['termination', 'exit', 'cancellation']):
            title = f"TERMINATION: {first_line}"
        elif any(kw in lower for kw in ['indemnit', 'liability', 'limitation']):
            title = f"LIABILITY: {first_line}"
        elif any(kw in lower for kw in ['intellectual property', 'ip rights', 'copyright', 'patent']):
            title = f"IP RIGHTS: {first_line}"

        if len(sec) <= CHUNK_SZ:
            chunks.append({"text": sec, "section_title": title})
        else:
            # For large sections, split on sub-clause boundaries (e.g., 6.1, 6.2, 7.3)
            sub_pattern = r'\n(?=\d{1,2}\.\d{1,2}\s)'
            sub_sections = re.split(sub_pattern, sec)

            if len(sub_sections) > 1:
                # Group sub-sections into chunks that fit within CHUNK_SZ
                current = ""
                current_title = title
                for j, sub in enumerate(sub_sections):
                    sub = sub.strip()
                    if not sub: continue
                    sub_title = sub.split('\n')[0].strip()[:80]

                    if len(current) + len(sub) + 2 <= CHUNK_SZ:
                        current = (current + "\n\n" + sub).strip() if current else sub
                    else:
                        if current and len(current.strip()) >= 50:
                            chunks.append({"text": current, "section_title": current_title})
                        current = sub
                        current_title = f"{title} > {sub_title}"

                if current and len(current.strip()) >= 50:
                    chunks.append({"text": current, "section_title": current_title})
            else:
                # No sub-clauses found, use sliding window
                for i in range(0, len(sec), CHUNK_SZ - CHUNK_OV):
                    t = sec[i:i + CHUNK_SZ]
                    if len(t.strip()) < 50: continue
                    chunks.append({"text": t, "section_title": title if i == 0 else f"{title} (cont.)"})

    return chunks

def embed_contract(cid, content, name):
    sb.table("contract_chunks").delete().eq("contract_id", cid).execute()
    chunks = chunk_text(content)
    if not chunks: return 0
    total = 0
    for i in range(0, len(chunks), EMBEDDING_BATCH_SIZE):
        batch = chunks[i:i + EMBEDDING_BATCH_SIZE]
        embs = oai_emb([c["text"] for c in batch])
        rows = [{"contract_id": cid, "chunk_index": i+j, "chunk_text": c["text"],
                 "section_title": c["section_title"], "embedding": e}
                for j, (c, e) in enumerate(zip(batch, embs))]
        sb.table("contract_chunks").insert(rows).execute()
        total += len(rows)
    return total

def hybrid_search(query, cids=None, n=30):
    """Hybrid semantic + keyword search with relevance scoring."""
    sem, kw = [], []
    query_types = classify_query(query)

    # 1. Semantic search via embeddings
    try:
        emb = oai_emb([query])[0]
        p = {"query_embedding": emb, "match_count": n}
        if cids: p["filter_contract_ids"] = cids
        sem = sb.rpc("match_chunks", p).execute().data
    except Exception as e: log.debug(f"hybrid_search semantic: {e}")

    # 2. Keyword search — extract key terms and search
    try:
        # Search with full query
        safe_query = query.replace("%", "\\%").replace("_", "\\_")
        q = sb.table("contract_chunks").select("contract_id,chunk_text,section_title").ilike("chunk_text", f"%{safe_query}%")
        if cids: q = q.in_("contract_id", cids)
        kw = q.limit(10).execute().data

        # Also search individual important words (numbers, names, specific terms)
        words = [w for w in re.split(r'\s+', query)
                 if len(w) > 3 and w.lower() not in SEARCH_STOPWORDS]
        for w in words[:3]:
            try:
                safe_w = w.replace("%", "\\%").replace("_", "\\_")
                q2 = sb.table("contract_chunks").select("contract_id,chunk_text,section_title").ilike("chunk_text", f"%{safe_w}%")
                if cids: q2 = q2.in_("contract_id", cids)
                kw += q2.limit(5).execute().data
            except Exception:
                pass

        # 2b. Section-title–aware search for typed queries
        section_keywords = {
            "financial": ["FINANCIAL", "payment", "fee", "compensation"],
            "legal": ["LIABILITY", "CONFIDENTIALITY", "TERMINATION", "IP RIGHTS"],
            "dates": ["TERMINATION", "effective", "commence"],
            "risk": ["LIABILITY", "TERMINATION", "indemnit"],
        }
        for qt in query_types:
            for sk in section_keywords.get(qt, []):
                try:
                    safe_sk = sk.replace("%", "\\%").replace("_", "\\_")
                    q3 = sb.table("contract_chunks").select("contract_id,chunk_text,section_title").ilike("section_title", f"%{safe_sk}%")
                    if cids: q3 = q3.in_("contract_id", cids)
                    kw += q3.limit(5).execute().data
                except Exception:
                    pass
    except Exception as e: log.debug(f"hybrid_search keyword: {e}")

    # 3. Deduplicate and merge results with scoring
    seen, out = set(), []
    for c in sem:
        k = hash(c.get("chunk_text","")[:200])
        if k not in seen:
            seen.add(k)
            # Boost relevance if section title matches query type
            score = c.get("similarity", 0.5)
            title = c.get("section_title", "").lower()
            for qt in query_types:
                if qt in title or any(sk.lower() in title for sk in section_keywords.get(qt, [])):
                    score = min(score + 0.1, 1.0)
            c["similarity"] = score
            out.append(c)
    for c in kw:
        k = hash(c.get("chunk_text","")[:200])
        if k not in seen: seen.add(k); out.append({**c, "similarity": 0.5})

    # Sort by relevance score
    out.sort(key=lambda x: x.get("similarity", 0), reverse=True)
    return out


# ─── System Prompt Builder ───────────────────────────────────────────────
def build_prompt(summary, context, query_types=None, learnings=""):
    """Build an optimized system prompt based on query classification and past learnings."""

    # Base identity and grounding
    base = """You are an expert CLM (Contract Lifecycle Management) assistant for EMB (Expand My Business / Mantarav Private Limited).

COMPANY CONTEXT:
- EMB is a technology services broker based in India
- Services: Cloud Resell (AWS/Azure/GCP), Resource Augmentation, AI/Software Development
- Business Model: Broker between clients (revenue) and vendors (cost). Margin = client value - vendor cost
- Contracts involve SOWs, MSAs, NDAs, SLAs, vendor agreements, and service contracts"""

    # Query-type specific instructions
    type_instructions = {
        "financial": """
FINANCIAL ANALYSIS FOCUS:
- Extract exact monetary values, payment schedules, and milestones
- Calculate total contract value, monthly/quarterly breakdowns
- Identify payment terms (Net 30/60/90), late payment penalties
- Flag margin implications (client vs vendor amounts)
- Highlight hidden costs, escalation clauses, price revision terms
- Present financial data in tables with currency formatting""",

        "dates": """
DATE & TIMELINE FOCUS:
- List all dates: effective, start, end, renewal, notice periods
- Calculate remaining duration and days to key deadlines
- Identify auto-renewal clauses and opt-out windows
- Flag approaching deadlines (within 30/60/90 days)
- Present timeline data in chronological order""",

        "legal": """
LEGAL ANALYSIS FOCUS:
- Analyze clauses against Indian Contract Act and IT Act standards
- Identify one-sided or unfavourable terms
- Check enforceability of non-compete, indemnity, limitation of liability
- Compare against market-standard clause language
- Flag missing standard protective clauses
- Reference relevant Indian legal precedents where applicable""",

        "comparison": """
COMPARISON FOCUS:
- Create side-by-side comparison tables
- Highlight differences in key terms (value, duration, SLA, penalties)
- Rate each contract's terms (favourable/neutral/unfavourable)
- Identify which contract is more protective for EMB
- Note unique clauses in each contract""",

        "risk": """
RISK ASSESSMENT FOCUS:
- Categorize risks: Legal, Financial, Operational, Compliance
- Rate each risk: Critical/High/Medium/Low
- Identify missing clauses that create exposure
- Flag unlimited liability, broad indemnity, weak termination rights
- Suggest specific mitigation actions for each risk
- Present risk matrix summary""",

        "summary": """
SUMMARY FOCUS:
- Provide structured overview with key terms highlighted
- Include: parties, type, value, duration, key obligations
- List top 3-5 important clauses and their implications
- Note any unusual or non-standard terms
- End with overall assessment (favourable/neutral/concerning)""",
    }

    # Build type-specific guidance
    type_guidance = ""
    if query_types:
        for qt in query_types:
            if qt in type_instructions:
                type_guidance += type_instructions[qt]

    if not type_guidance:
        type_guidance = """
GENERAL ANALYSIS:
- Provide thorough, well-structured answers
- Cover financial, legal, and operational aspects as relevant
- Highlight any risks or concerns proactively"""

    learning_section = ""
    if learnings:
        learning_section = f"""

LEARNING FROM PAST INTERACTIONS:
{learnings}
Use this feedback to adjust your response style. Avoid approaches that received negative feedback. Emulate patterns from positively-rated responses."""

    return f"""{base}

CONTRACTS IN SCOPE: {summary}

RELEVANT CONTRACT SECTIONS:
{context}
{type_guidance}{learning_section}

RESPONSE RULES:
1. Answer ONLY from provided contract data. If information is not in the contracts, say so clearly.
2. Quote exact numbers, dates, and clause text. Always cite the contract name and section reference.
3. Use rich markdown formatting: tables for comparisons, **bold** for key terms, bullet lists for clarity.
4. Proactively flag risks, approaching deadlines, and missing protections.
5. For financial queries, always include a summary table.
6. When multiple contracts are involved, use comparison tables.
7. End substantive answers with a "Key Takeaways" section (2-3 bullet points).
8. If a question is ambiguous, state your interpretation before answering."""


# ─── Follow-up Suggestion Generator ────────────────────────────────────
def generate_followups(query, response_text, contract_names):
    """Generate 2-3 smart follow-up question suggestions based on the conversation."""
    suggestions = []

    # Pattern-based fast suggestions (no API call needed)
    lower_q = query.lower()
    lower_r = response_text.lower() if response_text else ""

    if any(w in lower_q for w in ["payment", "value", "cost", "fee"]):
        suggestions.append("What are the penalty clauses for late payments?")
        suggestions.append("Compare payment terms across all contracts")
    elif any(w in lower_q for w in ["risk", "issue", "concern"]):
        suggestions.append("What clauses are missing from this contract?")
        suggestions.append("What is the liability cap and indemnity coverage?")
    elif any(w in lower_q for w in ["expir", "renew", "terminat"]):
        suggestions.append("What is the notice period for termination?")
        suggestions.append("Are there auto-renewal clauses?")
    elif any(w in lower_q for w in ["summar", "overview", "about"]):
        suggestions.append("What are the key financial terms?")
        suggestions.append("What risks should I be aware of?")
        suggestions.append("When does this contract expire?")
    elif any(w in lower_q for w in ["sla", "performance", "deliverable"]):
        suggestions.append("What are the penalties for SLA breach?")
        suggestions.append("What are the acceptance criteria?")
    else:
        # Generic useful follow-ups
        if contract_names:
            name = contract_names[0] if isinstance(contract_names[0], str) else contract_names[0].get("name", "this contract")
            suggestions.append(f"Summarize the key risks in {name}")
            suggestions.append("What are the financial obligations?")
            suggestions.append("List all important deadlines")

    # Check response for topics not yet explored
    if "indemnit" in lower_r and "indemnit" not in lower_q:
        suggestions.append("Explain the indemnity clause in detail")
    if "terminat" in lower_r and "terminat" not in lower_q:
        suggestions.append("What are the termination conditions?")
    if "confidential" in lower_r and "confidential" not in lower_q:
        suggestions.append("What does the confidentiality clause cover?")

    # Deduplicate and limit
    seen = set()
    unique = []
    for s in suggestions:
        if s.lower() not in seen:
            seen.add(s.lower())
            unique.append(s)
    return unique[:3]


# ─── OCR via GPT-4o Vision ─────────────────────────────────────────────
def ocr_pdf_pages(pdf_bytes, max_pages=MAX_OCR_PAGES):
    """Extract text from scanned/image PDF pages using GPT-4o vision.
    Renders each page as PNG, sends to GPT-4o for OCR.
    Returns (full_text, page_count, ocr_page_count).
    """
    import fitz
    h = oai_h()
    if not h:
        raise ValueError("OPENAI_API_KEY not set — required for OCR")

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page_count = len(doc)
    pages_to_ocr = min(page_count, max_pages)
    all_text = []

    for i in range(pages_to_ocr):
        page = doc[i]
        # Render page at 200 DPI for good OCR quality without huge images
        mat = fitz.Matrix(200/72, 200/72)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img_bytes = pix.tobytes("png")
        b64_img = base64.b64encode(img_bytes).decode("utf-8")

        try:
            msgs = [
                {"role": "system", "content": """You are a precise OCR engine for legal/business documents.
Extract ALL text from this scanned document page exactly as written.

RULES:
1. Preserve original formatting, paragraphs, and line breaks exactly
2. For tables: use | column | separators | with header rows
3. For numbered clauses: maintain exact numbering (1.1, 1.2, etc.)
4. For signatures: note [Signature], [Stamp], [Seal] placeholders
5. For handwritten text: extract as best as possible, mark uncertain words with [?]
6. Preserve ALL numbers, dates, and monetary amounts exactly
7. Do NOT summarize, interpret, or add any text not on the page"""},
                {"role": "user", "content": [
                    {"type": "text", "text": f"Extract all text from page {i+1} of {pages_to_ocr}:"},
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/png;base64,{b64_img}",
                        "detail": "high"
                    }}
                ]}
            ]
            r = http.post(f"{OAI_URL}/chat/completions", headers=h,
                json={"model": "gpt-4o", "max_tokens": 4096, "messages": msgs,
                      "temperature": 0.1},
                timeout=60)
            r.raise_for_status()
            page_text = r.json()["choices"][0]["message"]["content"]
            all_text.append(page_text.strip())
            log.debug(f"OCR page {i+1}/{pages_to_ocr}: {len(page_text)} chars")
        except Exception as e:
            log.warning(f"OCR failed on page {i+1}: {e}")
            all_text.append(f"[Page {i+1}: OCR failed]")

    doc.close()
    full_text = "\n\n--- Page Break ---\n\n".join(all_text)
    return full_text, page_count, pages_to_ocr
