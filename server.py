"""Day 2 -- Invoice Data Extractor, local Flask server.

Pure-local: bound to 127.0.0.1:1002 by default. Day-N port convention:
each day binds to port 1000 + N (Day 2 = 1002) so multiple days can run
side-by-side.

Routes:
  GET  /                         renders index.html, sets CSRF cookie
  POST /api/extract              multi-file upload -> per-file extraction
  GET  /api/status               environment + sample availability
  GET  /api/download/<filename>  serve a file from outputs/
  POST /api/shutdown             debug-only clean stop
  GET  /favicon.ico              static SVG icon

Same hardening as Day 1: 10 MB upload cap, secure_filename + safe_join,
single-flight semaphore on /api/extract, CSRF double-submit cookie,
generic 500 to client + full traceback in logs/server.log.
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import secrets
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from csv_writer import write_csv
from excel_writer import write_workbook
from flask import Flask, abort, jsonify, make_response, render_template, request, send_file
from ledger import Ledger
from pdf_loader import IMAGE_EXTS, MAX_PAGES, PDF_EXTS
from pipeline import extract_invoice, to_dict
from werkzeug.utils import safe_join, secure_filename

from shared.config import ANTHROPIC_API_KEY

HERE = Path(__file__).resolve().parent
SAMPLE_DIR = HERE / "sample_data"
OUTPUTS = HERE / "outputs"
UPLOADS = HERE / "uploads"
LOGS = HERE / "logs"

# Bundled samples surfaced to the UI.
SAMPLES: dict[str, tuple[str, str]] = {
    "uk_clean":     ("sample_clean_uk.pdf",     "Northwind Office Supplies -- clean UK PDF, GBP, 5 lines"),
    "eu_vat":       ("sample_eu_vat.pdf",       "Berlin Engineering -- EUR, 8 lines, 2 pages"),
    "us_consulting":("sample_us_consulting.pdf","Bridgewater Strategic -- USD services, no VAT"),
    "scanned":      ("sample_scanned.jpg",      "Coastal Marine -- scanned JPEG (degraded)"),
}

MAX_UPLOAD_BYTES = 10 * 1024 * 1024   # 10 MB -- bigger than Day 1's 5MB to fit multi-page PDFs
CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
ALLOWED_EXTS = IMAGE_EXTS | PDF_EXTS

app = Flask(
    __name__,
    template_folder=str(HERE / "templates"),
    static_folder=str(HERE / "static"),
)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

_extract_lock = threading.Lock()
_ledger = Ledger(OUTPUTS / "ledger.jsonl")


# ---- Logging ------------------------------------------------------------

LOGS.mkdir(parents=True, exist_ok=True)
_handler = logging.handlers.RotatingFileHandler(
    LOGS / "server.log", maxBytes=512_000, backupCount=3, encoding="utf-8",
)
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[_handler, logging.StreamHandler()])
log = logging.getLogger("day02.server")


# ---- Helpers ------------------------------------------------------------

def _env_key_ok() -> bool:
    return bool(ANTHROPIC_API_KEY) and not ANTHROPIC_API_KEY.startswith("sk-ant-placeholder")


def _ensure_csrf_cookie(resp):
    if not request.cookies.get(CSRF_COOKIE_NAME):
        resp.set_cookie(
            CSRF_COOKIE_NAME, secrets.token_urlsafe(24),
            samesite="Strict", httponly=False, secure=False, max_age=24 * 3600,
        )
    return resp


def _csrf_check() -> bool:
    cookie = request.cookies.get(CSRF_COOKIE_NAME, "")
    header = request.headers.get(CSRF_HEADER_NAME, "")
    return bool(cookie) and secrets.compare_digest(cookie, header)


def _samples_for_template():
    out = []
    for sid, (fname, label) in SAMPLES.items():
        if (SAMPLE_DIR / fname).exists():
            out.append({"id": sid, "filename": fname, "label": label})
    return out


# ---- Routes -------------------------------------------------------------

@app.route("/")
def index():
    resp = make_response(render_template(
        "index.html",
        env_key_ok=_env_key_ok(),
        samples=_samples_for_template(),
        max_pages=MAX_PAGES,
        max_upload_mb=MAX_UPLOAD_BYTES // (1024 * 1024),
    ))
    return _ensure_csrf_cookie(resp)


@app.route("/api/status")
def status():
    return jsonify(
        env_key_ok=_env_key_ok(),
        samples=_samples_for_template(),
        max_pages=MAX_PAGES,
        max_upload_mb=MAX_UPLOAD_BYTES // (1024 * 1024),
        ledger_size=len(_ledger),
    )


@app.route("/api/ledger", methods=["GET"])
def ledger_list():
    """Return ledger entries, newest first. Capped server-side."""
    return jsonify(entries=_ledger.entries(), size=len(_ledger))


@app.route("/api/ledger/clear", methods=["POST"])
def ledger_clear():
    if not _csrf_check():
        return jsonify(error="CSRF token missing or invalid. Refresh the page."), 403
    n = _ledger.clear()
    log.info("ledger cleared: %d entries removed", n)
    return jsonify(cleared=n, size=len(_ledger))


@app.route("/api/ledger/<entry_id>", methods=["DELETE"])
def ledger_remove(entry_id: str):
    if not _csrf_check():
        return jsonify(error="CSRF token missing or invalid. Refresh the page."), 403
    # entry IDs are 12-char hex from uuid4; guard against path tricks.
    if not entry_id or len(entry_id) > 64 or not entry_id.replace("-", "").isalnum():
        return jsonify(error="Invalid entry id."), 400
    ok = _ledger.remove(entry_id)
    if not ok:
        return jsonify(error="Entry not found."), 404
    return jsonify(removed=entry_id, size=len(_ledger))


@app.route("/api/extract", methods=["POST"])
def extract():
    if not _csrf_check():
        return jsonify(error="CSRF token missing or invalid. Refresh the page."), 403

    if not _extract_lock.acquire(blocking=False):
        return jsonify(error="Another extraction is already in flight. Wait for it to finish."), 429

    started = time.time()
    try:
        skip_ai = request.form.get("skip_ai") == "true"
        api_key_override = (request.form.get("api_key") or "").strip() or None
        model_choice = (request.form.get("model") or "").strip() or None
        use_samples = request.form.get("use_samples") == "true"
        sample_ids = (request.form.getlist("sample_ids") or [])

        files_to_process: list[tuple[str, bytes]] = []   # (display_name, bytes)

        if use_samples:
            if not sample_ids:
                # Default: all bundled samples
                sample_ids = list(SAMPLES.keys())
            for sid in sample_ids:
                if sid not in SAMPLES:
                    continue
                fname, _ = SAMPLES[sid]
                p = SAMPLE_DIR / fname
                if not p.exists():
                    continue
                files_to_process.append((fname, p.read_bytes()))
        else:
            uploads = request.files.getlist("files")
            if not uploads:
                return jsonify(error="No files uploaded. Pick one or more PDF/image files."), 400
            UPLOADS.mkdir(parents=True, exist_ok=True)
            for f in uploads:
                if not f.filename:
                    continue
                safe_name = secure_filename(f.filename) or "upload.pdf"
                ext = Path(safe_name).suffix.lower()
                if ext not in ALLOWED_EXTS:
                    return jsonify(error=f"Unsupported file type: {ext} (allowed: {sorted(ALLOWED_EXTS)})."), 400
                data = f.read()
                # Keep a copy on disk for audit/debug. UUID prefix avoids
                # collisions when two files arrive in the same second.
                disk_target = UPLOADS / f"{uuid.uuid4().hex[:8]}_{safe_name}"
                disk_target.write_bytes(data)
                files_to_process.append((safe_name, data))

        if not files_to_process:
            return jsonify(error="Nothing to process."), 400

        results = []
        dup_payloads: list[dict | None] = []
        for display_name, data in files_to_process:
            r = extract_invoice(
                data, display_name,
                model=model_choice,
                api_key=api_key_override,
                skip_ai=skip_ai,
            )
            results.append(r)

            # Duplicate check + add -- only for real successful extractions.
            # Skip stubs and errored runs so they don't pollute the ledger.
            payload: dict | None = None
            if not r.skipped and r.error is None:
                match = _ledger.check(r.invoice)
                if match is None:
                    _ledger.add(r.invoice, display_name)
                    r.duplicate_status = "unique"
                    payload = {"status": "unique", "match": None}
                else:
                    r.duplicate_status = match.status
                    r.duplicate_match_filename = match.matched_filename
                    payload = {"status": match.status, "match": match.to_dict()}
            dup_payloads.append(payload)

        # Always write CSV + Excel; client gets the filenames to download.
        # Writers read duplicate_status/duplicate_match_filename off ExtractResult.
        csv_path = write_csv(results, OUTPUTS / "invoices.csv")
        xlsx_path = write_workbook(results, OUTPUTS)

        elapsed_ms = int((time.time() - started) * 1000)
        total_cost = sum(r.cost_usd for r in results)
        n_dupes = sum(1 for d in dup_payloads if d and d.get("status") in ("exact", "suspicious", "fuzzy"))
        log.info(
            "extract OK n=%d ai=%s ms=%d cost_usd=%.4f dupes=%d",
            len(results), not skip_ai, elapsed_ms, total_cost, n_dupes,
        )

        result_dicts = [to_dict(r) for r in results]
        for rd, dup in zip(result_dicts, dup_payloads, strict=True):
            if dup is not None:
                rd["duplicate"] = dup

        return jsonify(
            results=result_dicts,
            csv_filename=csv_path.name,
            xlsx_filename=xlsx_path.name,
            total_cost_usd=round(total_cost, 6),
            elapsed_ms=elapsed_ms,
            count=len(results),
            duplicates_found=n_dupes,
            ledger_size=len(_ledger),
        )
    except ValueError as e:
        log.warning("extract validation error: %s", e)
        return jsonify(error=str(e)), 400
    except Exception:
        log.exception("extract unexpected error")
        return jsonify(error="Server error during extraction. See logs/server.log for details."), 500
    finally:
        _extract_lock.release()


@app.errorhandler(413)
def _too_large(_e):
    return jsonify(error=f"Upload exceeds {MAX_UPLOAD_BYTES // 1024 // 1024} MB limit."), 413


@app.route("/api/download/<path:filename>")
def download(filename: str):
    safe = secure_filename(filename) or ""
    if not safe:
        abort(400)
    full = safe_join(str(OUTPUTS), safe)
    if not full or not Path(full).is_file():
        return jsonify(error=f"Not found: {safe}"), 404
    return send_file(full, as_attachment=True, download_name=safe)


@app.route("/api/shutdown", methods=["POST"])
def shutdown():
    if not (app.debug or os.getenv("DAY02_ALLOW_SHUTDOWN") == "1"):
        return jsonify(error="Shutdown not enabled. Run with --debug or DAY02_ALLOW_SHUTDOWN=1."), 403
    if not _csrf_check():
        return jsonify(error="CSRF token missing."), 403
    threading.Thread(target=lambda: (time.sleep(0.2), os._exit(0)), daemon=True).start()
    return jsonify(stopped=True)


@app.route("/favicon.ico")
def favicon():
    p = HERE / "static" / "favicon.svg"
    if p.exists():
        return send_file(p)
    return ("", 204)


# ---- CLI ---------------------------------------------------------------

def main():
    # Day-N convention: port 1000 + N (Day 2 = 1002).
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.getenv("DAY02_PORT", "1002")))
    parser.add_argument("--host", default="127.0.0.1", help="Loopback by default -- keep it that way unless you know why.")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    print()
    print("  Day 2 - Invoice Data Extractor")
    print(f"  Local URL:  http://{args.host}:{args.port}/")
    print("  Press Ctrl+C to stop.")
    print()
    app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=args.debug)


if __name__ == "__main__":
    main()
