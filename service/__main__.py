"""
Entry point for running the service.

Usage:
    python -m service                       # Start API server
    python -m service build [--no-clean]    # Build graph
    python -m service embed                 # Embed data (cache-aware)
    python -m service update                # Incremental graph update
    python -m service migrate               # Migrate from old output/
    python -m service stats                 # Show stats
    python -m service mcp                   # Start MCP server
    python -m service pipeline <cmd>        # Full pipeline CLI
"""

from __future__ import annotations

import sys


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"

    if cmd in ("serve", "api"):
        import uvicorn
        from .config import get_settings

        s = get_settings()
        uvicorn.run(
            "service.api:app",
            host=s.api_host,
            port=s.api_port,
            log_level=s.log_level.lower(),
            reload=False,
        )

    elif cmd == "build":
        import asyncio
        from .graph_builder import Neo4jGraphBuilder
        from .embedding_cache import EmbeddingCache
        from .neo4j_driver import close_driver, get_driver

        async def _build():
            await get_driver()
            cache = EmbeddingCache.load()
            builder = Neo4jGraphBuilder()
            clean = "--no-clean" not in sys.argv
            meta = await builder.build(clean=clean, cache=cache)
            await close_driver()
            return meta

        meta = asyncio.run(_build())
        print(f"\n✓ Build complete: {meta.get('total_nodes')} nodes, "
              f"{meta.get('total_edges')} edges in {meta.get('build_duration_sec')}s")

    elif cmd == "embed":
        import asyncio
        from .graph_builder import Neo4jGraphBuilder
        from .embedding_cache import EmbeddingCache

        async def _embed():
            cache = EmbeddingCache.load()
            builder = Neo4jGraphBuilder()
            return await builder.embed_only(cache=cache)

        result = asyncio.run(_embed())
        print(f"\n✓ Embedding complete: {result.get('cached_vectors', 0)} cached, "
              f"{result.get('newly_embedded', 0)} newly embedded")

    elif cmd == "update":
        import asyncio
        from .graph_builder import Neo4jGraphBuilder
        from .embedding_cache import EmbeddingCache
        from .neo4j_driver import close_driver, get_driver

        async def _update():
            await get_driver()
            cache = EmbeddingCache.load()
            builder = Neo4jGraphBuilder()
            meta = await builder.update(cache=cache)
            await close_driver()
            return meta

        meta = asyncio.run(_update())
        print(f"\n✓ Update complete: {meta.get('total_nodes')} nodes, "
              f"{meta.get('total_edges')} edges")

    elif cmd == "migrate":
        from .migrate import main as migrate_main
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        migrate_main()

    elif cmd == "stats":
        import asyncio
        import json
        from .query_engine import GraphRAGEngine
        from .neo4j_driver import close_driver, get_driver

        async def _stats():
            await get_driver()
            engine = GraphRAGEngine()
            stats = await engine.get_stats()
            await close_driver()
            return stats

        stats = asyncio.run(_stats())
        print(json.dumps(stats, indent=2, ensure_ascii=False))

    elif cmd == "mcp":
        from .mcp_server import main as mcp_main
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        mcp_main()

    elif cmd == "pipeline":
        from .pipeline import main as pipeline_main
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        pipeline_main()

    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
