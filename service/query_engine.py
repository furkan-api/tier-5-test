"""
GraphRAG Query Engine — production Neo4j-backed.

Takes a user query, performs vector search in Neo4j, expands via
Cypher graph traversal, and returns structured context for LLM consumption.

This replaces the old in-memory GraphRAG class.
All heavy lifting happens in Neo4j, not Python.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .embeddings import BaseEmbedder, get_embedder
from .graph_store import get_build_meta, get_graph_stats, get_neighborhood, get_node
from .vector_search import (
    vector_search,
    vector_search_with_graph_expansion_simple,
)

logger = logging.getLogger(__name__)


# ── Result Types ──────────────────────────────────────────────────────────────

@dataclass
class SeedNode:
    """A single seed node from vector search."""
    node_id: str
    score: float
    node_type: str = ""
    text_preview: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class QueryResult:
    """Complete result from a GraphRAG query."""
    query: str
    seed_nodes: list[SeedNode]
    expanded_nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    context_texts: list[dict[str, Any]]
    subgraph_node_count: int = 0
    subgraph_edge_count: int = 0
    latency_ms: float = 0.0

    def to_context_string(self, max_chars: int = 8000) -> str:
        """Merged context text suitable for LLM consumption."""
        parts: list[str] = []
        total = 0
        for item in self.context_texts:
            block = (
                f"[{item['node_type'].upper()}] {item['node_id']}\n"
                f"{item['text']}\n"
                f"---"
            )
            if total + len(block) > max_chars:
                remaining = len(self.context_texts) - len(parts)
                parts.append(f"... [{remaining} more sources truncated]")
                break
            parts.append(block)
            total += len(block)
        return "\n".join(parts)


# ── Query Engine ──────────────────────────────────────────────────────────────

class GraphRAGEngine:
    """Production GraphRAG query engine backed by Neo4j.

    Usage:
        engine = GraphRAGEngine()
        result = await engine.query("iş sözleşmesinin feshi")
    """

    def __init__(self, embedder: BaseEmbedder | None = None):
        self._embedder = embedder

    @property
    def embedder(self) -> BaseEmbedder:
        if self._embedder is None:
            self._embedder = get_embedder()
        return self._embedder

    async def query(
        self,
        query_text: str,
        top_k: int = 10,
        expand_hops: int = 2,
        max_expanded: int = 50,
        score_threshold: float = 0.3,
        max_context_chars: int = 8000,
        node_type_filter: list[str] | None = None,
        include_context: bool = True,
    ) -> QueryResult:
        """Execute a full GraphRAG query.

        1. Embed the query text
        2. Vector search in Neo4j → seed nodes
        3. Graph expansion via Cypher → expanded subgraph
        4. Build context for LLM

        Returns a QueryResult with all data.
        """
        t0 = time.perf_counter()

        # 1. Embed query
        query_vec = self.embedder.embed_query(query_text)

        # 2+3. Combined vector search + graph expansion
        result = await vector_search_with_graph_expansion_simple(
            query_vector=query_vec.tolist(),
            top_k=top_k,
            expand_hops=expand_hops,
            max_expanded=max_expanded,
            score_threshold=score_threshold,
            node_type_filter=node_type_filter,
        )

        # Build seed nodes
        seeds = []
        for s in result["seed_nodes"]:
            seeds.append(SeedNode(
                node_id=s["node_id"],
                score=s.get("score", 0.0),
                node_type=s.get("node_type", ""),
                text_preview=s.get("text_preview", ""),
                metadata=s.get("metadata", {}),
            ))

        # Build context texts (seed nodes first, then expanded)
        context_texts = []
        if include_context:
            # Seed nodes (with full text from Neo4j)
            for s in seeds:
                node_data = await get_node(s.node_id)
                text = node_data.get("embed_text", s.text_preview) if node_data else s.text_preview
                metadata = node_data.get("metadata", s.metadata) if node_data else s.metadata
                context_texts.append({
                    "node_id": s.node_id,
                    "node_type": s.node_type,
                    "text": text,
                    "metadata": metadata,
                    "is_seed": True,
                    "score": s.score,
                })

            # Expanded nodes
            for n in result["expanded_nodes"]:
                node_data = await get_node(n["node_id"])
                text = node_data.get("embed_text", n.get("text_preview", "")) if node_data else n.get("text_preview", "")
                context_texts.append({
                    "node_id": n["node_id"],
                    "node_type": n.get("node_type", ""),
                    "text": text,
                    "metadata": n.get("metadata", {}),
                    "is_seed": False,
                })

        latency = (time.perf_counter() - t0) * 1000

        return QueryResult(
            query=query_text,
            seed_nodes=seeds,
            expanded_nodes=result["expanded_nodes"],
            edges=result["edges"],
            context_texts=context_texts,
            subgraph_node_count=result["subgraph_node_count"],
            subgraph_edge_count=result["subgraph_edge_count"],
            latency_ms=round(latency, 2),
        )

    async def get_node_detail(self, node_id: str) -> dict[str, Any] | None:
        """Fetch full node detail from Neo4j."""
        return await get_node(node_id)

    async def get_node_neighborhood(
        self, node_id: str, hops: int = 1
    ) -> dict[str, Any] | None:
        """Fetch neighborhood subgraph from Neo4j."""
        return await get_neighborhood(node_id, hops=hops)

    async def get_stats(self) -> dict[str, Any]:
        """Get graph statistics."""
        stats = await get_graph_stats()
        meta = await get_build_meta()
        if meta:
            stats["manifest"] = meta
        return stats

    async def health_check(self) -> dict[str, Any]:
        """Check if the service and Neo4j are healthy."""
        try:
            stats = await get_graph_stats()
            meta = await get_build_meta()
            return {
                "status": "ready" if stats.get("graph", {}).get("nodes", 0) > 0 else "empty",
                "graph_loaded": stats.get("graph", {}).get("nodes", 0) > 0,
                "nodes": stats.get("graph", {}).get("nodes", 0),
                "edges": stats.get("graph", {}).get("edges", 0),
                "built_at": meta.get("built_at") if meta else None,
            }
        except Exception as e:
            return {
                "status": "error",
                "graph_loaded": False,
                "error": str(e),
            }
