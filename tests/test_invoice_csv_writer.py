"""CSV writer round-trip tests."""
from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from csv_writer import COLUMNS, write_csv


# Lightweight stand-in for ExtractResult so we don't import the pipeline.
@dataclass
class _R:
    invoice: dict
    source_filename: str
    cost_usd: float = 0.0


def _fake_invoice(n_lines: int = 2) -> dict:
    return {
        "vendor": {"name": "Test Co", "vat_number": "GB123", "address": "1 Test St"},
        "invoice_number": "INV-1",
        "invoice_date": "2026-04-01",
        "due_date": "2026-05-01",
        "currency": "GBP",
        "line_items": [
            {"description": f"Item {i+1}", "quantity": i + 1, "unit_price": 10.0, "total": (i + 1) * 10.0}
            for i in range(n_lines)
        ],
        "subtotal": sum((i + 1) * 10.0 for i in range(n_lines)),
        "tax_amount": 6.0,
        "tax_rate": 0.20,
        "total_amount": sum((i + 1) * 10.0 for i in range(n_lines)) + 6.0,
        "confidence": "high",
        "extraction_warnings": [],
    }


def test_write_csv_one_row_per_line_item(tmp_path):
    results = [_R(invoice=_fake_invoice(3), source_filename="a.pdf")]
    out = write_csv(results, tmp_path / "out.csv")
    assert out.exists()
    with out.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 3
    for row in rows:
        assert row["vendor_name"] == "Test Co"
        assert row["invoice_number"] == "INV-1"
        assert row["currency"] == "GBP"


def test_write_csv_emits_one_row_for_invoice_with_no_lines(tmp_path):
    inv = _fake_invoice()
    inv["line_items"] = []
    out = write_csv([_R(invoice=inv, source_filename="empty.pdf")], tmp_path / "empty.csv")
    with out.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["line_description"] == ""
    assert rows[0]["vendor_name"] == "Test Co"


def test_write_csv_columns_match_spec(tmp_path):
    out = write_csv([_R(invoice=_fake_invoice(1), source_filename="c.pdf")], tmp_path / "c.csv")
    with out.open(encoding="utf-8-sig") as f:
        header = next(csv.reader(f))
    assert header == COLUMNS


def test_write_csv_aggregates_multiple_invoices(tmp_path):
    results = [
        _R(invoice=_fake_invoice(2), source_filename="a.pdf"),
        _R(invoice=_fake_invoice(3), source_filename="b.pdf"),
    ]
    out = write_csv(results, tmp_path / "multi.csv")
    with out.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 5
    assert {r["source_filename"] for r in rows} == {"a.pdf", "b.pdf"}


def test_write_csv_serialises_warnings_as_semicolon_list(tmp_path):
    inv = _fake_invoice(1)
    inv["extraction_warnings"] = ["A", "B", "C"]
    out = write_csv([_R(invoice=inv, source_filename="w.pdf")], tmp_path / "w.csv")
    with out.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["extraction_warnings"] == "A; B; C"
