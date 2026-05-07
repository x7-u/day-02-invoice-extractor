"""Schema normalisation + arithmetic validation."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from invoice_schema import KNOWN_CURRENCIES, _to_number, normalise, validate

# -- _to_number ---------------------------------------------------------

def test_to_number_handles_strings():
    assert _to_number("1,234.56") == 1234.56
    assert _to_number("£1,234") == 1234.0
    assert _to_number("$ 12.50") == 12.50
    assert _to_number("") is None
    assert _to_number(None) is None
    assert _to_number(float("nan")) is None


def test_to_number_passes_through_numbers():
    assert _to_number(42) == 42.0
    assert _to_number(3.14) == 3.14


# -- normalise ----------------------------------------------------------

def test_normalise_minimal_input():
    raw = {"vendor": {"name": "Acme"}, "total_amount": "100.00"}
    inv = normalise(raw)
    assert inv["vendor"]["name"] == "Acme"
    assert inv["total_amount"] == 100.0
    assert inv["line_items"] == []
    assert inv["confidence"] == "medium"  # default


def test_normalise_drops_invalid_confidence():
    raw = {"vendor": {"name": "X"}, "confidence": "extremely high"}
    inv = normalise(raw)
    assert inv["confidence"] == "medium"


def test_normalise_handles_none_vendor():
    raw = {"total_amount": 100}
    inv = normalise(raw)
    assert inv["vendor"]["name"] == "(unknown)"


def test_normalise_currency_uppercased():
    raw = {"vendor": {"name": "X"}, "currency": "gbp"}
    assert normalise(raw)["currency"] == "GBP"


def test_normalise_truncates_long_dates():
    raw = {"vendor": {"name": "X"}, "invoice_date": "2026-04-12T00:00:00Z"}
    assert normalise(raw)["invoice_date"] == "2026-04-12"


# -- validate -----------------------------------------------------------

def _good_invoice():
    return normalise({
        "vendor": {"name": "Test Co"},
        "invoice_number": "INV-1",
        "invoice_date": "2026-04-01",
        "due_date": "2026-05-01",
        "currency": "GBP",
        "line_items": [
            {"description": "Widget", "quantity": 2, "unit_price": 50.0, "total": 100.0},
            {"description": "Gizmo",  "quantity": 1, "unit_price": 50.0, "total": 50.0},
        ],
        "subtotal": 150.0,
        "tax_amount": 30.0,
        "tax_rate": 0.20,
        "total_amount": 180.0,
        "confidence": "high",
    })


def test_validate_clean_invoice_no_warnings():
    inv = _good_invoice()
    inv2, warnings = validate(inv)
    assert warnings == []
    assert inv2["confidence"] == "high"


def test_validate_flags_line_sum_mismatch():
    inv = _good_invoice()
    inv["subtotal"] = 200.0  # but line items sum to 150
    _, warnings = validate(inv)
    assert any("Line items sum" in w for w in warnings)


def test_validate_flags_total_mismatch():
    inv = _good_invoice()
    inv["total_amount"] = 999.0
    _, warnings = validate(inv)
    assert any("Subtotal + tax" in w for w in warnings)


def test_validate_flags_negative_total():
    inv = _good_invoice()
    inv["total_amount"] = -180.0
    _, warnings = validate(inv)
    assert any("negative" in w.lower() for w in warnings)


def test_validate_flags_date_order():
    inv = _good_invoice()
    inv["invoice_date"] = "2026-06-01"  # after due
    _, warnings = validate(inv)
    assert any("after due date" in w for w in warnings)


def test_validate_flags_unknown_currency():
    inv = _good_invoice()
    inv["currency"] = "ZZZ"
    _, warnings = validate(inv)
    assert any("not in the recognised" in w for w in warnings)
    # Sanity: real currencies pass.
    inv["currency"] = "GBP"
    _, warnings = validate(inv)
    assert not any("not in the recognised" in w for w in warnings)


def test_validate_downgrades_confidence_on_warnings():
    inv = _good_invoice()
    inv["subtotal"] = 999.0
    inv["total_amount"] = -50.0  # 2 problems
    inv["invoice_date"] = "2026-06-01"  # 3rd problem
    inv2, warnings = validate(inv)
    assert len(warnings) >= 3
    assert inv2["confidence"] == "low"


# -- Currency table -----------------------------------------------------

def test_known_currencies_includes_majors():
    for c in ("GBP", "EUR", "USD"):
        assert c in KNOWN_CURRENCIES
