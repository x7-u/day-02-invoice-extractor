"""Day-2 test isolation.

Both day folders define modules with the same basename (excel_writer, pipeline,
csv_writer, etc). pytest collects them all in one process, so without this
conftest the second day to run inherits cached imports from the first.

This conftest runs once when pytest enters this directory: it evicts the
conflicting module names from sys.modules and prepends Day 2's folder to
sys.path so the next `from excel_writer import …` resolves to Day 2's version.
"""
from __future__ import annotations

import sys
from pathlib import Path

DAY_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = DAY_ROOT.parent

_CONFLICTING = {
    "excel_writer", "pipeline", "csv_writer", "ratios", "sectors",
    "pdf_loader", "invoice_schema", "main", "server", "ledger",
}
def _evict_and_set_path():
    for name in list(_CONFLICTING):
        sys.modules.pop(name, None)
    for p in (str(DAY_ROOT), str(PROJECT_ROOT)):
        if p in sys.path:
            sys.path.remove(p)
    sys.path.insert(0, str(DAY_ROOT))
    sys.path.insert(0, str(PROJECT_ROOT))


# Run at conftest load (before this dir's tests are collected) AND register a
# pytest hook that re-runs before each test file is collected -- this is what
# protects us from a sibling day's tests loading their version of excel_writer.py
# in between our conftest load and our own test imports.
_evict_and_set_path()


def pytest_collectstart(collector):
    # collector.path is a pathlib.Path on modern pytest
    p = getattr(collector, "path", None) or getattr(collector, "fspath", None)
    if p is None:
        return
    sp = str(p)
    if str(DAY_ROOT) in sp:
        _evict_and_set_path()

# Prepend, with PROJECT_ROOT first so `from shared.X import Y` keeps working.
for p in (str(DAY_ROOT), str(PROJECT_ROOT)):
    if p in sys.path:
        sys.path.remove(p)
sys.path.insert(0, str(DAY_ROOT))
sys.path.insert(0, str(PROJECT_ROOT))
