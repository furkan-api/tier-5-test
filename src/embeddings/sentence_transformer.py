"""
SentenceTransformer-based embedding provider.

Runs locally, no API key required. Multilingual models are recommended
for Turkish legal texts.
"""

from typing import List, Optional

import numpy as np

from .base import BaseEmbedder


class SentenceTransformerEmbedder(BaseEmbedder):
    """Uses the HuggingFace sentence-transformers library."""

    def __init__(
        self,
        model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        dimension: int = 384,
        device: Optional[str] = None,
        batch_size: int = 64,
    ):
        super().__init__(model_name=model_name, dimension=dimension)
        self.batch_size = batch_size

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "sentence-transformers package required: pip install sentence-transformers"
            )

        self._model = SentenceTransformer(model_name, device=device)
        # Get the actual dimension from the model
        self.dimension = self._model.get_sentence_embedding_dimension()

    def embed_texts(self, texts: List[str]) -> np.ndarray:
        embeddings = self._model.encode(
            texts,
            batch_size=self.batch_size,
            show_progress_bar=True,
            normalize_embeddings=True,
        )
        return np.array(embeddings, dtype=np.float32)

    def embed_query(self, query: str) -> np.ndarray:
        embedding = self._model.encode(
            query,
            normalize_embeddings=True,
        )
        return np.array(embedding, dtype=np.float32)
