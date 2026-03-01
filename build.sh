#!/usr/bin/env bash
# build.sh — Build a fresh graph from the current dataset.
# Usage: ./build.sh
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

PYTHON="$DIR/venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
    echo "ERROR: venv not found. Run: python3 -m venv venv && venv/bin/pip install -r requirements.txt"
    exit 1
fi

# Remove previous build so we start fresh
if [[ -d "$DIR/output" ]]; then
    echo "Removing previous build …"
    rm -rf "$DIR/output" "$DIR/output_prev"
fi

echo "Building graph from graph_data/ …"
$PYTHON -c "
from src.graph_store import GraphStore
from src.main import create_embedder

embedder = create_embedder()
store   = GraphStore()
m       = store.build(embedder)

print()
print(f'Build complete in {m.build_duration_sec}s')
print(f'  Nodes: {m.total_nodes}  Edges: {m.total_edges}')
print(f'  Node types: {m.node_types}')
print(f'  Edge types: {m.edge_types}')
print(f'  Manifest saved to output/manifest.json')
"

echo "Done."
