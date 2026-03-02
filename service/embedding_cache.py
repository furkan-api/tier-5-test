"""
Persistent Embedding Cache — content-hash-based deduplication.

Stores embeddings on disk (numpy .npz) keyed by a SHA-256 hash of the
embed_text.  On subsequent runs only new / changed texts are embedded;
everything else is loaded from cache in milliseconds.

Storage layout:
    graph_data/embeddings/
        cache.npz          ← numpy array (N × dim)
        cache_meta.json    ← id→row mapping, text hashes, model info

Best practices implemented:
    • Content hashing  — same text → same hash → cache hit
    • Model fingerprint — cache auto-invalidates when model changes
    • Atomic writes    — writes to tmp then renames (no corruption)
    • Compact storage  — float32 numpy binary, ~1.5 KB per 384-d vector
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .config import get_settings

logger = logging.getLogger(__name__)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _text_hash(text: str) -> str:
    """Deterministic SHA-256 of the embed text (first 16 hex chars)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _model_fingerprint(model_name: str, dimension: int) -> str:
    """Fingerprint that changes when the embedding model changes."""
    raw = f"{model_name}||{dimension}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


# ── Cache ─────────────────────────────────────────────────────────────────────


@dataclass
class CacheStats:
    total_cached: int = 0
    hits: int = 0
    misses: int = 0
    invalidated: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0


class EmbeddingCache:
    """Persistent, content-hash-based embedding cache.

    Usage:
        cache = EmbeddingCache.load(cache_dir)
        vec = cache.get(node_id, embed_text)
        if vec is None:
            vec = embedder.embed_texts([embed_text])[0]
            cache.put(node_id, embed_text, vec)
        cache.save()
    """

    def __init__(
        self,
        cache_dir: Path,
        model_name: str,
        dimension: int,
    ):
        self.cache_dir = Path(cache_dir)
        self.model_name = model_name
        self.dimension = dimension
        self.model_fp = _model_fingerprint(model_name, dimension)

        # In-memory storage
        self._vectors: dict[str, np.ndarray] = {}     # node_id → vector
        self._hashes: dict[str, str] = {}              # node_id → text_hash
        self._dirty = False
        self.stats = CacheStats()

    # ── Persistence paths ─────────────────────────────────────────────

    @property
    def _npz_path(self) -> Path:
        return self.cache_dir / "cache.npz"

    @property
    def _meta_path(self) -> Path:
        return self.cache_dir / "cache_meta.json"

    # ── Core API ──────────────────────────────────────────────────────

    def get(self, node_id: str, embed_text: str) -> np.ndarray | None:
        """Look up a cached embedding.

        Returns the vector if the text hash matches, else None.
        """
        th = _text_hash(embed_text)

        if node_id in self._hashes and self._hashes[node_id] == th:
            self.stats.hits += 1
            return self._vectors[node_id]

        # Text changed → old embedding is stale
        if node_id in self._hashes:
            self.stats.invalidated += 1

        self.stats.misses += 1
        return None

    def put(self, node_id: str, embed_text: str, vector: np.ndarray) -> None:
        """Store an embedding in the cache."""
        self._vectors[node_id] = vector.astype(np.float32)
        self._hashes[node_id] = _text_hash(embed_text)
        self._dirty = True

    def get_all(self, node_ids: list[str]) -> dict[str, np.ndarray]:
        """Bulk get — returns only cached embeddings."""
        return {
            nid: self._vectors[nid]
            for nid in node_ids
            if nid in self._vectors
        }

    def has(self, node_id: str, embed_text: str) -> bool:
        """Check if an up-to-date embedding exists for this text."""
        th = _text_hash(embed_text)
        return (
            node_id in self._hashes
            and self._hashes[node_id] == th
            and node_id in self._vectors
        )

    @property
    def size(self) -> int:
        return len(self._vectors)

    # ── Load / Save ───────────────────────────────────────────────────

    @classmethod
    def load(
        cls,
        cache_dir: Path | None = None,
        model_name: str | None = None,
        dimension: int | None = None,
    ) -> "EmbeddingCache":
        """Load cache from disk (or create empty)."""
        s = get_settings()
        cache_dir = cache_dir or (s.graph_data_dir / "embeddings")
        model_name = model_name or s.embedding_model
        dimension = dimension or s.embedding_dimension

        cache = cls(cache_dir, model_name, dimension)

        if not cache._meta_path.exists():
            logger.info("No embedding cache found. Starting fresh.")
            return cache

        # Load metadata
        with open(cache._meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        # Check model fingerprint — invalidate if model changed
        stored_fp = meta.get("model_fingerprint", "")
        if stored_fp != cache.model_fp:
            logger.warning(
                "Embedding model changed (%s → %s). Cache invalidated.",
                meta.get("model_name", "?"), model_name,
            )
            return cache

        # Load hashes
        cache._hashes = meta.get("hashes", {})

        # Load vectors
        if cache._npz_path.exists():
            data = np.load(cache._npz_path, allow_pickle=False)
            node_ids = meta.get("node_ids", [])
            vectors = data["vectors"]

            if len(node_ids) != len(vectors):
                logger.warning("Cache corrupted (id/vector count mismatch). Starting fresh.")
                return cls(cache_dir, model_name, dimension)

            for i, nid in enumerate(node_ids):
                cache._vectors[nid] = vectors[i]

        cache.stats.total_cached = len(cache._vectors)
        logger.info(
            "Embedding cache loaded: %d vectors (%s, dim=%d)",
            len(cache._vectors), model_name, dimension,
        )
        return cache

    def save(self) -> None:
        """Persist cache to disk atomically."""
        if not self._dirty and self._npz_path.exists():
            logger.debug("Cache unchanged, skipping save.")
            return

        self.cache_dir.mkdir(parents=True, exist_ok=True)

        node_ids = list(self._vectors.keys())
        if node_ids:
            matrix = np.stack(
                [self._vectors[nid] for nid in node_ids],
                axis=0,
            )
        else:
            matrix = np.empty((0, self.dimension), dtype=np.float32)

        # Atomic write: numpy vectors
        tmp_npz = self.cache_dir / ".cache_tmp.npz"
        np.savez_compressed(tmp_npz, vectors=matrix)
        tmp_npz.rename(self._npz_path)

        # Atomic write: metadata
        meta = {
            "model_name": self.model_name,
            "model_fingerprint": self.model_fp,
            "dimension": self.dimension,
            "count": len(node_ids),
            "node_ids": node_ids,
            "hashes": self._hashes,
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        tmp_meta = self.cache_dir / ".cache_meta_tmp.json"
        with open(tmp_meta, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
        tmp_meta.rename(self._meta_path)

        self._dirty = False
        logger.info(
            "Embedding cache saved: %d vectors (%.1f MB)",
            len(node_ids),
            self._npz_path.stat().st_size / (1024 * 1024),
        )

    # ── Utilities ─────────────────────────────────────────────────────

    def remove(self, node_id: str) -> bool:
        """Remove a node from the cache."""
        removed = node_id in self._vectors
        self._vectors.pop(node_id, None)
        self._hashes.pop(node_id, None)
        if removed:
            self._dirty = True
        return removed

    def prune(self, valid_node_ids: set[str]) -> int:
        """Remove cached entries for nodes that no longer exist."""
        stale = set(self._vectors.keys()) - valid_node_ids
        for nid in stale:
            self._vectors.pop(nid, None)
            self._hashes.pop(nid, None)
        if stale:
            self._dirty = True
            logger.info("Pruned %d stale entries from embedding cache.", len(stale))
        return len(stale)

    def summary(self) -> dict[str, Any]:
        """Return a summary of cache state and stats."""
        return {
            "cached_vectors": len(self._vectors),
            "model": self.model_name,
            "dimension": self.dimension,
            "cache_file_exists": self._npz_path.exists(),
            "cache_size_mb": (
                round(self._npz_path.stat().st_size / (1024 * 1024), 2)
                if self._npz_path.exists()
                else 0
            ),
            "stats": {
                "hits": self.stats.hits,
                "misses": self.stats.misses,
                "invalidated": self.stats.invalidated,
                "hit_rate": f"{self.stats.hit_rate:.1%}",
            },
        }
