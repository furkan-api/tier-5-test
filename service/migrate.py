"""
Migration script: existing JSON/GraphML/FAISS data → Neo4j.

Reads the current graph_data/ JSON files and output/ artifacts,
then writes everything into Neo4j with embeddings and edges.

Usage:
    python -m service.migrate [--clean] [--skip-embeddings]

Or from Python:
    import asyncio
    from service.migrate import migrate
    asyncio.run(migrate())
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np

from service.config import get_settings
from service.embeddings import create_embedder, BaseEmbedder
from service.graph_builder import Neo4jGraphBuilder
from service.graph_store import (
    clear_graph,
    create_edges_batch,
    ensure_schema,
    save_build_meta,
    upsert_nodes_batch_simple,
)
from service.neo4j_driver import close_driver, get_driver
from service.vector_search import update_embeddings_batch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def migrate_from_output(
    output_dir: Path | None = None,
    clean: bool = True,
) -> dict:
    """Migrate from existing output/ directory (nodes.json, edges.json, FAISS index).

    This preserves the exact same embeddings — no re-embedding needed.
    """
    s = get_settings()
    output_dir = output_dir or (Path(__file__).resolve().parent.parent / "output")

    logger.info("Migrating from %s", output_dir)
    t0 = time.time()

    # Connect
    await get_driver()
    await ensure_schema()

    if clean:
        logger.info("Clearing existing graph …")
        await clear_graph()

    # 1. Load nodes
    nodes_path = output_dir / "nodes.json"
    if not nodes_path.exists():
        raise FileNotFoundError(f"nodes.json not found at {nodes_path}")

    with open(nodes_path, "r", encoding="utf-8") as f:
        nodes_dict = json.load(f)
    logger.info("Loaded %d nodes from nodes.json", len(nodes_dict))

    # 2. Load existing FAISS embeddings (if available)
    faiss_dir = output_dir / "vector_store"
    embeddings_map: dict[str, list[float]] = {}

    if (faiss_dir / "faiss.index").exists() and (faiss_dir / "id_map.json").exists():
        try:
            import faiss
            index = faiss.read_index(str(faiss_dir / "faiss.index"))
            with open(faiss_dir / "id_map.json", "r", encoding="utf-8") as f:
                id_map = json.load(f)

            logger.info("Loaded FAISS index: %d vectors, dim=%d", index.ntotal, index.d)

            for i, nid in enumerate(id_map):
                vec = np.zeros(index.d, dtype=np.float32)
                index.reconstruct(i, vec)
                embeddings_map[nid] = vec.tolist()

            logger.info("Extracted %d embeddings from FAISS", len(embeddings_map))
        except ImportError:
            logger.warning("faiss-cpu not installed, will re-embed")
        except Exception as e:
            logger.warning("Failed to read FAISS index: %s", e)

    # 3. If no FAISS embeddings, generate fresh ones
    if not embeddings_map:
        logger.info("Generating embeddings from scratch …")
        embedder = create_embedder()
        node_ids = list(nodes_dict.keys())
        texts = [nodes_dict[nid].get("embed_text", "") for nid in node_ids]
        vecs = embedder.embed_texts(texts)
        for i, nid in enumerate(node_ids):
            embeddings_map[nid] = vecs[i].tolist()
        logger.info("Generated %d embeddings", len(embeddings_map))

    # 4. Prepare and upsert nodes with embeddings
    nodes_for_neo4j = []
    for nid, node_data in nodes_dict.items():
        node = dict(node_data)
        if nid in embeddings_map:
            node["embedding"] = embeddings_map[nid]
        nodes_for_neo4j.append(node)

    node_count = await upsert_nodes_batch_simple(nodes_for_neo4j)
    logger.info("Upserted %d nodes into Neo4j", node_count)

    # 5. Load and create edges
    edges_path = output_dir / "edges.json"
    edge_count = 0
    if edges_path.exists():
        with open(edges_path, "r", encoding="utf-8") as f:
            edges = json.load(f)
        logger.info("Loaded %d edges from edges.json", len(edges))
        edge_count = await create_edges_batch(edges)
        logger.info("Created %d edges in Neo4j", edge_count)
    else:
        logger.warning("No edges.json found. Run builder to create edges.")

    # 6. Save build metadata
    duration = time.time() - t0
    meta = {
        "version": "2.0",
        "built_at": f"migrated from {output_dir}",
        "build_duration_sec": round(duration, 2),
        "total_nodes": node_count,
        "total_edges": edge_count,
        "source": "migration",
    }
    await save_build_meta(meta)

    logger.info("Migration complete: %d nodes, %d edges in %.1fs", node_count, edge_count, duration)
    return meta


async def migrate_fresh(clean: bool = True) -> dict:
    """Full build from JSON source data → Neo4j.

    Equivalent to POST /build but runs as a CLI command.
    """
    await get_driver()
    builder = Neo4jGraphBuilder()
    meta = await builder.build(clean=clean)
    return meta


async def migrate(
    mode: str = "output",
    output_dir: Path | None = None,
    clean: bool = True,
) -> dict:
    """Main migration entrypoint."""
    try:
        if mode == "output":
            return await migrate_from_output(output_dir=output_dir, clean=clean)
        else:
            return await migrate_fresh(clean=clean)
    finally:
        await close_driver()


def main():
    parser = argparse.ArgumentParser(description="Migrate GraphRAG data to Neo4j")
    parser.add_argument(
        "--mode", choices=["output", "fresh"], default="output",
        help="'output' = migrate from output/ dir, 'fresh' = rebuild from graph_data/",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="Path to existing output/ directory (default: ./output)",
    )
    parser.add_argument(
        "--clean", action="store_true", default=True,
        help="Clear Neo4j before migration (default: True)",
    )
    parser.add_argument(
        "--no-clean", action="store_false", dest="clean",
        help="Don't clear existing data",
    )
    args = parser.parse_args()

    result = asyncio.run(migrate(
        mode=args.mode,
        output_dir=args.output_dir,
        clean=args.clean,
    ))

    print("\n✓ Migration complete:")
    for k, v in result.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
