"""Flask API for Contract Manager — Vercel serverless deployment with Supabase."""

import os
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from openai import OpenAI
from supabase import create_client

app = Flask(__name__, static_folder="../public", static_url_path="")

# --- Supabase Setup ---
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://execvrooffolrkjqqeor.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImV4ZWN2cm9vZmZvbHJranFxZW9yIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzU3OTQ0MzgsImV4cCI6MjA5MTM3MDQzOH0.ePRSiu6a60mzkEL2bOC0TOUy3JOQdXt_rItfZExWVVs")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


# --- Routes ---

@app.route("/")
def serve_frontend():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/contracts", methods=["GET"])
def list_contracts():
    contract_type = request.args.get("type")
    query = supabase.table("contracts").select("id, name, party_name, contract_type, start_date, end_date, value, added_on, notes").order("added_on", desc=True)
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
    return jsonify({"id": contract_id, "message": "Contract added successfully"}), 201


@app.route("/api/contracts/<int:contract_id>", methods=["GET"])
def get_contract(contract_id):
    result = supabase.table("contracts").select("*").eq("id", contract_id).execute()
    if not result.data:
        return jsonify({"error": "Contract not found"}), 404
    return jsonify(result.data[0])


@app.route("/api/contracts/<int:contract_id>", methods=["DELETE"])
def delete_contract(contract_id):
    # Check if exists
    check = supabase.table("contracts").select("id").eq("id", contract_id).execute()
    if not check.data:
        return jsonify({"error": "Contract not found"}), 404
    supabase.table("contracts").delete().eq("id", contract_id).execute()
    return jsonify({"message": "Contract deleted"})


@app.route("/api/search", methods=["GET"])
def search_contracts():
    query = request.args.get("q", "")
    if not query:
        return jsonify([])

    # Search across name, party_name, and content using ilike
    search_term = f"%{query}%"
    result = supabase.table("contracts").select(
        "id, name, party_name, contract_type, start_date, end_date, value"
    ).or_(
        f"name.ilike.{search_term},party_name.ilike.{search_term},content.ilike.{search_term}"
    ).limit(20).execute()

    return jsonify(result.data)


@app.route("/api/chat", methods=["POST"])
def chat():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return jsonify({"error": "OPENAI_API_KEY not configured on server"}), 500

    data = request.json
    user_message = data.get("message", "")
    history = data.get("history", [])
    contract_ids = data.get("contract_ids")

    if not user_message:
        return jsonify({"error": "No message provided"}), 400

    # Load contracts from Supabase
    query = supabase.table("contracts").select("id, name, party_name, contract_type, start_date, end_date, value, content, notes")
    if contract_ids:
        query = query.in_("id", contract_ids)
    result = query.execute()
    contracts = result.data

    # Build context
    if not contracts:
        contract_context = "No contracts are currently loaded in the system."
    else:
        parts = []
        for c in contracts:
            header = f"--- CONTRACT #{c['id']}: {c['name']} ---"
            meta = f"Party: {c['party_name']} | Type: {c['contract_type'].upper()}"
            if c.get("start_date"):
                meta += f" | Start: {c['start_date']}"
            if c.get("end_date"):
                meta += f" | End: {c['end_date']}"
            if c.get("value"):
                meta += f" | Value: {c['value']}"
            parts.append(f"{header}\n{meta}\n\n{c['content']}\n{'=' * 60}")
        contract_context = "\n\n".join(parts)

    system_prompt = f"""You are a contract analysis assistant for a finance team. You have access to the company's client and vendor contracts.

Your job is to:
1. Answer questions about specific contracts accurately by referencing the exact clauses and terms
2. Compare terms across contracts when asked
3. Highlight important dates, payment terms, penalties, and obligations
4. Flag any risks or unusual clauses when asked
5. Always cite which contract and which section your answer comes from

Rules:
- Only answer based on the contract data provided. Never make up information.
- If a question cannot be answered from the available contracts, say so clearly.
- Be precise with numbers, dates, and financial figures — quote them exactly as they appear.
- When referencing a contract, mention the contract name and the party name.

Here are the contracts you have access to:

{contract_context}"""

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o",
            max_tokens=4096,
            messages=messages,
        )
        reply = response.choices[0].message.content
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
