"""
Embedding provider factory.

Creates the appropriate embedder based on configuration.
Reuses the existing src/embeddings/ implementations.
"""

from __future__ import annotations

import logging
from functools import lru_cache

import numpy as np

from .config import Settings, get_settings

logger = logging.getLogger(__name__)


class BaseEmbedder:
    """Minimal interface for embedding providers."""

    def __init__(self, model_name: str, dimension: int):
        self.model_name = model_name
        self.dimension = dimension

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        raise NotImplementedError

    def embed_query(self, query: str) -> np.ndarray:
        raise NotImplementedError


class SentenceTransformerEmbedder(BaseEmbedder):
    """Sentence Transformers embedding provider."""

    def __init__(self, model_name: str, dimension: int, batch_size: int = 64):
        super().__init__(model_name, dimension)
        self.batch_size = batch_size
        self._model = None

    @property
    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name)
            logger.info("Loaded SentenceTransformer: %s", self.model_name)
        return self._model

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        vecs = self.model.encode(
            texts,
            batch_size=self.batch_size,
            show_progress_bar=len(texts) > 100,
            normalize_embeddings=True,
        )
        return vecs.astype(np.float32)

    def embed_query(self, query: str) -> np.ndarray:
        vec = self.model.encode(
            query,
            normalize_embeddings=True,
        )
        return vec.astype(np.float32)


class OpenAIEmbedder(BaseEmbedder):
    """OpenAI embedding provider."""

    def __init__(
        self,
        model_name: str,
        dimension: int,
        api_key: str | None = None,
        batch_size: int = 64,
    ):
        super().__init__(model_name, dimension)
        self.batch_size = batch_size
        self._api_key = api_key

    def _get_client(self):
        import openai
        return openai.OpenAI(api_key=self._api_key)

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        client = self._get_client()
        all_vecs = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            response = client.embeddings.create(model=self.model_name, input=batch)
            vecs = [item.embedding for item in response.data]
            all_vecs.extend(vecs)
        arr = np.array(all_vecs, dtype=np.float32)
        # Normalize
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1
        return arr / norms

    def embed_query(self, query: str) -> np.ndarray:
        client = self._get_client()
        response = client.embeddings.create(model=self.model_name, input=query)
        vec = np.array(response.data[0].embedding, dtype=np.float32)
        norm = np.linalg.norm(vec)
        return vec / norm if norm > 0 else vec


class GeminiEmbedder(BaseEmbedder):
    """Google Gemini embedding provider (google-genai SDK)."""

    def __init__(
        self,
        model_name: str,
        dimension: int,
        api_key: str | None = None,
        batch_size: int = 100,
    ):
        super().__init__(model_name, dimension)
        self.batch_size = batch_size
        self._api_key = api_key

    def _get_client(self):
        try:
            from google import genai  # google-genai>=1.0
        except ImportError:
            raise ImportError(
                "google-genai is required for Gemini embeddings.\n"
                "Install it with: pip install google-genai"
            )
        return genai.Client(api_key=self._api_key)

    def _embed_batch(self, texts: list[str], task_type: str) -> list[list[float]]:
        from google.genai import types
        client = self._get_client()
        response = client.models.embed_content(
            model=self.model_name,
            contents=texts,
            config=types.EmbedContentConfig(task_type=task_type),
        )
        return [emb.values for emb in response.embeddings]

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        all_vecs: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            logger.info("Gemini embed batch %d/%d", i // self.batch_size + 1, -(-len(texts) // self.batch_size))
            all_vecs.extend(self._embed_batch(batch, "RETRIEVAL_DOCUMENT"))
        arr = np.array(all_vecs, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1
        return arr / norms

    def embed_query(self, query: str) -> np.ndarray:
        vecs = self._embed_batch([query], "RETRIEVAL_QUERY")
        vec = np.array(vecs[0], dtype=np.float32)
        norm = np.linalg.norm(vec)
        return vec / norm if norm > 0 else vec


def create_embedder(settings: Settings | None = None) -> BaseEmbedder:
    """Factory to create the configured embedding provider."""
    s = settings or get_settings()

    if s.embedding_provider == "openai":
        api_key = s.openai_api_key.get_secret_value() if s.openai_api_key else None
        return OpenAIEmbedder(
            model_name=s.embedding_model,
            dimension=s.embedding_dimension,
            api_key=api_key,
            batch_size=s.embedding_batch_size,
        )
    elif s.embedding_provider == "gemini":
        api_key = s.gemini_api_key.get_secret_value() if s.gemini_api_key else None
        return GeminiEmbedder(
            model_name=s.embedding_model,
            dimension=s.embedding_dimension,
            api_key=api_key,
            batch_size=s.embedding_batch_size,
        )
    else:
        return SentenceTransformerEmbedder(
            model_name=s.embedding_model,
            dimension=s.embedding_dimension,
            batch_size=s.embedding_batch_size,
        )


@lru_cache(maxsize=1)
def get_embedder() -> BaseEmbedder:
    """Return a cached singleton embedder instance."""
    return create_embedder()
