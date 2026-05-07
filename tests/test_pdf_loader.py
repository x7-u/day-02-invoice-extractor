"""pdf_loader smoke tests -- page count, JPEG validity, image input."""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from pdf_loader import IMAGE_EXTS, MAX_DIM, MAX_PAGES, PDF_EXTS, pages_as_jpeg, truncated

SAMPLES = ROOT / "sample_data"


def test_constants_sensible():
    assert MAX_PAGES >= 1
    assert MAX_DIM >= 800
    assert ".pdf" in PDF_EXTS
    assert ".jpg" in IMAGE_EXTS


def test_load_clean_pdf_returns_one_page():
    p = SAMPLES / "sample_clean_uk.pdf"
    if not p.exists():
        pytest.skip("Sample PDF not built -- run sample_data/_build.py first.")
    pages = pages_as_jpeg(p)
    assert len(pages) == 1
    img = Image.open(io.BytesIO(pages[0]))
    assert img.format == "JPEG"
    assert max(img.size) <= MAX_DIM


def test_load_two_page_pdf():
    p = SAMPLES / "sample_eu_vat.pdf"
    if not p.exists():
        pytest.skip("Sample PDF not built")
    pages = pages_as_jpeg(p)
    assert len(pages) == 2
    for jp in pages:
        Image.open(io.BytesIO(jp)).verify()


def test_load_image_returns_one_page():
    p = SAMPLES / "sample_scanned.jpg"
    if not p.exists():
        pytest.skip("Sample JPEG not built")
    pages = pages_as_jpeg(p)
    assert len(pages) == 1
    img = Image.open(io.BytesIO(pages[0]))
    assert img.format == "JPEG"


def test_load_from_bytes_with_filename_hint():
    p = SAMPLES / "sample_clean_uk.pdf"
    if not p.exists():
        pytest.skip("Sample PDF not built")
    pages = pages_as_jpeg(p.read_bytes(), filename_hint="invoice.pdf")
    assert len(pages) == 1


def test_load_rejects_unsupported_type():
    with pytest.raises(ValueError):
        pages_as_jpeg(b"not a real file", filename_hint="data.txt")


def test_truncated_returns_false_for_short_pdf():
    p = SAMPLES / "sample_clean_uk.pdf"
    if not p.exists():
        pytest.skip("Sample PDF not built")
    assert truncated(p) is False


def test_filename_hint_required_for_bytes():
    with pytest.raises(ValueError, match="filename_hint"):
        pages_as_jpeg(b"raw bytes")
