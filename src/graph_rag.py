"""
GraphRAG Query Engine.

Takes a user query, finds the nearest nodes via vector search,
expands the subgraph via BFS/neighborhood traversal, and returns
enriched context.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import networkx as nx
import numpy as np

from .embeddings.base import BaseEmbedder
from .vector_store import VectorStore

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    """Represents the result of a single query."""

    query: str
    seed_nodes: List[Tuple[str, float]]  # (node_id, score)
    expanded_nodes: List[str]
    subgraph: nx.DiGraph
    context_texts: List[Dict[str, Any]]

    def to_context_string(self, max_chars: int = 8000) -> str:
        """Merged context text that can be sent to an LLM."""
        parts: List[str] = []
        total = 0
        for item in self.context_texts:
            block = (
                f"[{item['node_type'].upper()}] {item['node_id']}\n"
                f"{item['text']}\n"
                f"---"
            )
            if total + len(block) > max_chars:
                break
            parts.append(block)
            total += len(block)
        return "\n".join(parts)

    def summary(self) -> str:
        """Short summary info."""
        edge_types = {}
        for u, v, d in self.subgraph.edges(data=True):
            et = d.get("edge_type", "?")
            edge_types[et] = edge_types.get(et, 0) + 1

        return (
            f"Query: {self.query}\n"
            f"Seed nodes: {len(self.seed_nodes)}\n"
            f"Expanded nodes: {len(self.expanded_nodes)}\n"
            f"Subgraph: {self.subgraph.number_of_nodes()} nodes, "
            f"{self.subgraph.number_of_edges()} edges\n"
            f"Edge types: {edge_types}"
        )


class GraphRAG:
    """
    Graph-based Retrieval Augmented Generation engine.

    Usage:
        rag = GraphRAG(graph, vector_store, embedder, nodes_data)
        result = rag.query("muvazaa nedeniyle tapu iptali")
    """

    def __init__(
        self,
        graph: nx.DiGraph,
        vector_store: VectorStore,
        embedder: BaseEmbedder,
        nodes_data: Dict[str, Dict[str, Any]],
    ):
        self.graph = graph
        self.vector_store = vector_store
        self.embedder = embedder
        self.nodes = nodes_data

    # ── Ana sorgu ─────────────────────────────────────────────────────────

    def query(
        self,
        query_text: str,
        top_k: int = 10,
        expand_hops: int = 2,
        max_expanded: int = 50,
        score_threshold: float = 0.3,
    ) -> RetrievalResult:
        """
        Find the relevant graph subset for a query.

        Args:
            query_text: User query string.
            top_k: Number of top results from vector search.
            expand_hops: Number of hops for graph expansion.
            max_expanded: Maximum number of nodes during expansion.
            score_threshold: Minimum similarity threshold.
        """
        # 1) Vector search -> seed nodes
        query_vec = self.embedder.embed_query(query_text)
        seed_results = self.vector_store.search(
            query_vec, top_k=top_k, threshold=score_threshold
        )

        seed_ids = [nid for nid, _ in seed_results]
        logger.info("Vector search: %d seed nodes found.", len(seed_ids))

        # 2) Graph expansion (BFS)
        expanded = self._expand_nodes(seed_ids, hops=expand_hops, max_nodes=max_expanded)
        logger.info("Graph expansion: %d -> %d nodes.", len(seed_ids), len(expanded))

        # 3) Extract subgraph
        subgraph = self.graph.subgraph(expanded).copy()

        # 4) Context texts: seed nodes first, then the rest
        context_texts = self._build_context(seed_ids, expanded)

        return RetrievalResult(
            query=query_text,
            seed_nodes=seed_results,
            expanded_nodes=list(expanded),
            subgraph=subgraph,
            context_texts=context_texts,
        )

    # ── Graph expansion ──────────────────────────────────────────────

    def _expand_nodes(
        self,
        seed_ids: List[str],
        hops: int = 2,
        max_nodes: int = 50,
    ) -> Set[str]:
        """Collect neighbors from seed nodes via BFS."""
        visited: Set[str] = set(seed_ids)
        frontier = set(seed_ids)

        for hop in range(hops):
            if len(visited) >= max_nodes:
                break
            next_frontier: Set[str] = set()
            for nid in frontier:
                if nid not in self.graph:
                    continue
                # Both outgoing and incoming neighbors
                neighbors = set(self.graph.successors(nid)) | set(self.graph.predecessors(nid))
                for nb in neighbors:
                    if nb not in visited:
                        next_frontier.add(nb)
                        visited.add(nb)
                        if len(visited) >= max_nodes:
                            break
                if len(visited) >= max_nodes:
                    break
            frontier = next_frontier

        return visited

    # ── Context building ─────────────────────────────────────────────

    def _build_context(
        self,
        seed_ids: List[str],
        all_ids: Set[str],
    ) -> List[Dict[str, Any]]:
        """Build an ordered context list for seed and expanded nodes."""
        context: List[Dict[str, Any]] = []
        added: Set[str] = set()

        def _add_node(nid: str, is_seed: bool = False):
            if nid in added or nid not in self.nodes:
                return
            added.add(nid)
            node = self.nodes[nid]
            # Related edge info
            edges_info = []
            if nid in self.graph:
                for _, target, data in self.graph.edges(nid, data=True):
                    edges_info.append({
                        "target": target,
                        "edge_type": data.get("edge_type", ""),
                    })
                for source, _, data in self.graph.in_edges(nid, data=True):
                    edges_info.append({
                        "source": source,
                        "edge_type": data.get("edge_type", ""),
                    })

            context.append({
                "node_id": nid,
                "node_type": node.get("node_type", ""),
                "text": node.get("embed_text", ""),
                "metadata": node.get("metadata", {}),
                "is_seed": is_seed,
                "edges": edges_info,
            })

        # Seed nodes first
        for nid in seed_ids:
            _add_node(nid, is_seed=True)

        # Remaining nodes
        for nid in sorted(all_ids - set(seed_ids)):
            _add_node(nid)

        return context

    # ── Helpers ────────────────────────────────────────────────────────

    def get_node_neighborhood(
        self, node_id: str, hops: int = 1
    ) -> Optional[nx.DiGraph]:
        """Return the neighborhood subgraph for a specific node."""
        if node_id not in self.graph:
            return None
        expanded = self._expand_nodes([node_id], hops=hops, max_nodes=100)
        return self.graph.subgraph(expanded).copy()

    @classmethod
    def load(
        cls,
        output_dir: str | Path,
        embedder: BaseEmbedder,
    ) -> "GraphRAG":
        """Load from a previously saved graph and vector store on disk."""
        output_dir = Path(output_dir)

        # Graph
        graph = nx.read_graphml(str(output_dir / "graph.graphml"))
        # Convert to DiGraph if needed
        if not isinstance(graph, nx.DiGraph):
            graph = nx.DiGraph(graph)

        # Nodes data
        with open(output_dir / "nodes.json", "r", encoding="utf-8") as f:
            nodes_data = json.load(f)

        # Vector store
        vector_store = VectorStore.load(output_dir / "vector_store")

        return cls(graph, vector_store, embedder, nodes_data)
