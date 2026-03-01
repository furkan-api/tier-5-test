"""
GraphRAG Main Entry Point.

Usage:
    # Build graph
    python -m src.main build

    # Run a query
    python -m src.main query "muvazaa nedeniyle tapu iptali"

    # Show statistics
    python -m src.main stats

Environment Variables:
    EMBEDDING_PROVIDER  : sentence_transformer | openai  (default: sentence_transformer)
    EMBEDDING_MODEL     : Model name (default: paraphrase-multilingual-MiniLM-L12-v2)
    OPENAI_API_KEY      : API key if using OpenAI
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import sys
from pathlib import Path

from .config import (
    DEFAULT_EMBEDDING_PROVIDER,
    DEFAULT_EMBEDDING_MODEL,
    EMBEDDING_REGISTRY,
    OUTPUT_DIR,
    EDGE_RULES_PATH,
    GRAPH_DATA_DIR,
    LOG_LEVEL,
    QUERY_TOP_K,
    QUERY_EXPAND_HOPS,
    QUERY_MAX_CONTEXT_CHARS,
)
from .embeddings.base import BaseEmbedder
from .graph_builder import GraphBuilder
from .graph_rag import GraphRAG

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Embedding factory
# ──────────────────────────────────────────────────────────────────────────────

def create_embedder(
    provider: str | None = None,
    model_name: str | None = None,
    **kwargs,
) -> BaseEmbedder:
    """
    Create an embedding provider based on configuration.

    Args:
        provider: Key in EMBEDDING_REGISTRY (e.g. 'sentence_transformer', 'openai').
        model_name: Model name to use. If None, the registry default is used.
        **kwargs: Additional parameters forwarded to the embedding class.
    """
    provider = provider or DEFAULT_EMBEDDING_PROVIDER

    if provider not in EMBEDDING_REGISTRY:
        raise ValueError(
            f"Unknown embedding provider: '{provider}'. "
            f"Supported: {list(EMBEDDING_REGISTRY.keys())}"
        )

    reg = EMBEDDING_REGISTRY[provider]
    module = importlib.import_module(reg["module"])
    cls = getattr(module, reg["class"])

    params = dict(reg["default_params"])
    if model_name:
        params["model_name"] = model_name
    params.update(kwargs)

    return cls(**params)


# ──────────────────────────────────────────────────────────────────────────────
# CLI commands
# ──────────────────────────────────────────────────────────────────────────────

def cmd_build(args: argparse.Namespace) -> None:
    """Build the graph and save to disk."""
    embedder = create_embedder(provider=args.provider, model_name=args.model)

    builder = GraphBuilder(
        embedder=embedder,
        edge_rules_path=args.rules,
        data_dir=args.data_dir,
    )

    graph = builder.build()

    output_dir = Path(args.output)
    builder.save(output_dir)

    print(f"\n✓ Graph built and saved: {output_dir}")
    print(f"  Nodes : {graph.number_of_nodes()}")
    print(f"  Edges : {graph.number_of_edges()}")
    print(f"  Embed : {embedder}")


def cmd_query(args: argparse.Namespace) -> None:
    """Run a query against the saved graph."""
    output_dir = Path(args.output)
    if not (output_dir / "graph.graphml").exists():
        print("Error: Build the graph first using the 'build' command.")
        sys.exit(1)

    embedder = create_embedder(provider=args.provider, model_name=args.model)
    rag = GraphRAG.load(output_dir, embedder)

    result = rag.query(
        query_text=args.query_text,
        top_k=args.top_k,
        expand_hops=args.hops,
    )

    print("\n" + "=" * 70)
    print(result.summary())
    print("=" * 70)

    if args.verbose:
        print("\n--- Context ---")
        print(result.to_context_string(max_chars=args.max_chars))
    else:
        print("\nSeed nodes:")
        for nid, score in result.seed_nodes[:10]:
            node_type = rag.nodes.get(nid, {}).get("node_type", "?")
            text_preview = rag.nodes.get(nid, {}).get("embed_text", "")[:80]
            print(f"  [{score:.3f}] ({node_type}) {nid}")
            print(f"          {text_preview}...")

    # JSON output
    if args.json_output:
        out = {
            "query": result.query,
            "seed_nodes": [{"id": nid, "score": s} for nid, s in result.seed_nodes],
            "expanded_count": len(result.expanded_nodes),
            "subgraph_nodes": result.subgraph.number_of_nodes(),
            "subgraph_edges": result.subgraph.number_of_edges(),
            "context": result.context_texts,
        }
        json_path = output_dir / "last_query_result.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"\nJSON output: {json_path}")


def cmd_stats(args: argparse.Namespace) -> None:
    """Display graph statistics."""
    import networkx as nx

    output_dir = Path(args.output)
    if not (output_dir / "graph.graphml").exists():
        print("Error: Build the graph first using the 'build' command.")
        sys.exit(1)

    graph = nx.read_graphml(str(output_dir / "graph.graphml"))

    # Node types
    node_types: dict[str, int] = {}
    for nid, data in graph.nodes(data=True):
        nt = data.get("node_type", "unknown")
        node_types[nt] = node_types.get(nt, 0) + 1

    # Edge types
    edge_types: dict[str, int] = {}
    for u, v, data in graph.edges(data=True):
        et = data.get("edge_type", "unknown")
        edge_types[et] = edge_types.get(et, 0) + 1

    print(f"\n{'='*50}")
    print(f"  GraphRAG Statistics")
    print(f"{'='*50}")
    print(f"  Total nodes  : {graph.number_of_nodes()}")
    print(f"  Total edges  : {graph.number_of_edges()}")
    print()
    print("  Node types:")
    for nt, cnt in sorted(node_types.items(), key=lambda x: -x[1]):
        print(f"    {nt:20s} : {cnt}")
    print()
    print("  Edge types:")
    for et, cnt in sorted(edge_types.items(), key=lambda x: -x[1]):
        print(f"    {et:20s} : {cnt}")

    # Degree statistics
    degrees = [d for _, d in graph.degree()]
    if degrees:
        avg_deg = sum(degrees) / len(degrees)
        print(f"\n  Average degree    : {avg_deg:.1f}")
        print(f"  Max degree        : {max(degrees)}")
        print(f"  Isolated nodes    : {degrees.count(0)}")

    # Connected components (undirected)
    ug = graph.to_undirected()
    components = list(nx.connected_components(ug))
    print(f"  Components        : {len(components)}")
    if components:
        largest = max(len(c) for c in components)
        print(f"  Largest component : {largest} nodes")


# ──────────────────────────────────────────────────────────────────────────────
# Argparse
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="GraphRAG - Graph-based RAG for Turkish Legal System",
    )
    parser.add_argument(
        "--provider", "-p",
        default=None,
        help=f"Embedding provider: {list(EMBEDDING_REGISTRY.keys())} (default: {DEFAULT_EMBEDDING_PROVIDER})",
    )
    parser.add_argument(
        "--model", "-m",
        default=None,
        help="Embedding model name (default: registry default)",
    )
    parser.add_argument(
        "--output", "-o",
        default=str(OUTPUT_DIR),
        help=f"Output directory (default: {OUTPUT_DIR})",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command")

    # ── build ──
    sp_build = subparsers.add_parser("build", help="Build and save the graph")
    sp_build.add_argument("--data-dir", default=str(GRAPH_DATA_DIR), help="Data directory")
    sp_build.add_argument("--rules", default=str(EDGE_RULES_PATH), help="Edge rules file")

    # ── query ──
    sp_query = subparsers.add_parser("query", help="Run a query on the graph")
    sp_query.add_argument("query_text", help="Query text")
    sp_query.add_argument("--top-k", type=int, default=QUERY_TOP_K, help=f"Vector search top-K (default: {QUERY_TOP_K})")
    sp_query.add_argument("--hops", type=int, default=QUERY_EXPAND_HOPS, help=f"Graph expansion hops (default: {QUERY_EXPAND_HOPS})")
    sp_query.add_argument("--verbose", "-v", action="store_true", help="Verbose context output")
    sp_query.add_argument("--max-chars", type=int, default=QUERY_MAX_CONTEXT_CHARS, help=f"Context character limit (default: {QUERY_MAX_CONTEXT_CHARS})")
    sp_query.add_argument("--json-output", action="store_true", help="Also write to JSON file")

    # ── stats ──
    sp_stats = subparsers.add_parser("stats", help="Show graph statistics")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.command == "build":
        cmd_build(args)
    elif args.command == "query":
        cmd_query(args)
    elif args.command == "stats":
        cmd_stats(args)


if __name__ == "__main__":
    main()
