"""AI Chatbot module for contract Q&A using OpenAI API."""

import os
from openai import OpenAI
from contract_cli.database import get_all_contracts_for_chat, search_contracts

SYSTEM_PROMPT = """You are a contract analysis assistant for a finance team. You have access to the company's client and vendor contracts.

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
"""


def build_contract_context(contracts):
    """Build context string from contract data."""
    if not contracts:
        return "No contracts are currently loaded in the system."

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
        if c.get("notes"):
            meta += f"\nNotes: {c['notes']}"
        parts.append(f"{header}\n{meta}\n\n{c['content']}\n{'=' * 60}")

    return "\n\n".join(parts)


def chat_session(contract_ids=None):
    """Run an interactive chat session about contracts.

    Args:
        contract_ids: Optional list of contract IDs to focus on. If None, loads all contracts.

    Returns a callable that takes a question and returns an answer.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "OPENAI_API_KEY environment variable is not set.\n"
            "Get your API key from https://platform.openai.com/api-keys\n"
            "Then run: export OPENAI_API_KEY='your-key-here'"
        )

    client = OpenAI(api_key=api_key)
    contracts = get_all_contracts_for_chat(contract_ids)
    contract_context = build_contract_context(contracts)

    system_message = f"{SYSTEM_PROMPT}\n\nHere are the contracts you have access to:\n\n{contract_context}"

    messages = [{"role": "system", "content": system_message}]

    def ask(user_question):
        messages.append({"role": "user", "content": user_question})

        response = client.chat.completions.create(
            model="gpt-4o",
            max_tokens=4096,
            messages=messages,
        )

        assistant_reply = response.choices[0].message.content
        messages.append({"role": "assistant", "content": assistant_reply})
        return assistant_reply

    return ask
