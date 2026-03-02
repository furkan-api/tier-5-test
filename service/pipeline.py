#!/usr/bin/env python3
"""
GraphRAG Pipeline — single entry-point for all graph operations.

Usage:
    python pipeline.py embed             # 1. Embed data (cache-aware)
    python pipeline.py build             # 2. Full build (embed + graph)
    python pipeline.py embed-missing     # 3. Embed only missing/changed
    python pipeline.py update            # 4. Incremental graph update
    python pipeline.py validate          # 5. Validate data against ontology
    python pipeline.py cache-info        # 6. Show embedding cache stats
    python pipeline.py similarity        # 7. Create similarity edges (post-build)

Options:
    --clean / --no-clean    Clear graph before build (default: --clean)
    --prune                 Remove stale cache entries
    --verbose / -v          Debug-level logging
    --dry-run               Validate + count without writing to Neo4j

Examples:
    # First time: embed everything and build the graph
    python pipeline.py build

    # Add new data files → embed only new, update graph
    python pipeline.py update

    # Pre-compute embeddings offline (no Neo4j needed)
    python pipeline.py embed

    # Check data quality before building
    python pipeline.py validate
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

from .config import get_settings
from .embedding_cache import EmbeddingCache
from .embeddings import get_embedder
from .graph_builder import Neo4jGraphBuilder, create_similarity_edges_neo4j
from .neo4j_driver import close_driver, get_driver

logger = logging.getLogger("pipeline")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet noisy libraries
    for noisy in ("neo4j", "httpx", "httpcore", "urllib3", "sentence_transformers"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _print_header(title: str) -> None:
    w = 60
    print(f"\n{'━' * w}")
    print(f"  {title}")
    print(f"{'━' * w}")


def _print_result(data: dict, indent: int = 2) -> None:
    for k, v in data.items():
        if isinstance(v, dict):
            print(f"{'':>{indent}}{k}:")
            _print_result(v, indent + 4)
        else:
            print(f"{'':>{indent}}{k}: {v}")


# ── Commands ──────────────────────────────────────────────────────────────────

async def cmd_embed(args: argparse.Namespace) -> None:
    """Embed all data using the cache (skips already-embedded texts)."""
    _print_header("EMBED — Vectorize Data")

    builder = Neo4jGraphBuilder()
    cache = EmbeddingCache.load()

    result = await builder.embed_only(cache=cache)

    _print_header("Embedding Result")
    _print_result(result)


async def cmd_build(args: argparse.Namespace) -> None:
    """Full pipeline: embed + build graph in Neo4j."""
    _print_header(f"BUILD — Full Graph Pipeline (clean={args.clean})")

    await get_driver()
    cache = EmbeddingCache.load()

    builder = Neo4jGraphBuilder()

    if args.dry_run:
        result = await builder.build(validate_only=True)
        _print_header("Dry-Run Result (no writes)")
        _print_result(result)
        return

    result = await builder.build(clean=args.clean, cache=cache)

    if args.similarity:
        _print_header("Creating Similarity Edges")
        sim_count = await create_similarity_edges_neo4j()
        result["similarity_edges"] = sim_count

    _print_header("Build Result")
    _print_result(result)


async def cmd_embed_missing(args: argparse.Namespace) -> None:
    """Embed only missing or changed texts (no Neo4j needed)."""
    _print_header("EMBED-MISSING — Incremental Embedding")

    cache = EmbeddingCache.load()
    builder = Neo4jGraphBuilder()

    result = await builder.embed_only(cache=cache)

    if args.prune:
        builder.load_ontology()
        builder.load_nodes()
        pruned = cache.prune(set(builder.nodes.keys()))
        cache.save()
        result["pruned_stale_entries"] = pruned

    _print_header("Embedding Result")
    _print_result(result)


async def cmd_update(args: argparse.Namespace) -> None:
    """Incremental update: embed missing + upsert into Neo4j."""
    _print_header("UPDATE — Incremental Graph Update")

    await get_driver()
    cache = EmbeddingCache.load()

    builder = Neo4jGraphBuilder()
    result = await builder.update(cache=cache)

    if args.similarity:
        _print_header("Updating Similarity Edges")
        sim_count = await create_similarity_edges_neo4j()
        result["similarity_edges"] = sim_count

    _print_header("Update Result")
    _print_result(result)


async def cmd_validate(args: argparse.Namespace) -> None:
    """Validate data against ontology without building."""
    _print_header("VALIDATE — Data Quality Check")

    builder = Neo4jGraphBuilder()
    result = await builder.build(validate_only=True)

    _print_header("Validation Result")
    _print_result({
        "nodes": result["nodes"],
        "explicit_edges": result["explicit_edges"],
        "edge_rules": result["edge_rules"],
        "validation_errors": result["validation_errors"],
    })

    if result["errors"]:
        print(f"\n  Errors (first {len(result['errors'])}):")
        for err in result["errors"]:
            print(f"    ⚠ {err}")
    else:
        print("\n  ✓ All data valid.")


async def cmd_cache_info(args: argparse.Namespace) -> None:
    """Show embedding cache statistics."""
    _print_header("CACHE INFO")

    cache = EmbeddingCache.load()
    summary = cache.summary()

    _print_result(summary)

    if args.prune:
        builder = Neo4jGraphBuilder()
        builder.load_ontology()
        builder.load_nodes()
        pruned = cache.prune(set(builder.nodes.keys()))
        cache.save()
        print(f"\n  Pruned {pruned} stale entries.")


async def cmd_similarity(args: argparse.Namespace) -> None:
    """Create similarity edges using Neo4j's vector index (post-build)."""
    _print_header("SIMILARITY — Vector-Based Edges")

    await get_driver()

    count = await create_similarity_edges_neo4j(
        threshold=args.threshold,
        max_neighbors=args.max_neighbors,
    )

    print(f"\n  Created {count} similarity edges.")


# ── Main ──────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline",
        description="GraphRAG Pipeline — embed, build, update, validate",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Debug-level logging",
    )

    sub = parser.add_subparsers(dest="command", help="Pipeline command")

    # embed
    p_embed = sub.add_parser(
        "embed",
        help="Vectorize/embed all data (cache-aware, skip already-embedded)",
    )

    # build
    p_build = sub.add_parser(
        "build",
        help="Full build: embed + graph generation",
    )
    p_build.add_argument(
        "--clean", action="store_true", default=True,
        help="Clear graph before build (default)",
    )
    p_build.add_argument(
        "--no-clean", action="store_false", dest="clean",
        help="Don't clear existing graph",
    )
    p_build.add_argument(
        "--dry-run", action="store_true",
        help="Validate only, no writes",
    )
    p_build.add_argument(
        "--similarity", action="store_true",
        help="Also create similarity edges after build",
    )

    # embed-missing
    p_missing = sub.add_parser(
        "embed-missing",
        help="Embed only missing/changed texts (no Neo4j needed)",
    )
    p_missing.add_argument(
        "--prune", action="store_true",
        help="Remove stale cache entries for deleted nodes",
    )

    # update
    p_update = sub.add_parser(
        "update",
        help="Incremental: embed missing + upsert new/changed into Neo4j",
    )
    p_update.add_argument(
        "--similarity", action="store_true",
        help="Also create/update similarity edges",
    )

    # validate
    sub.add_parser(
        "validate",
        help="Validate data against ontology without building",
    )

    # cache-info
    p_cache = sub.add_parser(
        "cache-info",
        help="Show embedding cache statistics",
    )
    p_cache.add_argument(
        "--prune", action="store_true",
        help="Remove stale cache entries",
    )

    # similarity
    p_sim = sub.add_parser(
        "similarity",
        help="Create similarity edges via Neo4j vector index (post-build)",
    )
    p_sim.add_argument(
        "--threshold", type=float, default=0.82,
        help="Minimum similarity score (default: 0.82)",
    )
    p_sim.add_argument(
        "--max-neighbors", type=int, default=5,
        help="Max similar neighbors per node (default: 5)",
    )

    return parser


COMMAND_MAP = {
    "embed": cmd_embed,
    "build": cmd_build,
    "embed-missing": cmd_embed_missing,
    "update": cmd_update,
    "validate": cmd_validate,
    "cache-info": cmd_cache_info,
    "similarity": cmd_similarity,
}


async def run(args: argparse.Namespace) -> None:
    handler = COMMAND_MAP.get(args.command)
    if handler is None:
        build_parser().print_help()
        return

    try:
        await handler(args)
    finally:
        try:
            await close_driver()
        except Exception:
            pass


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    _setup_logging(args.verbose)
    t0 = time.time()

    asyncio.run(run(args))

    duration = time.time() - t0
    print(f"\n  Done in {duration:.1f}s.\n")


if __name__ == "__main__":
    main()
