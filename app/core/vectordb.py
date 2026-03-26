from __future__ import annotations

from pymilvus import Collection, connections, utility

from app.core.config import get_settings


def connect_milvus(uri: str | None = None):
    """Connect to Milvus. Safe to call multiple times."""
    milvus_uri = uri or get_settings().milvus_uri
    connections.connect(uri=milvus_uri)


def get_collection(name: str | None = None) -> Collection:
    """Get a Milvus collection by name. Connects if needed."""
    collection_name = name or get_settings().collection_name
    connect_milvus()
    if not utility.has_collection(collection_name):
        raise RuntimeError(f"Collection '{collection_name}' not found. Run embedding pipeline first.")
    collection = Collection(collection_name)
    collection.load()
    return collection
