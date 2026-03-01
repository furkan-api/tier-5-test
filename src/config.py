"""
Configuration constants and embedding registry.

Parameters are read from a .env file (or environment variables) and
managed centrally here.

To add a new embedding provider, register it in EMBEDDING_REGISTRY.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# ─── Load .env file ──────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
_env_path = PROJECT_ROOT / ".env"
load_dotenv(_env_path)


def _env(key: str, default: str = "") -> str:
    """Read an environment variable; return default if empty."""
    val = os.getenv(key, "").strip()
    return val if val else default


def _env_int(key: str, default: int) -> int:
    """Read an environment variable as int."""
    val = _env(key, "")
    return int(val) if val else default


def _env_float(key: str, default: float) -> float:
    """Read an environment variable as float."""
    val = _env(key, "")
    return float(val) if val else default


# ─── Logging ─────────────────────────────────────────────────────────────────
LOG_LEVEL = _env("LOG_LEVEL", "INFO")

# ─── Paths ───────────────────────────────────────────────────────────────────
_graph_data_dir = _env("GRAPH_DATA_DIR", "")
_edge_rules_path = _env("EDGE_RULES_PATH", "")
_output_dir = _env("OUTPUT_DIR", "")

GRAPH_DATA_DIR = Path(_graph_data_dir) if _graph_data_dir else (PROJECT_ROOT / "graph_data")
EDGE_RULES_PATH = Path(_edge_rules_path) if _edge_rules_path else (GRAPH_DATA_DIR / "edge_rules.json")
OUTPUT_DIR = Path(_output_dir) if _output_dir else (PROJECT_ROOT / "output")

# ─── Data files ──────────────────────────────────────────────────────────────
DATA_FILES = [
    "kanunlar.json",
    "kararlar_bam.json",
    "kararlar_ilk_derece.json",
    "kararlar_yargitay.json",
    "maddeler_hmk.json",
    "maddeler_ik.json",
    "maddeler_tbk.json",
    "maddeler_tmk.json",
]

# ─── Embedding settings ──────────────────────────────────────────────────────
DEFAULT_EMBEDDING_PROVIDER = _env("EMBEDDING_PROVIDER", "sentence_transformer")
DEFAULT_EMBEDDING_MODEL = _env(
    "EMBEDDING_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)
DEFAULT_EMBEDDING_DIMENSION = _env_int("EMBEDDING_DIMENSION", 384)
DEFAULT_EMBEDDING_BATCH_SIZE = _env_int("EMBEDDING_BATCH_SIZE", 64)

# OpenAI API key (from .env or environment)
OPENAI_API_KEY = _env("OPENAI_API_KEY", "")

# Supported embedding providers registry.
# Each entry: (module_path, class_name, default_params)
EMBEDDING_REGISTRY: dict[str, dict] = {
    "sentence_transformer": {
        "module": "src.embeddings.sentence_transformer",
        "class": "SentenceTransformerEmbedder",
        "default_params": {
            "model_name": DEFAULT_EMBEDDING_MODEL
                if DEFAULT_EMBEDDING_PROVIDER == "sentence_transformer"
                else "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            "dimension": DEFAULT_EMBEDDING_DIMENSION
                if DEFAULT_EMBEDDING_PROVIDER == "sentence_transformer"
                else 384,
            "batch_size": DEFAULT_EMBEDDING_BATCH_SIZE,
        },
    },
    "openai": {
        "module": "src.embeddings.openai_embedder",
        "class": "OpenAIEmbedder",
        "default_params": {
            "model_name": DEFAULT_EMBEDDING_MODEL
                if DEFAULT_EMBEDDING_PROVIDER == "openai"
                else "text-embedding-3-small",
            "dimension": DEFAULT_EMBEDDING_DIMENSION
                if DEFAULT_EMBEDDING_PROVIDER == "openai"
                else 1536,
            "batch_size": DEFAULT_EMBEDDING_BATCH_SIZE,
            "api_key": OPENAI_API_KEY or None,
        },
    },
}

# ─── Graph settings ──────────────────────────────────────────────────────────
DEFAULT_SIMILARITY_THRESHOLD = _env_float("SIMILARITY_THRESHOLD", 0.82)
DEFAULT_MAX_NEIGHBORS = _env_int("MAX_NEIGHBORS", 5)
FAISS_INDEX_TYPE = _env("FAISS_INDEX_TYPE", "flat")

# ─── Query settings ──────────────────────────────────────────────────────────
QUERY_TOP_K = _env_int("QUERY_TOP_K", 10)
QUERY_EXPAND_HOPS = _env_int("QUERY_EXPAND_HOPS", 2)
QUERY_MAX_EXPANDED = _env_int("QUERY_MAX_EXPANDED", 50)
QUERY_SCORE_THRESHOLD = _env_float("QUERY_SCORE_THRESHOLD", 0.3)
QUERY_MAX_CONTEXT_CHARS = _env_int("QUERY_MAX_CONTEXT_CHARS", 8000)

# ─── API settings ────────────────────────────────────────────────────────────
API_HOST = _env("API_HOST", "0.0.0.0")
API_PORT = _env_int("API_PORT", 8000)
