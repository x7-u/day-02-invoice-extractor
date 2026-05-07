"""Generate bundled sample invoices for the extractor.

Three PDFs (clean digital invoices) and one JPEG (a noisy "scanned" version)
covering different layouts, currencies, and tax regimes.

Run from anywhere:
    python day-02-invoice-extractor/sample_data/_build.py

Each sample is built from a small dict of facts so the consistency checker
can verify line items sum to subtotal, subtotal + tax = total. The values
are also exposed as a module-level dict for use in tests / golden fixtures.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, PageBreak,
)
from PIL import Image, ImageFilter

OUT_DIR = Path(__file__).resolve().parent


# -- Data classes ----------------------------------------------------------

@dataclass
class LineItem:
    description: str
    quantity: float
    unit_price: float

    @property
    def total(self) -> float:
        return round(self.quantity * self.unit_price, 2)


@dataclass
class Invoice:
    filename: str
    vendor_name: str
    vendor_address: str
    vendor_vat: str | None
    invoice_number: str
    invoice_date: str          # ISO 8601
    due_date: str | None       # ISO 8601 or None
    currency: str              # "GBP", "EUR", "USD"
    currency_symbol: str
    line_items: list[LineItem]
    tax_rate: float            # e.g. 0.20
    page_size: tuple = field(default=A4)
    notes: str = ""

    @property
    def subtotal(self) -> float:
        return round(sum(li.total for li in self.line_items), 2)

    @property
    def tax_amount(self) -> float:
        return round(self.subtotal * self.tax_rate, 2)

    @property
    def total(self) -> float:
        return round(self.subtotal + self.tax_amount, 2)


# -- Sample definitions ----------------------------------------------------

SAMPLES: list[Invoice] = [
    Invoice(
        filename="sample_clean_uk.pdf",
        vendor_name="Northwind Office Supplies Ltd",
        vendor_address="42 Castle Street, Reading, RG1 7QS, United Kingdom",
        vendor_vat="GB 482 9173 50",
        invoice_number="INV-2026-00417",
        invoice_date="2026-04-12",
        due_date="2026-05-12",
        currency="GBP",
        currency_symbol="£",  # £
        tax_rate=0.20,
        line_items=[
            LineItem("A4 Premium Copier Paper, 80gsm (5 reams)", 12, 18.50),
            LineItem("Black Ballpoint Pens, box of 50",          8, 11.20),
            LineItem("Document Wallet, foolscap, blue (pack 25)", 6, 8.95),
            LineItem("Whiteboard Markers, mixed colours (10)",   15, 6.40),
            LineItem("Office Chair Mat, 1.2m x 0.9m",             3, 42.00),
        ],
        notes="Payment by BACS to NatWest 60-13-22 / 24187531. Reference INV-2026-00417.",
    ),
    Invoice(
        filename="sample_eu_vat.pdf",
        vendor_name="Berlin Engineering Werkzeuge GmbH",
        vendor_address="Friedrichstrasse 134, 10117 Berlin, Germany",
        vendor_vat="DE 814 521 396",
        invoice_number="2026-EU-0942",
        invoice_date="2026-04-08",
        due_date="2026-05-23",
        currency="EUR",
        currency_symbol="€",  # €
        tax_rate=0.19,
        line_items=[
            LineItem("Precision Caliper, digital, 0-150mm",       2, 89.50),
            LineItem("Torque Wrench, 1/2\" drive, 40-200 Nm",     1, 142.00),
            LineItem("Multimeter, true RMS, CAT IV 600V",          3, 178.40),
            LineItem("Insulated Screwdriver Set (12 pieces)",      4, 58.20),
            LineItem("Cable Lugs, M8, copper (pack 100)",         12, 14.75),
            LineItem("Heat-shrink Tubing, assorted sizes",        20, 6.30),
            LineItem("Safety Glasses, anti-scratch (pack 10)",     5, 18.90),
            LineItem("Workshop Apron, heavy duty cotton",          8, 24.60),
        ],
        notes="Bitte Zahlung per Banküberweisung. IBAN DE89 1003 0400 0942 7716 80.",
    ),
    Invoice(
        filename="sample_us_consulting.pdf",
        vendor_name="Bridgewater Strategic Advisory LLC",
        vendor_address="500 Boylston Street, Suite 1900, Boston, MA 02116, USA",
        vendor_vat=None,  # US doesn't use VAT in the same way
        invoice_number="BSA-INV-3421",
        invoice_date="2026-04-15",
        due_date="2026-05-15",
        currency="USD",
        currency_symbol="$",
        tax_rate=0.0,
        page_size=letter,
        line_items=[
            LineItem("Strategy consulting (March 2026)", 38, 285.00),
            LineItem("Market research report (custom)",   1, 4500.00),
            LineItem("Stakeholder workshop facilitation", 2, 1850.00),
        ],
        notes="Net 30. Wire transfer to Bank of America 011000138 / 4467 8821 0094.",
    ),
    Invoice(
        # This one is also rendered to PDF, then converted to a noisy JPEG to
        # simulate a scanned document.
        filename="sample_scanned.pdf",  # intermediate
        vendor_name="Coastal Marine Services Ltd",
        vendor_address="Pier 4, Plymouth Sound, PL1 3AB, United Kingdom",
        vendor_vat="GB 217 6843 90",
        invoice_number="CMS/26/0188",
        invoice_date="2026-04-21",
        due_date="2026-05-21",
        currency="GBP",
        currency_symbol="£",
        tax_rate=0.20,
        line_items=[
            LineItem("Hull cleaning, vessel < 12m",           1, 380.00),
            LineItem("Anode replacement, zinc (4 anodes)",    4, 28.50),
            LineItem("Engine service, marine diesel",         1, 425.00),
            LineItem("Dry-dock charge per day",               3, 95.00),
        ],
        notes="Scanned copy. Original signed at quayside.",
    ),
]


# -- PDF rendering ---------------------------------------------------------

def _money(symbol: str, amount: float) -> str:
    return f"{symbol}{amount:,.2f}"


def _render_pdf(inv: Invoice, out_path: Path) -> None:
    """Render an invoice to a single-page (or multi-page) PDF via ReportLab."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=inv.page_size,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title=f"Invoice {inv.invoice_number}",
    )

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=22, leading=24,
                        textColor=colors.HexColor("#0B1E3F"), spaceAfter=12)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=11, leading=13,
                        textColor=colors.HexColor("#525A66"), spaceAfter=4)
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=10, leading=13)
    body_right = ParagraphStyle("body_right", parent=body, alignment=2)  # right
    body_bold = ParagraphStyle("body_bold", parent=body, fontName="Helvetica-Bold")

    story = []

    # Header
    story.append(Paragraph("INVOICE", h1))
    story.append(Paragraph(inv.vendor_name, body_bold))
    for addr_line in inv.vendor_address.split(", "):
        story.append(Paragraph(addr_line, body))
    if inv.vendor_vat:
        story.append(Paragraph(f"VAT: {inv.vendor_vat}", body))
    story.append(Spacer(1, 8 * mm))

    # Invoice meta as a 2-col table
    meta_rows = [
        ["Invoice number", inv.invoice_number],
        ["Invoice date",   inv.invoice_date],
    ]
    if inv.due_date:
        meta_rows.append(["Due date", inv.due_date])
    meta_rows.append(["Currency", inv.currency])

    meta_tbl = Table(meta_rows, colWidths=[40 * mm, 80 * mm])
    meta_tbl.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 10),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#525A66")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(meta_tbl)
    story.append(Spacer(1, 6 * mm))

    # Line items table
    headers = ["Description", "Qty", "Unit price", "Total"]
    rows = [headers]
    for li in inv.line_items:
        rows.append([
            li.description,
            f"{li.quantity:g}",
            _money(inv.currency_symbol, li.unit_price),
            _money(inv.currency_symbol, li.total),
        ])

    rows.append(["", "", "Subtotal", _money(inv.currency_symbol, inv.subtotal)])
    if inv.tax_rate > 0:
        rows.append(["", "", f"VAT ({inv.tax_rate*100:.0f}%)", _money(inv.currency_symbol, inv.tax_amount)])
    rows.append(["", "", "Total", _money(inv.currency_symbol, inv.total)])

    tbl = Table(rows, colWidths=[95 * mm, 18 * mm, 25 * mm, 32 * mm], repeatRows=1)
    tbl.setStyle(TableStyle([
        # header row
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0B1E3F")),
        ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
        ("FONT",       (0, 0), (-1, 0), "Helvetica-Bold", 10),
        # body
        ("FONT",       (0, 1), (-1, -1), "Helvetica", 10),
        ("ALIGN",      (1, 1), (-1, -1), "RIGHT"),
        ("ALIGN",      (0, 0), (0, -1), "LEFT"),
        # totals
        ("FONT",       (-2, -1), (-1, -1), "Helvetica-Bold", 11),
        ("LINEABOVE",  (-2, -3), (-1, -3), 0.5, colors.HexColor("#525A66")),
        ("LINEABOVE",  (-2, -1), (-1, -1), 1.0, colors.HexColor("#0B1E3F")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 8 * mm))

    if inv.notes:
        story.append(Paragraph("Notes:", h2))
        story.append(Paragraph(inv.notes, body))

    # For the EU multi-line invoice with 8 lines we deliberately push to 2 pages
    # to exercise the multi-page path in the loader.
    if len(inv.line_items) >= 8:
        story.append(PageBreak())
        story.append(Paragraph("Page 2 -- Terms & conditions", h1))
        story.append(Paragraph(
            "Payment terms net 45 days from invoice date. Late payment charged at "
            "5% above the European Central Bank reference rate. Goods remain the "
            "property of the seller until paid in full. Disputes governed by the "
            "law of the Federal Republic of Germany.",
            body,
        ))

    doc.build(buf_args=story) if False else doc.build(story)  # noqa: S106

    out_path.write_bytes(buf.getvalue())


def _render_scanned_jpeg(inv: Invoice, out_path: Path) -> None:
    """Render to PDF, rasterise the first page, then degrade for a 'scan' look."""
    import pypdfium2 as pdfium
    from PIL import ImageEnhance

    pdf_path = OUT_DIR / "_tmp_scanned.pdf"
    _render_pdf(inv, pdf_path)
    try:
        doc = pdfium.PdfDocument(str(pdf_path))
        try:
            page = doc[0]
            # 150 DPI gives ~1240px wide for A4, plenty for the model.
            pil_img = page.render(scale=150 / 72).to_pil().convert("RGB")
            page.close()
        finally:
            doc.close()  # release the file handle so we can delete it below

        # Light degradation to look photocopier-like.
        noisy = pil_img.rotate(0.6, fillcolor=(255, 255, 255), resample=Image.BICUBIC)
        noisy = noisy.filter(ImageFilter.GaussianBlur(radius=0.5))
        noisy = ImageEnhance.Contrast(noisy).enhance(0.92)
        noisy = ImageEnhance.Brightness(noisy).enhance(1.04)
        noisy.save(out_path, "JPEG", quality=78, optimize=True)
    finally:
        try:
            pdf_path.unlink()
        except (FileNotFoundError, PermissionError):
            pass


# -- Consistency checks ----------------------------------------------------

def _consistency_checks() -> None:
    for inv in SAMPLES:
        # line items sum to subtotal (within 0.01 due to rounding)
        line_total = sum(li.total for li in inv.line_items)
        if abs(line_total - inv.subtotal) > 0.02:
            raise AssertionError(
                f"{inv.filename}: line-item total {line_total} != subtotal {inv.subtotal}"
            )
        # subtotal + tax = total
        expected_total = round(inv.subtotal + inv.tax_amount, 2)
        if abs(expected_total - inv.total) > 0.02:
            raise AssertionError(
                f"{inv.filename}: subtotal+tax {expected_total} != total {inv.total}"
            )
        # invoice_date <= due_date
        if inv.due_date and inv.invoice_date > inv.due_date:
            raise AssertionError(f"{inv.filename}: invoice_date > due_date")


# -- Entry point -----------------------------------------------------------

def main() -> None:
    _consistency_checks()
    print("All consistency checks passed.\n")

    for inv in SAMPLES:
        if inv.filename.endswith(".pdf") and inv.filename != "sample_scanned.pdf":
            _render_pdf(inv, OUT_DIR / inv.filename)
            print(f"  Wrote {inv.filename}  ({len(inv.line_items)} line items, {inv.currency})")
        elif inv.filename == "sample_scanned.pdf":
            jpeg_name = inv.filename.replace(".pdf", ".jpg")
            _render_scanned_jpeg(inv, OUT_DIR / jpeg_name)
            print(f"  Wrote {jpeg_name}  (rasterised + degraded JPEG)")


if __name__ == "__main__":
    main()
