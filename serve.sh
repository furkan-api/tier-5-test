#!/usr/bin/env bash
# serve.sh — Start the GraphRAG API server.
# Usage: ./serve.sh [--port PORT]
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

PYTHON="$DIR/venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
    echo "ERROR: venv not found. Run: python3 -m venv venv && venv/bin/pip install -r requirements.txt"
    exit 1
fi

# Read defaults from .env (fallback values)
HOST="${API_HOST:-0.0.0.0}"
PORT="${API_PORT:-8000}"

# CLI override
while [[ $# -gt 0 ]]; do
    case "$1" in
        --port) PORT="$2"; shift 2 ;;
        --host) HOST="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# Check if port is already in use
if lsof -i :"$PORT" -sTCP:LISTEN &>/dev/null; then
    echo "Port $PORT is already in use."
    echo "  PID: $(lsof -ti :"$PORT" -sTCP:LISTEN)"
    echo "  Kill it first or use: ./serve.sh --port <other>"
    exit 1
fi

echo "Starting GraphRAG API on $HOST:$PORT …"
exec $PYTHON -m uvicorn src.api:app --host "$HOST" --port "$PORT"
