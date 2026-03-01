#!/usr/bin/env bash
# serve.sh — Start the GraphRAG API server (production mode).
#
# Usage: ./serve.sh [--port PORT] [--host HOST] [--workers N] [--reload]
#
# Options:
#   --port PORT      Listen port        (default: 8000 / API_PORT env)
#   --host HOST      Listen host        (default: 0.0.0.0 / API_HOST env)
#   --workers N      Uvicorn workers    (default: 1; use >1 for production)
#   --reload         Enable auto-reload (dev mode, implies workers=1)
#
# Neo4j must already be running before starting the API.
# Start Neo4j:  docker compose up -d neo4j
# Build graph:  ./build.sh json

set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

PYTHON="${GRAPHRAG_PYTHON:-$DIR/venv/bin/python}"

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
info() { echo -e "${BLUE}[serve]${NC} $*"; }
die()  { echo -e "${RED}[serve] ERROR:${NC} $*" >&2; exit 1; }

if [[ ! -x "$PYTHON" ]]; then
    die "Python not found at $PYTHON\n  Run: python3 -m venv venv && venv/bin/pip install -r requirements.txt"
fi

HOST="${API_HOST:-0.0.0.0}"
PORT="${API_PORT:-8000}"
WORKERS=1
RELOAD=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)    PORT="$2";    shift 2 ;;
        --host)    HOST="$2";    shift 2 ;;
        --workers) WORKERS="$2"; shift 2 ;;
        --reload)  RELOAD=true;  shift ;;
        -h|--help)
            echo "Usage: ./serve.sh [--port PORT] [--host HOST] [--workers N] [--reload]"
            exit 0 ;;
        *) die "Unknown option: $1" ;;
    esac
done

# Reload mode forces single worker
if $RELOAD && [[ "$WORKERS" -gt 1 ]]; then
    echo "Warning: --reload forces workers=1"
    WORKERS=1
fi

# Check if port is already in use
if lsof -i :"$PORT" -sTCP:LISTEN &>/dev/null; then
    die "Port $PORT is already in use (PID $(lsof -ti :"$PORT" -sTCP:LISTEN 2>/dev/null || echo '?')).\n  Kill it first or use: ./serve.sh --port <other>"
fi

# Neo4j check
info "Checking Neo4j …"
if ! $PYTHON - <<'EOF' 2>/dev/null
import asyncio
async def _chk():
    from service.neo4j_driver import get_driver
    d = await get_driver()
    await d.verify_connectivity()
asyncio.run(_chk())
EOF
then
    die "Cannot reach Neo4j. Start it with: docker compose up -d neo4j"
fi
info "Neo4j OK."

info "Starting GraphRAG API on $HOST:$PORT (workers=$WORKERS) …"
echo ""

EXTRA_ARGS=()
$RELOAD && EXTRA_ARGS+=("--reload")

exec $PYTHON -m uvicorn service.api:app \
    --host "$HOST" \
    --port "$PORT" \
    --workers "$WORKERS" \
    "${EXTRA_ARGS[@]}"
