#!/usr/bin/env bash
# Day 02 — Invoice Data Extractor, local launcher (macOS / Linux)
# Day-N port convention: port = 1000 + N (Day 2 = 1002)
# Usage: ./start.sh [port]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

PORT="${1:-1002}"

if [ ! -x "../.venv/bin/python" ]; then
  echo "ERROR: virtual environment not found at ../.venv/"
  echo
  echo "First-time setup (from the project root):"
  echo "  python -m venv .venv"
  echo "  ./.venv/bin/python -m pip install -r requirements.txt"
  echo "  cp .env.example .env"
  echo "  \$EDITOR .env   # paste your ANTHROPIC_API_KEY"
  exit 1
fi

if [ ! -f server.py ]; then
  echo "ERROR: server.py not found next to start.sh"
  exit 1
fi

if [ ! -f ../.env ]; then
  echo "NOTICE: ../.env not found — AI extraction will be unavailable until you create it."
fi

is_port_busy() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -i ":$1" -sTCP:LISTEN -P -n >/dev/null 2>&1
  else
    (echo > /dev/tcp/127.0.0.1/"$1") >/dev/null 2>&1
  fi
}

if is_port_busy "$PORT"; then
  echo "NOTICE: port $PORT is busy. Trying 1102, 1202, 1302..."
  for P in 1102 1202 1302; do
    if ! is_port_busy "$P"; then PORT="$P"; break; fi
  done
fi

( sleep 2 && (
    if command -v open >/dev/null 2>&1; then open "http://127.0.0.1:$PORT/"
    elif command -v xdg-open >/dev/null 2>&1; then xdg-open "http://127.0.0.1:$PORT/"
    fi
  ) ) &

echo
echo "Starting Day 02 — Invoice Data Extractor on port $PORT ..."
echo "Local URL:  http://127.0.0.1:$PORT/"
echo "Press Ctrl+C to stop."
echo

PYTHONIOENCODING=utf-8 ../.venv/bin/python server.py --port "$PORT"
