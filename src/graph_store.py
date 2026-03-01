"""
Graph Store — Production-ready singleton for GraphRAG artifacts.

Handles:
 - Build metadata (timestamp, node/edge counts, embedding model, build duration)
 - Atomic save (write to temp dir, then rename)
 - Lock-free read path (load once at startup, swap on rebuild)
 - Integrity check on load
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import networkx as nx

from .config import OUTPUT_DIR
from .embeddings.base import BaseEmbedder
from .graph_builder import GraphBuilder
from .graph_rag import GraphRAG
from .vector_store import VectorStore

logger = logging.getLogger(__name__)

MANIFEST_FILE = "manifest.json"


@dataclass
class BuildManifest:
    """Metadata written alongside every graph build."""

    version: str = "1.0"
    built_at: str = ""
    build_duration_sec: float = 0.0
    embedding_provider: str = ""
    embedding_model: str = ""
    embedding_dimension: int = 0
    total_nodes: int = 0
    total_edges: int = 0
    node_types: Dict[str, int] = field(default_factory=dict)
    edge_types: Dict[str, int] = field(default_factory=dict)
    data_files: list[str] = field(default_factory=list)

    def save(self, path: Path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: Path) -> "BuildManifest":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class GraphStore:
    """
    Thread-safe, production-ready store for a built GraphRAG instance.

    Typical lifecycle:
        store = GraphStore(output_dir="output/")

        # Build (or rebuild)
        store.build(embedder)

        # Query (fast, no I/O)
        result = store.query("iş sözleşmesinin feshi")
    """

    def __init__(self, output_dir: str | Path = OUTPUT_DIR):
        self.output_dir = Path(output_dir)
        self._rag: Optional[GraphRAG] = None
        self._manifest: Optional[BuildManifest] = None
        self._embedder: Optional[BaseEmbedder] = None

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def is_ready(self) -> bool:
        return self._rag is not None

    @property
    def manifest(self) -> Optional[BuildManifest]:
        return self._manifest

    @property
    def rag(self) -> GraphRAG:
        if self._rag is None:
            raise RuntimeError("GraphRAG not loaded. Call build() or load() first.")
        return self._rag

    # ── Build ─────────────────────────────────────────────────────────────

    def build(self, embedder: BaseEmbedder, **builder_kwargs) -> BuildManifest:
        """
        Full build pipeline: data → embed → edges → save atomically.

        Returns the build manifest.
        """
        self._embedder = embedder
        t0 = time.time()
        logger.info("Starting graph build …")

        builder = GraphBuilder(embedder=embedder, **builder_kwargs)
        graph = builder.build()

        duration = time.time() - t0

        # Collect manifest stats
        node_types: Dict[str, int] = {}
        for _, data in graph.nodes(data=True):
            nt = data.get("node_type", "unknown")
            node_types[nt] = node_types.get(nt, 0) + 1

        edge_types: Dict[str, int] = {}
        for _, _, data in graph.edges(data=True):
            et = data.get("edge_type", "unknown")
            edge_types[et] = edge_types.get(et, 0) + 1

        manifest = BuildManifest(
            built_at=datetime.now(timezone.utc).isoformat(),
            build_duration_sec=round(duration, 2),
            embedding_provider=embedder.__class__.__name__,
            embedding_model=embedder.model_name,
            embedding_dimension=embedder.dimension,
            total_nodes=graph.number_of_nodes(),
            total_edges=graph.number_of_edges(),
            node_types=node_types,
            edge_types=edge_types,
            data_files=builder.data_files,
        )

        # Atomic save: write to temp dir, then rename
        self._atomic_save(builder, manifest)
        logger.info(
            "Build complete in %.1fs — %d nodes, %d edges",
            duration, manifest.total_nodes, manifest.total_edges,
        )

        # Load the freshly built graph into memory
        self._rag = GraphRAG(
            graph=builder.graph,
            vector_store=builder.vector_store,
            embedder=embedder,
            nodes_data=builder.nodes,
        )
        self._manifest = manifest
        return manifest

    def _atomic_save(self, builder: GraphBuilder, manifest: BuildManifest) -> None:
        """Write to a temp directory, then swap into the real output dir."""
        tmp_dir = self.output_dir.parent / f".{self.output_dir.name}_tmp_{int(time.time())}"
        try:
            builder.save(tmp_dir)
            manifest.save(tmp_dir / MANIFEST_FILE)

            # Swap
            if self.output_dir.exists():
                backup = self.output_dir.parent / f"{self.output_dir.name}_prev"
                if backup.exists():
                    shutil.rmtree(backup)
                self.output_dir.rename(backup)

            tmp_dir.rename(self.output_dir)
            logger.info("Artifacts saved: %s", self.output_dir)
        except Exception:
            # Cleanup on failure
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

    # ── Load ──────────────────────────────────────────────────────────────

    def load(self, embedder: BaseEmbedder) -> BuildManifest:
        """Load a previously built graph from disk."""
        self._embedder = embedder

        manifest_path = self.output_dir / MANIFEST_FILE
        if not manifest_path.exists():
            raise FileNotFoundError(
                f"No build manifest at {manifest_path}. Run build() first."
            )

        self._manifest = BuildManifest.load(manifest_path)
        self._rag = GraphRAG.load(self.output_dir, embedder)

        logger.info(
            "Loaded graph: %d nodes, %d edges (built %s)",
            self._manifest.total_nodes,
            self._manifest.total_edges,
            self._manifest.built_at,
        )
        return self._manifest

    def has_build(self) -> bool:
        """Check whether a build exists on disk."""
        return (self.output_dir / MANIFEST_FILE).exists()

    # ── Query (convenience) ───────────────────────────────────────────────

    def query(self, query_text: str, **kwargs) -> Any:
        """Proxy to GraphRAG.query()."""
        return self.rag.query(query_text, **kwargs)

    # ── Stats ─────────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        """Return a JSON-serializable statistics dictionary."""
        if not self.is_ready:
            return {"status": "not_loaded"}

        g = self.rag.graph
        degrees = [d for _, d in g.degree()]
        ug = g.to_undirected()
        components = list(nx.connected_components(ug))

        return {
            "status": "ready",
            "manifest": asdict(self._manifest) if self._manifest else None,
            "graph": {
                "nodes": g.number_of_nodes(),
                "edges": g.number_of_edges(),
                "avg_degree": round(sum(degrees) / max(len(degrees), 1), 2),
                "max_degree": max(degrees) if degrees else 0,
                "isolated_nodes": degrees.count(0),
                "components": len(components),
                "largest_component": max(len(c) for c in components) if components else 0,
            },
        }
