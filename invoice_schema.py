"""Schema + validators for extracted invoice data.

The shape we ask Claude to return is documented here in one place so the
prompt, the validators, and the renderers stay in sync. Validation is pure
Python -- we check arithmetic consistency and date ordering, and add warnings
to a list. We never mutate the model's numeric outputs (a hallucinated total
should be flagged, not silently corrected).
"""
from __future__ import annotations

import datetime as dt
import math
import re
from typing import Any, TypedDict


class LineItem(TypedDict, total=False):
    description: str
    quantity: float | None
    unit_price: float | None
    total: float | None


class Vendor(TypedDict, total=False):
    name: str
    address: str | None
    vat_number: str | None


class Invoice(TypedDict, total=False):
    vendor: Vendor
    invoice_number: str | None
    invoice_date: str | None       # ISO 8601 (YYYY-MM-DD)
    due_date: str | None           # ISO 8601 or None
    currency: str | None           # ISO 4217 (GBP, EUR, USD, ...)
    line_items: list[LineItem]
    subtotal: float | None
    tax_amount: float | None
    tax_rate: float | None
    total_amount: float | None
    notes: str | None
    confidence: str                # "high" | "medium" | "low"
    extraction_warnings: list[str]


# Common currency codes we expect to see; not exhaustive but covers the bundled samples.
KNOWN_CURRENCIES = {
    "GBP", "EUR", "USD", "JPY", "CHF", "CAD", "AUD", "NZD", "SEK", "NOK", "DKK",
    "HKD", "SGD", "INR", "CNY", "ZAR", "AED", "SAR", "BRL", "MXN", "PLN", "CZK",
    "HUF", "TRY", "ILS",
}


# JSON schema we describe to the model. Keep it short -- every byte costs.
SCHEMA_DESC = (
    "{\n"
    '  "vendor": {"name": string, "address": string|null, "vat_number": string|null},\n'
    '  "invoice_number": string|null,\n'
    '  "invoice_date": "YYYY-MM-DD"|null,\n'
    '  "due_date": "YYYY-MM-DD"|null,\n'
    '  "currency": ISO-4217-code|null,\n'
    '  "line_items": [{"description": string, "quantity": number|null, "unit_price": number|null, "total": number|null}],\n'
    '  "subtotal": number|null,\n'
    '  "tax_amount": number|null,\n'
    '  "tax_rate": number|null,\n'
    '  "total_amount": number|null,\n'
    '  "notes": string|null,\n'
    '  "confidence": "high"|"medium"|"low",\n'
    '  "extraction_warnings": [string, ...]\n'
    "}"
)


# ---- Helpers -----------------------------------------------------------

def _is_iso_date(s: Any) -> bool:
    if not isinstance(s, str):
        return False
    try:
        dt.date.fromisoformat(s[:10])
        return True
    except ValueError:
        return False


def _to_number(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        return float(v)
    if isinstance(v, str):
        s = re.sub(r"[^\d\-.]", "", v)
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


# ---- Normalisation -----------------------------------------------------

def normalise(raw: dict) -> Invoice:
    """Coerce raw model output into an Invoice TypedDict.

    Doesn't validate arithmetic -- that's `validate()`'s job. This step just
    cleans types: numbers as floats, missing fields as None, line_items always
    a list, etc.
    """
    vendor_raw = raw.get("vendor") or {}
    vendor: Vendor = {
        "name": str(vendor_raw.get("name") or "").strip() or "(unknown)",
        "address": (vendor_raw.get("address") or None) and str(vendor_raw["address"]).strip(),
        "vat_number": (vendor_raw.get("vat_number") or None) and str(vendor_raw["vat_number"]).strip(),
    }

    line_items_raw = raw.get("line_items") or []
    line_items: list[LineItem] = []
    for li in line_items_raw:
        if not isinstance(li, dict):
            continue
        line_items.append({
            "description": str(li.get("description") or "").strip(),
            "quantity": _to_number(li.get("quantity")),
            "unit_price": _to_number(li.get("unit_price")),
            "total": _to_number(li.get("total")),
        })

    currency = (raw.get("currency") or None)
    if currency:
        currency = str(currency).strip().upper()

    confidence = str(raw.get("confidence") or "medium").lower()
    if confidence not in ("high", "medium", "low"):
        confidence = "medium"

    inv: Invoice = {
        "vendor": vendor,
        "invoice_number": (raw.get("invoice_number") or None) and str(raw["invoice_number"]).strip(),
        "invoice_date": (raw.get("invoice_date") or None) and str(raw["invoice_date"]).strip()[:10],
        "due_date": (raw.get("due_date") or None) and str(raw["due_date"]).strip()[:10],
        "currency": currency,
        "line_items": line_items,
        "subtotal": _to_number(raw.get("subtotal")),
        "tax_amount": _to_number(raw.get("tax_amount")),
        "tax_rate": _to_number(raw.get("tax_rate")),
        "total_amount": _to_number(raw.get("total_amount")),
        "notes": (raw.get("notes") or None) and str(raw["notes"]).strip(),
        "confidence": confidence,
        "extraction_warnings": list(raw.get("extraction_warnings") or []),
    }
    return inv


# ---- Validation --------------------------------------------------------

def validate(inv: Invoice) -> tuple[Invoice, list[str]]:
    """Cross-check arithmetic and dates. Returns (invoice, warnings).

    Tolerance for sum checks is 1% of the total or £1, whichever is larger
    (real invoices have rounding noise on multi-rate VAT).
    """
    warnings: list[str] = list(inv.get("extraction_warnings") or [])

    # Arithmetic: sum of line totals should equal subtotal.
    li_totals = [li.get("total") for li in inv.get("line_items", []) if li.get("total") is not None]
    if li_totals and inv.get("subtotal") is not None:
        line_sum = round(sum(li_totals), 2)
        sub = inv["subtotal"]
        tol = max(abs(sub) * 0.01, 1.0)
        if abs(line_sum - sub) > tol:
            warnings.append(
                f"Line items sum to {line_sum:.2f} but subtotal is {sub:.2f} (delta {line_sum - sub:+.2f})."
            )

    # Subtotal + tax = total
    if inv.get("subtotal") is not None and inv.get("total_amount") is not None:
        sub = inv["subtotal"]
        tax = inv.get("tax_amount") or 0.0
        expected = round(sub + tax, 2)
        actual = inv["total_amount"]
        tol = max(abs(actual) * 0.01, 1.0)
        if abs(expected - actual) > tol:
            warnings.append(
                f"Subtotal + tax = {expected:.2f} but total_amount is {actual:.2f}."
            )

    # Negative totals
    if (inv.get("total_amount") or 0) < 0:
        warnings.append("Total amount is negative -- credit note? Verify manually.")

    # Date ordering
    inv_date = inv.get("invoice_date")
    due_date = inv.get("due_date")
    if inv_date and due_date and _is_iso_date(inv_date) and _is_iso_date(due_date):
        if inv_date > due_date:
            warnings.append(f"Invoice date {inv_date} is after due date {due_date}.")

    # Date format
    if inv_date and not _is_iso_date(inv_date):
        warnings.append(f"Invoice date '{inv_date}' is not ISO 8601 (YYYY-MM-DD).")
    if due_date and not _is_iso_date(due_date):
        warnings.append(f"Due date '{due_date}' is not ISO 8601 (YYYY-MM-DD).")

    # Currency
    cur = inv.get("currency")
    if cur and cur not in KNOWN_CURRENCIES:
        warnings.append(f"Currency '{cur}' is not in the recognised ISO 4217 list.")

    # Empty line items but non-zero total -- likely an extraction failure.
    if not inv.get("line_items") and (inv.get("total_amount") or 0) > 0:
        warnings.append("Total amount is set but no line items extracted -- likely incomplete extraction.")

    inv = {**inv, "extraction_warnings": warnings}

    # Downgrade confidence if any warnings fired.
    if warnings and inv.get("confidence") == "high":
        inv["confidence"] = "medium"
    if len(warnings) >= 3 and inv.get("confidence") == "medium":
        inv["confidence"] = "low"

    return inv, warnings
