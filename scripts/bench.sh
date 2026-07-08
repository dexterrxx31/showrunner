#!/usr/bin/env bash
# Benchmark the Python origin (FastAPI/uvicorn) vs the Go origin, serving the
# same channel. Requires: wrk, go, the venv, and demo assets (make_demo_assets).
#
#   ./scripts/bench.sh
set -euo pipefail
cd "$(dirname "$0")/.."

SNAP=$(mktemp /tmp/sr-snap.XXXX.json)
BIN=$(mktemp -u /tmp/sr-origin.XXXX)

.venv/bin/python scripts/export_snapshot.py "$SNAP"
(cd go-origin && go build -o "$BIN" .)

.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 >/tmp/bench_py.log 2>&1 &
PY=$!
SHOWRUNNER_SNAPSHOT="$SNAP" SHOWRUNNER_ADDR=127.0.0.1:8100 "$BIN" >/tmp/bench_go.log 2>&1 &
GO=$!
trap 'kill $PY $GO 2>/dev/null || true; rm -f "$SNAP" "$BIN"' EXIT
sleep 4

echo "### Python origin (FastAPI + uvicorn, 1 worker)"
wrk -t4 -c64 -d10s http://127.0.0.1:8000/channel/demo/playlist.m3u8 | grep -E "Requests/sec|Latency"
echo
echo "### Go origin (net/http)"
wrk -t4 -c64 -d10s http://127.0.0.1:8100/channel/demo/playlist.m3u8 | grep -E "Requests/sec|Latency"
