from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from neo4j import GraphDatabase, Driver, Session, WRITE_ACCESS

from app.core.config import get_settings

_driver: Driver | None = None


def connect_neo4j(
    uri: str | None = None,
    user: str | None = None,
    password: str | None = None,
) -> Driver:
    """Connect to Neo4j. Safe to call multiple times (no-op if already connected)."""
    global _driver
    if _driver is not None:
        return _driver
    settings = get_settings()
    _driver = GraphDatabase.driver(
        uri or settings.neo4j_uri,
        auth=(user or settings.neo4j_user, password or settings.neo4j_password),
        keep_alive=True,
    )
    _driver.verify_connectivity()
    return _driver


def get_neo4j_driver() -> Driver:
    """Return the singleton Neo4j driver, connecting if needed."""
    global _driver
    if _driver is None:
        return connect_neo4j()
    return _driver


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Context manager yielding a Neo4j session. Use for one unit of work."""
    driver = get_neo4j_driver()
    session = driver.session(database="neo4j", default_access_mode=WRITE_ACCESS)
    try:
        yield session
    finally:
        session.close()


def close_neo4j() -> None:
    """Close the Neo4j driver. Called on application shutdown."""
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None
