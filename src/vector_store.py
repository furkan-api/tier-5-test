"""
Vector Store - FAISS-based.

Stores node embeddings and performs similarity search.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

try:
    import faiss
except ImportError:
    faiss = None  # type: ignore


class VectorStore:
    """FAISS-based vector store."""

    def __init__(self, dimension: int, index_type: str = "flat"):
        if faiss is None:
            raise ImportError("faiss-cpu package required: pip install faiss-cpu")

        self.dimension = dimension
        self.index_type = index_type
        self._index: faiss.Index = self._create_index(dimension, index_type)
        self._id_map: List[str] = []  # FAISS internal idx → node_id

    @staticmethod
    def _create_index(dimension: int, index_type: str) -> "faiss.Index":
        if index_type == "flat":
            return faiss.IndexFlatIP(dimension)  # Inner product (cosine for normalized vectors)
        elif index_type == "ivf":
            quantizer = faiss.IndexFlatIP(dimension)
            index = faiss.IndexIVFFlat(quantizer, dimension, min(16, dimension))
            return index
        else:
            raise ValueError(f"Unknown index type: {index_type}")

    def add(self, node_ids: List[str], embeddings: np.ndarray) -> None:
        """Add embedding vectors to the store."""
        assert embeddings.shape[0] == len(node_ids)
        assert embeddings.shape[1] == self.dimension

        if self.index_type == "ivf" and not self._index.is_trained:
            self._index.train(embeddings)

        self._index.add(embeddings.astype(np.float32))
        self._id_map.extend(node_ids)

    def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 10,
        threshold: float = 0.0,
    ) -> List[Tuple[str, float]]:
        """
        Return the nearest nodes to the query vector.

        Returns:
            List of (node_id, similarity_score) tuples.
        """
        if query_vector.ndim == 1:
            query_vector = query_vector.reshape(1, -1)

        scores, indices = self._index.search(query_vector.astype(np.float32), top_k)

        results: List[Tuple[str, float]] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:
                continue
            if score < threshold:
                continue
            results.append((self._id_map[idx], float(score)))

        return results

    def get_vector(self, node_id: str) -> Optional[np.ndarray]:
        """Return the vector for a specific node."""
        if node_id not in self._id_map:
            return None
        idx = self._id_map.index(node_id)
        vec = np.zeros(self.dimension, dtype=np.float32)
        self._index.reconstruct(idx, vec)
        return vec

    def similarity(self, id_a: str, id_b: str) -> float:
        """Compute cosine similarity between two node vectors."""
        vec_a = self.get_vector(id_a)
        vec_b = self.get_vector(id_b)
        if vec_a is None or vec_b is None:
            return 0.0
        dot = float(np.dot(vec_a, vec_b))
        norm = float(np.linalg.norm(vec_a) * np.linalg.norm(vec_b))
        return dot / norm if norm > 0 else 0.0

    def find_similar_pairs(
        self,
        threshold: float = 0.82,
        max_neighbors: int = 5,
    ) -> List[Tuple[str, str, float]]:
        """
        Find all pairs of vectors with similarity above the threshold.

        Returns:
            List of (node_id_a, node_id_b, similarity) tuples.
        """
        n = self._index.ntotal
        if n == 0:
            return []

        # k+1 because the node itself appears in results
        k = min(max_neighbors + 1, n)
        all_vectors = np.zeros((n, self.dimension), dtype=np.float32)
        for i in range(n):
            self._index.reconstruct(i, all_vectors[i])

        scores, indices = self._index.search(all_vectors, k)

        pairs: List[Tuple[str, str, float]] = []
        seen = set()
        for i in range(n):
            for j_pos in range(k):
                j = indices[i][j_pos]
                if j < 0 or j == i:
                    continue
                sim = float(scores[i][j_pos])
                if sim < threshold:
                    continue
                pair_key = tuple(sorted((i, j)))
                if pair_key not in seen:
                    seen.add(pair_key)
                    pairs.append((self._id_map[i], self._id_map[j], sim))

        return pairs

    def save(self, directory: str | Path) -> None:
        """Save the index and ID map to disk."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._index, str(directory / "faiss.index"))
        with open(directory / "id_map.json", "w", encoding="utf-8") as f:
            json.dump(self._id_map, f, ensure_ascii=False)

    @classmethod
    def load(cls, directory: str | Path, index_type: str = "flat") -> "VectorStore":
        """Load from a previously saved index on disk."""
        directory = Path(directory)
        index = faiss.read_index(str(directory / "faiss.index"))
        with open(directory / "id_map.json", "r", encoding="utf-8") as f:
            id_map = json.load(f)

        store = cls.__new__(cls)
        store.dimension = index.d
        store.index_type = index_type
        store._index = index
        store._id_map = id_map
        return store

    @property
    def size(self) -> int:
        return self._index.ntotal
