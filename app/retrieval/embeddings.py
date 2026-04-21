from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from openai import OpenAI

from app.core.config import get_settings

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------

DIMENSIONS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "BAAI/bge-m3": 1024,
    "multilingual-e5-large": 1024,
    "bge-base-en-v1.5": 768,
    "gemini-embedding-2-preview": 3072,
}

# Models that hit the real OpenAI API (api.openai.com)
OPENAI_API_MODELS = {"text-embedding-3-small", "text-embedding-3-large"}

# Models served via a custom OpenAI-compatible endpoint (EMBEDDING_BASE_URL)
OPENAI_COMPAT_MODELS = {"BAAI/bge-m3"}

# Union used for routing checks elsewhere
OPENAI_MODELS = OPENAI_API_MODELS | OPENAI_COMPAT_MODELS

# Models loaded directly via SentenceTransformer
LOCAL_MODELS = {"multilingual-e5-large", "bge-base-en-v1.5"}

# Models served via Google Generative AI API
GEMINI_MODELS = {"gemini-embedding-2-preview"}

HF_MODEL_IDS = {
    "multilingual-e5-large": "intfloat/multilingual-e5-large",
    "bge-base-en-v1.5": "BAAI/bge-base-en-v1.5",
}

# Lazy cache for loaded SentenceTransformer instances (avoid repeated 1 GB loads)
_local_models: dict = {}


@dataclass
class GeminiClient:
    api_key: str


def _get_local_model(model_name: str):
    """Load and cache a SentenceTransformer model."""
    if model_name not in _local_models:
        from sentence_transformers import SentenceTransformer

        hf_id = HF_MODEL_IDS[model_name]
        _local_models[model_name] = SentenceTransformer(hf_id)
    return _local_models[model_name]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_embedding_client() -> OpenAI | GeminiClient | None:
    """Return the appropriate embedding client for the configured model."""
    settings = get_settings()
    if settings.embedding_model in OPENAI_API_MODELS:
        # Real OpenAI API — let the SDK use its default base URL.
        return OpenAI(api_key=settings.openai_api_key)
    if settings.embedding_model in OPENAI_COMPAT_MODELS:
        # Custom OpenAI-compatible endpoint (local or external).
        return OpenAI(
            api_key=settings.openai_api_key or "local",
            base_url=settings.embedding_base_url,
        )
    if settings.embedding_model in GEMINI_MODELS:
        if not settings.gemini_api_key:
            raise ValueError("GEMINI_API_KEY must be set in .env for Gemini embedding models.")
        return GeminiClient(api_key=settings.gemini_api_key)
    return None


def embed_texts(
    client: OpenAI | GeminiClient | None,
    texts: list[str],
    model: str | None = None,
    prefix: str | None = None,
) -> list[list[float]]:
    """Embed a batch of texts. Returns list of float vectors.

    Args:
        client: Embedding client (OpenAI, GeminiClient, or None for local models).
        texts: Texts to embed.
        model: Model name. Falls back to settings.embedding_model.
        prefix: Optional prefix prepended to each text (e.g. "query: " or
                "passage: " for E5 models). Ignored for OpenAI/Gemini models.
    """
    model = model or get_settings().embedding_model

    if model in OPENAI_MODELS:
        response = client.embeddings.create(model=model, input=texts)
        return [item.embedding for item in response.data]

    if model in GEMINI_MODELS:
        from google import genai
        from google.genai import errors as genai_errors
        from google.genai import types

        genai_client = genai.Client(api_key=client.api_key)
        config = types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT")
        vectors = []
        for text in texts:
            attempt = 0
            while True:
                attempt += 1
                try:
                    response = genai_client.models.embed_content(
                        model=model,
                        contents=text,
                        config=config,
                    )
                    break
                except genai_errors.APIError as e:
                    status = getattr(e, "code", None) or getattr(e, "status_code", None)
                    retriable = status is None or status >= 500 or status == 429
                    if not retriable:
                        raise
                    sleep_s = min(60, 2 ** min(attempt, 6))
                    log.warning(
                        "Gemini embed_content failed (status=%s, attempt=%d): %s. Retrying in %ds.",
                        status, attempt, e, sleep_s,
                    )
                    time.sleep(sleep_s)
                except Exception as e:
                    sleep_s = min(60, 2 ** min(attempt, 6))
                    log.warning(
                        "Gemini embed_content network error (attempt=%d): %s. Retrying in %ds.",
                        attempt, e, sleep_s,
                    )
                    time.sleep(sleep_s)
            vectors.append(list(response.embeddings[0].values))
        return vectors

    if model in LOCAL_MODELS:
        st_model = _get_local_model(model)
        if prefix:
            texts = [prefix + t for t in texts]
        embeddings = st_model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return [emb.tolist() for emb in embeddings]

    raise ValueError(f"Unknown embedding model: {model!r}. Known: {sorted(DIMENSIONS)}")
