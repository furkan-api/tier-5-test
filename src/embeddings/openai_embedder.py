"""
OpenAI API-based embedding provider.

Requires the OPENAI_API_KEY environment variable.
"""

from typing import List

import numpy as np

from .base import BaseEmbedder
from ..config import OPENAI_API_KEY


class OpenAIEmbedder(BaseEmbedder):
    """Uses the OpenAI Embeddings API."""

    def __init__(
        self,
        model_name: str = "text-embedding-3-small",
        dimension: int = 1536,
        api_key: str | None = None,
        batch_size: int = 100,
    ):
        super().__init__(model_name=model_name, dimension=dimension)
        self.batch_size = batch_size
        self.api_key = api_key or OPENAI_API_KEY or None

        if not self.api_key:
            raise ValueError(
                "OpenAI API key is required. Set the OPENAI_API_KEY environment "
                "variable or pass it via the api_key parameter."
            )

        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("openai package required: pip install openai")

        self._client = OpenAI(api_key=self.api_key)

    def _get_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Send batch requests to the OpenAI API."""
        all_embeddings = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            response = self._client.embeddings.create(
                model=self.model_name,
                input=batch,
            )
            batch_embeddings = [item.embedding for item in response.data]
            all_embeddings.extend(batch_embeddings)
        return all_embeddings

    def embed_texts(self, texts: List[str]) -> np.ndarray:
        embeddings = self._get_embeddings(texts)
        arr = np.array(embeddings, dtype=np.float32)
        # L2 normalize
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1
        return arr / norms

    def embed_query(self, query: str) -> np.ndarray:
        embeddings = self._get_embeddings([query])
        arr = np.array(embeddings[0], dtype=np.float32)
        norm = np.linalg.norm(arr)
        if norm > 0:
            arr = arr / norm
        return arr
