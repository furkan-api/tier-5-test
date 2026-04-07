import logging
from functools import lru_cache
from typing import Generator

from pymilvus import MilvusClient

from app.core.config import Settings, get_settings
from app.core.vectordb import get_client

log = logging.getLogger(__name__)


@lru_cache
def get_milvus_client() -> MilvusClient:
    return get_client()


def get_current_settings() -> Settings:
    return get_settings()


def get_neo4j_session() -> Generator:
    """FastAPI dependency: yields a Neo4j session, or None if unavailable."""
    session = None
    try:
        from app.core.graphdb import get_neo4j_driver
        driver = get_neo4j_driver()
        session = driver.session(database="neo4j")
    except Exception as e:
        log.warning("Neo4j session unavailable: %s — graph retrieval disabled", e)

    try:
        yield session
    finally:
        if session is not None:
            session.close()
