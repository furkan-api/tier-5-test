#!/usr/bin/env bash
# update.sh — Update the existing graph with a (possibly changed) dataset.
# Usage: ./update.sh [path/to/graph_data]
#
# If a path is given it is set as GRAPH_DATA_DIR for the build.
# The previous build is kept as output_prev/ for rollback.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

PYTHON="$DIR/venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
    echo "ERROR: venv not found. Run: python3 -m venv venv && venv/bin/pip install -r requirements.txt"
    exit 1
fi

# Optional: override data directory
DATA_DIR="${1:-}"
if [[ -n "$DATA_DIR" ]]; then
    export GRAPH_DATA_DIR="$DATA_DIR"
    echo "Using dataset: $DATA_DIR"
else
    echo "Using default dataset (graph_data/)"
fi

if [[ -d "$DIR/output" ]]; then
    echo "Previous build exists — it will be kept as output_prev/"
else
    echo "No previous build found — performing initial build."
fi

echo "Rebuilding graph …"
$PYTHON -c "
from src.graph_store import GraphStore
from src.main import create_embedder

embedder = create_embedder()
store   = GraphStore()
m       = store.build(embedder)

print()
print(f'Update complete in {m.build_duration_sec}s')
print(f'  Nodes: {m.total_nodes}  Edges: {m.total_edges}')
print(f'  Node types: {m.node_types}')
print(f'  Edge types: {m.edge_types}')
print(f'  Previous build backed up to output_prev/')
"

echo "Done."
