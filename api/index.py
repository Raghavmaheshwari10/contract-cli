"""Flask API for Contract Manager — Vercel deployment with Supabase + RAG."""

import os
import re
import json as json_module
import httpx
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from openai import OpenAI
from supabase import create_client

app = Flask(__name__, static_folder="../public", static_url_path="")

# --- Config ---
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://execvrooffolrkjqqeor.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImV4ZWN2cm9vZmZvbHJranFxZW9yIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzU3OTQ0MzgsImV4cCI6MjA5MTM3MDQzOH0.ePRSiu6a60mzkEL2bOC0TOUy3JOQdXt_rItfZExWVVs")
EMBEDDING_MODEL = "text-embedding-3-small"
CHUNK_SIZE = 800  # chars per chunk
CHUNK_OVERLAP = 150  # overlap between chunks

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def get_openai_client():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    return OpenAI(api_key=api_key, timeout=httpx.Timeout(55.0))


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


def create_embeddings(texts, client):
    """Create embeddings for a list of texts using OpenAI."""
    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=texts,
    )
    return [item.embedding for item in response.data]


def embed_contract(contract_id, content, contract_name, client):
    """Chunk a contract, create embeddings, and store in Supabase."""
    # Delete existing chunks for this contract (re-embedding)
    supabase.table("contract_chunks").delete().eq("contract_id", contract_id).execute()

    chunks = chunk_contract(content, contract_name)
    if not chunks:
        return 0

    # Create embeddings in batches of 20
    batch_size = 20
    total_stored = 0

    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        texts = [c["text"] for c in batch]
        embeddings = create_embeddings(texts, client)

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


def semantic_search(query, client, contract_ids=None, match_count=12):
    """Find the most relevant contract chunks for a query."""
    # Embed the query
    query_embedding = create_embeddings([query], client)[0]

    # Call the match_chunks function in Supabase
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
    client = get_openai_client()
    if client:
        try:
            chunks_count = embed_contract(contract_id, data["content"], data["name"], client)
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
    client = get_openai_client()
    if not client:
        return jsonify({"error": "OPENAI_API_KEY not configured"}), 500

    result = supabase.table("contracts").select("id, name, content").eq("id", contract_id).execute()
    if not result.data:
        return jsonify({"error": "Contract not found"}), 404

    contract = result.data[0]
    try:
        count = embed_contract(contract["id"], contract["content"], contract["name"], client)
        return jsonify({"message": f"Embedded {count} chunks", "chunks": count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
    client = get_openai_client()
    if not client:
        return jsonify({"error": "OPENAI_API_KEY not configured on server"}), 500

    data = request.json
    content = data.get("content", "")
    if not content:
        return jsonify({"error": "No content provided"}), 400

    preview = content[:3000]

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=300,
            messages=[
                {"role": "system", "content": """Extract contract metadata from the text and return ONLY a JSON object with these fields:
- "name": a short descriptive contract title (e.g., "Cloud Service Agreement")
- "party_name": the other party's company name (not the company that owns the contract)
- "contract_type": either "client" or "vendor" (client = someone paying us or we serve them, vendor = someone we pay or who supplies to us)
- "start_date": in YYYY-MM-DD format, or null
- "end_date": in YYYY-MM-DD format, or null
- "value": total contract value as a string with currency (e.g., "USD 378,000"), or null
- "notes": a one-line summary of what this contract covers

Return ONLY valid JSON, no markdown, no explanation."""},
                {"role": "user", "content": preview}
            ],
        )
        reply = response.choices[0].message.content.strip()
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
    client = get_openai_client()
    if not client:
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
            user_message, client,
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

    system_prompt = f"""You are a contract analysis assistant for a finance team. You answer questions using the contract data provided below.

CONTRACTS IN SYSTEM:
{contracts_summary}

RELEVANT CONTRACT SECTIONS (retrieved via semantic search, ranked by relevance):

{contract_context}

INSTRUCTIONS:
1. Answer ONLY based on the contract data above. Never make up information.
2. Be precise with numbers, dates, and financial figures — quote them exactly.
3. Always cite which contract and which section your answer comes from.
4. If the answer cannot be found in the provided sections, say so clearly.
5. Compare terms across contracts when asked.
6. Flag risks or unusual clauses when asked."""

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            max_tokens=4096,
            messages=messages,
        )
        reply = response.choices[0].message.content

        # Include sources in response
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
