"""
Entry point for running the service.

Usage:
    python -m service                  # Start API server
    python -m service build            # Build graph
    python -m service migrate          # Migrate from old output/
    python -m service stats            # Show stats
    python -m service mcp              # Start MCP server
"""

from __future__ import annotations

import sys


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("serve", "api"):
        # Start API server
        import uvicorn
        from service.config import get_settings

        s = get_settings()
        uvicorn.run(
            "service.api:app",
            host=s.api_host,
            port=s.api_port,
            log_level=s.log_level.lower(),
            reload=False,
        )

    elif sys.argv[1] == "build":
        import asyncio
        from service.graph_builder import Neo4jGraphBuilder
        from service.neo4j_driver import close_driver, get_driver

        async def _build():
            await get_driver()
            builder = Neo4jGraphBuilder()
            clean = "--no-clean" not in sys.argv
            meta = await builder.build(clean=clean)
            await close_driver()
            return meta

        meta = asyncio.run(_build())
        print(f"\n✓ Build complete: {meta.get('total_nodes')} nodes, "
              f"{meta.get('total_edges')} edges in {meta.get('build_duration_sec')}s")

    elif sys.argv[1] == "migrate":
        from service.migrate import main as migrate_main
        # Pass remaining args
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        migrate_main()

    elif sys.argv[1] == "stats":
        import asyncio
        import json
        from service.query_engine import GraphRAGEngine
        from service.neo4j_driver import close_driver, get_driver

        async def _stats():
            await get_driver()
            engine = GraphRAGEngine()
            stats = await engine.get_stats()
            await close_driver()
            return stats

        stats = asyncio.run(_stats())
        print(json.dumps(stats, indent=2, ensure_ascii=False))

    elif sys.argv[1] == "mcp":
        from service.mcp_server import main as mcp_main
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        mcp_main()

    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
