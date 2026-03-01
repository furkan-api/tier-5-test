"""
Graph Builder — loads JSON data, embeds text, creates edges in Neo4j.

Replaces the old NetworkX-based GraphBuilder. Instead of building an
in-memory graph and saving to disk, this writes directly into Neo4j.

Pipeline:
  1. Load JSON data files
  2. Load edge rules
  3. Upsert nodes into Neo4j (with embeddings)
  4. Apply edge rules → create relationships in Neo4j
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np

from .config import Settings, get_settings
from .embeddings import BaseEmbedder, get_embedder
from .graph_store import (
    clear_graph,
    create_edges_batch,
    create_typed_edges_batch,
    ensure_schema,
    save_build_meta,
    upsert_nodes_batch_simple,
)
from .neo4j_driver import execute_query, execute_write
from .vector_search import update_embeddings_batch

logger = logging.getLogger(__name__)


# ── Helper: resolve nested field ──────────────────────────────────────────────

def _resolve_field(node: dict[str, Any], field_path: str) -> Any:
    """Access nested fields via dot notation. E.g. 'metadata.kisaltma'"""
    parts = field_path.split(".")
    current = node
    for p in parts:
        if isinstance(current, dict):
            current = current.get(p)
        else:
            return None
        if current is None:
            return None
    return current


def _parse_kanun_atif(atif: str) -> Optional[str]:
    """Convert law reference strings to node IDs."""
    kanun_no_to_kisaltma = {
        "6098": "TBK",
        "4721": "TMK",
        "4857": "IK",
        "6100": "HMK",
    }
    parts = atif.split("/")
    if len(parts) != 2:
        return None
    kanun_part, madde_no = parts
    kisaltma = kanun_part.strip()
    if kisaltma.isdigit():
        kisaltma = kanun_no_to_kisaltma.get(kisaltma, kisaltma)
    return f"{kisaltma}_M{madde_no.strip()}"


def _matches_node_type(node: dict, rule_node_type) -> bool:
    """Check whether a node's type matches the rule's node type."""
    if rule_node_type == "*":
        return True
    nt = node.get("node_type", "")
    if isinstance(rule_node_type, list):
        return nt in rule_node_type
    return nt == rule_node_type


# ── Main Builder ──────────────────────────────────────────────────────────────

class Neo4jGraphBuilder:
    """Builds or rebuilds the graph in Neo4j from JSON source data.

    Usage:
        builder = Neo4jGraphBuilder()
        await builder.build()   # Full pipeline

    Or step-by-step:
        builder = Neo4jGraphBuilder()
        builder.load_data()
        builder.load_edge_rules()
        await builder.ingest_nodes(embedder)
        await builder.apply_edge_rules()
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.nodes: dict[str, dict[str, Any]] = {}
        self.data_files: list[str] = list(self.settings.data_files)
        self._edge_rules: list[dict[str, Any]] = []

    # ── Data Loading (local, same as before) ──────────────────────────────

    def load_data(self) -> "Neo4jGraphBuilder":
        """Load node data from JSON files."""
        total = 0
        for fname in self.data_files:
            fpath = self.settings.graph_data_dir / fname
            if not fpath.exists():
                logger.warning("File not found: %s", fpath)
                continue
            with open(fpath, "r", encoding="utf-8") as f:
                items = json.load(f)
            for item in items:
                self.nodes[item["node_id"]] = item
            total += len(items)
            logger.info("Loaded: %s → %d nodes", fname, len(items))

        logger.info("Total %d nodes loaded from disk.", total)
        return self

    def load_edge_rules(self) -> "Neo4jGraphBuilder":
        """Load edge rules from JSON."""
        with open(self.settings.edge_rules_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._edge_rules = data.get("rules", [])
        logger.info("%d edge rules loaded.", len(self._edge_rules))
        return self

    # ── Node Ingestion ────────────────────────────────────────────────────

    async def ingest_nodes(self, embedder: BaseEmbedder | None = None) -> int:
        """Embed all nodes and upsert into Neo4j with embeddings.

        Returns the number of nodes ingested.
        """
        embedder = embedder or get_embedder()

        node_ids = list(self.nodes.keys())
        texts = [self.nodes[nid].get("embed_text", "") for nid in node_ids]

        # 1. Generate embeddings
        logger.info("Embedding %d texts with %s …", len(texts), embedder.model_name)
        t0 = time.time()
        embeddings = embedder.embed_texts(texts)
        logger.info("Embedding done in %.1fs.", time.time() - t0)

        # 2. Prepare nodes with embedding vectors
        nodes_with_vecs = []
        for i, nid in enumerate(node_ids):
            node = dict(self.nodes[nid])
            node["embedding"] = embeddings[i].tolist()
            nodes_with_vecs.append(node)

        # 3. Upsert into Neo4j
        logger.info("Upserting %d nodes into Neo4j …", len(nodes_with_vecs))
        count = await upsert_nodes_batch_simple(nodes_with_vecs)
        return count

    # ── Edge Building ─────────────────────────────────────────────────────

    async def apply_edge_rules(self) -> int:
        """Apply all edge rules and create relationships in Neo4j.

        Returns total edge count.
        """
        if not self._edge_rules:
            self.load_edge_rules()

        total_edges = 0
        for rule in self._edge_rules:
            cond_type = rule["condition"]["type"]

            handler = _CONDITION_HANDLERS.get(cond_type)
            if handler is None:
                logger.warning("Unknown condition type: %s (rule: %s)", cond_type, rule["rule_id"])
                continue

            t0 = time.time()
            edges = handler(self, rule)
            count = len(edges)

            if edges:
                await create_edges_batch(edges)

            logger.info(
                "Rule '%s' → %d edges (%.1fs)",
                rule["rule_id"], count, time.time() - t0,
            )
            total_edges += count

        logger.info("Total %d edges created.", total_edges)
        return total_edges

    # ── Full Pipeline ─────────────────────────────────────────────────────

    async def build(
        self,
        clean: bool = True,
        embedder: BaseEmbedder | None = None,
    ) -> dict[str, Any]:
        """Full build pipeline.

        Args:
            clean: If True, clears existing graph before building.
            embedder: Optional embedder override.

        Returns build metadata dict.
        """
        t0 = time.time()

        # Schema
        await ensure_schema()

        # Clear if requested
        if clean:
            logger.info("Clearing existing graph …")
            await clear_graph()

        # Load data from disk
        self.load_data()
        self.load_edge_rules()

        # Ingest nodes
        node_count = await self.ingest_nodes(embedder)

        # Apply edge rules
        edge_count = await self.apply_edge_rules()

        duration = time.time() - t0

        # Node type distribution
        node_types: dict[str, int] = {}
        for n in self.nodes.values():
            nt = n.get("node_type", "unknown")
            node_types[nt] = node_types.get(nt, 0) + 1

        # Build metadata
        meta = {
            "version": "2.0",
            "built_at": datetime.now(timezone.utc).isoformat(),
            "build_duration_sec": round(duration, 2),
            "embedding_model": (embedder or get_embedder()).model_name,
            "embedding_dimension": self.settings.embedding_dimension,
            "total_nodes": node_count,
            "total_edges": edge_count,
            "data_files": json.dumps(self.data_files),
        }
        await save_build_meta(meta)

        logger.info(
            "Build complete: %d nodes, %d edges in %.1fs",
            node_count, edge_count, duration,
        )
        return {**meta, "node_types": node_types}


# ── Edge Rule Handlers ────────────────────────────────────────────────────────
# These work on the in-memory self.nodes dict to compute edges,
# then return edge dicts for batch insertion into Neo4j.


def _handle_metadata_match(builder: Neo4jGraphBuilder, rule: dict) -> list[dict]:
    """Edges where two nodes share equal metadata field values."""
    cond = rule["condition"]
    src_field = cond["source_field"]
    tgt_field = cond["target_field"]
    edge_type = rule["edge_type"]
    weight = rule.get("weight", 1.0)
    bidirectional = rule.get("bidirectional", False)

    sources = [n for n in builder.nodes.values() if _matches_node_type(n, rule["source_node_type"])]
    targets = [n for n in builder.nodes.values() if _matches_node_type(n, rule["target_node_type"])]

    target_index: dict[Any, list[str]] = {}
    for t in targets:
        val = _resolve_field(t, tgt_field)
        if val is not None:
            target_index.setdefault(val, []).append(t["node_id"])

    edges = []
    for s in sources:
        s_val = _resolve_field(s, src_field)
        if s_val is None:
            continue
        for tid in target_index.get(s_val, []):
            if s["node_id"] == tid:
                continue
            edges.append({
                "source": s["node_id"], "target": tid,
                "edge_type": edge_type, "weight": weight,
                "rule_id": rule["rule_id"],
            })
            if bidirectional:
                edges.append({
                    "source": tid, "target": s["node_id"],
                    "edge_type": edge_type, "weight": weight,
                    "rule_id": rule["rule_id"],
                })
    return edges


def _handle_metadata_list_contains(builder: Neo4jGraphBuilder, rule: dict) -> list[dict]:
    """Edges where a source's list field contains the target node_id."""
    cond = rule["condition"]
    src_field = cond["source_field"]
    tgt_field = cond["target_field"]
    edge_type = rule["edge_type"]
    weight = rule.get("weight", 1.0)

    sources = [n for n in builder.nodes.values() if _matches_node_type(n, rule["source_node_type"])]
    target_ids = {n["node_id"] for n in builder.nodes.values() if _matches_node_type(n, rule["target_node_type"])}

    edges = []
    for s in sources:
        id_list = _resolve_field(s, src_field)
        if not isinstance(id_list, list):
            continue
        for ref_id in id_list:
            if tgt_field == "node_id" and ref_id in target_ids:
                edges.append({
                    "source": s["node_id"], "target": ref_id,
                    "edge_type": edge_type, "weight": weight,
                    "rule_id": rule["rule_id"],
                })
    return edges


def _handle_field_equals_node_id(builder: Neo4jGraphBuilder, rule: dict) -> list[dict]:
    """Edges where a source field equals the target's node_id."""
    cond = rule["condition"]
    src_field = cond["source_field"]
    edge_type = rule["edge_type"]
    weight = rule.get("weight", 1.0)

    sources = [n for n in builder.nodes.values() if _matches_node_type(n, rule["source_node_type"])]
    target_ids = {n["node_id"] for n in builder.nodes.values() if _matches_node_type(n, rule["target_node_type"])}

    edges = []
    for s in sources:
        ref = _resolve_field(s, src_field)
        if ref and ref in target_ids:
            edges.append({
                "source": s["node_id"], "target": ref,
                "edge_type": edge_type, "weight": weight,
                "rule_id": rule["rule_id"],
            })
    return edges


def _handle_kanun_atif_parse(builder: Neo4jGraphBuilder, rule: dict) -> list[dict]:
    """Parse law reference strings and create edges."""
    cond = rule["condition"]
    src_field = cond["source_field"]
    edge_type = rule["edge_type"]
    weight = rule.get("weight", 1.0)

    sources = [n for n in builder.nodes.values() if _matches_node_type(n, rule["source_node_type"])]
    all_ids = set(builder.nodes.keys())

    edges = []
    for s in sources:
        atif_list = _resolve_field(s, src_field)
        if not isinstance(atif_list, list):
            continue
        for atif_str in atif_list:
            target_id = _parse_kanun_atif(atif_str)
            if target_id and target_id in all_ids:
                edges.append({
                    "source": s["node_id"], "target": target_id,
                    "edge_type": edge_type, "weight": weight,
                    "rule_id": rule["rule_id"],
                })
    return edges


def _handle_id_list_reference(builder: Neo4jGraphBuilder, rule: dict) -> list[dict]:
    """Create edges from references in a source's ID list field."""
    cond = rule["condition"]
    src_field = cond["source_field"]
    edge_type = rule["edge_type"]
    weight = rule.get("weight", 1.0)

    sources = [n for n in builder.nodes.values() if _matches_node_type(n, rule["source_node_type"])]
    target_ids = {n["node_id"] for n in builder.nodes.values() if _matches_node_type(n, rule["target_node_type"])}

    edges = []
    for s in sources:
        ref_list = _resolve_field(s, src_field)
        if not isinstance(ref_list, list):
            continue
        for ref_id in ref_list:
            if ref_id in target_ids:
                edges.append({
                    "source": s["node_id"], "target": ref_id,
                    "edge_type": edge_type, "weight": weight,
                    "rule_id": rule["rule_id"],
                })
    return edges


def _handle_cosine_similarity(builder: Neo4jGraphBuilder, rule: dict) -> list[dict]:
    """Cosine similarity edges — computed via Neo4j vector search after build.

    For the initial build, this creates edges based on pairwise similarity
    computed in Python. For production, consider running this as a
    post-build Cypher job using the vector index.
    """
    logger.info(
        "Cosine similarity edges will be computed post-build via Neo4j vector index. "
        "Skipping in-memory computation for large graphs."
    )
    # Return empty — these will be created via a separate post-build step
    # using Neo4j's native vector similarity
    return []


def _handle_contradictory_decisions(builder: Neo4jGraphBuilder, rule: dict) -> list[dict]:
    """Edges between contradictory court decisions.

    Same logic as the original, but returns edge dicts
    instead of adding to networkx directly.
    """
    cond = rule["condition"]
    sim_threshold = cond.get("similarity_threshold", 0.70)
    edge_type = rule["edge_type"]
    weight = rule.get("weight", 0.8)

    LEHTE = {"kabul", "kısmen kabul", "onama"}
    ALEYHTE = {"ret", "red", "bozma", "kısmi bozma", "bozma (usul)"}

    def _outcome_group(sonuc: str) -> str | None:
        s = sonuc.strip().lower()
        if s in LEHTE:
            return "LEHTE"
        if s in ALEYHTE:
            return "ALEYHTE"
        return None

    decisions = []
    for n in builder.nodes.values():
        if n.get("node_type") != "karar":
            continue
        meta = n.get("metadata", {})
        sonuc = meta.get("sonuc", "")
        grp = _outcome_group(sonuc)
        if grp is None:
            continue
        hukuk = meta.get("hukuk_dali", "")
        hukuk_root = hukuk.split(" - ")[0].strip().lower() if hukuk else ""
        decisions.append({
            "node_id": n["node_id"],
            "hukuk_root": hukuk_root,
            "outcome_group": grp,
        })

    by_field: dict[str, list] = {}
    for d in decisions:
        by_field.setdefault(d["hukuk_root"], []).append(d)

    edges = []
    seen = set()
    for field, group in by_field.items():
        if len(group) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if a["outcome_group"] == b["outcome_group"]:
                    continue
                pair_key = tuple(sorted([a["node_id"], b["node_id"]]))
                if pair_key in seen:
                    continue
                seen.add(pair_key)
                # Bidirectional
                for src, tgt in [(a["node_id"], b["node_id"]), (b["node_id"], a["node_id"])]:
                    edges.append({
                        "source": src, "target": tgt,
                        "edge_type": edge_type, "weight": weight,
                        "rule_id": rule["rule_id"],
                        "hukuk_dali": field,
                    })

    return edges


# Condition type → handler mapping
_CONDITION_HANDLERS = {
    "metadata_match": _handle_metadata_match,
    "metadata_list_contains": _handle_metadata_list_contains,
    "field_equals_node_id": _handle_field_equals_node_id,
    "kanun_atif_parse": _handle_kanun_atif_parse,
    "id_list_reference": _handle_id_list_reference,
    "cosine_similarity": _handle_cosine_similarity,
    "contradictory_decisions": _handle_contradictory_decisions,
}


# ── Post-Build: Similarity Edges via Neo4j ────────────────────────────────────

async def create_similarity_edges_neo4j(
    threshold: float = 0.82,
    max_neighbors: int = 5,
    edge_type: str = "BENZER_ANLAM",
    batch_size: int = 100,
) -> int:
    """Create cosine similarity edges using Neo4j's vector index.

    This is the production-grade replacement for the in-memory FAISS
    pairwise similarity computation. Runs entirely in the database.
    """
    s = get_settings()

    # Process in batches to avoid memory issues on huge graphs
    total = 0
    offset = 0

    while True:
        records = await execute_query(
            """
            MATCH (n:LegalNode)
            WHERE n.embedding IS NOT NULL
            WITH n
            ORDER BY n.node_id
            SKIP $offset LIMIT $batch_size

            // For each node, find similar nodes via vector index
            CALL {
                WITH n
                CALL db.index.vector.queryNodes($index_name, $k, n.embedding)
                YIELD node AS similar, score
                WHERE similar <> n
                  AND score >= $threshold
                  AND NOT EXISTS { (n)-[:RELATED {edge_type: $edge_type}]->(similar) }
                RETURN similar, score
            }

            // Create edges
            MERGE (n)-[r:RELATED {edge_type: $edge_type}]->(similar)
            SET r.weight = score, r.similarity = score
            RETURN count(r) AS created
            """,
            {
                "index_name": s.vector_index_name,
                "k": max_neighbors + 1,
                "threshold": threshold,
                "edge_type": edge_type,
                "offset": offset,
                "batch_size": batch_size,
            },
        )

        created = records[0]["created"] if records else 0
        total += created
        offset += batch_size

        if created == 0 and offset > 0:
            # Check if we've processed all nodes
            remaining = await execute_query(
                "MATCH (n:LegalNode) WHERE n.embedding IS NOT NULL "
                "RETURN count(n) AS cnt"
            )
            if remaining and offset >= remaining[0]["cnt"]:
                break

        logger.debug("Similarity edges batch: offset=%d, created=%d", offset, created)

    logger.info("Created %d similarity edges via Neo4j vector index.", total)
    return total
