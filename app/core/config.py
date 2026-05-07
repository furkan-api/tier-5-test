from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://legal_rag:legal_rag_dev@localhost:5432/legal_rag"
    milvus_uri: str = "http://10.20.47.192:19530"
    openai_api_key: str = ""
    gemini_api_key: str = ""
    anthropic_api_key: str = ""
    embedding_base_url: str | None = None
    embedding_model: str = "gemini-embedding-2-preview"
    embedding_dimension: int = 3072
    collection_name: str = "chunks"
    chunk_max_tokens: int = 1024
    chunk_overlap: int = 50
    corpus_dir: Path = Path(__file__).resolve().parent.parent.parent / "corpus"
    neo4j_uri: str = "neo4j://10.20.32.34:7687"
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

    # LLM-based decision extraction (app.ingestion.llm_process)
    #
    # The defaults below act as the global fallback for every stage of the
    # staged extraction pipeline (`metadata`, `summary`,
    # `cited_court_decisions`, `cited_law_articles`). Each stage can override
    # any of (model, base_url, api_key) via its dedicated setting; an unset
    # stage-specific value falls back to the global default.
    llm_extract_model: str = "gemini-2.5-flash-lite"
    # When set, switch from the native Gemini SDK to an OpenAI-compatible
    # client pointed at this base URL. Works with Ollama, vLLM, LM Studio,
    # llama.cpp's server, and Gemini's /v1beta/openai/ endpoint.
    llm_extract_base_url: str | None = None
    # API key for the OpenAI-compatible endpoint. Local servers usually
    # accept any non-empty value (e.g. "ollama"); falls back to gemini_api_key.
    llm_extract_api_key: str = ""
    # Legacy single-pass system prompt (kept for A/B comparison).
    llm_extract_system_prompt: Path = (
        Path(__file__).resolve().parent.parent
        / "ingestion" / "prompts" / "decision_extraction_v2.md"
    )
    llm_extract_output_dir: Path = (
        Path(__file__).resolve().parent.parent.parent
        / "eval" / "llm_extractions"
    )
    llm_extract_gold_dir: Path = (
        Path(__file__).resolve().parent.parent.parent
        / "eval" / "llm_extractions_gold"
    )

    # ---- Staged extraction pipeline ---------------------------------------
    # Each stage's prompt lives in app/ingestion/prompts/. Per-stage model /
    # base_url / api_key let each stage run on a different backend; an unset
    # value (None for str|None, empty string for str) falls back to the
    # global llm_extract_* defaults above.

    # `metadata` — court/case metadata + outcome + IRAC + fact pattern + concepts
    llm_stage_metadata_prompt: Path = (
        Path(__file__).resolve().parent.parent
        / "ingestion" / "prompts" / "extract_metadata.md"
    )
    llm_stage_metadata_model: str | None = None
    llm_stage_metadata_base_url: str | None = None
    llm_stage_metadata_api_key: str | None = None

    # `summary` — single-paragraph Turkish narrative
    llm_stage_summary_prompt: Path = (
        Path(__file__).resolve().parent.parent
        / "ingestion" / "prompts" / "extract_summary.md"
    )
    llm_stage_summary_model: str | None = None
    llm_stage_summary_base_url: str | None = None
    llm_stage_summary_api_key: str | None = None

    # `cited_court_decisions` — citation graph (decision side)
    llm_stage_citations_decisions_prompt: Path = (
        Path(__file__).resolve().parent.parent
        / "ingestion" / "prompts" / "extract_cited_court_decisions.md"
    )
    llm_stage_citations_decisions_model: str | None = None
    llm_stage_citations_decisions_base_url: str | None = None
    llm_stage_citations_decisions_api_key: str | None = None

    # `cited_law_articles` — citation graph (statute side)
    llm_stage_citations_laws_prompt: Path = (
        Path(__file__).resolve().parent.parent
        / "ingestion" / "prompts" / "extract_cited_law_articles.md"
    )
    llm_stage_citations_laws_model: str | None = None
    llm_stage_citations_laws_base_url: str | None = None
    llm_stage_citations_laws_api_key: str | None = None

    # Where each stage writes its intermediate JSON. Defaults to a
    # _stages/ subdirectory next to llm_extract_output_dir; intermediates
    # are kept on disk after merge so a failed stage can be retried in
    # isolation and so the merged output is auditable back to its sources.
    llm_stages_intermediate_dir: Path = (
        Path(__file__).resolve().parent.parent.parent
        / "eval" / "llm_extractions" / "_stages"
    )

    model_config = {"env_file": ".env", "extra": "ignore", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
