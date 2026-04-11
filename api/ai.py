"""OpenAI helpers, RAG, chunking, OCR for CLM API."""

import os, sys, re, time, base64
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json as J
import requests as http

from config import sb, log, OAI_URL, EMB_MODEL, CHUNK_SZ, CHUNK_OV

# ─── OpenAI ──────────────────────────────────────────────────────────────
def oai_h():
    k = os.environ.get("OPENAI_API_KEY", "").strip()
    return {"Authorization": f"Bearer {k}", "Content-Type": "application/json"} if k else None

def oai_chat(msgs, model="gpt-4o", max_tok=4096, retries=2):
    h = oai_h()
    if not h: raise ValueError("OPENAI_API_KEY not set")
    for i in range(retries + 1):
        try:
            r = http.post(f"{OAI_URL}/chat/completions", headers=h,
                json={"model": model, "max_tokens": max_tok, "messages": msgs}, timeout=55)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            if i < retries: time.sleep(1)
            else: raise

def oai_stream(msgs, model="gpt-4o", max_tok=4096):
    h = oai_h()
    if not h: raise ValueError("OPENAI_API_KEY not set")
    r = http.post(f"{OAI_URL}/chat/completions", headers=h,
        json={"model": model, "max_tokens": max_tok, "messages": msgs, "stream": True},
        timeout=120, stream=True)
    r.raise_for_status()
    for line in r.iter_lines():
        if not line: continue
        d = line.decode("utf-8")
        if d.startswith("data: ") and d != "data: [DONE]":
            try:
                c = J.loads(d[6:])["choices"][0].get("delta", {}).get("content", "")
                if c: yield c
            except: continue

def oai_emb(texts, retries=2):
    h = oai_h()
    if not h: raise ValueError("OPENAI_API_KEY not set")
    for i in range(retries + 1):
        try:
            r = http.post(f"{OAI_URL}/embeddings", headers=h,
                json={"model": EMB_MODEL, "input": texts}, timeout=55)
            r.raise_for_status()
            return [x["embedding"] for x in r.json()["data"]]
        except Exception as e:
            if i < retries: time.sleep(1)
            else: raise

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
    for i in range(0, len(chunks), 20):
        batch = chunks[i:i+20]
        embs = oai_emb([c["text"] for c in batch])
        rows = [{"contract_id": cid, "chunk_index": i+j, "chunk_text": c["text"],
                 "section_title": c["section_title"], "embedding": e}
                for j, (c, e) in enumerate(zip(batch, embs))]
        sb.table("contract_chunks").insert(rows).execute()
        total += len(rows)
    return total

def hybrid_search(query, cids=None, n=30):
    sem, kw = [], []
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
        q = sb.table("contract_chunks").select("contract_id,chunk_text,section_title").ilike("chunk_text", f"%{query}%")
        if cids: q = q.in_("contract_id", cids)
        kw = q.limit(10).execute().data

        # Also search individual important words (numbers, names, specific terms)
        words = [w for w in re.split(r'\s+', query) if len(w) > 3 and w.lower() not in
                 {'what', 'which', 'when', 'where', 'this', 'that', 'with', 'from', 'have', 'does',
                  'about', 'many', 'much', 'there', 'their', 'they', 'been', 'will', 'would',
                  'could', 'should', 'contract', 'agreement', 'mentioned', 'provide'}]
        for w in words[:3]:
            try:
                q2 = sb.table("contract_chunks").select("contract_id,chunk_text,section_title").ilike("chunk_text", f"%{w}%")
                if cids: q2 = q2.in_("contract_id", cids)
                kw += q2.limit(5).execute().data
            except: pass
    except Exception as e: log.debug(f"hybrid_search keyword: {e}")

    # 3. Deduplicate and merge results
    seen, out = set(), []
    for c in sem:
        k = hash(c.get("chunk_text","")[:200])
        if k not in seen: seen.add(k); out.append(c)
    for c in kw:
        k = hash(c.get("chunk_text","")[:200])
        if k not in seen: seen.add(k); out.append({**c, "similarity": 0.5})
    return out

# ─── System Prompt Builder ───────────────────────────────────────────────
def build_prompt(summary, context):
    return f"""You are an expert CLM (Contract Lifecycle Management) assistant for EMB (Expand My Business / Mantarav Private Limited).

COMPANY: EMB -- technology services broker. Cloud Resell (AWS/Azure/GCP), Resource Augmentation, AI/Software Development.
BUSINESS MODEL: Broker between clients (revenue) and vendors (cost). Margin = client value - vendor cost.

CONTRACTS: {summary}

RELEVANT SECTIONS:
{context}

CAPABILITIES: Financial analysis, SLA review, risk assessment, compliance check, clause comparison, deadline tracking, margin analysis.

RULES:
1. Answer ONLY from provided contract data. Never fabricate.
2. Quote exact numbers, dates, clauses. Cite contract name + section.
3. Use markdown formatting (tables, bold, lists).
4. Flag risks and deadlines proactively.
5. For comparisons, use tables."""


# ─── OCR via GPT-4o Vision ─────────────────────────────────────────────
def ocr_pdf_pages(pdf_bytes, max_pages=50):
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
                {"role": "system", "content": "You are an OCR engine. Extract ALL text from this scanned document page exactly as written. Preserve the original formatting, paragraphs, tables, headings, and line breaks. Do NOT summarize or interpret — extract the raw text only. If the page has a table, format it with | separators."},
                {"role": "user", "content": [
                    {"type": "text", "text": f"Extract all text from page {i+1}:"},
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/png;base64,{b64_img}",
                        "detail": "high"
                    }}
                ]}
            ]
            r = http.post(f"{OAI_URL}/chat/completions", headers=h,
                json={"model": "gpt-4o", "max_tokens": 4096, "messages": msgs},
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
