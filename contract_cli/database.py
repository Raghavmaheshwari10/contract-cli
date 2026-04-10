"""Database layer for contract storage using SQLite."""

import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "contracts.db")
CONTRACTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "contracts_store")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize database tables."""
    os.makedirs(CONTRACTS_DIR, exist_ok=True)
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contracts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            party_name TEXT NOT NULL,
            contract_type TEXT NOT NULL CHECK(contract_type IN ('client', 'vendor')),
            start_date TEXT,
            end_date TEXT,
            value TEXT,
            file_path TEXT NOT NULL,
            content TEXT NOT NULL,
            added_on TEXT NOT NULL,
            notes TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS contracts_fts
        USING fts5(name, party_name, content, contract_id UNINDEXED)
    """)
    conn.commit()
    conn.close()


def add_contract(name, party_name, contract_type, file_path, start_date=None, end_date=None, value=None, notes=""):
    """Add a new contract to the database."""
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    # Copy file to contracts store
    stored_filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.path.basename(file_path)}"
    stored_path = os.path.join(CONTRACTS_DIR, stored_filename)

    with open(stored_path, "w", encoding="utf-8") as f:
        f.write(content)

    conn = get_connection()
    cursor = conn.execute(
        """INSERT INTO contracts (name, party_name, contract_type, start_date, end_date, value, file_path, content, added_on, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, party_name, contract_type, start_date, end_date, value, stored_path, content, datetime.now().isoformat(), notes),
    )
    contract_id = cursor.lastrowid

    # Index in FTS
    conn.execute(
        "INSERT INTO contracts_fts (name, party_name, content, contract_id) VALUES (?, ?, ?, ?)",
        (name, party_name, content, str(contract_id)),
    )
    conn.commit()
    conn.close()
    return contract_id


def list_contracts(contract_type=None):
    """List all contracts, optionally filtered by type."""
    conn = get_connection()
    if contract_type:
        rows = conn.execute(
            "SELECT id, name, party_name, contract_type, start_date, end_date, value, added_on FROM contracts WHERE contract_type = ? ORDER BY added_on DESC",
            (contract_type,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, name, party_name, contract_type, start_date, end_date, value, added_on FROM contracts ORDER BY added_on DESC"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_contract(contract_id):
    """Get a single contract by ID."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM contracts WHERE id = ?", (contract_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_contract(contract_id):
    """Delete a contract by ID."""
    conn = get_connection()
    contract = conn.execute("SELECT file_path FROM contracts WHERE id = ?", (contract_id,)).fetchone()
    if not contract:
        conn.close()
        return False

    # Remove stored file
    if os.path.exists(contract["file_path"]):
        os.remove(contract["file_path"])

    conn.execute("DELETE FROM contracts WHERE id = ?", (contract_id,))
    conn.execute("DELETE FROM contracts_fts WHERE contract_id = ?", (str(contract_id),))
    conn.commit()
    conn.close()
    return True


def search_contracts(query):
    """Full-text search across contracts."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT c.id, c.name, c.party_name, c.contract_type, c.start_date, c.end_date, c.value,
                  snippet(contracts_fts, 2, '>>>', '<<<', '...', 64) as snippet
           FROM contracts_fts fts
           JOIN contracts c ON c.id = CAST(fts.contract_id AS INTEGER)
           WHERE contracts_fts MATCH ?
           ORDER BY rank
           LIMIT 20""",
        (query,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_contracts_for_chat(contract_ids=None):
    """Get contract contents for chatbot context."""
    conn = get_connection()
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
    return [dict(r) for r in rows]
