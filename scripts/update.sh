#!/usr/bin/env bash
# update.sh — Incrementally update the GraphRAG graph with new/changed data.
#
# SUBCOMMANDS
#   nodes [--data DIR] [--files f1.json,f2.json]
#       Upsert nodes from JSON files (add new + update changed).
#       Embeddings are generated only for new/updated nodes.
#       Existing nodes NOT in the new files are left untouched.
#       Options:
#         --data DIR         Source data directory (default: graph_data/)
#         --files f1,f2,...  Specific JSON files to process (default: all)
#
#   vector [--data DIR] [--files f1.json,f2.json]
#       Re-embed only nodes from the specified files.
#       Use when you've updated text content of existing nodes.
#       Options:
#         --data DIR         Source data directory (default: graph_data/)
#         --files f1,f2,...  Specific JSON files to re-embed (default: all)
#
#   edges [--data DIR]
#       Re-apply edge rules after adding new nodes.
#       Drops ALL existing edges and re-creates them from scratch
#       so that new nodes are properly connected.
#
#   full [--data DIR] [--files f1.json,f2.json]
#       Full incremental update: upsert nodes → re-embed → re-apply edges.
#       Equivalent to: nodes + vector + edges in one pass.
#
# EXAMPLES
#   ./update.sh nodes                              # upsert all JSON files
#   ./update.sh nodes --files kararlar_yargitay.json   # update one file
#   ./update.sh vector --files kararlar_yargitay.json  # re-embed one file
#   ./update.sh edges                              # reconnect after node changes
#   ./update.sh full                               # nodes + embed + edges
#   ./update.sh full --files new_data.json         # add a new JSON file

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$DIR"

PYTHON="${GRAPHRAG_PYTHON:-$DIR/venv/bin/python}"

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
info()  { echo -e "${BLUE}[update]${NC} $*"; }
ok()    { echo -e "${GREEN}[update]${NC} $*"; }
warn()  { echo -e "${YELLOW}[update]${NC} $*"; }
die()   { echo -e "${RED}[update] ERROR:${NC} $*" >&2; exit 1; }

check_python() {
    if [[ ! -x "$PYTHON" ]]; then
        die "Python not found at $PYTHON
  Run: python3 -m venv venv && venv/bin/pip install -r requirements.txt"
    fi
}

check_neo4j() {
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
        die "Cannot connect to Neo4j. Start it with: docker compose up -d neo4j"
    fi
    ok "Neo4j is reachable."
}

# Parse common flags
parse_data_files() {
    # Sets DATA_DIR and FILES_LIST from "$@"
    DATA_DIR=""
    FILES_LIST=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --data)  DATA_DIR="$2"; shift 2 ;;
            --files) FILES_LIST="$2"; shift 2 ;;
            *)       die "Unknown option: $1" ;;
        esac
    done
}

# ── Subcommand: nodes ─────────────────────────────────────────────────────────
cmd_nodes() {
    parse_data_files "$@"
    check_python
    check_neo4j

    [[ -n "$DATA_DIR" ]] && export GRAPH_DATA_DIR="$DATA_DIR" && info "Data directory: $DATA_DIR"

    FILES_ARG="None"
    if [[ -n "$FILES_LIST" ]]; then
        # Convert comma-separated to Python list literal
        FILES_ARG="[$(echo "$FILES_LIST" | sed "s/,/', '/g; s/^/'/; s/$/'/" )]"
        info "Files: $FILES_LIST"
    fi

    info "Upserting nodes (no graph clear) …"
    echo ""

    $PYTHON - <<PYEOF
import asyncio

async def main():
    from service.graph_builder import Neo4jGraphBuilder
    from service.graph_store import ensure_schema
    from service.config import get_settings
    from service.neo4j_driver import close_driver

    try:
        await ensure_schema()

        builder = Neo4jGraphBuilder()

        # Override file list if specific files requested
        files_override = ${FILES_ARG}
        if files_override is not None:
            builder.data_files = files_override

        builder.load_data()
        print(f"  Loaded {len(builder.nodes)} nodes from JSON")

        count = await builder.ingest_nodes()
        print(f"  Upserted: {count} nodes into Neo4j")
    finally:
        await close_driver()

asyncio.run(main())
PYEOF

    echo ""
    ok "Node upsert complete."
}

# ── Subcommand: vector ────────────────────────────────────────────────────────
cmd_vector() {
    parse_data_files "$@"
    check_python
    check_neo4j

    [[ -n "$DATA_DIR" ]] && export GRAPH_DATA_DIR="$DATA_DIR" && info "Data directory: $DATA_DIR"

    FILES_ARG="None"
    if [[ -n "$FILES_LIST" ]]; then
        FILES_ARG="[$(echo "$FILES_LIST" | sed "s/,/', '/g; s/^/'/; s/$/'/" )]"
        info "Files: $FILES_LIST"
    fi

    info "Re-embedding nodes …"
    echo ""

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

        files_override = ${FILES_ARG}
        if files_override is not None:
            builder.data_files = files_override

        builder.load_data()
        embedder = get_embedder()

        print(f"  Embedder: {embedder.model_name}")
        print(f"  Nodes to re-embed: {len(builder.nodes)}")
        print()

        node_ids = list(builder.nodes.keys())
        texts    = [builder.nodes[nid].get("embed_text", "") for nid in node_ids]

        print("  Generating embeddings …")
        t0 = time.time()
        embeddings = embedder.embed_texts(texts)
        print(f"  Done in {time.time()-t0:.1f}s")

        print("  Writing embeddings to Neo4j …")
        rows = [{"node_id": nid, "embedding": embeddings[i].tolist()}
                for i, nid in enumerate(node_ids)]
        updated = await update_embeddings_batch(rows)
        print(f"  Updated: {updated} nodes")

    finally:
        await close_driver()

asyncio.run(main())
PYEOF

    echo ""
    ok "Vector update complete."
}

# ── Subcommand: edges ─────────────────────────────────────────────────────────
cmd_edges() {
    parse_data_files "$@"
    check_python
    check_neo4j

    [[ -n "$DATA_DIR" ]] && export GRAPH_DATA_DIR="$DATA_DIR"

    info "Re-applying all edge rules (drops and recreates edges) …"
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
    ok "Edge update complete."
}

# ── Subcommand: full ──────────────────────────────────────────────────────────
cmd_full() {
    parse_data_files "$@"
    check_python
    check_neo4j

    [[ -n "$DATA_DIR" ]] && export GRAPH_DATA_DIR="$DATA_DIR" && info "Data directory: $DATA_DIR"

    FILES_ARG="None"
    if [[ -n "$FILES_LIST" ]]; then
        FILES_ARG="[$(echo "$FILES_LIST" | sed "s/,/', '/g; s/^/'/; s/$/'/" )]"
        info "Files: $FILES_LIST"
    fi

    info "Full incremental update: upsert nodes → re-embed → re-apply edges …"
    echo ""

    $PYTHON - <<PYEOF
import asyncio, time

async def main():
    from service.graph_builder import Neo4jGraphBuilder
    from service.embeddings import get_embedder
    from service.vector_search import update_embeddings_batch
    from service.graph_store import ensure_schema
    from service.neo4j_driver import close_driver, execute_write

    try:
        await ensure_schema()

        builder = Neo4jGraphBuilder()

        files_override = ${FILES_ARG}
        if files_override is not None:
            builder.data_files = files_override

        builder.load_data()
        builder.load_edge_rules()

        print(f"  Nodes loaded: {len(builder.nodes)}")
        print()

        # Step 1: Upsert nodes (without embeddings first)
        print("[1/3] Upserting nodes …")
        count = await builder.ingest_nodes()
        print(f"  Done: {count} nodes upserted")
        print()

        # Step 2: Re-embed
        print("[2/3] Generating embeddings …")
        embedder = get_embedder()
        node_ids = list(builder.nodes.keys())
        texts = [builder.nodes[nid].get("embed_text", "") for nid in node_ids]
        t0 = time.time()
        embeddings = embedder.embed_texts(texts)
        print(f"  Embedded in {time.time()-t0:.1f}s")
        rows = [{"node_id": nid, "embedding": embeddings[i].tolist()}
                for i, nid in enumerate(node_ids)]
        updated = await update_embeddings_batch(rows)
        print(f"  Updated: {updated} embeddings")
        print()

        # Step 3: Re-apply edges
        print("[3/3] Re-applying edge rules …")
        await execute_write("MATCH ()-[r:RELATED]->() DELETE r")
        edge_count = await builder.apply_edge_rules()
        print(f"  Edges created: {edge_count}")

    finally:
        await close_driver()

asyncio.run(main())
PYEOF

    echo ""
    ok "Full incremental update complete."
}

# ── Usage ─────────────────────────────────────────────────────────────────────
usage() {
    echo ""
    echo -e "${BOLD}Usage:${NC}  ./update.sh <subcommand> [options]"
    echo ""
    echo -e "${BOLD}Subcommands:${NC}"
    echo "  nodes    Upsert nodes from JSON (no DB clear, no re-embed)"
    echo "  vector   Re-embed nodes (update embeddings only)"
    echo "  edges    Re-apply edge rules (drops + recreates all edges)"
    echo "  full     nodes + vector + edges in one pass"
    echo ""
    echo -e "${BOLD}Common options:${NC}"
    echo "  --data DIR          Override data directory  (default: graph_data/)"
    echo "  --files f1,f2,...   Process specific JSON files (comma-separated)"
    echo ""
    echo -e "${BOLD}Examples:${NC}"
    echo "  ./update.sh nodes                               # upsert all data files"
    echo "  ./update.sh nodes --files kararlar_yargitay.json"
    echo "  ./update.sh vector --files new_file.json        # re-embed one file"
    echo "  ./update.sh edges                               # rebuild all edges"
    echo "  ./update.sh full                                # full incremental"
    echo "  ./update.sh full --files new_kararlar.json      # add a new source"
    echo ""
}

# ── Entrypoint ────────────────────────────────────────────────────────────────
SUBCOMMAND="${1:-}"
shift || true

case "$SUBCOMMAND" in
    nodes)  cmd_nodes  "$@" ;;
    vector) cmd_vector "$@" ;;
    edges)  cmd_edges  "$@" ;;
    full)   cmd_full   "$@" ;;
    ""|help|-h|--help) usage ;;
    *) die "Unknown subcommand: '$SUBCOMMAND'. Run './update.sh --help' for usage." ;;
esac
