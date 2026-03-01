#!/usr/bin/env bash
# debug.sh — Start the GraphRAG Service API (Neo4j) then launch the CLI.
# Usage: ./debug.sh [--port PORT] [--api URL]
#
# Make sure Neo4j is running and the graph is built before launching:
#   docker compose up -d neo4j
#   ./build.sh json          ← full fresh build (first time)
#   ./build.sh status        ← check current state
#   ./update.sh full         ← add/update data without clearing
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

PYTHON="$DIR/venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
    echo "ERROR: venv not found. Run: python3 -m venv venv && venv/bin/pip install -r requirements.txt"
    exit 1
fi

HOST="${API_HOST:-0.0.0.0}"
PORT="${API_PORT:-8000}"
API_URL=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port) PORT="$2"; shift 2 ;;
        --host) HOST="$2"; shift 2 ;;
        --api)  API_URL="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# Default API URL
if [[ -z "$API_URL" ]]; then
    API_URL="http://localhost:$PORT"
fi

# ── Check / start the API server ─────────────────────────────────────────────

API_RUNNING=false

if curl -sf "$API_URL/health" &>/dev/null; then
    echo "API already running at $API_URL"
    API_RUNNING=true
fi

API_PID=""

if [[ "$API_RUNNING" == false ]]; then
    echo "Starting API server on $HOST:$PORT …"
    $PYTHON -m uvicorn service.api:app --host "$HOST" --port "$PORT" &
    API_PID=$!

    # Wait for server to be ready (max 30s)
    echo -n "Waiting for API"
    for i in $(seq 1 30); do
        if curl -sf "$API_URL/health" &>/dev/null; then
            echo " ready."
            break
        fi
        echo -n "."
        sleep 1
        if [[ $i -eq 30 ]]; then
            echo " timeout!"
            echo "ERROR: API did not start within 30s."
            kill "$API_PID" 2>/dev/null || true
            exit 1
        fi
    done
fi

# ── Cleanup handler ──────────────────────────────────────────────────────────

cleanup() {
    if [[ -n "$API_PID" ]]; then
        echo ""
        echo "Stopping API server (PID $API_PID) …"
        kill "$API_PID" 2>/dev/null || true
        wait "$API_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

# ── Launch CLI ────────────────────────────────────────────────────────────────

echo "Launching CLI …"
GRAPHRAG_API="$API_URL" $PYTHON cli.py
