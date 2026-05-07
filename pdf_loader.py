"""Convert input files (PDF or image) to a list of JPEG byte-strings.

Each output JPEG corresponds to one page of the source. Multi-page PDFs are
capped at MAX_PAGES so we don't blow the token budget on huge documents.

Image inputs (jpg/png/webp) get a single-page list back, after EXIF-rotation
and a long-edge clamp to MAX_DIM pixels (Anthropic's vision sweet spot).
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageOps

MAX_PAGES = 5
MAX_DIM = 1568        # Anthropic's recommended max for vision
JPEG_QUALITY = 88

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
PDF_EXTS = {".pdf"}


def _pil_to_jpeg(img: Image.Image, quality: int = JPEG_QUALITY) -> bytes:
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    if max(img.size) > MAX_DIM:
        img.thumbnail((MAX_DIM, MAX_DIM), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def pages_as_jpeg(source: bytes | Path | str, *, filename_hint: str | None = None) -> list[bytes]:
    """Return a list of JPEG byte-strings -- one per page.

    Source may be a Path or raw bytes. For raw bytes, pass `filename_hint` so we
    can pick the right loader (e.g. "invoice.pdf" vs "scan.jpg").
    """
    if isinstance(source, (str, Path)):
        path = Path(source)
        ext = path.suffix.lower()
        data = path.read_bytes()
    else:
        data = source
        if not filename_hint:
            raise ValueError("filename_hint is required when source is raw bytes")
        ext = Path(filename_hint).suffix.lower()

    if ext in PDF_EXTS:
        return _pdf_pages(data)
    if ext in IMAGE_EXTS:
        return [_pil_to_jpeg(Image.open(io.BytesIO(data)))]
    raise ValueError(f"Unsupported file type: {ext} (allowed: {sorted(IMAGE_EXTS | PDF_EXTS)})")


def _pdf_pages(data: bytes) -> list[bytes]:
    """Rasterise a PDF (in-memory) to a list of JPEG byte-strings, one per page."""
    import pypdfium2 as pdfium  # local import to keep this module fast on text-only days

    out: list[bytes] = []
    doc = pdfium.PdfDocument(data)
    try:
        n_pages = min(len(doc), MAX_PAGES)
        for i in range(n_pages):
            page = doc[i]
            try:
                # 150 DPI -> roughly 1240px wide for A4. Then we clamp to MAX_DIM
                # in _pil_to_jpeg if needed.
                pil_img = page.render(scale=150 / 72).to_pil()
            finally:
                page.close()
            out.append(_pil_to_jpeg(pil_img))
    finally:
        doc.close()
    return out


def truncated(source: bytes | Path | str, *, filename_hint: str | None = None) -> bool:
    """Did we have to truncate the PDF? (Used by the pipeline to add a warning.)"""
    if isinstance(source, (str, Path)):
        ext = Path(source).suffix.lower()
        data = Path(source).read_bytes()
    else:
        data = source
        ext = Path(filename_hint or "").suffix.lower()
    if ext not in PDF_EXTS:
        return False
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(data)
    try:
        return len(doc) > MAX_PAGES
    finally:
        doc.close()
