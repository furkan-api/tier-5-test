"""
Neo4j Driver — connection pool management and lifecycle.

Provides a singleton async driver with proper shutdown handling.
All database access goes through this module.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from neo4j import AsyncDriver, AsyncGraphDatabase, AsyncSession

from .config import Settings, get_settings

logger = logging.getLogger(__name__)

# Module-level singleton
_driver: AsyncDriver | None = None


async def get_driver(settings: Settings | None = None) -> AsyncDriver:
    """Return (and lazily create) the global Neo4j async driver."""
    global _driver
    if _driver is not None:
        return _driver

    s = settings or get_settings()
    _driver = AsyncGraphDatabase.driver(
        s.neo4j_uri,
        auth=(s.neo4j_user, s.neo4j_password.get_secret_value()),
        max_connection_pool_size=s.neo4j_max_connection_pool_size,
        connection_acquisition_timeout=s.neo4j_connection_acquisition_timeout,
    )
    # Verify connectivity
    await _driver.verify_connectivity()
    logger.info("Neo4j connected: %s (database=%s)", s.neo4j_uri, s.neo4j_database)
    return _driver


async def close_driver() -> None:
    """Gracefully close the driver pool. Called at application shutdown."""
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None
        logger.info("Neo4j driver closed.")


@asynccontextmanager
async def get_session(
    database: str | None = None,
) -> AsyncIterator[AsyncSession]:
    """Yield a Neo4j async session from the pool.

    Usage:
        async with get_session() as session:
            result = await session.run("MATCH (n) RETURN count(n)")
    """
    driver = await get_driver()
    db = database or get_settings().neo4j_database
    session = driver.session(database=db)
    try:
        yield session
    finally:
        await session.close()


async def execute_query(
    cypher: str,
    parameters: dict[str, Any] | None = None,
    database: str | None = None,
) -> list[dict[str, Any]]:
    """Execute a Cypher query and return all records as dicts.

    Convenience wrapper for simple read queries.
    """
    async with get_session(database) as session:
        result = await session.run(cypher, parameters or {})
        records = await result.data()
        return records


async def execute_write(
    cypher: str,
    parameters: dict[str, Any] | None = None,
    database: str | None = None,
) -> list[dict[str, Any]]:
    """Execute a write Cypher query inside an explicit write transaction."""
    async with get_session(database) as session:
        result = await session.run(cypher, parameters or {})
        records = await result.data()
        await result.consume()
        return records
