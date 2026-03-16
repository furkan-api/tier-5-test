#!/usr/bin/env bash
# refresh_cache_with_data.sh — Re-embed all graph_data/ nodes and refresh output/ cache.
#
# ⚠  EXPENSIVE STEP — this calls the embedding model for every new/changed node.
#    Run this when raw source data in graph_data/ has changed and you need to
#    update the shared output/ folder before distributing it to others.
#
# ── What this script does ─────────────────────────────────────────────────────
#   1. Validates source data in graph_data/
#   2. Runs embed_only on all nodes  (hits cache for unchanged, embeds the rest)
#   3. Saves the refreshed cache back to graph_data/embeddings/
#   4. Syncs nodes, edges, ontology and cache into output/
#
#   After this script finishes, share the output/ folder with others and they
#   can rebuild the graph with scripts/build_from_cache.sh — no re-embedding.
#
# ── Prerequisites ─────────────────────────────────────────────────────────────
#   - Python 3.10+          →  python3 -m venv venv && venv/bin/pip install -r requirements.txt
#   - Embedding model       →  sentence-transformers (installed via requirements.txt)
#   - .env with model config (EMBEDDING_MODEL / EMBEDDING_PROVIDER if non-default)
#   - Neo4j is NOT required for this step
#
# ── Usage ─────────────────────────────────────────────────────────────────────
#   ./scripts/refresh_cache_with_data.sh             # embed all (incremental — skips cached)
#   ./scripts/refresh_cache_with_data.sh --force     # re-embed everything, ignore existing cache
#   ./scripts/refresh_cache_with_data.sh --no-sync   # only refresh cache, skip output/ sync
#   ./scripts/refresh_cache_with_data.sh --data DIR  # use a different source data dir
#   ./scripts/refresh_cache_with_data.sh --help
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$DIR"

PYTHON="${GRAPHRAG_PYTHON:-$DIR/venv/bin/python}"
SOURCE_DIR="$DIR/output"
OUTPUT_DIR="$DIR/output"

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${BLUE}[refresh]${NC} $*"; }
ok()      { echo -e "${GREEN}[refresh]${NC} ✓ $*"; }
warn()    { echo -e "${YELLOW}[refresh]${NC} ⚠ $*"; }
die()     { echo -e "${RED}[refresh] ERROR:${NC} $*" >&2; exit 1; }
header()  { echo -e "\n${BOLD}$*${NC}"; }

# ── Parse args ────────────────────────────────────────────────────────────────
FORCE=false
SYNC=true

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force)      FORCE=true;              shift ;;
        --no-sync)    SYNC=false;              shift ;;
        --data)       [[ -n "${2:-}" ]] || die "--data requires a directory argument"
                      SOURCE_DIR="$(cd "$2" && pwd)"; shift 2 ;;
        -h|--help)
            sed -n '/^# ── Usage/,/^# ──/p' "$0" | head -n -1 | sed 's/^# \?//'
            exit 0 ;;
        *) die "Unknown option: $1\n  Use --help for usage." ;;
    esac
done

# ── Step 1: Check Python ──────────────────────────────────────────────────────
header "── Step 1 / 4  Checking prerequisites"

if [[ ! -x "$PYTHON" ]]; then
    die "Python not found at $PYTHON
  Run:  python3 -m venv venv && venv/bin/pip install -r requirements.txt"
fi
ok "Python: $($PYTHON --version)"

# ── Step 2: Validate source data ─────────────────────────────────────────────
header "── Step 2 / 4  Validating source data in $SOURCE_DIR"

missing=()
[[ -d "$SOURCE_DIR/nodes" ]]       || missing+=("$SOURCE_DIR/nodes/")
[[ -d "$SOURCE_DIR/edges" ]]       || missing+=("$SOURCE_DIR/edges/")
[[ -f "$SOURCE_DIR/ontology.json" ]] || missing+=("$SOURCE_DIR/ontology.json")
[[ -f "$SOURCE_DIR/edges/edge_rules.json" ]] || missing+=("$SOURCE_DIR/edges/edge_rules.json")

if [[ ${#missing[@]} -gt 0 ]]; then
    die "Missing required files/directories:\n$(printf '  • %s\n' "${missing[@]}")\n\nMake sure graph_data/ contains the processed node/edge files."
fi

node_count=$(ls "$SOURCE_DIR/nodes/"*.jsonl 2>/dev/null | wc -l | tr -d ' ')
edge_count=$(ls "$SOURCE_DIR/edges/"*.jsonl 2>/dev/null | wc -l | tr -d ' ')

[[ "$node_count" -gt 0 ]] || die "No .jsonl node files found in $SOURCE_DIR/nodes/"

ok "Source data valid"
info "  Node files : $node_count"
info "  Edge files : $edge_count"

# Show existing cache status if present
if [[ -f "$SOURCE_DIR/embeddings/cache_meta.json" ]]; then
    cached_vecs=$($PYTHON -c "
import json
with open('$SOURCE_DIR/embeddings/cache_meta.json') as f:
    m = json.load(f)
print(len(m.get('hashes', {})))
" 2>/dev/null || echo "?")
    info "  Existing cache: $cached_vecs vectors (will be reused where unchanged)"
else
    info "  No existing cache — all nodes will be embedded from scratch."
    mkdir -p "$SOURCE_DIR/embeddings"
fi

if $FORCE; then
    warn "  --force: clearing existing cache before embedding."
    rm -f "$SOURCE_DIR/embeddings/cache.npz" "$SOURCE_DIR/embeddings/cache_meta.json"
fi

# ── Step 3: Embed all nodes ───────────────────────────────────────────────────
header "── Step 3 / 4  Embedding nodes"
info "Source dir  : $SOURCE_DIR"
info "This may take several minutes for large datasets …"

FORCE_PY=$( $FORCE && echo "True" || echo "False" )

$PYTHON - <<PYEOF
import asyncio, time, sys, os
from pathlib import Path

os.environ["GRAPH_DATA_DIR"] = "$SOURCE_DIR"

async def main():
    from service.graph_builder import Neo4jGraphBuilder
    from service.embedding_cache import EmbeddingCache

    cache_dir = Path("$SOURCE_DIR") / "embeddings"
    cache_dir.mkdir(parents=True, exist_ok=True)

    cache = EmbeddingCache.load(cache_dir=cache_dir)
    print(f"  Cache loaded: {cache.size} existing vectors")

    builder = Neo4jGraphBuilder()
    t0 = time.time()
    summary = await builder.embed_only(cache=cache)
    dur = time.time() - t0

    print(f"\n  Total nodes   : {summary.get('total_nodes', summary.get('size', '?'))}")
    print(f"  Newly embedded: {summary.get('newly_embedded', '?')}")
    print(f"  Cache hits    : {summary.get('newly_embedded', 0) and summary.get('size', 0) - summary.get('newly_embedded', 0) or '?'}")
    print(f"  Duration      : {dur:.1f}s")
    print(f"  Cache location: $SOURCE_DIR/embeddings/")

asyncio.run(main())
PYEOF

ok "Embedding complete. Cache saved to $SOURCE_DIR/embeddings/."

# ── Step 4: Sync to output/ (only when source ≠ output) ─────────────────────
if [[ "$(realpath "$SOURCE_DIR")" == "$(realpath "$OUTPUT_DIR")" ]]; then
    info "Source is output/ — skipping sync (already in place)."
elif ! $SYNC; then
    warn "Skipping output/ sync (--no-sync). output/ was NOT updated."
    echo ""
    ok "Done. Cache refreshed in $SOURCE_DIR/embeddings/."
    echo ""
    echo "  To sync manually later, run:"
    echo "    rsync -av $SOURCE_DIR/nodes/      $OUTPUT_DIR/nodes/"
    echo "    rsync -av $SOURCE_DIR/edges/      $OUTPUT_DIR/edges/"
    echo "    rsync -av $SOURCE_DIR/embeddings/ $OUTPUT_DIR/embeddings/"
    echo "    cp        $SOURCE_DIR/ontology.json $OUTPUT_DIR/ontology.json"
    exit 0
else
    header "── Step 4 / 4  Syncing to output/"
    info "Copying refreshed data from $SOURCE_DIR → $OUTPUT_DIR"

    mkdir -p "$OUTPUT_DIR/nodes" "$OUTPUT_DIR/edges" "$OUTPUT_DIR/embeddings"

    rsync -av --delete "$SOURCE_DIR/nodes/"     "$OUTPUT_DIR/nodes/"     2>&1 | grep -E '^\s*(deleting|[^ ]+\.jsonl)' || true
    ok "nodes/ synced"

    rsync -av "$SOURCE_DIR/edges/"              "$OUTPUT_DIR/edges/"     2>&1 | grep -E '^\s*(deleting|[^ ]+\.(jsonl|json))' || true
    ok "edges/ synced"

    rsync -av "$SOURCE_DIR/embeddings/"         "$OUTPUT_DIR/embeddings/" 2>&1 | grep -E '^\s*(deleting|cache\.)' || true
    ok "embeddings/ synced"

    cp "$SOURCE_DIR/ontology.json" "$OUTPUT_DIR/ontology.json"
    ok "ontology.json copied"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}─────────────────────────────────────────────────────────${NC}"
echo -e "${GREEN}  Refresh complete!${NC}"
echo ""
echo "  output/ is ready to share. Recipients can rebuild the graph with:"
echo "    ./scripts/build_from_cache.sh"
echo ""

# Print final cache stats
$PYTHON - <<PYEOF 2>/dev/null || true
import json
from pathlib import Path
meta_path = Path("$OUTPUT_DIR/embeddings/cache_meta.json")
if meta_path.exists():
    m = json.load(open(meta_path))
    print(f"  Cached vectors : {len(m.get('hashes', {}))}")
    print(f"  Embedding model: {m.get('model_name', 'unknown')}")
    print(f"  Dimension      : {m.get('dimension', '?')}")
PYEOF

echo -e "${BOLD}─────────────────────────────────────────────────────────${NC}"
