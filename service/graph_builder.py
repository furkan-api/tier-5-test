"""
Ontology-Driven Graph Builder — production-grade pipeline.

Reads the formal ontology definition, validates data, builds the graph in Neo4j.

Pipeline:
  1. Load & parse ontology (graph_data/ontology.json)
  2. Load & validate node files (graph_data/nodes/*.json)
  3. Load explicit edges (graph_data/edges/structural_edges.json)
  4. Embed node texts via configured embedder
  5. Upsert nodes + embeddings into Neo4j
  6. Create explicit edges in Neo4j
  7. Apply dynamic edge rules (graph_data/edges/edge_rules.json)
  8. Save build metadata
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
from .embedding_cache import EmbeddingCache
from .embeddings import BaseEmbedder, get_embedder
from .graph_store import (
    clear_graph,
    create_edges_batch,
    ensure_schema,
    save_build_meta,
    upsert_nodes_batch_simple,
)
from .neo4j_driver import execute_query, execute_write
from .vector_search import update_embeddings_batch

logger = logging.getLogger(__name__)


# ── Ontology ──────────────────────────────────────────────────────────────────

class Ontology:
    """Parsed ontology definition.

    Provides node/edge type validation, property checking,
    and schema-aware operations for the build pipeline.
    """

    def __init__(self, path: Path):
        with open(path, "r", encoding="utf-8") as f:
            self._raw = json.load(f)

        self.version: str = self._raw.get("version", "0.0.0")
        self.name: str = self._raw.get("name", "")
        self.domain: str = self._raw.get("domain", "")

        self.node_types: dict[str, dict] = self._raw.get("node_types", {})
        self.edge_types: dict[str, dict] = self._raw.get("edge_types", {})
        self.constraints: list[dict] = self._raw.get("constraints", [])
        self.indexes: list[dict] = self._raw.get("indexes", [])
        self.build_config: dict = self._raw.get("build_config", {})

        # Build reverse lookup: node_type string → ontology key
        self._type_enum_to_key: dict[str, str] = {}
        for key, typedef in self.node_types.items():
            props = typedef.get("properties", {})
            nt_prop = props.get("node_type", {})
            enums = nt_prop.get("enum", [])
            for e in enums:
                self._type_enum_to_key[e] = key

        logger.info(
            "Ontology loaded: %s v%s — %d node types, %d edge types",
            self.name, self.version,
            len(self.node_types), len(self.edge_types),
        )

    @property
    def valid_node_types(self) -> set[str]:
        """All valid node_type enum values."""
        return set(self._type_enum_to_key.keys())

    @property
    def valid_edge_types(self) -> set[str]:
        """All valid edge_type names."""
        return set(self.edge_types.keys())

    def get_node_type_def(self, node_type: str) -> dict | None:
        """Get the ontology definition for a node type."""
        key = self._type_enum_to_key.get(node_type)
        if key:
            return self.node_types.get(key)
        return None

    def get_edge_type_def(self, edge_type: str) -> dict | None:
        """Get the ontology definition for an edge type."""
        return self.edge_types.get(edge_type)

    def validate_node(self, node: dict) -> list[str]:
        """Validate a single node against the ontology.

        Returns list of validation error messages (empty == valid).
        """
        errors = []
        nid = node.get("node_id", "<missing>")

        # Required fields
        for field in ("node_id", "node_type", "embed_text"):
            if not node.get(field):
                errors.append(f"Node {nid}: missing required field '{field}'")

        # Node type check
        nt = node.get("node_type", "")
        if nt and nt not in self.valid_node_types:
            errors.append(f"Node {nid}: unknown node_type '{nt}'")

        # Embed text minimum length
        et = node.get("embed_text", "")
        if et and len(et) < 5:
            errors.append(f"Node {nid}: embed_text too short ({len(et)} chars)")

        return errors

    def validate_edge(self, edge: dict, node_ids: set[str]) -> list[str]:
        """Validate a single edge against the ontology.

        Returns list of validation error messages.
        """
        errors = []
        eid = edge.get("edge_id", "<missing>")

        for field in ("source", "target", "edge_type"):
            if not edge.get(field):
                errors.append(f"Edge {eid}: missing required field '{field}'")

        src = edge.get("source", "")
        tgt = edge.get("target", "")
        if src and src not in node_ids:
            errors.append(f"Edge {eid}: source '{src}' not found in node set")
        if tgt and tgt not in node_ids:
            errors.append(f"Edge {eid}: target '{tgt}' not found in node set")

        etype = edge.get("edge_type", "")
        if etype and etype not in self.valid_edge_types:
            errors.append(f"Edge {eid}: unknown edge_type '{etype}'")

        weight = edge.get("weight", 1.0)
        if not (0.0 <= weight <= 1.0):
            errors.append(f"Edge {eid}: weight {weight} out of range [0, 1]")

        return errors


# ── Helper Functions ──────────────────────────────────────────────────────────

def _resolve_field(node: dict[str, Any], field_path: str) -> Any:
    """Access nested fields via dot notation. E.g. 'metadata.kisaltma'."""
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
    """Ontology-driven graph builder for production workloads.

    Usage:
        builder = Neo4jGraphBuilder()
        result = await builder.build()   # Full pipeline

    Or step-by-step:
        builder = Neo4jGraphBuilder()
        builder.load_ontology()
        builder.load_nodes()
        builder.load_explicit_edges()
        await builder.ingest_nodes(embedder)
        await builder.ingest_explicit_edges()
        await builder.apply_edge_rules()
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.ontology: Ontology | None = None
        self.nodes: dict[str, dict[str, Any]] = {}
        self.explicit_edges: list[dict[str, Any]] = []
        self._edge_rules: list[dict[str, Any]] = []
        self._validation_errors: list[str] = []

    # ── Ontology Loading ──────────────────────────────────────────────────

    def load_ontology(self) -> "Neo4jGraphBuilder":
        """Load and parse the ontology definition."""
        ontology_path = self.settings.graph_data_dir / "ontology.json"
        if not ontology_path.exists():
            raise FileNotFoundError(
                f"Ontology file not found: {ontology_path}. "
                "A production build requires graph_data/ontology.json."
            )
        self.ontology = Ontology(ontology_path)
        return self

    # ── Node Loading ──────────────────────────────────────────────────────

    def load_nodes(self) -> "Neo4jGraphBuilder":
        """Load node data from JSON files in nodes/ directory.

        Uses the ontology build_config to determine which files to load.
        Falls back to scanning the nodes/ directory.
        """
        if not self.ontology:
            self.load_ontology()

        nodes_dir = self.settings.graph_data_dir / (
            self.ontology.build_config.get("node_files_dir", "nodes")
        )
        node_files = self.ontology.build_config.get("node_files", [])

        # If no explicit file list, scan directory
        if not node_files:
            node_files = [
                f.name for f in sorted(nodes_dir.glob("*.json"))
            ]

        total = 0
        for fname in node_files:
            fpath = nodes_dir / fname
            if not fpath.exists():
                logger.warning("Node file not found: %s", fpath)
                continue
            with open(fpath, "r", encoding="utf-8") as f:
                items = json.load(f)

            loaded = 0
            for item in items:
                # Validate against ontology
                errors = self.ontology.validate_node(item)
                if errors:
                    self._validation_errors.extend(errors)
                    logger.warning("Validation errors for %s: %s", item.get("node_id"), errors)
                    # Still load the node — log warnings but don't block
                self.nodes[item["node_id"]] = item
                loaded += 1

            total += loaded
            logger.info("Loaded: %s → %d nodes", fname, loaded)

        # Also load legacy flat files if they exist (backward compatibility)
        legacy_files = self.settings.data_files
        if legacy_files:
            for fname in legacy_files:
                fpath = self.settings.graph_data_dir / fname
                if not fpath.exists():
                    continue
                # Skip if nodes/ directory versions already loaded
                if any(n.endswith(fname) for n in node_files):
                    continue
                with open(fpath, "r", encoding="utf-8") as f:
                    items = json.load(f)
                for item in items:
                    if item["node_id"] not in self.nodes:
                        self.nodes[item["node_id"]] = item
                        total += 1
                logger.info("Legacy file: %s → loaded", fname)

        logger.info(
            "Total %d nodes loaded. Validation: %d warnings.",
            total, len(self._validation_errors),
        )
        return self

    # ── Explicit Edge Loading ─────────────────────────────────────────────

    def load_explicit_edges(self) -> "Neo4jGraphBuilder":
        """Load pre-computed structural edges from edges/ directory."""
        if not self.ontology:
            self.load_ontology()

        edges_dir = self.settings.graph_data_dir / (
            self.ontology.build_config.get("edge_files_dir", "edges")
        )
        edge_files = self.ontology.build_config.get("edge_files", [])

        if not edge_files:
            edge_files = [
                f.name for f in sorted(edges_dir.glob("*.json"))
                if f.name != "edge_rules.json"  # Rules loaded separately
            ]

        node_ids = set(self.nodes.keys())
        total = 0
        edge_errors = 0

        for fname in edge_files:
            fpath = edges_dir / fname
            if not fpath.exists():
                logger.warning("Edge file not found: %s", fpath)
                continue
            with open(fpath, "r", encoding="utf-8") as f:
                items = json.load(f)

            for edge in items:
                errors = self.ontology.validate_edge(edge, node_ids)
                if errors:
                    edge_errors += 1
                    # Only log first few errors to avoid spam
                    if edge_errors <= 20:
                        logger.warning("Edge validation: %s", errors)
                    continue  # Skip invalid edges
                self.explicit_edges.append(edge)

            total += len(items)
            logger.info("Loaded: %s → %d edges (%d valid)", fname, len(items), len(self.explicit_edges))

        if edge_errors > 20:
            logger.warning("... and %d more edge validation errors suppressed.", edge_errors - 20)

        logger.info(
            "Total %d explicit edges loaded (%d skipped due to validation).",
            len(self.explicit_edges), edge_errors,
        )
        return self

    # ── Edge Rule Loading ─────────────────────────────────────────────────

    def load_edge_rules(self) -> "Neo4jGraphBuilder":
        """Load dynamic edge rules from edges/edge_rules.json."""
        if not self.ontology:
            self.load_ontology()

        edges_dir = self.settings.graph_data_dir / (
            self.ontology.build_config.get("edge_files_dir", "edges")
        )
        rules_file = edges_dir / (
            self.ontology.build_config.get("edge_rules_file", "edge_rules.json")
        )

        # Fallback to old location
        if not rules_file.exists():
            rules_file = self.settings.edge_rules_path
        if not rules_file or not rules_file.exists():
            logger.warning("No edge rules file found. Skipping dynamic edges.")
            return self

        with open(rules_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._edge_rules = data.get("rules", [])
        logger.info("%d dynamic edge rules loaded.", len(self._edge_rules))
        return self

    # ── Node Ingestion ────────────────────────────────────────────────────

    async def ingest_nodes(
        self,
        embedder: BaseEmbedder | None = None,
        cache: EmbeddingCache | None = None,
    ) -> int:
        """Embed nodes and upsert into Neo4j.

        Uses the EmbeddingCache to skip already-embedded texts.
        Only texts with changed content or missing embeddings are sent
        to the embedder — everything else is served from cache.

        Returns the number of nodes ingested.
        """
        embedder = embedder or get_embedder()
        cache = cache or EmbeddingCache.load()

        node_ids = list(self.nodes.keys())
        texts = [self.nodes[nid].get("embed_text", "") for nid in node_ids]

        # 1. Partition into cached vs. needs-embedding
        to_embed_ids: list[str] = []
        to_embed_texts: list[str] = []
        cached_vecs: dict[str, np.ndarray] = {}

        for nid, text in zip(node_ids, texts):
            vec = cache.get(nid, text)
            if vec is not None:
                cached_vecs[nid] = vec
            else:
                to_embed_ids.append(nid)
                to_embed_texts.append(text)

        logger.info(
            "Embeddings: %d cached (%.0f%% hit), %d to embed",
            len(cached_vecs),
            cache.stats.hit_rate * 100,
            len(to_embed_ids),
        )

        # 2. Embed only the new / changed texts
        if to_embed_texts:
            logger.info(
                "Embedding %d texts with %s …",
                len(to_embed_texts), embedder.model_name,
            )
            t0 = time.time()
            new_vecs = embedder.embed_texts(to_embed_texts)
            logger.info("Embedding done in %.1fs.", time.time() - t0)

            for i, nid in enumerate(to_embed_ids):
                cache.put(nid, self.nodes[nid].get("embed_text", ""), new_vecs[i])
                cached_vecs[nid] = new_vecs[i]
        else:
            logger.info("All embeddings served from cache — nothing to embed.")

        # 3. Prune stale entries and save cache
        cache.prune(set(node_ids))
        cache.save()

        # 4. Prepare nodes with embedding vectors
        nodes_with_vecs = []
        for nid in node_ids:
            node = dict(self.nodes[nid])
            vec = cached_vecs[nid]
            node["embedding"] = vec.tolist() if isinstance(vec, np.ndarray) else vec
            nodes_with_vecs.append(node)

        # 5. Upsert into Neo4j
        logger.info("Upserting %d nodes into Neo4j …", len(nodes_with_vecs))
        count = await upsert_nodes_batch_simple(nodes_with_vecs)
        return count

    # ── Explicit Edge Ingestion ───────────────────────────────────────────

    async def ingest_explicit_edges(self) -> int:
        """Insert pre-computed structural edges into Neo4j.

        Returns the number of edges created.
        """
        if not self.explicit_edges:
            logger.info("No explicit edges to ingest.")
            return 0

        # Convert to the format expected by create_edges_batch
        edge_rows = []
        for e in self.explicit_edges:
            row = {
                "source": e["source"],
                "target": e["target"],
                "edge_type": e["edge_type"],
                "weight": e.get("weight", 1.0),
                "rule_id": f"explicit_{e.get('edge_id', '')}",
            }
            # Include additional properties
            props = e.get("properties", {})
            for k, v in props.items():
                if not isinstance(v, (dict, list)):
                    row[k] = v
            edge_rows.append(row)

        logger.info("Ingesting %d explicit edges …", len(edge_rows))
        count = await create_edges_batch(edge_rows)
        return count

    # ── Dynamic Edge Building ─────────────────────────────────────────────

    async def apply_edge_rules(self) -> int:
        """Apply dynamic edge rules and create relationships in Neo4j.

        Returns total edge count.
        """
        if not self._edge_rules:
            self.load_edge_rules()

        total_edges = 0
        for rule in self._edge_rules:
            cond_type = rule["condition"]["type"]

            handler = _CONDITION_HANDLERS.get(cond_type)
            if handler is None:
                logger.warning(
                    "Unknown condition type: %s (rule: %s)",
                    cond_type, rule["rule_id"],
                )
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

        logger.info("Total %d dynamic edges created.", total_edges)
        return total_edges

    # ── Full Pipeline ─────────────────────────────────────────────────────

    async def build(
        self,
        clean: bool = True,
        embedder: BaseEmbedder | None = None,
        validate_only: bool = False,
        cache: EmbeddingCache | None = None,
    ) -> dict[str, Any]:
        """Full ontology-driven build pipeline.

        Args:
            clean: If True, clears existing graph before building.
            embedder: Optional embedder override.
            validate_only: If True, only validates data without writing to Neo4j.
            cache: Optional embedding cache override.

        Returns build metadata dict.
        """
        t0 = time.time()

        # 1. Load ontology
        self.load_ontology()
        logger.info(
            "Ontology: %s v%s (%d node types, %d edge types)",
            self.ontology.name, self.ontology.version,
            len(self.ontology.node_types), len(self.ontology.edge_types),
        )

        # 2. Load data
        self.load_nodes()
        self.load_explicit_edges()
        self.load_edge_rules()

        # Validation summary
        if self._validation_errors:
            logger.warning(
                "⚠ %d validation warnings detected. Review logs for details.",
                len(self._validation_errors),
            )

        if validate_only:
            return {
                "mode": "validate_only",
                "nodes": len(self.nodes),
                "explicit_edges": len(self.explicit_edges),
                "edge_rules": len(self._edge_rules),
                "validation_errors": len(self._validation_errors),
                "errors": self._validation_errors[:50],
            }

        # 3. Schema setup
        await ensure_schema()

        # 4. Clear if requested
        if clean:
            logger.info("Clearing existing graph …")
            await clear_graph()

        # 5. Ingest nodes with embeddings (uses cache)
        node_count = await self.ingest_nodes(embedder, cache=cache)

        # 6. Ingest explicit edges
        explicit_edge_count = await self.ingest_explicit_edges()

        # 7. Apply dynamic edge rules
        dynamic_edge_count = await self.apply_edge_rules()

        total_edges = explicit_edge_count + dynamic_edge_count
        duration = time.time() - t0

        # Node type distribution
        node_types: dict[str, int] = {}
        for n in self.nodes.values():
            nt = n.get("node_type", "unknown")
            node_types[nt] = node_types.get(nt, 0) + 1

        # Edge type distribution
        edge_types: dict[str, int] = {}
        for e in self.explicit_edges:
            et = e.get("edge_type", "RELATED")
            edge_types[et] = edge_types.get(et, 0) + 1

        # Build metadata
        meta = {
            "version": "2.0",
            "ontology_version": self.ontology.version if self.ontology else "unknown",
            "built_at": datetime.now(timezone.utc).isoformat(),
            "build_duration_sec": round(duration, 2),
            "embedding_model": (embedder or get_embedder()).model_name,
            "embedding_dimension": self.settings.embedding_dimension,
            "total_nodes": node_count,
            "total_edges": total_edges,
            "explicit_edges": explicit_edge_count,
            "dynamic_edges": dynamic_edge_count,
            "validation_warnings": len(self._validation_errors),
            "data_files": json.dumps(
                self.ontology.build_config.get("node_files", [])
            ),
        }
        await save_build_meta(meta)

        logger.info(
            "Build complete: %d nodes, %d edges (%d explicit + %d dynamic) in %.1fs",
            node_count, total_edges, explicit_edge_count, dynamic_edge_count, duration,
        )
        return {**meta, "node_types": node_types, "edge_types": edge_types}

    # ── Embed-Only Pipeline ───────────────────────────────────────────

    async def embed_only(
        self,
        embedder: BaseEmbedder | None = None,
        cache: EmbeddingCache | None = None,
    ) -> dict[str, Any]:
        """Only embed data and persist to cache — no Neo4j interaction.

        Use this to pre-compute embeddings before building the graph,
        or to warm the cache for incremental updates.
        """
        t0 = time.time()
        embedder = embedder or get_embedder()
        cache = cache or EmbeddingCache.load()

        self.load_ontology()
        self.load_nodes()

        node_ids = list(self.nodes.keys())
        to_embed_ids: list[str] = []
        to_embed_texts: list[str] = []
        hits = 0

        for nid in node_ids:
            text = self.nodes[nid].get("embed_text", "")
            if cache.has(nid, text):
                hits += 1
            else:
                to_embed_ids.append(nid)
                to_embed_texts.append(text)

        logger.info(
            "Embed-only: %d total, %d cached, %d to embed",
            len(node_ids), hits, len(to_embed_ids),
        )

        if to_embed_texts:
            logger.info("Embedding %d texts …", len(to_embed_texts))
            vecs = embedder.embed_texts(to_embed_texts)
            for i, nid in enumerate(to_embed_ids):
                cache.put(nid, self.nodes[nid].get("embed_text", ""), vecs[i])

        cache.prune(set(node_ids))
        cache.save()

        summary = cache.summary()
        summary["duration_sec"] = round(time.time() - t0, 2)
        summary["newly_embedded"] = len(to_embed_ids)
        return summary

    # ── Incremental Update ────────────────────────────────────────────

    async def update(
        self,
        embedder: BaseEmbedder | None = None,
        cache: EmbeddingCache | None = None,
    ) -> dict[str, Any]:
        """Incremental update: embed missing, upsert new/changed, add edges.

        Unlike build(clean=True), this:
          • Does NOT clear the existing graph
          • Only embeds texts not already in the cache
          • Upserts all nodes (MERGE — creates or updates)
          • Re-creates all edges (idempotent via MERGE)

        Ideal for adding new data files or updating existing nodes.
        """
        return await self.build(
            clean=False,
            embedder=embedder,
            cache=cache,
        )


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

    sources = [
        n for n in builder.nodes.values()
        if _matches_node_type(n, rule["source_node_type"])
    ]
    targets = [
        n for n in builder.nodes.values()
        if _matches_node_type(n, rule["target_node_type"])
    ]

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
                "source": s["node_id"],
                "target": tid,
                "edge_type": edge_type,
                "weight": weight,
                "rule_id": rule["rule_id"],
            })
            if bidirectional:
                edges.append({
                    "source": tid,
                    "target": s["node_id"],
                    "edge_type": edge_type,
                    "weight": weight,
                    "rule_id": rule["rule_id"],
                })
    return edges


def _handle_metadata_list_contains(
    builder: Neo4jGraphBuilder, rule: dict
) -> list[dict]:
    """Edges where a source's list field contains the target node_id."""
    cond = rule["condition"]
    src_field = cond["source_field"]
    tgt_field = cond["target_field"]
    edge_type = rule["edge_type"]
    weight = rule.get("weight", 1.0)

    sources = [
        n for n in builder.nodes.values()
        if _matches_node_type(n, rule["source_node_type"])
    ]
    target_ids = {
        n["node_id"]
        for n in builder.nodes.values()
        if _matches_node_type(n, rule["target_node_type"])
    }

    edges = []
    for s in sources:
        id_list = _resolve_field(s, src_field)
        if not isinstance(id_list, list):
            continue
        for ref_id in id_list:
            if tgt_field == "node_id" and ref_id in target_ids:
                edges.append({
                    "source": s["node_id"],
                    "target": ref_id,
                    "edge_type": edge_type,
                    "weight": weight,
                    "rule_id": rule["rule_id"],
                })
    return edges


def _handle_field_equals_node_id(
    builder: Neo4jGraphBuilder, rule: dict
) -> list[dict]:
    """Edges where a source field equals the target's node_id."""
    cond = rule["condition"]
    src_field = cond["source_field"]
    edge_type = rule["edge_type"]
    weight = rule.get("weight", 1.0)

    sources = [
        n for n in builder.nodes.values()
        if _matches_node_type(n, rule["source_node_type"])
    ]
    target_ids = {
        n["node_id"]
        for n in builder.nodes.values()
        if _matches_node_type(n, rule["target_node_type"])
    }

    edges = []
    for s in sources:
        ref = _resolve_field(s, src_field)
        if ref and ref in target_ids:
            edges.append({
                "source": s["node_id"],
                "target": ref,
                "edge_type": edge_type,
                "weight": weight,
                "rule_id": rule["rule_id"],
            })
    return edges


def _handle_kanun_atif_parse(
    builder: Neo4jGraphBuilder, rule: dict
) -> list[dict]:
    """Parse law reference strings and create edges."""
    cond = rule["condition"]
    src_field = cond["source_field"]
    edge_type = rule["edge_type"]
    weight = rule.get("weight", 1.0)

    sources = [
        n for n in builder.nodes.values()
        if _matches_node_type(n, rule["source_node_type"])
    ]
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
                    "source": s["node_id"],
                    "target": target_id,
                    "edge_type": edge_type,
                    "weight": weight,
                    "rule_id": rule["rule_id"],
                })
    return edges


def _handle_id_list_reference(
    builder: Neo4jGraphBuilder, rule: dict
) -> list[dict]:
    """Create edges from references in a source's ID list field."""
    cond = rule["condition"]
    src_field = cond["source_field"]
    edge_type = rule["edge_type"]
    weight = rule.get("weight", 1.0)

    sources = [
        n for n in builder.nodes.values()
        if _matches_node_type(n, rule["source_node_type"])
    ]
    target_ids = {
        n["node_id"]
        for n in builder.nodes.values()
        if _matches_node_type(n, rule["target_node_type"])
    }

    edges = []
    for s in sources:
        ref_list = _resolve_field(s, src_field)
        if not isinstance(ref_list, list):
            continue
        for ref_id in ref_list:
            if ref_id in target_ids:
                edges.append({
                    "source": s["node_id"],
                    "target": ref_id,
                    "edge_type": edge_type,
                    "weight": weight,
                    "rule_id": rule["rule_id"],
                })
    return edges


def _handle_cosine_similarity(
    builder: Neo4jGraphBuilder, rule: dict
) -> list[dict]:
    """Cosine similarity edges — computed post-build via Neo4j vector index.

    Returns empty during main build; similarity edges are created separately
    using create_similarity_edges_neo4j() after the vector index is populated.
    """
    logger.info(
        "Cosine similarity edges will be computed post-build via Neo4j vector index. "
        "Skipping in-memory computation for large graphs."
    )
    return []


def _handle_contradictory_decisions(
    builder: Neo4jGraphBuilder, rule: dict
) -> list[dict]:
    """Edges between contradictory court decisions."""
    cond = rule["condition"]
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
    for field_name, group in by_field.items():
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
                for src, tgt in [
                    (a["node_id"], b["node_id"]),
                    (b["node_id"], a["node_id"]),
                ]:
                    edges.append({
                        "source": src,
                        "target": tgt,
                        "edge_type": edge_type,
                        "weight": weight,
                        "rule_id": rule["rule_id"],
                        "hukuk_dali": field_name,
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
    edge_type: str = "SEMANTIK_BENZER",
    batch_size: int = 100,
) -> int:
    """Create cosine similarity edges using Neo4j's vector index.

    This is the production-grade replacement for the in-memory FAISS
    pairwise similarity computation. Runs entirely in the database.
    """
    s = get_settings()

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

            CALL {
                WITH n
                CALL db.index.vector.queryNodes($index_name, $k, n.embedding)
                YIELD node AS similar, score
                WHERE similar <> n
                  AND score >= $threshold
                  AND NOT EXISTS { (n)-[:RELATED {edge_type: $edge_type}]->(similar) }
                RETURN similar, score
            }

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
            remaining = await execute_query(
                "MATCH (n:LegalNode) WHERE n.embedding IS NOT NULL "
                "RETURN count(n) AS cnt"
            )
            if remaining and offset >= remaining[0]["cnt"]:
                break

        logger.debug("Similarity edges batch: offset=%d, created=%d", offset, created)

    logger.info("Created %d similarity edges via Neo4j vector index.", total)
    return total
