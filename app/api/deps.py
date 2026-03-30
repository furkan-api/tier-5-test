from functools import lru_cache
from typing import Generator

from neo4j import Driver, Session
from pymilvus import Collection

from app.core.config import Settings, get_settings
from app.core.graphdb import connect_neo4j
from app.core.vectordb import get_collection


@lru_cache
def get_milvus_collection() -> Collection:
    return get_collection()


def get_current_settings() -> Settings:
    return get_settings()


@lru_cache
def get_neo4j_driver() -> Driver:
    return connect_neo4j()


def get_neo4j_session() -> Generator[Session, None, None]:
    """FastAPI dependency: yields a fresh Neo4j session per request."""
    driver = get_neo4j_driver()
    session = driver.session(database="neo4j")
    try:
        yield session
    finally:
        session.close()
