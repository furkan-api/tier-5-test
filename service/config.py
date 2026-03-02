"""
Centralised configuration for the GraphRAG service.

All settings are read from environment variables (or .env file).
Pydantic Settings provides validation, type coercion, and documentation.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Application settings — loaded from env vars / .env file."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Neo4j ─────────────────────────────────────────────────────────────
    neo4j_uri: str = Field(
        default="bolt://localhost:7687",
        description="Neo4j Bolt URI",
    )
    neo4j_user: str = Field(default="neo4j")
    neo4j_password: SecretStr = Field(default=SecretStr("neo4j"))
    neo4j_database: str = Field(
        default="neo4j",
        description="Neo4j database name (Enterprise: per-tenant DBs)",
    )
    neo4j_max_connection_pool_size: int = Field(default=50)
    neo4j_connection_acquisition_timeout: float = Field(default=60.0)

    # ── Embedding ─────────────────────────────────────────────────────────
    embedding_provider: Literal["sentence_transformer", "openai"] = Field(
        default="sentence_transformer",
    )
    embedding_model: str = Field(
        default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    )
    embedding_dimension: int = Field(default=384)
    embedding_batch_size: int = Field(default=64)
    openai_api_key: Optional[SecretStr] = Field(default=None)

    # ── Vector index ──────────────────────────────────────────────────────
    vector_index_name: str = Field(
        default="node_embedding_index",
        description="Name of the Neo4j vector index",
    )
    vector_similarity_function: Literal["cosine", "euclidean"] = Field(
        default="cosine",
    )

    # ── Query defaults ────────────────────────────────────────────────────
    query_top_k: int = Field(default=10, ge=1, le=100)
    query_expand_hops: int = Field(default=2, ge=0, le=5)
    query_max_expanded: int = Field(default=50, ge=1, le=500)
    query_score_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    query_max_context_chars: int = Field(default=8000, ge=100, le=50000)

    # ── Graph data (for build/migration) ──────────────────────────────────
    graph_data_dir: Path = Field(default=PROJECT_ROOT / "graph_data")
    ontology_path: Optional[Path] = None
    edge_rules_path: Optional[Path] = None
    data_files: list[str] = Field(
        default=[],
        description="Legacy flat data files (backward compat). "
        "Production builds use ontology.json → build_config → node_files.",
    )

    # ── API ───────────────────────────────────────────────────────────────
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)
    log_level: str = Field(default="INFO")

    # ── Computed ──────────────────────────────────────────────────────────
    @field_validator("graph_data_dir", mode="before")
    @classmethod
    def _default_graph_data_dir(cls, v):
        if v is None or (isinstance(v, str) and v.strip() == ""):
            return PROJECT_ROOT / "graph_data"
        return v

    @field_validator("edge_rules_path", mode="before")
    @classmethod
    def _default_edge_rules(cls, v, info):
        if v is None or (isinstance(v, str) and v.strip() == ""):
            gd = info.data.get("graph_data_dir", PROJECT_ROOT / "graph_data")
            return Path(gd) / "edges" / "edge_rules.json"
        return v

    @field_validator("ontology_path", mode="before")
    @classmethod
    def _default_ontology(cls, v, info):
        if v is None or (isinstance(v, str) and v.strip() == ""):
            gd = info.data.get("graph_data_dir", PROJECT_ROOT / "graph_data")
            return Path(gd) / "ontology.json"
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached singleton settings."""
    return Settings()
