"""Flask API for Contract Manager — Vercel deployment with Supabase + RAG."""

import os
import re
import io
import json as json_module
import requests as req_lib
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from supabase import create_client
import fitz  # PyMuPDF for PDF text extraction

app = Flask(__name__, static_folder="../public", static_url_path="")

# --- Config ---
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://execvrooffolrkjqqeor.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImV4ZWN2cm9vZmZvbHJranFxZW9yIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzU3OTQ0MzgsImV4cCI6MjA5MTM3MDQzOH0.ePRSiu6a60mzkEL2bOC0TOUy3JOQdXt_rItfZExWVVs")
OPENAI_API_URL = "https://api.openai.com/v1"
EMBEDDING_MODEL = "text-embedding-3-small"
CHUNK_SIZE = 800  # chars per chunk
CHUNK_OVERLAP = 150  # overlap between chunks

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def openai_headers():
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def openai_chat(messages, model="gpt-4o", max_tokens=4096):
    """Call OpenAI Chat API using requests (avoids SDK connection issues on serverless)."""
    headers = openai_headers()
    if not headers:
        raise ValueError("OPENAI_API_KEY not configured")
    resp = req_lib.post(
        f"{OPENAI_API_URL}/chat/completions",
        headers=headers,
        json={"model": model, "max_tokens": max_tokens, "messages": messages},
        timeout=55,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def openai_embeddings(texts):
    """Call OpenAI Embeddings API using requests."""
    headers = openai_headers()
    if not headers:
        raise ValueError("OPENAI_API_KEY not configured")
    resp = req_lib.post(
        f"{OPENAI_API_URL}/embeddings",
        headers=headers,
        json={"model": EMBEDDING_MODEL, "input": texts},
        timeout=55,
    )
    resp.raise_for_status()
    return [item["embedding"] for item in resp.json()["data"]]


# --- Chunking & Embedding ---

def chunk_contract(content, contract_name=""):
    """Split contract text into overlapping chunks, preserving section boundaries."""
    # Try to split by numbered sections first (e.g., "1.", "2.", "10.")
    section_pattern = r'\n(?=\d{1,2}[\.\)]\s+[A-Z])'
    sections = re.split(section_pattern, content)

    chunks = []
    for section in sections:
        section = section.strip()
        if not section:
            continue

        # Extract section title (first line)
        lines = section.split('\n')
        section_title = lines[0].strip()[:100] if lines else ""

        # If section is small enough, keep as one chunk
        if len(section) <= CHUNK_SIZE:
            chunks.append({
                "text": section,
                "section_title": section_title,
            })
        else:
            # Split large sections into overlapping chunks
            for i in range(0, len(section), CHUNK_SIZE - CHUNK_OVERLAP):
                chunk_text = section[i:i + CHUNK_SIZE]
                if len(chunk_text.strip()) < 50:
                    continue
                chunks.append({
                    "text": chunk_text,
                    "section_title": section_title if i == 0 else f"{section_title} (cont.)",
                })

    return chunks


def embed_contract(contract_id, content, contract_name):
    """Chunk a contract, create embeddings, and store in Supabase."""
    supabase.table("contract_chunks").delete().eq("contract_id", contract_id).execute()

    chunks = chunk_contract(content, contract_name)
    if not chunks:
        return 0

    batch_size = 20
    total_stored = 0

    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        texts = [c["text"] for c in batch]
        embeddings = openai_embeddings(texts)

        rows = []
        for j, (chunk, embedding) in enumerate(zip(batch, embeddings)):
            rows.append({
                "contract_id": contract_id,
                "chunk_index": i + j,
                "chunk_text": chunk["text"],
                "section_title": chunk["section_title"],
                "embedding": embedding,
            })

        supabase.table("contract_chunks").insert(rows).execute()
        total_stored += len(rows)

    return total_stored


def semantic_search(query, contract_ids=None, match_count=12):
    """Find the most relevant contract chunks for a query."""
    query_embedding = openai_embeddings([query])[0]

    params = {
        "query_embedding": query_embedding,
        "match_count": match_count,
    }
    if contract_ids:
        params["filter_contract_ids"] = contract_ids

    result = supabase.rpc("match_chunks", params).execute()
    return result.data


# --- Routes ---

@app.route("/")
def serve_frontend():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/contracts", methods=["GET"])
def list_contracts():
    contract_type = request.args.get("type")
    query = supabase.table("contracts").select(
        "id, name, party_name, contract_type, start_date, end_date, value, added_on, notes"
    ).order("added_on", desc=True)
    if contract_type:
        query = query.eq("contract_type", contract_type)
    result = query.execute()
    return jsonify(result.data)


@app.route("/api/contracts", methods=["POST"])
def add_contract():
    data = request.json
    required = ["name", "party_name", "contract_type", "content"]
    for field in required:
        if not data.get(field):
            return jsonify({"error": f"Missing required field: {field}"}), 400

    if data["contract_type"] not in ("client", "vendor"):
        return jsonify({"error": "contract_type must be 'client' or 'vendor'"}), 400

    row = {
        "name": data["name"],
        "party_name": data["party_name"],
        "contract_type": data["contract_type"],
        "start_date": data.get("start_date") or None,
        "end_date": data.get("end_date") or None,
        "value": data.get("value") or None,
        "content": data["content"],
        "added_on": datetime.now().isoformat(),
        "notes": data.get("notes", ""),
    }

    result = supabase.table("contracts").insert(row).execute()
    contract_id = result.data[0]["id"]

    # Auto-embed the contract for RAG
    chunks_count = 0
    if openai_headers():
        try:
            chunks_count = embed_contract(contract_id, data["content"], data["name"])
        except Exception:
            pass  # Non-blocking — contract is saved even if embedding fails

    return jsonify({
        "id": contract_id,
        "message": "Contract added successfully",
        "chunks_embedded": chunks_count,
    }), 201


@app.route("/api/contracts/<int:contract_id>", methods=["GET"])
def get_contract(contract_id):
    result = supabase.table("contracts").select("*").eq("id", contract_id).execute()
    if not result.data:
        return jsonify({"error": "Contract not found"}), 404
    return jsonify(result.data[0])


@app.route("/api/contracts/<int:contract_id>", methods=["DELETE"])
def delete_contract(contract_id):
    check = supabase.table("contracts").select("id").eq("id", contract_id).execute()
    if not check.data:
        return jsonify({"error": "Contract not found"}), 404
    # Chunks auto-deleted via CASCADE
    supabase.table("contracts").delete().eq("id", contract_id).execute()
    return jsonify({"message": "Contract deleted"})


@app.route("/api/contracts/<int:contract_id>/embed", methods=["POST"])
def embed_single_contract(contract_id):
    """Manually trigger embedding for a contract."""
    if not openai_headers():
        return jsonify({"error": "OPENAI_API_KEY not configured"}), 500

    result = supabase.table("contracts").select("id, name, content").eq("id", contract_id).execute()
    if not result.data:
        return jsonify({"error": "Contract not found"}), 404

    contract = result.data[0]
    try:
        count = embed_contract(contract["id"], contract["content"], contract["name"])
        return jsonify({"message": f"Embedded {count} chunks", "chunks": count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/upload-pdf", methods=["POST"])
def upload_pdf():
    """Extract text from uploaded PDF file."""
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Only PDF files are supported"}), 400

    try:
        pdf_bytes = file.read()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = ""
        for page in doc:
            text += page.get_text() + "\n"
        doc.close()

        if not text.strip():
            return jsonify({"error": "Could not extract text from PDF. It may be scanned/image-based."}), 400

        return jsonify({"content": text.strip(), "pages": len(doc)})
    except Exception as e:
        return jsonify({"error": f"Failed to process PDF: {str(e)}"}), 500


@app.route("/api/search", methods=["GET"])
def search_contracts():
    query = request.args.get("q", "")
    if not query:
        return jsonify([])

    search_term = f"%{query}%"
    result = supabase.table("contracts").select(
        "id, name, party_name, contract_type, start_date, end_date, value"
    ).or_(
        f"name.ilike.{search_term},party_name.ilike.{search_term},content.ilike.{search_term}"
    ).limit(20).execute()

    return jsonify(result.data)


@app.route("/api/parse", methods=["POST"])
def parse_contract():
    """Use AI to extract metadata from contract text for auto-fill."""
    if not openai_headers():
        return jsonify({"error": "OPENAI_API_KEY not configured on server"}), 500

    data = request.json
    content = data.get("content", "")
    if not content:
        return jsonify({"error": "No content provided"}), 400

    preview = content[:3000]

    try:
        reply = openai_chat(
            messages=[
                {"role": "system", "content": """Extract contract metadata from the text and return ONLY a JSON object with these fields:
- "name": a short descriptive contract title (e.g., "Cloud Service Agreement")
- "party_name": the other party's company name (not the company that owns the contract)
- "contract_type": either "client" or "vendor" (client = companies who pay us for cloud/RA/AI/software services — our revenue; vendor = companies/subcontractors we pay to deliver services — our cost. We are a broker between them)
- "start_date": in YYYY-MM-DD format, or null
- "end_date": in YYYY-MM-DD format, or null
- "value": total contract value as a string with currency (e.g., "USD 378,000"), or null
- "notes": a one-line summary of what this contract covers

Return ONLY valid JSON, no markdown, no explanation."""},
                {"role": "user", "content": preview}
            ],
            model="gpt-4o-mini",
            max_tokens=300,
        ).strip()

        if reply.startswith("```"):
            reply = reply.split("\n", 1)[1] if "\n" in reply else reply[3:]
            if reply.endswith("```"):
                reply = reply[:-3]
            reply = reply.strip()

        parsed = json_module.loads(reply)
        return jsonify(parsed)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/chat", methods=["POST"])
def chat():
    """RAG-powered chat — semantic search + LLM."""
    if not openai_headers():
        return jsonify({"error": "OPENAI_API_KEY not configured on server"}), 500

    data = request.json
    user_message = data.get("message", "")
    history = data.get("history", [])
    contract_ids = data.get("contract_ids")

    if not user_message:
        return jsonify({"error": "No message provided"}), 400

    # Step 1: Semantic search — find relevant chunks
    try:
        relevant_chunks = semantic_search(
            user_message,
            contract_ids=contract_ids,
            match_count=15,
        )
    except Exception:
        relevant_chunks = []

    # Step 2: Get contract metadata for referenced contracts
    referenced_ids = list(set(c["contract_id"] for c in relevant_chunks)) if relevant_chunks else []

    if referenced_ids:
        contracts_meta = supabase.table("contracts").select(
            "id, name, party_name, contract_type, start_date, end_date, value"
        ).in_("id", referenced_ids).execute().data
    else:
        contracts_meta = []

    # Build contracts lookup
    meta_lookup = {c["id"]: c for c in contracts_meta}

    # Step 3: Build RAG context from relevant chunks
    if relevant_chunks:
        context_parts = []
        for chunk in relevant_chunks:
            c_meta = meta_lookup.get(chunk["contract_id"], {})
            c_name = c_meta.get("name", f"Contract #{chunk['contract_id']}")
            c_party = c_meta.get("party_name", "Unknown")
            similarity = f"{chunk['similarity']:.2f}" if chunk.get('similarity') else "N/A"

            context_parts.append(
                f"[Source: {c_name} | Party: {c_party} | Section: {chunk.get('section_title', 'N/A')} | Relevance: {similarity}]\n"
                f"{chunk['chunk_text']}"
            )
        contract_context = "\n\n---\n\n".join(context_parts)

        # Add contract summaries
        summaries = []
        for c in contracts_meta:
            s = f"- {c['name']} (Party: {c['party_name']}, Type: {c['contract_type'].upper()}"
            if c.get("start_date"):
                s += f", Start: {c['start_date']}"
            if c.get("end_date"):
                s += f", End: {c['end_date']}"
            if c.get("value"):
                s += f", Value: {c['value']}"
            s += ")"
            summaries.append(s)
        contracts_summary = "\n".join(summaries)
    else:
        # Fallback: load full contracts if no embeddings exist
        query = supabase.table("contracts").select(
            "id, name, party_name, contract_type, start_date, end_date, value, content"
        )
        if contract_ids:
            query = query.in_("id", contract_ids)
        result = query.execute()

        if result.data:
            parts = []
            for c in result.data:
                header = f"--- {c['name']} (Party: {c['party_name']}) ---"
                parts.append(f"{header}\n{c['content']}")
            contract_context = "\n\n".join(parts)
            contracts_summary = "All contracts loaded (no embeddings yet — full text used)."
        else:
            contract_context = "No contracts are currently in the system."
            contracts_summary = "None"

    system_prompt = f"""You are an expert contract analysis assistant for the finance team of a technology services company.

ABOUT OUR COMPANY (EMB / Mantarav Private Limited):
We are EMB (Expand My Business / Mantarav Private Limited), a technology services broker company.
Our corporate office: Incuspaze, Phase 4, Maruti Udyog, Sector 18, Gurgaon, Haryana, 122015.

SERVICE VERTICALS:
1. Cloud Resell, Migration & Managed Services — AWS/Azure/GCP partner-led billing, cloud migration, managed infrastructure services. We transfer customer AWS billing to our partner account, provide discounts, and handle optimization.
2. Resource Augmentation (RA) — placing skilled IT professionals (developers, DevOps, QA, data engineers, etc.) at client sites on time & material or fixed-cost basis.
3. AI Development & Software Development — building custom AI solutions, ML models, chatbots, and full-stack software products for clients.

BUSINESS MODEL:
- We act as a BROKER between clients and vendors
- CLIENT contracts = companies who pay us for services (our revenue). Examples: AWS bill transfer agreements, managed services contracts, RA staffing contracts, software development contracts
- VENDOR contracts = companies/subcontractors we pay to deliver those services (our cost). Examples: AWS (cloud infrastructure), freelancers, dev agencies, staffing vendors
- Our margin = Client contract value - Vendor contract value for the same project/service
- For cloud resell: our margin = discount we get from AWS minus discount we pass to customer
- We ensure smooth delivery between both sides

CONTRACTS IN SYSTEM:
{contracts_summary}

RELEVANT CONTRACT SECTIONS (retrieved via semantic search, ranked by relevance):

{contract_context}

QUESTION CATEGORIES YOU MUST BE EXPERT IN:

**Financial & Payment Questions:**
- Payment terms (Net 30, Net 45, etc.), invoice cycles, billing frequency
- Total contract value, monthly/annual fees, rate cards
- Late payment penalties, interest on overdue amounts
- Early payment discounts, volume discounts
- Payment milestones, advance payments, retention amounts
- Currency, GST/tax applicability, TDS deductions
- Margin analysis: what we charge client vs what we pay vendor

**Timeline & Duration Questions:**
- Contract start date, end date, total duration
- Renewal terms (auto-renewal, notice period for non-renewal)
- Lock-in periods, minimum commitment duration
- Project milestones, delivery timelines, go-live dates
- Ramp-up and ramp-down schedules (especially for RA contracts)

**SLA & Performance Questions:**
- Uptime guarantees, response times, resolution times
- SLA credits and penalties for non-compliance
- Performance benchmarks and KPIs
- Reporting frequency and format
- Escalation matrix and incident response

**Resource & Staffing Questions (RA Contracts):**
- Resource rates (hourly/daily/monthly), rate escalation clauses
- Replacement timelines if resource leaves
- Background verification, NDA requirements
- Working hours, overtime rates, holiday billing
- Bench period policies, notice period for resource release

**Legal & Compliance Questions:**
- Termination clauses (for convenience, for cause, immediate)
- Notice periods for termination
- Confidentiality and NDA terms, survival period
- Non-compete and non-solicitation clauses
- Intellectual property ownership
- Data protection, GDPR compliance, data residency
- Insurance requirements (professional liability, cyber insurance)
- Indemnification and limitation of liability
- Force majeure clauses

**Risk & Exposure Questions:**
- What happens if vendor fails to deliver?
- Penalty exposure: max penalties we could face
- Liability caps (ours vs theirs)
- Auto-renewal traps (contracts that renew without action)
- Contracts expiring soon that need attention
- Mismatches between client SLAs and vendor SLAs (our risk gap)
- Single points of failure (one vendor serving multiple clients)

**Comparison & Cross-Contract Questions:**
- Compare payment terms across all vendor contracts
- Which contracts have the best/worst termination flexibility?
- Compare SLA commitments across client contracts vs vendor contracts
- Find all contracts expiring in next 3/6/12 months
- Which contracts have auto-renewal? Which need manual renewal?
- Rate comparison across RA contracts
- Find all contracts with a specific clause (e.g., non-compete, IP ownership)

**Cloud Resell / AWS Bill Transfer Questions:**
- What discount are we offering to this customer on their AWS bill?
- What is the customer's monthly AWS consumption?
- Does the discount apply to AWS Marketplace and AWS Support charges?
- What are the billing transfer terms? How quickly must the customer transfer billing?
- Can the customer buy Savings Plans or Reserved Instances directly from AWS during the contract?
- What happens to AWS billing if the contract is terminated?
- Who is responsible for AWS infrastructure management?
- Who bears the risk if AWS has downtime or SLA breach?
- Are AWS Marketplace charges billed in USD? What exchange rate applies?
- What are EMB's responsibilities vs customer's responsibilities in cloud management?
- Is there a minimum consumption commitment?
- Does the discount apply on unblended or blended AWS rates?

**Broker-Specific / Margin Questions:**
- What's our margin on this project (client rate vs vendor rate)?
- What discount are we giving vs what discount we get from AWS?
- Are our vendor SLAs aligned with what we promised the client?
- If a vendor contract ends, which client deliveries are at risk?
- Do our vendor payment terms give us enough buffer before client payment is due?
- Are there any gaps in insurance coverage between client requirements and vendor policies?
- What is our total revenue exposure if this client terminates?
- What is the payment cycle gap (when we pay vendor vs when client pays us)?

**Specific Contract Deep-Dive Questions:**
- Summarize this contract in 5 bullet points
- What are the key risks in this contract for us?
- What are all the termination scenarios and their consequences?
- List all financial obligations (fees, penalties, interest, taxes)
- What are the notice periods for different actions in this contract?
- What survives after termination of this contract?
- Who are the key contacts and signatories?
- What is the dispute resolution mechanism?
- Is there an arbitration clause? Where is the seat/venue?
- What governing law applies?
- What are the data privacy obligations?
- What are the indemnification obligations? Are they mutual or one-sided?
- What is the liability cap? How is it calculated?
- Does this contract have force majeure coverage?
- What IP rights does each party retain?
- Can either party assign this contract to a third party?
- Is there an auto-renewal clause? How do we opt out?

INSTRUCTIONS:
1. Answer ONLY based on the contract data provided above. Never make up or assume information.
2. Be precise with numbers, dates, and financial figures — quote them EXACTLY as they appear.
3. Always cite the contract name, party name, and specific section/clause number.
4. If the answer cannot be found in the provided contract sections, say so clearly.
5. When comparing contracts, use a structured table format for clarity.
6. Proactively flag risks, mismatches, or important deadlines when relevant.
7. For financial questions, show calculations and breakdowns where applicable.
8. When discussing RA contracts, consider billing days, utilization, and bench risks.
9. Understand that as a broker, both client satisfaction AND vendor management matter."""

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    try:
        reply = openai_chat(messages=messages, model="gpt-4o", max_tokens=4096)

        sources = []
        for c in contracts_meta:
            sources.append({"id": c["id"], "name": c["name"], "party": c["party_name"]})

        return jsonify({
            "reply": reply,
            "sources": sources,
            "chunks_used": len(relevant_chunks),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
