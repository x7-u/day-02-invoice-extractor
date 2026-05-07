"""Day 2 -- invoice extraction pipeline.

Takes raw bytes (image or PDF), pages it via pdf_loader, calls Claude's
vision API with a structured-JSON prompt, normalises and validates the
output, returns an ExtractResult with cost stats.

Imported by both the CLI (main.py) and the Flask server (server.py).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from invoice_schema import SCHEMA_DESC, Invoice, normalise, validate
from pdf_loader import MAX_PAGES, pages_as_jpeg, truncated

from shared.config import CLAUDE_MODEL_FAST
from shared.llm_client import ask_claude_json_with_stats, image_block

SYSTEM_PROMPT = (
    "You are an invoice data extraction assistant. Look at the invoice image(s) "
    "and return a single JSON object that matches this exact schema (no prose, "
    "no markdown fences):\n\n"
    f"{SCHEMA_DESC}\n\n"
    "Numbers must be numeric (no currency symbols, no commas). Dates must be ISO 8601 "
    "(YYYY-MM-DD). If a field is unreadable, use null. If you are uncertain about "
    "any value, list a short explanation in extraction_warnings. The 'confidence' "
    "field reflects your overall confidence in the extracted data."
)


@dataclass
class ExtractResult:
    invoice: Invoice
    n_pages: int
    truncated: bool                      # PDF had more than MAX_PAGES
    cost_usd: float
    input_tokens: int
    output_tokens: int
    model: str
    skipped: bool = False                # True when --no-ai used
    error: str | None = None             # set when extraction itself failed
    source_filename: str = ""
    # Populated by the server after the ledger check. Stays None for stub /
    # errored / skipped runs so they don't pollute the exports.
    duplicate_status: str | None = None             # "unique" | "exact" | "suspicious" | "fuzzy"
    duplicate_match_filename: str | None = None     # filename of the prior matching extraction


# ---- Stub for offline / --no-ai runs ----------------------------------

def stub_result(filename: str, reason: str = "AI extraction skipped") -> ExtractResult:
    inv: Invoice = {
        "vendor": {"name": "(stub - AI skipped)", "address": None, "vat_number": None},
        "invoice_number": None,
        "invoice_date": None,
        "due_date": None,
        "currency": None,
        "line_items": [],
        "subtotal": None,
        "tax_amount": None,
        "tax_rate": None,
        "total_amount": None,
        "notes": reason,
        "confidence": "low",
        "extraction_warnings": [reason],
    }
    return ExtractResult(
        invoice=inv, n_pages=0, truncated=False,
        cost_usd=0.0, input_tokens=0, output_tokens=0,
        model="", skipped=True, source_filename=filename,
    )


# ---- Main extraction --------------------------------------------------

def extract_invoice(
    file_data: bytes,
    filename: str,
    *,
    model: str | None = None,
    api_key: str | None = None,
    skip_ai: bool = False,
) -> ExtractResult:
    """Extract a single invoice from a file's raw bytes.

    Multi-page PDFs are paged and sent as multiple image blocks in the same
    request -- the model sees them as a single document.
    """
    if skip_ai:
        return stub_result(filename, "Skipped (UI option)")

    try:
        pages = pages_as_jpeg(file_data, filename_hint=filename)
    except ValueError as e:
        return ExtractResult(
            invoice=stub_result(filename).invoice, n_pages=0, truncated=False,
            cost_usd=0.0, input_tokens=0, output_tokens=0,
            model="", skipped=True, error=str(e), source_filename=filename,
        )

    was_truncated = truncated(file_data, filename_hint=filename)

    # Build the multimodal user content: image block(s) followed by a tiny instruction.
    user_content: list[dict] = [image_block(p) for p in pages]
    user_content.append({"type": "text", "text": "Extract the invoice data per the schema."})

    # Cache the system prompt so repeated runs in a session amortise input cost.
    system_blocks = [{
        "type": "text",
        "text": SYSTEM_PROMPT,
        "cache_control": {"type": "ephemeral"},
    }]

    try:
        raw, stats = ask_claude_json_with_stats(
            user_content,
            system=system_blocks,
            max_tokens=1500,
            model=(model or CLAUDE_MODEL_FAST),
            api_key=api_key,
        )
    except Exception as e:
        # Friendly failure -- keep going, surface the error to the UI.
        result = stub_result(filename, f"AI call failed: {type(e).__name__}")
        result.error = str(e)[:300]
        result.source_filename = filename
        return result

    inv = normalise(raw)
    if was_truncated:
        inv.setdefault("extraction_warnings", [])
        inv["extraction_warnings"].insert(
            0,
            f"PDF has more than {MAX_PAGES} pages -- only the first {MAX_PAGES} were processed.",
        )
    inv, _ = validate(inv)

    return ExtractResult(
        invoice=inv,
        n_pages=len(pages),
        truncated=was_truncated,
        cost_usd=stats.cost_usd,
        input_tokens=stats.input_tokens,
        output_tokens=stats.output_tokens,
        model=stats.model,
        source_filename=filename,
    )


# ---- Helpers used by server / CLI -------------------------------------

def to_dict(result: ExtractResult) -> dict[str, Any]:
    """Flatten ExtractResult into a JSON-safe dict for the API and CLI output."""
    return {
        "filename": result.source_filename,
        "invoice": result.invoice,
        "n_pages": result.n_pages,
        "truncated": result.truncated,
        "cost_usd": round(result.cost_usd, 6),
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "model": result.model,
        "skipped": result.skipped,
        "error": result.error,
        "duplicate_status": result.duplicate_status,
        "duplicate_match_filename": result.duplicate_match_filename,
    }
