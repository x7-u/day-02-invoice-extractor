"""Tests for Day 2's invoice ledger and duplicate detection."""
from __future__ import annotations

import json

import pytest
from ledger import (
    Ledger,
    fuzzy_fingerprint,
    strict_fingerprint,
    vendor_invnum_key,
)

# ---- Fixtures ---------------------------------------------------------

def _invoice(**overrides):
    base = {
        "vendor": {"name": "Northwind Office Supplies", "address": None, "vat_number": None},
        "invoice_number": "INV-2025-001",
        "invoice_date": "2025-09-15",
        "due_date": None,
        "currency": "GBP",
        "line_items": [{"description": "Pens", "quantity": 10, "unit_price": 1.0, "total": 10.0}],
        "subtotal": 10.00,
        "tax_amount": 2.00,
        "tax_rate": 0.20,
        "total_amount": 12.00,
        "notes": None,
        "confidence": "high",
        "extraction_warnings": [],
    }
    base.update(overrides)
    return base


@pytest.fixture
def tmp_ledger(tmp_path):
    return Ledger(tmp_path / "ledger.jsonl")


# ---- Fingerprinting ---------------------------------------------------

class TestFingerprints:
    def test_strict_fingerprint_stable_across_formatting(self):
        """Whitespace, case and punctuation in vendor/invoice number should
        not affect the fingerprint -- the same invoice re-typed slightly
        differently must still collide."""
        a = _invoice(
            vendor={"name": "  Northwind  Office Supplies, Ltd.  "},
            invoice_number="inv 2025/001",
        )
        b = _invoice(
            vendor={"name": "northwind office supplies ltd"},
            invoice_number="INV-2025-001",
        )
        assert strict_fingerprint(a) == strict_fingerprint(b)

    def test_strict_fingerprint_changes_on_total(self):
        a = _invoice(total_amount=100.00)
        b = _invoice(total_amount=100.01)
        assert strict_fingerprint(a) != strict_fingerprint(b)

    def test_strict_fingerprint_none_when_invoice_number_missing(self):
        assert strict_fingerprint(_invoice(invoice_number=None)) is None

    def test_strict_fingerprint_none_when_total_missing(self):
        assert strict_fingerprint(_invoice(total_amount=None)) is None

    def test_fuzzy_fingerprint_does_not_need_invoice_number(self):
        inv = _invoice(invoice_number=None)
        assert fuzzy_fingerprint(inv) is not None

    def test_vendor_invnum_key_requires_both(self):
        assert vendor_invnum_key(_invoice()) is not None
        assert vendor_invnum_key(_invoice(invoice_number=None)) is None
        assert vendor_invnum_key(_invoice(vendor={"name": ""})) is None


# ---- Ledger persistence ----------------------------------------------

class TestLedgerPersistence:
    def test_add_and_persist_creates_file(self, tmp_ledger):
        entry = tmp_ledger.add(_invoice(), filename="a.pdf")
        assert entry is not None
        assert tmp_ledger.path.exists()
        # JSONL: one line per entry
        lines = tmp_ledger.path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        loaded = json.loads(lines[0])
        assert loaded["vendor"] == "Northwind Office Supplies"
        assert loaded["total_amount"] == 12.00

    def test_reload_from_disk(self, tmp_path):
        path = tmp_path / "ledger.jsonl"
        l1 = Ledger(path)
        l1.add(_invoice(invoice_number="A1"), filename="a.pdf")
        l1.add(_invoice(invoice_number="A2"), filename="b.pdf")

        l2 = Ledger(path)
        assert len(l2) == 2
        entries = l2.entries()
        assert {e["invoice_number"] for e in entries} == {"A1", "A2"}

    def test_corrupt_lines_are_skipped(self, tmp_path):
        path = tmp_path / "ledger.jsonl"
        path.write_text(
            json.dumps({"id": "ok1", "vendor": "X", "total_amount": 1.0}) + "\n"
            "this line is not json\n"
            + json.dumps({"id": "ok2", "vendor": "Y", "total_amount": 2.0}) + "\n",
            encoding="utf-8",
        )
        loaded = Ledger(path)
        assert len(loaded) == 2

    def test_clear_empties_file(self, tmp_ledger):
        tmp_ledger.add(_invoice(), filename="a.pdf")
        n = tmp_ledger.clear()
        assert n == 1
        assert len(tmp_ledger) == 0
        # file should still exist but be empty
        assert tmp_ledger.path.read_text(encoding="utf-8") == ""

    def test_remove_by_id(self, tmp_ledger):
        e1 = tmp_ledger.add(_invoice(invoice_number="A1"), filename="a.pdf")
        tmp_ledger.add(_invoice(invoice_number="A2"), filename="b.pdf")
        assert tmp_ledger.remove(e1["id"]) is True
        assert tmp_ledger.remove("nonexistent") is False
        remaining = [e["invoice_number"] for e in tmp_ledger.entries()]
        assert remaining == ["A2"]


# ---- Duplicate detection ----------------------------------------------

class TestDuplicateDetection:
    def test_unique_invoice_returns_none(self, tmp_ledger):
        tmp_ledger.add(_invoice(invoice_number="A1"), filename="a.pdf")
        match = tmp_ledger.check(_invoice(invoice_number="DIFFERENT-INV"))
        assert match is None

    def test_exact_duplicate(self, tmp_ledger):
        tmp_ledger.add(_invoice(), filename="a.pdf")
        match = tmp_ledger.check(_invoice())
        assert match is not None
        assert match.status == "exact"
        assert match.matched_filename == "a.pdf"

    def test_exact_duplicate_robust_to_formatting(self, tmp_ledger):
        tmp_ledger.add(
            _invoice(vendor={"name": "Northwind Office Supplies"}, invoice_number="INV-2025-001"),
            filename="a.pdf",
        )
        match = tmp_ledger.check(_invoice(
            vendor={"name": "northwind office supplies"},
            invoice_number="inv 2025/001",
        ))
        assert match is not None
        assert match.status == "exact"

    def test_same_invoice_number_different_total_is_suspicious(self, tmp_ledger):
        tmp_ledger.add(_invoice(total_amount=100.00), filename="a.pdf")
        match = tmp_ledger.check(_invoice(total_amount=120.00))
        assert match is not None
        assert match.status == "suspicious"
        assert "total" in match.explanation.lower()

    def test_same_invoice_number_different_date_is_suspicious(self, tmp_ledger):
        tmp_ledger.add(_invoice(invoice_date="2025-09-15"), filename="a.pdf")
        match = tmp_ledger.check(_invoice(invoice_date="2025-10-01"))
        assert match is not None
        assert match.status == "suspicious"

    def test_fuzzy_match_when_neither_has_invoice_number(self, tmp_ledger):
        tmp_ledger.add(_invoice(invoice_number=None), filename="a.pdf")
        match = tmp_ledger.check(_invoice(invoice_number=None))
        assert match is not None
        assert match.status == "fuzzy"

    def test_fuzzy_does_not_fire_when_existing_has_invoice_number(self, tmp_ledger):
        # If the previous entry had an invoice number, a new one without one
        # is not necessarily a duplicate -- could be a separate invoice that
        # the model just failed to read the number off. Don't false-positive.
        tmp_ledger.add(_invoice(invoice_number="A1"), filename="a.pdf")
        match = tmp_ledger.check(_invoice(invoice_number=None))
        assert match is None

    def test_different_currency_is_not_a_duplicate(self, tmp_ledger):
        tmp_ledger.add(_invoice(currency="GBP"), filename="a.pdf")
        match = tmp_ledger.check(_invoice(currency="EUR"))
        # vendor + invoice number match, but vendor_invnum check only fires
        # when totals/dates differ -- same total/date, different currency
        # currently slips through as unique. That's the documented behaviour:
        # currency lives in the strict fingerprint only.
        assert match is None or match.status != "exact"

    def test_check_returns_most_recent_match(self, tmp_ledger):
        tmp_ledger.add(_invoice(invoice_number="A1"), filename="first.pdf")
        tmp_ledger.add(_invoice(invoice_number="B2"), filename="middle.pdf")
        tmp_ledger.add(_invoice(invoice_number="A1"), filename="dup.pdf")
        # Now check the original -- should match the most recent (dup.pdf)
        match = tmp_ledger.check(_invoice(invoice_number="A1"))
        assert match is not None
        assert match.matched_filename == "dup.pdf"


# ---- Add gating -------------------------------------------------------

class TestAddGating:
    def test_stub_invoices_not_added(self, tmp_ledger):
        stub = _invoice(
            vendor={"name": "(stub - AI skipped)"},
            invoice_number=None,
            total_amount=None,
        )
        result = tmp_ledger.add(stub, filename="stub.pdf")
        assert result is None
        assert len(tmp_ledger) == 0

    def test_thin_invoice_not_added(self, tmp_ledger):
        # No total and no invoice number -- nothing to fingerprint usefully.
        thin = _invoice(invoice_number=None, total_amount=None)
        result = tmp_ledger.add(thin, filename="thin.pdf")
        assert result is None

    def test_invoice_with_only_total_is_added(self, tmp_ledger):
        # Total present, invoice_number missing -- still useful for fuzzy.
        ok = _invoice(invoice_number=None)
        result = tmp_ledger.add(ok, filename="ok.pdf")
        assert result is not None
        assert len(tmp_ledger) == 1
