"""Day 2 -- Invoice Data Extractor CLI.

Usage:
    python main.py --input sample_data/sample_clean_uk.pdf
    python main.py --input sample_data/                       # process every file in the folder
    python main.py --input invoice.pdf --no-ai                # skip the API call (stub output)
    python main.py --input sample_data/ --model claude-sonnet-4-6
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from csv_writer import write_csv
from excel_writer import write_workbook
from pdf_loader import IMAGE_EXTS, PDF_EXTS
from pipeline import extract_invoice


def _gather_inputs(arg: Path) -> list[Path]:
    if arg.is_file():
        return [arg]
    if arg.is_dir():
        return sorted(
            p for p in arg.iterdir()
            if p.is_file() and p.suffix.lower() in (IMAGE_EXTS | PDF_EXTS)
        )
    return []


def _print_summary(r) -> None:
    inv = r.invoice
    vendor = (inv.get("vendor") or {}).get("name") or "n/a"
    n_lines = len(inv.get("line_items") or [])
    cur = inv.get("currency") or ""
    total = inv.get("total_amount")
    total_str = f"{cur} {total:,.2f}" if total is not None else "n/a"
    print(f"  {r.source_filename}")
    print(f"    vendor:      {vendor}")
    print(f"    invoice #:   {inv.get('invoice_number') or 'n/a'}")
    print(f"    date:        {inv.get('invoice_date') or 'n/a'}")
    print(f"    line items:  {n_lines}")
    print(f"    total:       {total_str}")
    print(f"    confidence:  {(inv.get('confidence') or 'n/a').upper()}")
    if inv.get("extraction_warnings"):
        for w in inv["extraction_warnings"]:
            print(f"    !  {w}")
    if r.cost_usd:
        print(f"    cost:        ${r.cost_usd:.4f}  ({r.input_tokens} in / {r.output_tokens} out, {r.model})")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Day 2 -- Invoice Data Extractor")
    parser.add_argument("--input", required=True,
                        help="Path to a PDF/image file, or a folder containing them")
    parser.add_argument("--no-ai", action="store_true", help="Skip the AI call (stub output)")
    parser.add_argument("--model", default=None, help="Override the Claude model (e.g. claude-sonnet-4-6)")
    parser.add_argument("--out-dir", default=None, help="Where to write CSV/Excel (defaults to ./outputs)")
    parser.add_argument("--json", action="store_true", help="Also print the raw JSON for each invoice")
    args = parser.parse_args()

    here = Path(__file__).resolve().parent
    src_arg = Path(args.input)
    if not src_arg.is_absolute():
        src_arg = (Path.cwd() / src_arg).resolve()
        if not src_arg.exists():
            src_arg = (here / args.input).resolve()
    if not src_arg.exists():
        sys.exit(f"Input not found: {src_arg}")

    out_dir = Path(args.out_dir) if args.out_dir else (here / "outputs")
    out_dir.mkdir(parents=True, exist_ok=True)

    inputs = _gather_inputs(src_arg)
    if not inputs:
        sys.exit(f"No PDF/image files found at {src_arg}")

    print(f"\nDay 2 -- Invoice Data Extractor -- {len(inputs)} file{'s' if len(inputs) != 1 else ''}\n")

    results = []
    for path in inputs:
        try:
            data = path.read_bytes()
            r = extract_invoice(
                data, path.name,
                model=args.model, skip_ai=args.no_ai,
            )
            results.append(r)
            _print_summary(r)
            if args.json:
                print(json.dumps(r.invoice, indent=2, default=str))
                print()
        except Exception as e:
            print(f"  ! {path.name}  (error: {type(e).__name__}: {e})")

    if not results:
        sys.exit("No results to write.")

    csv_path = write_csv(results, out_dir / "invoices.csv")
    xlsx_path = write_workbook(results, out_dir)

    total_cost = sum(r.cost_usd for r in results)
    print(f"Wrote: {csv_path}")
    print(f"Wrote: {xlsx_path}")
    print(f"Total cost: ${total_cost:.4f} across {len(results)} invoice{'s' if len(results) != 1 else ''}.")


if __name__ == "__main__":
    main()
