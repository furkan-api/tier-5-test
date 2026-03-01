"""
Embedding Abstract Base Class.

To add a new embedding provider:
1. Subclass BaseEmbedder
2. Implement embed_texts() and embed_query()
3. Register it in EMBEDDING_REGISTRY in config.py
"""

from abc import ABC, abstractmethod
from typing import List

import numpy as np


class BaseEmbedder(ABC):
    """Abstract base class for all embedding providers."""

    def __init__(self, model_name: str, dimension: int):
        self.model_name = model_name
        self.dimension = dimension

    @abstractmethod
    def embed_texts(self, texts: List[str]) -> np.ndarray:
        """
        Convert a list of texts into embedding vectors.

        Args:
            texts: List of texts to embed.

        Returns:
            numpy array of shape (N, dimension).
        """
        ...

    @abstractmethod
    def embed_query(self, query: str) -> np.ndarray:
        """
        Convert a single query text into an embedding vector.

        Args:
            query: The query text.

        Returns:
            numpy array of shape (dimension,).
        """
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(model={self.model_name}, dim={self.dimension})"
