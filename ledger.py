"""Day 2 -- Invoice ledger and duplicate detection.

A persistent JSONL store of every successfully extracted invoice. Each new
extraction is fingerprinted and checked against the ledger before being
appended. Three match flavours:

* ``exact`` -- same vendor + invoice number + date + currency + total.
  Near-certain duplicate (someone re-uploaded the same file).
* ``suspicious`` -- same vendor + invoice number, but the total or date
  disagrees. Could be a re-issue or an extraction error worth checking.
* ``fuzzy`` -- invoice number is missing on both sides, but vendor + date +
  currency + total all match. Possible duplicate; flag for human review.

Anything else is ``unique``. Stub / failed extractions are not added.

The store lives at ``outputs/ledger.jsonl`` so it co-locates with the CSV/
Excel exports that ``outputs/`` already gets gitignored for.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import threading
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

DEFAULT_LEDGER_PATH = Path(__file__).resolve().parent / "outputs" / "ledger.jsonl"
MAX_ENTRIES_RETURNED = 200   # cap on /api/ledger payload to keep it small


# ---- Normalisation ----------------------------------------------------

_VENDOR_PUNCT_RE = re.compile(r"[^a-z0-9 ]+")
_INV_NUM_PUNCT_RE = re.compile(r"[\s\-/_.]+")


def _norm_vendor(name: str | None) -> str:
    if not name:
        return ""
    s = name.lower().strip()
    s = _VENDOR_PUNCT_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _norm_invoice_number(num: str | None) -> str:
    if not num:
        return ""
    return _INV_NUM_PUNCT_RE.sub("", num.lower().strip())


def _round_money(v: float | None) -> float | None:
    if v is None:
        return None
    return round(float(v), 2)


# ---- Fingerprints ------------------------------------------------------

def strict_fingerprint(invoice: dict) -> str | None:
    """SHA-256 of (vendor, invoice_number, date, currency, total).

    Returns None when any of those fields are missing -- strict matching is
    only meaningful when we have all five.
    """
    vendor = _norm_vendor((invoice.get("vendor") or {}).get("name"))
    inv_num = _norm_invoice_number(invoice.get("invoice_number"))
    inv_date = invoice.get("invoice_date") or ""
    currency = (invoice.get("currency") or "").upper()
    total = _round_money(invoice.get("total_amount"))

    if not (vendor and inv_num and inv_date and currency and total is not None):
        return None

    payload = f"{vendor}|{inv_num}|{inv_date}|{currency}|{total:.2f}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fuzzy_fingerprint(invoice: dict) -> str | None:
    """SHA-256 of (vendor, date, currency, total). Used when invoice_number
    is missing on both candidates so strict matching can't fire."""
    vendor = _norm_vendor((invoice.get("vendor") or {}).get("name"))
    inv_date = invoice.get("invoice_date") or ""
    currency = (invoice.get("currency") or "").upper()
    total = _round_money(invoice.get("total_amount"))

    if not (vendor and inv_date and currency and total is not None):
        return None

    payload = f"{vendor}|{inv_date}|{currency}|{total:.2f}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def vendor_invnum_key(invoice: dict) -> str | None:
    """vendor + invoice_number -- used to spot 'same invoice, different total'."""
    vendor = _norm_vendor((invoice.get("vendor") or {}).get("name"))
    inv_num = _norm_invoice_number(invoice.get("invoice_number"))
    if not (vendor and inv_num):
        return None
    return f"{vendor}|{inv_num}"


# ---- Match results -----------------------------------------------------

@dataclass
class DuplicateMatch:
    status: str                      # "exact" | "suspicious" | "fuzzy"
    match_type: str                  # explanatory key for UI
    ledger_id: str
    matched_filename: str
    matched_extracted_at: str
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---- Ledger ------------------------------------------------------------

class Ledger:
    """Append-only JSONL store with in-memory mirror.

    Thread-safe under a single Ledger instance via an internal lock -- fine
    for the single-flight extract endpoint, where only one extract runs at
    a time anyway.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_LEDGER_PATH
        self._lock = threading.Lock()
        self._entries: list[dict] = []
        self._load()

    # -- persistence ----------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    self._entries.append(json.loads(line))
                except json.JSONDecodeError:
                    # Skip malformed lines rather than crashing -- we'd rather
                    # keep working on a corrupt ledger and let the user clear it.
                    continue
        except OSError:
            self._entries = []

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for entry in self._entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        tmp.replace(self.path)

    # -- queries --------------------------------------------------------

    def entries(self, limit: int = MAX_ENTRIES_RETURNED) -> list[dict]:
        with self._lock:
            return list(reversed(self._entries[-limit:]))

    def __len__(self) -> int:
        return len(self._entries)

    def check(self, invoice: dict) -> DuplicateMatch | None:
        """Return a DuplicateMatch describing the most serious match found,
        or None if this invoice looks unique."""
        strict = strict_fingerprint(invoice)
        fuzzy = fuzzy_fingerprint(invoice)
        vi_key = vendor_invnum_key(invoice)
        new_total = _round_money(invoice.get("total_amount"))
        new_date = invoice.get("invoice_date")

        with self._lock:
            # Walk newest-first so a UI shows the most recent collision.
            for entry in reversed(self._entries):
                if strict and entry.get("strict_fingerprint") == strict:
                    return DuplicateMatch(
                        status="exact",
                        match_type="exact",
                        ledger_id=entry["id"],
                        matched_filename=entry.get("filename", ""),
                        matched_extracted_at=entry.get("extracted_at", ""),
                        explanation=(
                            f"Identical to a previously extracted invoice from "
                            f"{entry.get('vendor', '?')} (#{entry.get('invoice_number', '?')}, "
                            f"{entry.get('currency', '')} {entry.get('total_amount', '?')})."
                        ),
                    )

            # Same vendor + invoice number but a different total/date is a
            # softer signal -- could be a re-issue, or it could be a vision
            # extraction mistake. Either way, surface it.
            if vi_key:
                for entry in reversed(self._entries):
                    if entry.get("vendor_invnum_key") != vi_key:
                        continue
                    diffs: list[str] = []
                    if new_total is not None and entry.get("total_amount") is not None:
                        if abs(new_total - entry["total_amount"]) > 0.01:
                            diffs.append(
                                f"total {entry['total_amount']:.2f} → {new_total:.2f}"
                            )
                    if new_date and entry.get("invoice_date") and new_date != entry["invoice_date"]:
                        diffs.append(f"date {entry['invoice_date']} → {new_date}")
                    if diffs:
                        return DuplicateMatch(
                            status="suspicious",
                            match_type="same_invoice_number_different_values",
                            ledger_id=entry["id"],
                            matched_filename=entry.get("filename", ""),
                            matched_extracted_at=entry.get("extracted_at", ""),
                            explanation=(
                                "Same vendor and invoice number as a prior entry, but "
                                + " and ".join(diffs)
                                + ". Possible re-issue or extraction error."
                            ),
                        )

            # Last resort: fuzzy match for invoices with no invoice_number on
            # either side. Less reliable but better than missing the dup.
            if fuzzy:
                for entry in reversed(self._entries):
                    # Only fire if neither side had a usable invoice number,
                    # otherwise strict / vendor-invnum would've caught it.
                    if entry.get("strict_fingerprint"):
                        continue
                    if entry.get("invoice_number"):
                        continue
                    if entry.get("fuzzy_fingerprint") == fuzzy:
                        return DuplicateMatch(
                            status="fuzzy",
                            match_type="fuzzy",
                            ledger_id=entry["id"],
                            matched_filename=entry.get("filename", ""),
                            matched_extracted_at=entry.get("extracted_at", ""),
                            explanation=(
                                "Same vendor, date, currency and total as a prior "
                                "entry; neither has an invoice number. Possible duplicate."
                            ),
                        )

        return None

    # -- mutations ------------------------------------------------------

    def add(self, invoice: dict, filename: str) -> dict | None:
        """Append a new ledger entry. Returns the entry, or None if the
        invoice is too thin to fingerprint (no vendor/total/date)."""
        if not _is_addable(invoice):
            return None

        entry = _entry_from_invoice(invoice, filename)
        with self._lock:
            self._entries.append(entry)
            self._flush()
        return entry

    def remove(self, entry_id: str) -> bool:
        with self._lock:
            before = len(self._entries)
            self._entries = [e for e in self._entries if e.get("id") != entry_id]
            if len(self._entries) == before:
                return False
            self._flush()
            return True

    def clear(self) -> int:
        with self._lock:
            n = len(self._entries)
            self._entries = []
            self._flush()
            return n


def _is_addable(invoice: dict) -> bool:
    """Reject entries we can't usefully fingerprint or reason about later."""
    vendor = _norm_vendor((invoice.get("vendor") or {}).get("name"))
    if not vendor or vendor.startswith("stub") or "stub" in vendor:
        return False
    if invoice.get("total_amount") is None and not invoice.get("invoice_number"):
        return False
    return True


def _entry_from_invoice(invoice: dict, filename: str) -> dict:
    return {
        "id": uuid.uuid4().hex[:12],
        "extracted_at": dt.datetime.now(dt.UTC).replace(microsecond=0, tzinfo=None).isoformat() + "Z",
        "filename": filename,
        "vendor": (invoice.get("vendor") or {}).get("name") or "",
        "invoice_number": invoice.get("invoice_number") or "",
        "invoice_date": invoice.get("invoice_date") or "",
        "currency": (invoice.get("currency") or "").upper(),
        "total_amount": _round_money(invoice.get("total_amount")),
        "strict_fingerprint": strict_fingerprint(invoice),
        "fuzzy_fingerprint": fuzzy_fingerprint(invoice),
        "vendor_invnum_key": vendor_invnum_key(invoice),
    }


# Convenience for callers that only want the dict shape.
def fingerprint_summary(invoice: dict) -> dict[str, str | None]:
    return {
        "strict_fingerprint": strict_fingerprint(invoice),
        "fuzzy_fingerprint": fuzzy_fingerprint(invoice),
        "vendor_invnum_key": vendor_invnum_key(invoice),
    }


__all__ = [
    "DEFAULT_LEDGER_PATH",
    "DuplicateMatch",
    "Ledger",
    "fingerprint_summary",
    "fuzzy_fingerprint",
    "strict_fingerprint",
    "vendor_invnum_key",
]
