from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://legal_rag:legal_rag_dev@localhost:5432/legal_rag"
    milvus_uri: str = "http://10.20.47.192:19530"
    openai_api_key: str = ""
    gemini_api_key: str = ""
    embedding_base_url: str | None = None
    embedding_model: str = "gemini-embedding-2-preview"
    embedding_dimension: int = 3072
    collection_name: str = "chunks"
    chunk_max_tokens: int = 1024
    chunk_overlap: int = 50
    corpus_dir: Path = Path(__file__).resolve().parent.parent.parent / "corpus"
    neo4j_uri: str = "bolt://10.20.32.34:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    graph_expansion_hops: int = 1
    ppr_alpha: float = 0.85
    graph_score_weight: float = 0.3

    # AWS S3 settings for bucket_download
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-east-1"
    s3_bucket_name: str = ""
    s3_prefix: str = ""
    s3_embedded_prefix: str = ""

    # MongoDB
    mongo_url: str = ""

    model_config = {"env_file": ".env", "extra": "ignore", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
