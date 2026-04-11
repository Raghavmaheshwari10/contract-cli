#!/usr/bin/env python3
"""
EMB CLM — Bulk PDF Contract Uploader
=====================================
Reads all PDFs from a folder, extracts text, optionally uses AI to
extract metadata, and uploads them to your CLM.

Usage:
  python3 bulk_upload.py /path/to/pdf/folder

The script will:
  1. Scan for all .pdf files in the folder (including subfolders)
  2. Extract text from each PDF using PyMuPDF
  3. Use AI (GPT-4o-mini) to auto-detect: contract name, party, type, dates, value
  4. Upload each contract to your CLM API
  5. Show progress and summary

Requirements:
  - pip3 install pymupdf  (already installed)
  - Your CLM must be deployed and accessible
"""

import os
import sys
import json
import time
import fitz  # PyMuPDF
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

# ═══ CONFIGURATION ═══
CLM_URL = "https://contract-cli-six.vercel.app"
CLM_PASSWORD = "emb@2024"

# ═══ COLORS ═══
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

def log(msg, color=RESET):
    print(f"{color}{msg}{RESET}")

def log_progress(current, total, name, status=""):
    bar_len = 30
    filled = int(bar_len * current / max(total, 1))
    bar = "█" * filled + "░" * (bar_len - filled)
    pct = int(100 * current / max(total, 1))
    short_name = name[:40] + "..." if len(name) > 40 else name
    print(f"\r  {bar} {pct:3d}% ({current}/{total}) {short_name:<44s} {status}", end="", flush=True)

# ═══ API HELPERS ═══
def api_request(path, method="GET", data=None, token=None):
    url = f"{CLM_URL}{path}"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode("utf-8"))
            return {"error": err.get("error", str(e))}
        except:
            return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}

def login():
    log(f"\n{BOLD}Connecting to CLM...{RESET}")
    result = api_request("/api/auth/login", method="POST", data={"password": CLM_PASSWORD})
    if "token" in result:
        log(f"  {GREEN}✓ Authenticated successfully{RESET}")
        return result["token"]
    else:
        log(f"  {RED}✗ Login failed: {result.get('error', 'Unknown')}{RESET}")
        log(f"  {DIM}Edit CLM_PASSWORD in this script if you changed it{RESET}")
        sys.exit(1)

# ═══ PDF PROCESSING ═══
def extract_pdf_text(pdf_path):
    """Extract text from a PDF file"""
    try:
        doc = fitz.open(pdf_path)
        pages = len(doc)
        text = ""
        for page in doc:
            text += page.get_text() + "\n"
        doc.close()
        return text.strip(), pages
    except Exception as e:
        return None, 0

def guess_contract_type(text, filename):
    """Simple heuristic to guess contract type from content"""
    text_lower = text.lower() if text else ""
    filename_lower = filename.lower()

    # Check for vendor keywords
    vendor_kw = ["vendor agreement", "supplier", "purchase order", "procurement",
                 "subcontract", "outsourcing", "vendor"]
    for kw in vendor_kw:
        if kw in text_lower or kw in filename_lower:
            return "vendor"
    return "client"

def extract_metadata_from_filename(filename):
    """Extract what we can from the filename"""
    name = Path(filename).stem
    # Clean up common patterns
    name = name.replace("_", " ").replace("-", " - ")
    # Remove common suffixes
    for suffix in [" signed", " executed", " final", " v1", " v2", " copy", " (1)"]:
        name = name.replace(suffix, "").replace(suffix.upper(), "")
    return name.strip()

# ═══ AI METADATA EXTRACTION ═══
def ai_extract_metadata(text, filename, token):
    """Use the CLM's AI parse endpoint to extract metadata"""
    if not text or len(text.strip()) < 50:
        return None

    try:
        # Use first 3000 chars for metadata extraction
        result = api_request("/api/parse", method="POST",
                           data={"content": text[:3000]}, token=token)
        if "error" not in result:
            return result
    except:
        pass
    return None

# ═══ MAIN ═══
def main():
    if len(sys.argv) < 2:
        log(f"\n{BOLD}EMB CLM — Bulk PDF Contract Uploader{RESET}")
        log(f"\n{YELLOW}Usage:{RESET}")
        log(f"  python3 bulk_upload.py /path/to/pdf/folder")
        log(f"\n{DIM}Options:{RESET}")
        log(f"  --no-ai       Skip AI metadata extraction (faster)")
        log(f"  --dry-run     Show what would be uploaded without uploading")
        log(f"  --status X    Set status for all imports (default: executed)")
        log(f"                Options: draft, pending, in_review, executed, rejected")
        log(f"\n{DIM}Examples:{RESET}")
        log(f"  python3 bulk_upload.py ~/Documents/contracts/")
        log(f"  python3 bulk_upload.py ~/contracts/ --no-ai")
        log(f"  python3 bulk_upload.py ~/contracts/ --status draft --dry-run")
        sys.exit(0)

    folder = sys.argv[1]
    use_ai = "--no-ai" not in sys.argv
    dry_run = "--dry-run" in sys.argv
    default_status = "executed"
    for i, arg in enumerate(sys.argv):
        if arg == "--status" and i + 1 < len(sys.argv):
            default_status = sys.argv[i + 1]

    if not os.path.isdir(folder):
        log(f"{RED}Error: '{folder}' is not a directory{RESET}")
        sys.exit(1)

    # Find all PDFs
    log(f"\n{BOLD}{'='*60}{RESET}")
    log(f"{BOLD}  EMB CLM — Bulk PDF Contract Uploader{RESET}")
    log(f"{BOLD}{'='*60}{RESET}")
    log(f"\n{CYAN}Scanning for PDFs in: {folder}{RESET}")

    pdfs = []
    for root, dirs, files in os.walk(folder):
        for f in sorted(files):
            if f.lower().endswith(".pdf") and not f.startswith("."):
                pdfs.append(os.path.join(root, f))

    if not pdfs:
        log(f"{RED}No PDF files found in {folder}{RESET}")
        sys.exit(1)

    log(f"  {GREEN}Found {len(pdfs)} PDF files{RESET}")
    log(f"  AI metadata: {'ON' if use_ai else 'OFF'}")
    log(f"  Default status: {default_status}")
    if dry_run:
        log(f"  {YELLOW}DRY RUN — no uploads will happen{RESET}")

    # Login
    token = None
    if not dry_run:
        token = login()

    # Process PDFs
    log(f"\n{BOLD}Processing PDFs...{RESET}\n")

    imported = 0
    skipped = 0
    failed = 0
    errors = []
    start_time = time.time()

    for i, pdf_path in enumerate(pdfs):
        filename = os.path.basename(pdf_path)
        log_progress(i + 1, len(pdfs), filename, "extracting...")

        # Extract text
        text, pages = extract_pdf_text(pdf_path)
        if not text or len(text.strip()) < 20:
            errors.append(f"  {RED}✗{RESET} {filename}: No text extracted (scanned/image PDF?)")
            skipped += 1
            continue

        # Build contract data
        contract = {
            "name": extract_metadata_from_filename(filename),
            "party_name": "To be updated",
            "contract_type": guess_contract_type(text, filename),
            "content": text[:500000],
            "status": default_status,
            "notes": f"Imported from: {filename} ({pages} pages)",
            "created_by": "Bulk Import",
        }

        # AI extraction
        if use_ai and token:
            log_progress(i + 1, len(pdfs), filename, "AI analyzing...")
            meta = ai_extract_metadata(text, filename, token)
            if meta:
                if meta.get("name"): contract["name"] = meta["name"]
                if meta.get("party_name"): contract["party_name"] = meta["party_name"]
                if meta.get("contract_type") in ("client", "vendor"): contract["contract_type"] = meta["contract_type"]
                if meta.get("start_date"): contract["start_date"] = meta["start_date"]
                if meta.get("end_date"): contract["end_date"] = meta["end_date"]
                if meta.get("value"): contract["value"] = meta["value"]
                if meta.get("department"): contract["department"] = meta["department"]
                if meta.get("jurisdiction"): contract["jurisdiction"] = meta["jurisdiction"]
            # Rate limit — avoid hammering API
            time.sleep(0.3)

        if dry_run:
            log_progress(i + 1, len(pdfs), filename, f"→ {contract['name'][:30]}")
            imported += 1
            continue

        # Upload
        log_progress(i + 1, len(pdfs), filename, "uploading...")
        result = api_request("/api/contracts", method="POST", data=contract, token=token)

        if "id" in result:
            imported += 1
        elif "error" in result:
            errors.append(f"  {RED}✗{RESET} {filename}: {result['error']}")
            failed += 1
        else:
            errors.append(f"  {RED}✗{RESET} {filename}: Unknown error")
            failed += 1

        # Small delay to avoid rate limits
        if (i + 1) % 10 == 0:
            time.sleep(0.5)

    # Summary
    elapsed = time.time() - start_time
    print()  # New line after progress bar
    log(f"\n{BOLD}{'='*60}{RESET}")
    log(f"{BOLD}  Upload Summary{RESET}")
    log(f"{BOLD}{'='*60}{RESET}")
    log(f"  {GREEN}✓ Imported:  {imported}{RESET}")
    if skipped:
        log(f"  {YELLOW}⊘ Skipped:   {skipped} (no text extractable){RESET}")
    if failed:
        log(f"  {RED}✗ Failed:    {failed}{RESET}")
    log(f"  {DIM}⏱ Time:      {elapsed:.1f}s ({elapsed/max(len(pdfs),1):.1f}s per PDF){RESET}")
    log(f"  {DIM}📁 Total:     {len(pdfs)} PDFs{RESET}")

    if errors:
        log(f"\n{YELLOW}Errors:{RESET}")
        for e in errors[:30]:
            log(e)
        if len(errors) > 30:
            log(f"  {DIM}... and {len(errors)-30} more{RESET}")

    if dry_run:
        log(f"\n{YELLOW}This was a dry run. Run without --dry-run to actually upload.{RESET}")
    else:
        log(f"\n{GREEN}Done! Open {CLM_URL} to see your contracts.{RESET}")

if __name__ == "__main__":
    main()
