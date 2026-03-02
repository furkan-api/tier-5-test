#!/usr/bin/env bash
# build.sh — Build or update the GraphRAG knowledge graph.
#
# SUBCOMMANDS
#   json [--data DIR] [--no-clean]
#       Build graph from scratch using JSON files in graph_data/ (default).
#       Clears Neo4j, embeds all nodes, creates all edges.
#       Options:
#         --data DIR     Override source data directory (default: graph_data/)
#         --no-clean     Keep existing Neo4j data (incremental upsert)
#
#   vector [--data DIR] [--skip-edges]
#       Re-embed all nodes and rebuild the vector index (no graph rebuild).
#       Use this when you change embedding model or dimension.
#       Options:
#         --data DIR     Override source data directory
#         --skip-edges   Skip re-applying edge rules after re-embedding
#
#   edges [--data DIR]
#       Re-apply edge rules only (nodes must already exist in Neo4j).
#       Use this when you only changed edge_rules.json.
#       Options:
#         --data DIR     Override source data directory
#
#   similarity [--threshold N] [--neighbors N]
#       Create/refresh semantic similarity edges via Neo4j vector index.
#       Does NOT re-embed — runs directly on existing embeddings.
#       Options:
#         --threshold N  Cosine similarity threshold (default: 0.82)
#         --neighbors N  Max neighbours per node (default: 5)
#
#   status
#       Show build status: Neo4j health, node/edge counts, last build time.
#
# EXAMPLES
#   ./build.sh json                          # full fresh build
#   ./build.sh json --data /path/to/data     # custom data directory
#   ./build.sh json --no-clean               # upsert without wiping
#   ./build.sh vector                        # re-embed + rebuild vector index
#   ./build.sh vector --skip-edges           # re-embed only (keep edges)
#   ./build.sh edges                         # re-apply edge rules only
#   ./build.sh similarity --threshold 0.85   # create similarity edges
#   ./build.sh status                        # check build state

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$DIR"

PYTHON="${GRAPHRAG_PYTHON:-$DIR/venv/bin/python}"

# ── Output colours ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
info()  { echo -e "${BLUE}[build]${NC} $*"; }
ok()    { echo -e "${GREEN}[build]${NC} $*"; }
warn()  { echo -e "${YELLOW}[build]${NC} $*"; }
die()   { echo -e "${RED}[build] ERROR:${NC} $*" >&2; exit 1; }

# ── Python / venv check ───────────────────────────────────────────────────────
check_python() {
    if [[ ! -x "$PYTHON" ]]; then
        die "Python not found at $PYTHON
  Run: python3 -m venv venv && venv/bin/pip install -r requirements.txt"
    fi
}

# ── Neo4j health check ────────────────────────────────────────────────────────
check_neo4j() {
    info "Checking Neo4j …"
    if ! $PYTHON - <<'EOF' 2>/dev/null
import asyncio, sys
async def _chk():
    from service.neo4j_driver import get_driver
    d = await get_driver()
    await d.verify_connectivity()
asyncio.run(_chk())
EOF
    then
        die "Cannot connect to Neo4j.
  Start it with:  docker compose up -d neo4j
  Check env:      cat .env | grep NEO4J"
    fi
    ok "Neo4j is reachable."
}

# ── Subcommand: json ──────────────────────────────────────────────────────────
cmd_json() {
    local data_dir="" clean=true

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --data)      data_dir="$2"; shift 2 ;;
            --no-clean)  clean=false; shift ;;
            *)           die "Unknown option for 'json': $1" ;;
        esac
    done

    check_python
    check_neo4j

    if [[ -n "$data_dir" ]]; then
        export GRAPH_DATA_DIR="$data_dir"
        info "Data directory: $data_dir"
    else
        info "Data directory: $DIR/graph_data/ (default)"
    fi

    if $clean; then
        info "Mode: FULL REBUILD (Neo4j graph will be cleared)"
    else
        info "Mode: INCREMENTAL UPSERT (existing nodes/edges kept)"
    fi

    info "Starting build …"
    echo ""

    CLEAN_FLAG=$( [[ "$clean" == "true" ]] && echo "True" || echo "False" )

    $PYTHON - <<PYEOF
import asyncio, time

async def main():
    from service.graph_builder import Neo4jGraphBuilder
    from service.neo4j_driver import close_driver

    clean = ${CLEAN_FLAG}

    try:
        builder = Neo4jGraphBuilder()
        meta = await builder.build(clean=clean)

        print()
        print(f"  Build complete in {meta['build_duration_sec']}s")
        print(f"  Nodes:       {meta['total_nodes']}")
        print(f"  Edges:       {meta['total_edges']}")
        print(f"  Embedding:   {meta['embedding_model']}")
        nt = meta.get('node_types', {})
        if nt:
            print(f"  Node types:")
            for k, v in sorted(nt.items(), key=lambda x: -x[1]):
                print(f"    {k:<22} {v}")
        print(f"  Built at:    {meta['built_at']}")
    finally:
        await close_driver()

asyncio.run(main())
PYEOF

    echo ""
    ok "Build finished."
}

# ── Subcommand: vector ────────────────────────────────────────────────────────
cmd_vector() {
    local data_dir="" skip_edges=false

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --data)       data_dir="$2"; shift 2 ;;
            --skip-edges) skip_edges=true; shift ;;
            *)            die "Unknown option for 'vector': $1" ;;
        esac
    done

    check_python
    check_neo4j

    if [[ -n "$data_dir" ]]; then
        export GRAPH_DATA_DIR="$data_dir"
        info "Data directory: $data_dir"
    fi

    info "Re-embedding all nodes and rebuilding vector index …"
    echo ""

    SKIP_FLAG=$( [[ "$skip_edges" == "true" ]] && echo "True" || echo "False" )

    $PYTHON - <<PYEOF
import asyncio, time

async def main():
    from service.graph_builder import Neo4jGraphBuilder
    from service.embeddings import get_embedder
    from service.vector_search import update_embeddings_batch
    from service.graph_store import ensure_schema
    from service.neo4j_driver import close_driver

    try:
        await ensure_schema()

        builder = Neo4jGraphBuilder()
        builder.load_data()

        embedder = get_embedder()
        print(f"  Embedder:  {embedder.model_name}")
        print(f"  Nodes:     {len(builder.nodes)}")
        print()

        node_ids = list(builder.nodes.keys())
        texts    = [builder.nodes[nid].get("embed_text", "") for nid in node_ids]

        print("  Generating embeddings …")
        t0 = time.time()
        embeddings = embedder.embed_texts(texts)
        print(f"  Done in {time.time()-t0:.1f}s")

        print("  Updating Neo4j embeddings …")
        rows = [{"node_id": nid, "embedding": embeddings[i].tolist()}
                for i, nid in enumerate(node_ids)]
        updated = await update_embeddings_batch(rows)
        print(f"  Updated: {updated} nodes")

        skip = ${SKIP_FLAG}
        if not skip:
            print()
            print("  Re-applying edge rules …")
            builder.load_edge_rules()
            edge_count = await builder.apply_edge_rules()
            print(f"  Edges created: {edge_count}")

    finally:
        await close_driver()

asyncio.run(main())
PYEOF

    echo ""
    ok "Vector re-embedding complete."
}

# ── Subcommand: edges ─────────────────────────────────────────────────────────
cmd_edges() {
    local data_dir=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --data) data_dir="$2"; shift 2 ;;
            *)      die "Unknown option for 'edges': $1" ;;
        esac
    done

    check_python
    check_neo4j

    if [[ -n "$data_dir" ]]; then
        export GRAPH_DATA_DIR="$data_dir"
    fi

    info "Re-applying edge rules (nodes unchanged) …"
    echo ""

    $PYTHON - <<'PYEOF'
import asyncio

async def main():
    from service.graph_builder import Neo4jGraphBuilder
    from service.neo4j_driver import close_driver, execute_write

    try:
        builder = Neo4jGraphBuilder()
        builder.load_data()
        builder.load_edge_rules()

        print("  Removing existing edges …")
        await execute_write("MATCH ()-[r:RELATED]->() DELETE r")

        print("  Applying edge rules …")
        edge_count = await builder.apply_edge_rules()
        print(f"  Edges created: {edge_count}")
    finally:
        await close_driver()

asyncio.run(main())
PYEOF

    echo ""
    ok "Edge rules applied."
}

# ── Subcommand: similarity ────────────────────────────────────────────────────
cmd_similarity() {
    local threshold=0.82 neighbors=5

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --threshold) threshold="$2"; shift 2 ;;
            --neighbors) neighbors="$2"; shift 2 ;;
            *)           die "Unknown option for 'similarity': $1" ;;
        esac
    done

    check_python
    check_neo4j

    info "Creating semantic similarity edges (threshold=$threshold, k=$neighbors) …"
    echo ""

    $PYTHON - <<PYEOF
import asyncio

async def main():
    from service.graph_builder import create_similarity_edges_neo4j
    from service.neo4j_driver import close_driver

    try:
        count = await create_similarity_edges_neo4j(
            threshold=float("$threshold"),
            max_neighbors=int("$neighbors"),
        )
        print(f"  Similarity edges created: {count}")
    finally:
        await close_driver()

asyncio.run(main())
PYEOF

    echo ""
    ok "Similarity edges done."
}

# ── Subcommand: status ────────────────────────────────────────────────────────
cmd_status() {
    check_python

    $PYTHON - <<'PYEOF'
import asyncio

async def main():
    from service.neo4j_driver import close_driver
    from service.graph_store import get_graph_stats, get_build_meta

    try:
        stats = await get_graph_stats()
        meta  = await get_build_meta()

        graph = stats.get("graph", stats)  # handle both flat and nested formats
        print()
        print(f"  {'Status':20} {stats.get('status', 'unknown')}")
        print(f"  {'Nodes':20} {graph.get('nodes', 0)}")
        print(f"  {'Edges':20} {graph.get('edges', 0)}")
        if graph.get('avg_degree'):
            print(f"  {'Avg degree':20} {graph.get('avg_degree', 0)}")
        if graph.get('isolated_nodes'):
            print(f"  {'Isolated nodes':20} {graph.get('isolated_nodes', 0)}")

        nt = stats.get("node_types", {})
        if nt:
            print()
            print("  Node types:")
            for k, v in sorted(nt.items(), key=lambda x: -x[1]):
                print(f"    {k:<22} {v}")

        et = stats.get("edge_types", {})
        if et:
            print()
            print("  Edge types:")
            for k, v in sorted(et.items(), key=lambda x: -x[1]):
                print(f"    {k:<22} {v}")

        if meta:
            print()
            print(f"  {'Built at':20} {meta.get('built_at', 'N/A')}")
            print(f"  {'Build duration':20} {meta.get('build_duration_sec', 'N/A')}s")
            print(f"  {'Embedding model':20} {meta.get('embedding_model', 'N/A')}")
        print()
    finally:
        await close_driver()

asyncio.run(main())
PYEOF
}

# ── Usage ─────────────────────────────────────────────────────────────────────
usage() {
    echo ""
    echo -e "${BOLD}Usage:${NC}  ./build.sh <subcommand> [options]"
    echo ""
    echo -e "${BOLD}Subcommands:${NC}"
    echo "  json        Full rebuild from JSON files → Neo4j (clears DB by default)"
    echo "  vector      Re-embed nodes + rebuild vector index (no DB clear)"
    echo "  edges       Re-apply edge rules only (keep nodes as-is)"
    echo "  similarity  Create semantic similarity edges via vector index"
    echo "  status      Show current build state and graph statistics"
    echo ""
    echo -e "${BOLD}Examples:${NC}"
    echo "  ./build.sh json                        # fresh build from graph_data/"
    echo "  ./build.sh json --data /my/data        # custom data directory"
    echo "  ./build.sh json --no-clean             # upsert without wiping Neo4j"
    echo "  ./build.sh vector                      # re-embed everything"
    echo "  ./build.sh vector --skip-edges         # re-embed, keep existing edges"
    echo "  ./build.sh edges                       # re-apply edge_rules.json"
    echo "  ./build.sh similarity --threshold 0.85"
    echo "  ./build.sh status"
    echo ""
}

# ── Entrypoint ────────────────────────────────────────────────────────────────
SUBCOMMAND="${1:-}"
shift || true

case "$SUBCOMMAND" in
    json)       cmd_json       "$@" ;;
    vector)     cmd_vector     "$@" ;;
    edges)      cmd_edges      "$@" ;;
    similarity) cmd_similarity "$@" ;;
    status)     cmd_status     ;;
    ""|help|-h|--help) usage ;;
    *) die "Unknown subcommand: '$SUBCOMMAND'. Run './build.sh --help' for usage." ;;
esac
