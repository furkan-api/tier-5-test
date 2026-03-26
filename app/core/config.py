from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://legal_rag:legal_rag_dev@localhost:5432/legal_rag"
    milvus_uri: str = "http://localhost:19530"
    openai_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = 1536
    collection_name: str = "chunks"
    chunk_max_tokens: int = 512
    chunk_overlap: int = 50
    corpus_dir: Path = Path(__file__).resolve().parent.parent.parent / "corpus"

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
