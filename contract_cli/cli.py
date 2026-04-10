"""Main CLI entry point for Contract Manager."""

import argparse
import os
import sys
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.markdown import Markdown
from rich.text import Text
from rich.prompt import Prompt, Confirm
from prompt_toolkit import prompt as pt_prompt
from prompt_toolkit.history import InMemoryHistory

from contract_cli.database import (
    init_db,
    add_contract,
    list_contracts,
    get_contract,
    delete_contract,
    search_contracts,
)
from contract_cli.chatbot import chat_session

console = Console()


def cmd_add(args):
    """Add a new contract."""
    file_path = os.path.abspath(args.file)
    if not os.path.exists(file_path):
        console.print(f"[red]Error:[/red] File not found: {file_path}")
        sys.exit(1)

    name = args.name or Prompt.ask("Contract name")
    party = args.party or Prompt.ask("Party name (client/vendor company)")
    ctype = args.type or Prompt.ask("Contract type", choices=["client", "vendor"])
    start = args.start or Prompt.ask("Start date (YYYY-MM-DD)", default="")
    end = args.end or Prompt.ask("End date (YYYY-MM-DD)", default="")
    value = args.value or Prompt.ask("Contract value", default="")
    notes = args.notes or Prompt.ask("Notes", default="")

    contract_id = add_contract(
        name=name,
        party_name=party,
        contract_type=ctype,
        file_path=file_path,
        start_date=start or None,
        end_date=end or None,
        value=value or None,
        notes=notes,
    )
    console.print(f"\n[green]Contract added successfully![/green] ID: [bold]{contract_id}[/bold]")


def cmd_list(args):
    """List all contracts."""
    contracts = list_contracts(args.type)

    if not contracts:
        console.print("[yellow]No contracts found.[/yellow]")
        return

    table = Table(title="Contracts", show_lines=True)
    table.add_column("ID", style="bold cyan", width=5)
    table.add_column("Name", style="bold")
    table.add_column("Party", style="magenta")
    table.add_column("Type", width=8)
    table.add_column("Start Date", width=12)
    table.add_column("End Date", width=12)
    table.add_column("Value", style="green")

    for c in contracts:
        type_style = "blue" if c["contract_type"] == "client" else "yellow"
        table.add_row(
            str(c["id"]),
            c["name"],
            c["party_name"],
            f"[{type_style}]{c['contract_type'].upper()}[/{type_style}]",
            c["start_date"] or "-",
            c["end_date"] or "-",
            c["value"] or "-",
        )

    console.print(table)
    console.print(f"\nTotal: [bold]{len(contracts)}[/bold] contracts")


def cmd_view(args):
    """View a specific contract."""
    contract = get_contract(args.id)
    if not contract:
        console.print(f"[red]Contract #{args.id} not found.[/red]")
        return

    # Header
    type_color = "blue" if contract["contract_type"] == "client" else "yellow"
    header = f"[bold]{contract['name']}[/bold]\n"
    header += f"Party: [magenta]{contract['party_name']}[/magenta] | Type: [{type_color}]{contract['contract_type'].upper()}[/{type_color}]\n"
    if contract["start_date"]:
        header += f"Start: {contract['start_date']} "
    if contract["end_date"]:
        header += f"| End: {contract['end_date']} "
    if contract["value"]:
        header += f"| Value: [green]{contract['value']}[/green]"
    if contract["notes"]:
        header += f"\nNotes: {contract['notes']}"

    console.print(Panel(header, title=f"Contract #{contract['id']}", border_style="cyan"))

    if args.full:
        console.print("\n[dim]--- Contract Content ---[/dim]\n")
        console.print(contract["content"])
    else:
        # Show first 30 lines
        lines = contract["content"].split("\n")
        preview = "\n".join(lines[:30])
        console.print(f"\n[dim]--- Preview (first 30 lines) ---[/dim]\n")
        console.print(preview)
        if len(lines) > 30:
            console.print(f"\n[dim]... {len(lines) - 30} more lines. Use --full to see entire contract.[/dim]")


def cmd_delete(args):
    """Delete a contract."""
    contract = get_contract(args.id)
    if not contract:
        console.print(f"[red]Contract #{args.id} not found.[/red]")
        return

    console.print(f"Contract: [bold]{contract['name']}[/bold] ({contract['party_name']})")
    if Confirm.ask("Are you sure you want to delete this contract?"):
        delete_contract(args.id)
        console.print("[green]Contract deleted.[/green]")
    else:
        console.print("[yellow]Cancelled.[/yellow]")


def cmd_search(args):
    """Search contracts."""
    query = " ".join(args.query)
    results = search_contracts(query)

    if not results:
        console.print(f"[yellow]No results found for:[/yellow] {query}")
        return

    table = Table(title=f"Search Results for '{query}'", show_lines=True)
    table.add_column("ID", style="bold cyan", width=5)
    table.add_column("Name", style="bold")
    table.add_column("Party", style="magenta")
    table.add_column("Type", width=8)
    table.add_column("Matching Snippet")

    for r in results:
        type_style = "blue" if r["contract_type"] == "client" else "yellow"
        snippet = r["snippet"].replace(">>>", "[bold red]").replace("<<<", "[/bold red]")
        table.add_row(
            str(r["id"]),
            r["name"],
            r["party_name"],
            f"[{type_style}]{r['contract_type'].upper()}[/{type_style}]",
            snippet,
        )

    console.print(table)


def cmd_chat(args):
    """Interactive chat with contracts."""
    console.print(
        Panel(
            "[bold]Contract Assistant[/bold]\n\n"
            "Ask any question about your contracts.\n"
            "The AI will search through all contracts and give you precise answers.\n\n"
            "[dim]Commands: 'quit' or 'exit' to leave | 'clear' to reset conversation[/dim]",
            border_style="green",
        )
    )

    contract_ids = None
    if args.ids:
        contract_ids = [int(x.strip()) for x in args.ids.split(",")]
        console.print(f"[dim]Focused on contract IDs: {contract_ids}[/dim]\n")
    else:
        console.print("[dim]Loaded all contracts.[/dim]\n")

    try:
        ask = chat_session(contract_ids)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    history = InMemoryHistory()

    while True:
        try:
            user_input = pt_prompt(
                "\n📋 You: ",
                history=history,
                multiline=False,
            ).strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye![/dim]")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            console.print("[dim]Goodbye![/dim]")
            break
        if user_input.lower() == "clear":
            ask = chat_session(contract_ids)
            console.print("[green]Conversation cleared.[/green]")
            continue

        try:
            with console.status("[bold green]Analyzing contracts...[/bold green]"):
                response = ask(user_input)
            console.print(Panel(Markdown(response), title="Assistant", border_style="green"))
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")


def main():
    init_db()

    parser = argparse.ArgumentParser(
        prog="contract-cli",
        description="Contract Manager CLI — Manage and query client & vendor contracts",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Add command
    add_parser = subparsers.add_parser("add", help="Add a new contract")
    add_parser.add_argument("file", help="Path to the contract file (.txt, .md, etc.)")
    add_parser.add_argument("--name", "-n", help="Contract name")
    add_parser.add_argument("--party", "-p", help="Party name")
    add_parser.add_argument("--type", "-t", choices=["client", "vendor"], help="Contract type")
    add_parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    add_parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    add_parser.add_argument("--value", "-v", help="Contract value")
    add_parser.add_argument("--notes", help="Additional notes")

    # List command
    list_parser = subparsers.add_parser("list", help="List all contracts")
    list_parser.add_argument("--type", "-t", choices=["client", "vendor"], help="Filter by type")

    # View command
    view_parser = subparsers.add_parser("view", help="View a contract")
    view_parser.add_argument("id", type=int, help="Contract ID")
    view_parser.add_argument("--full", "-f", action="store_true", help="Show full contract content")

    # Delete command
    del_parser = subparsers.add_parser("delete", help="Delete a contract")
    del_parser.add_argument("id", type=int, help="Contract ID")

    # Search command
    search_parser = subparsers.add_parser("search", help="Search contracts")
    search_parser.add_argument("query", nargs="+", help="Search query")

    # Chat command
    chat_parser = subparsers.add_parser("chat", help="Chat with AI about your contracts")
    chat_parser.add_argument("--ids", help="Comma-separated contract IDs to focus on (default: all)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        console.print("\n[bold cyan]Quick Start:[/bold cyan]")
        console.print("  1. Add a contract:  [green]python main.py add contract.txt -n 'Service Agreement' -p 'Acme Corp' -t client[/green]")
        console.print("  2. List contracts:  [green]python main.py list[/green]")
        console.print("  3. Chat with AI:    [green]python main.py chat[/green]")
        return

    commands = {
        "add": cmd_add,
        "list": cmd_list,
        "view": cmd_view,
        "delete": cmd_delete,
        "search": cmd_search,
        "chat": cmd_chat,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
