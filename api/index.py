"""Flask API for Contract Manager — Vercel serverless deployment."""

import os
import sys
import json
import sqlite3
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from openai import OpenAI

app = Flask(__name__, static_folder="../public", static_url_path="")

# --- Database Setup (uses /tmp on Vercel for ephemeral storage) ---
# For production, use a cloud database like Supabase, PlanetScale, or Neon

DB_PATH = os.environ.get("DB_PATH", "/tmp/contracts.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contracts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            party_name TEXT NOT NULL,
            contract_type TEXT NOT NULL CHECK(contract_type IN ('client', 'vendor')),
            start_date TEXT,
            end_date TEXT,
            value TEXT,
            content TEXT NOT NULL,
            added_on TEXT NOT NULL,
            notes TEXT DEFAULT ''
        )
    """)
    conn.commit()
    conn.close()


init_db()


# --- Routes ---

@app.route("/")
def serve_frontend():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/contracts", methods=["GET"])
def list_contracts():
    conn = get_db()
    contract_type = request.args.get("type")
    if contract_type:
        rows = conn.execute(
            "SELECT id, name, party_name, contract_type, start_date, end_date, value, added_on, notes FROM contracts WHERE contract_type = ? ORDER BY added_on DESC",
            (contract_type,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, name, party_name, contract_type, start_date, end_date, value, added_on, notes FROM contracts ORDER BY added_on DESC"
        ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/contracts", methods=["POST"])
def add_contract():
    data = request.json
    required = ["name", "party_name", "contract_type", "content"]
    for field in required:
        if not data.get(field):
            return jsonify({"error": f"Missing required field: {field}"}), 400

    if data["contract_type"] not in ("client", "vendor"):
        return jsonify({"error": "contract_type must be 'client' or 'vendor'"}), 400

    conn = get_db()
    cursor = conn.execute(
        """INSERT INTO contracts (name, party_name, contract_type, start_date, end_date, value, content, added_on, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            data["name"],
            data["party_name"],
            data["contract_type"],
            data.get("start_date"),
            data.get("end_date"),
            data.get("value"),
            data["content"],
            datetime.now().isoformat(),
            data.get("notes", ""),
        ),
    )
    contract_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return jsonify({"id": contract_id, "message": "Contract added successfully"}), 201


@app.route("/api/contracts/<int:contract_id>", methods=["GET"])
def get_contract(contract_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM contracts WHERE id = ?", (contract_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Contract not found"}), 404
    return jsonify(dict(row))


@app.route("/api/contracts/<int:contract_id>", methods=["DELETE"])
def delete_contract(contract_id):
    conn = get_db()
    row = conn.execute("SELECT id FROM contracts WHERE id = ?", (contract_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Contract not found"}), 404
    conn.execute("DELETE FROM contracts WHERE id = ?", (contract_id,))
    conn.commit()
    conn.close()
    return jsonify({"message": "Contract deleted"})


@app.route("/api/search", methods=["GET"])
def search_contracts():
    query = request.args.get("q", "")
    if not query:
        return jsonify([])

    conn = get_db()
    # Simple LIKE search (FTS not available in /tmp ephemeral DB easily)
    search_term = f"%{query}%"
    rows = conn.execute(
        """SELECT id, name, party_name, contract_type, start_date, end_date, value,
                  substr(content, max(1, instr(lower(content), lower(?)) - 80), 200) as snippet
           FROM contracts
           WHERE lower(name) LIKE lower(?) OR lower(party_name) LIKE lower(?) OR lower(content) LIKE lower(?)
           LIMIT 20""",
        (query, search_term, search_term, search_term),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


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

    # Load contracts
    conn = get_db()
    if contract_ids:
        placeholders = ",".join("?" * len(contract_ids))
        rows = conn.execute(
            f"SELECT id, name, party_name, contract_type, start_date, end_date, value, content, notes FROM contracts WHERE id IN ({placeholders})",
            contract_ids,
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, name, party_name, contract_type, start_date, end_date, value, content, notes FROM contracts"
        ).fetchall()
    conn.close()
    contracts = [dict(r) for r in rows]

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
