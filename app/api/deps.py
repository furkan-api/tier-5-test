from functools import lru_cache

from pymilvus import Collection

from app.core.config import Settings, get_settings
from app.core.vectordb import get_collection


@lru_cache
def get_milvus_collection() -> Collection:
    return get_collection()


def get_current_settings() -> Settings:
    return get_settings()
