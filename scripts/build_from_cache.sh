#!/usr/bin/env bash
# build_from_cache.sh — Build the Neo4j knowledge graph from pre-embedded output/ data.
#
# Recipient-friendly script: receives the output/ folder (nodes + edges + embeddings)
# and builds the full graph WITHOUT re-running the expensive embedding step.
#
# ── What this script does ─────────────────────────────────────────────────────
#   1. Validates required files (output/nodes, output/edges, output/embeddings)
#   2. Starts Neo4j via Docker Compose (if not already running)
#   3. Waits for Neo4j to become reachable
#   4. Runs the graph build (embeddings served from cache — no API calls)
#   5. Optionally creates semantic similarity edges
#
# ── Prerequisites ─────────────────────────────────────────────────────────────
#   - Python 3.10+  →  python3 -m venv venv && venv/bin/pip install -r requirements.txt
#   - Docker + docker compose  (for Neo4j)
#   - .env file  →  cp .env.example .env  then set NEO4J_PASSWORD
#
# ── Usage ─────────────────────────────────────────────────────────────────────
#   ./scripts/build_from_cache.sh               # full build
#   ./scripts/build_from_cache.sh --no-clean    # incremental (keep existing graph)
#   ./scripts/build_from_cache.sh --skip-sim    # skip semantic similarity edges
#   ./scripts/build_from_cache.sh --help
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$DIR"

PYTHON="${GRAPHRAG_PYTHON:-$DIR/venv/bin/python}"
OUTPUT_DIR="$DIR/output"

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${BLUE}[build]${NC} $*"; }
ok()      { echo -e "${GREEN}[build]${NC} ✓ $*"; }
warn()    { echo -e "${YELLOW}[build]${NC} ⚠ $*"; }
die()     { echo -e "${RED}[build] ERROR:${NC} $*" >&2; exit 1; }
header()  { echo -e "\n${BOLD}$*${NC}"; }

# ── Parse args ────────────────────────────────────────────────────────────────
CLEAN=true
SKIP_SIM=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-clean)   CLEAN=false;    shift ;;
        --skip-sim)   SKIP_SIM=true;  shift ;;
        -h|--help)
            sed -n '/^# ── Usage/,/^# ──/p' "$0" | head -n -1 | sed 's/^# \?//'
            exit 0 ;;
        *) die "Unknown option: $1\n  Use --help for usage." ;;
    esac
done

# ── Step 1: Check Python ──────────────────────────────────────────────────────
header "── Step 1 / 5  Checking prerequisites"

if [[ ! -x "$PYTHON" ]]; then
    die "Python not found at $PYTHON
  Run:  python3 -m venv venv && venv/bin/pip install -r requirements.txt"
fi
ok "Python: $($PYTHON --version)"

# ── Step 2: Validate output/ structure ───────────────────────────────────────
header "── Step 2 / 5  Validating output/ data"

missing=()
[[ -d "$OUTPUT_DIR/nodes" ]]      || missing+=("output/nodes/")
[[ -d "$OUTPUT_DIR/edges" ]]      || missing+=("output/edges/")
[[ -d "$OUTPUT_DIR/embeddings" ]] || missing+=("output/embeddings/")
[[ -f "$OUTPUT_DIR/ontology.json" ]]                    || missing+=("output/ontology.json")
[[ -f "$OUTPUT_DIR/embeddings/cache.npz" ]]             || missing+=("output/embeddings/cache.npz")
[[ -f "$OUTPUT_DIR/embeddings/cache_meta.json" ]]       || missing+=("output/embeddings/cache_meta.json")
[[ -f "$OUTPUT_DIR/edges/edge_rules.json" ]]            || missing+=("output/edges/edge_rules.json")

if [[ ${#missing[@]} -gt 0 ]]; then
    die "Missing required files/directories:\n$(printf '  • %s\n' "${missing[@]}")\n\nMake sure you have the complete output/ folder from the data provider."
fi

# Count node files
node_count=$(ls "$OUTPUT_DIR/nodes/"*.jsonl 2>/dev/null | wc -l | tr -d ' ')
edge_count=$(ls "$OUTPUT_DIR/edges/"*.jsonl 2>/dev/null | wc -l | tr -d ' ')
cached_vecs=$($PYTHON -c "
import json
with open('$OUTPUT_DIR/embeddings/cache_meta.json') as f:
    m = json.load(f)
print(len(m.get('hashes', {})))
" 2>/dev/null || echo "?")

ok "output/ structure valid"
info "  Node files   : $node_count"
info "  Edge files   : $edge_count"
info "  Cached vectors: $cached_vecs (no re-embedding needed)"

# ── Step 3: Start Neo4j ───────────────────────────────────────────────────────
header "── Step 3 / 5  Starting Neo4j"

if ! command -v docker &>/dev/null; then
    die "Docker not found. Install Docker Desktop and try again."
fi

if docker compose ps neo4j 2>/dev/null | grep -q "running\|Up"; then
    ok "Neo4j already running."
else
    info "Starting Neo4j via docker compose …"
    docker compose up -d neo4j
fi

# Wait for Neo4j to accept connections (up to 60s)
info "Waiting for Neo4j to be ready …"
READY=false
for i in $(seq 1 30); do
    if $PYTHON - <<'EOF' 2>/dev/null
import asyncio
async def _chk():
    from service.neo4j_driver import get_driver, close_driver
    d = await get_driver()
    await d.verify_connectivity()
    await close_driver()
asyncio.run(_chk())
EOF
    then
        READY=true
        break
    fi
    echo -n "."
    sleep 2
done
echo ""

$READY || die "Neo4j did not become ready within 60 seconds.
  Check: docker compose logs neo4j
  Verify NEO4J_PASSWORD in .env matches docker-compose.yml"

ok "Neo4j is reachable."

# ── Step 4: Build the graph ───────────────────────────────────────────────────
header "── Step 4 / 5  Building graph (embeddings from cache)"

if $CLEAN; then
    info "Mode: FULL REBUILD — existing Neo4j data will be cleared."
else
    info "Mode: INCREMENTAL — existing nodes/edges are kept."
fi

CLEAN_PY=$( $CLEAN && echo "True" || echo "False" )

$PYTHON - <<PYEOF
import asyncio, time, sys, os

os.environ["GRAPH_DATA_DIR"] = "$OUTPUT_DIR"

async def main():
    from service.graph_builder import Neo4jGraphBuilder
    from service.embedding_cache import EmbeddingCache
    from service.neo4j_driver import get_driver, close_driver
    from pathlib import Path

    await get_driver()

    # Load the pre-computed embedding cache from output/embeddings/
    cache = EmbeddingCache.load(
        cache_dir=Path("$OUTPUT_DIR/embeddings"),
    )
    print(f"  Cache: {cache.size} vectors loaded — no re-embedding will occur.")

    builder = Neo4jGraphBuilder()
    t0 = time.time()
    meta = await builder.build(clean=${CLEAN_PY}, cache=cache)
    await close_driver()

    dur = time.time() - t0
    print(f"\n  Nodes   : {meta.get('total_nodes', '?')}")
    print(f"  Edges   : {meta.get('total_edges', '?')}")
    print(f"  Duration: {dur:.1f}s")
    return meta

asyncio.run(main())
PYEOF

ok "Graph build complete."

# ── Step 5: Semantic similarity edges ─────────────────────────────────────────
if $SKIP_SIM; then
    warn "Skipping semantic similarity edges (--skip-sim)."
else
    header "── Step 5 / 5  Creating semantic similarity edges"
    info "This runs on existing Neo4j embeddings — no API calls needed."

    $PYTHON - <<'PYEOF'
import asyncio, os

async def main():
    from service.graph_builder import create_similarity_edges_neo4j
    from service.neo4j_driver import get_driver, close_driver

    await get_driver()
    count = await create_similarity_edges_neo4j()
    await close_driver()
    print(f"  Similarity edges created: {count}")

asyncio.run(main())
PYEOF

    ok "Similarity edges done."
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}All done!${NC} Your graph is ready."
echo ""
echo "  Start the API:  ./scripts/serve.sh"
echo "  Or:             python -m service"
echo ""
