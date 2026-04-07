from __future__ import annotations

from pymilvus import MilvusClient

from app.core.config import get_settings

_client: MilvusClient | None = None


def connect_milvus(uri: str | None = None) -> MilvusClient:
    """Connect to Milvus via REST (MilvusClient). Safe to call multiple times."""
    global _client
    if _client is not None:
        return _client
    milvus_uri = uri or get_settings().milvus_uri
    _client = MilvusClient(uri=milvus_uri)
    return _client


def get_client() -> MilvusClient:
    """Get the MilvusClient instance, connecting if needed."""
    if _client is None:
        connect_milvus()
    return _client
