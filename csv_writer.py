"""Flattened CSV output -- one row per line item with invoice metadata duplicated.

Column order is tuned for an accountant: vendor first, then invoice number /
date / currency, then per-line description / qty / price / total, then the
invoice-level totals and confidence band. This is the format you can paste
straight into a bookkeeping ledger or a power-query workflow.
"""
from __future__ import annotations

import csv
from pathlib import Path

from invoice_schema import Invoice

COLUMNS = [
    "vendor_name", "vendor_vat", "vendor_address",
    "invoice_number", "invoice_date", "due_date", "currency",
    "line_description", "line_quantity", "line_unit_price", "line_total",
    "subtotal", "tax_amount", "tax_rate", "total_amount",
    "confidence", "duplicate_status", "duplicate_match_filename",
    "extraction_warnings", "source_filename",
]


def _row(
    inv: Invoice,
    source_filename: str,
    line: dict | None,
    duplicate_status: str | None = None,
    duplicate_match_filename: str | None = None,
) -> dict:
    vendor = inv.get("vendor") or {}
    warnings = "; ".join(inv.get("extraction_warnings") or [])
    return {
        "vendor_name":    vendor.get("name", ""),
        "vendor_vat":     vendor.get("vat_number") or "",
        "vendor_address": vendor.get("address") or "",
        "invoice_number": inv.get("invoice_number") or "",
        "invoice_date":   inv.get("invoice_date") or "",
        "due_date":       inv.get("due_date") or "",
        "currency":       inv.get("currency") or "",
        "line_description": (line or {}).get("description") or "",
        "line_quantity":    (line or {}).get("quantity"),
        "line_unit_price":  (line or {}).get("unit_price"),
        "line_total":       (line or {}).get("total"),
        "subtotal":         inv.get("subtotal"),
        "tax_amount":       inv.get("tax_amount"),
        "tax_rate":         inv.get("tax_rate"),
        "total_amount":     inv.get("total_amount"),
        "confidence":       inv.get("confidence") or "",
        "duplicate_status":          duplicate_status or "",
        "duplicate_match_filename":  duplicate_match_filename or "",
        "extraction_warnings": warnings,
        "source_filename":  source_filename,
    }


def write_csv(extract_results: list, out_path: Path | str) -> Path:
    """Write a flattened CSV: one row per line item across all invoices.

    `extract_results` is a list of ExtractResult-like objects (from pipeline.py).
    Invoices with no line items still get one row (with the line_* columns blank)
    so the user can see they were processed.
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    with out.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for r in extract_results:
            inv = r.invoice
            source = r.source_filename
            dup_status = getattr(r, "duplicate_status", None)
            dup_match = getattr(r, "duplicate_match_filename", None)
            line_items = inv.get("line_items") or []
            if not line_items:
                writer.writerow(_row(inv, source, None, dup_status, dup_match))
            else:
                for li in line_items:
                    writer.writerow(_row(inv, source, li, dup_status, dup_match))
    return out
