from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://legal_rag:legal_rag_dev@localhost:5432/legal_rag"
    milvus_uri: str = "http://localhost:19530"
    openai_api_key: str = ""
    embedding_base_url: str = "http://localhost:8080"
    embedding_model: str = "BAAI/bge-m3"
    embedding_dimension: int = 1024
    collection_name: str = "chunks"
    chunk_max_tokens: int = 512
    chunk_overlap: int = 50
    corpus_dir: Path = Path(__file__).resolve().parent.parent.parent / "corpus"
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "legal_rag_neo4j"
    graph_expansion_hops: int = 1
    ppr_alpha: float = 0.85
    graph_score_weight: float = 0.3

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
