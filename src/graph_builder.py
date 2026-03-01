"""
Graph Builder - Constructs a NetworkX graph from JSON data.

Reads edge rules from edge_rules.json and evaluates conditions
to create edges between nodes.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import networkx as nx
import numpy as np

from .config import (
    DATA_FILES,
    EDGE_RULES_PATH,
    GRAPH_DATA_DIR,
)
from .embeddings.base import BaseEmbedder
from .vector_store import VectorStore

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Helper functions
# ──────────────────────────────────────────────────────────────────────────────

def _resolve_field(node: Dict[str, Any], field_path: str) -> Any:
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
    """
    Convert law reference strings to node IDs.
    E.g. 'TMK/706' → 'TMK_M706', '4857/18' → 'IK_M18'
    """
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
    # TMK/706 format
    kisaltma = kanun_part.strip()
    # Numeric law number check: 4857/18
    if kisaltma.isdigit():
        kisaltma = kanun_no_to_kisaltma.get(kisaltma, kisaltma)
    return f"{kisaltma}_M{madde_no.strip()}"


# ──────────────────────────────────────────────────────────────────────────────
# GraphBuilder
# ──────────────────────────────────────────────────────────────────────────────

class GraphBuilder:
    """
    Loads nodes from JSON data, generates embeddings, applies edge rules,
    and builds a NetworkX DiGraph.
    """

    def __init__(
        self,
        embedder: BaseEmbedder,
        edge_rules_path: str | Path = EDGE_RULES_PATH,
        data_dir: str | Path = GRAPH_DATA_DIR,
        data_files: List[str] | None = None,
    ):
        self.embedder = embedder
        self.edge_rules_path = Path(edge_rules_path)
        self.data_dir = Path(data_dir)
        self.data_files = data_files or DATA_FILES

        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.graph = nx.DiGraph()
        self.vector_store: Optional[VectorStore] = None
        self._edge_rules: List[Dict[str, Any]] = []

    # ── Data loading ─────────────────────────────────────────────────────

    def load_data(self) -> "GraphBuilder":
        """Load JSON files from the graph_data/ directory."""
        total = 0
        for fname in self.data_files:
            fpath = self.data_dir / fname
            if not fpath.exists():
                logger.warning("File not found: %s", fpath)
                continue
            with open(fpath, "r", encoding="utf-8") as f:
                items = json.load(f)
            for item in items:
                nid = item["node_id"]
                self.nodes[nid] = item
                # Add as NetworkX node
                self.graph.add_node(
                    nid,
                    node_type=item.get("node_type"),
                    embed_text=item.get("embed_text", ""),
                    **item.get("metadata", {}),
                )
            total += len(items)
            logger.info("Loaded: %s -> %d nodes", fname, len(items))

        logger.info("Total %d nodes loaded.", total)
        return self

    # ── Edge rules loading ────────────────────────────────────────────────

    def load_edge_rules(self) -> "GraphBuilder":
        """Load edge_rules.json."""
        with open(self.edge_rules_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._edge_rules = data.get("rules", [])
        logger.info("%d edge rules loaded.", len(self._edge_rules))
        return self

    # ── Embedding ─────────────────────────────────────────────────────────

    def build_embeddings(self) -> "GraphBuilder":
        """Convert all node embed_text fields into vectors."""
        node_ids = list(self.nodes.keys())
        texts = [self.nodes[nid].get("embed_text", "") for nid in node_ids]

        logger.info("Embedding %d texts (%s) ...", len(texts), self.embedder)
        embeddings = self.embedder.embed_texts(texts)

        self.vector_store = VectorStore(
            dimension=embeddings.shape[1],
        )
        self.vector_store.add(node_ids, embeddings)

        # Add vector index to graph nodes
        for i, nid in enumerate(node_ids):
            self.graph.nodes[nid]["_vec_idx"] = i

        logger.info("Embedding complete. VectorStore size: %d", self.vector_store.size)
        return self

    # ── Edge building ────────────────────────────────────────────────────

    def build_edges(self) -> "GraphBuilder":
        """Create all edges according to loaded rules."""
        if not self._edge_rules:
            self.load_edge_rules()

        total_edges = 0
        for rule in self._edge_rules:
            condition_type = rule["condition"]["type"]
            handler = _CONDITION_HANDLERS.get(condition_type)
            if handler is None:
                logger.warning("Unknown condition type: %s (rule: %s)", condition_type, rule["rule_id"])
                continue

            count = handler(self, rule)
            total_edges += count
            logger.info("Rule '%s' -> %d edges created.", rule["rule_id"], count)

        logger.info("Total %d edges created. Graph: %d nodes, %d edges",
                     total_edges, self.graph.number_of_nodes(), self.graph.number_of_edges())
        return self

    def build(self) -> nx.DiGraph:
        """Full pipeline: load data -> embed -> build edges."""
        self.load_data()
        self.load_edge_rules()
        self.build_embeddings()
        self.build_edges()
        return self.graph

    # ── Export ─────────────────────────────────────────────────────────

    def save(self, output_dir: str | Path) -> None:
        """Save graph and vector store to disk."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # NetworkX graph -> GraphML
        # Remove internal fields like _vec_idx
        g_export = self.graph.copy()
        for nid in g_export.nodes:
            g_export.nodes[nid].pop("_vec_idx", None)
            for key, val in list(g_export.nodes[nid].items()):
                if val is None:
                    del g_export.nodes[nid][key]
                elif isinstance(val, (list, dict)):
                    g_export.nodes[nid][key] = json.dumps(val, ensure_ascii=False)
        # Also sanitize edge attributes
        for u, v, data in g_export.edges(data=True):
            for key, val in list(data.items()):
                if val is None:
                    del data[key]
                elif isinstance(val, (list, dict)):
                    data[key] = json.dumps(val, ensure_ascii=False)

        nx.write_graphml(g_export, str(output_dir / "graph.graphml"))

        # Node data (JSON)
        with open(output_dir / "nodes.json", "w", encoding="utf-8") as f:
            json.dump(self.nodes, f, ensure_ascii=False, indent=2)

        # Edge list (JSON)
        edges_data = []
        for u, v, data in self.graph.edges(data=True):
            edges_data.append({"source": u, "target": v, **data})
        with open(output_dir / "edges.json", "w", encoding="utf-8") as f:
            json.dump(edges_data, f, ensure_ascii=False, indent=2)

        # Vector store
        if self.vector_store:
            self.vector_store.save(output_dir / "vector_store")

        logger.info("Graph and data saved: %s", output_dir)


# ──────────────────────────────────────────────────────────────────────────────
# Condition handlers
# ──────────────────────────────────────────────────────────────────────────────

def _matches_node_type(node: Dict, rule_node_type) -> bool:
    """Check whether a node's type matches the rule's node type."""
    if rule_node_type == "*":
        return True
    nt = node.get("node_type", "")
    if isinstance(rule_node_type, list):
        return nt in rule_node_type
    return nt == rule_node_type


def _handle_metadata_match(builder: GraphBuilder, rule: Dict) -> int:
    """Create edges where two nodes share equal metadata field values."""
    cond = rule["condition"]
    src_field = cond["source_field"]
    tgt_field = cond["target_field"]
    edge_type = rule["edge_type"]
    weight = rule.get("weight", 1.0)
    bidirectional = rule.get("bidirectional", False)

    count = 0
    sources = [n for n in builder.nodes.values() if _matches_node_type(n, rule["source_node_type"])]
    targets = [n for n in builder.nodes.values() if _matches_node_type(n, rule["target_node_type"])]

    # Index targets by field value
    target_index: Dict[Any, List[str]] = {}
    for t in targets:
        val = _resolve_field(t, tgt_field)
        if val is not None:
            target_index.setdefault(val, []).append(t["node_id"])

    for s in sources:
        s_val = _resolve_field(s, src_field)
        if s_val is None:
            continue
        matched_targets = target_index.get(s_val, [])
        for tid in matched_targets:
            if s["node_id"] == tid:
                continue  # No self-loops
            builder.graph.add_edge(s["node_id"], tid, edge_type=edge_type, weight=weight, rule_id=rule["rule_id"])
            count += 1
            if bidirectional:
                builder.graph.add_edge(tid, s["node_id"], edge_type=edge_type, weight=weight, rule_id=rule["rule_id"])
                count += 1

    return count


def _handle_metadata_list_contains(builder: GraphBuilder, rule: Dict) -> int:
    """Create edges where a source's list field contains the target node_id."""
    cond = rule["condition"]
    src_field = cond["source_field"]
    tgt_field = cond["target_field"]
    edge_type = rule["edge_type"]
    weight = rule.get("weight", 1.0)

    count = 0
    sources = [n for n in builder.nodes.values() if _matches_node_type(n, rule["source_node_type"])]
    target_ids = {n["node_id"] for n in builder.nodes.values() if _matches_node_type(n, rule["target_node_type"])}

    for s in sources:
        id_list = _resolve_field(s, src_field)
        if not isinstance(id_list, list):
            continue
        for ref_id in id_list:
            if tgt_field == "node_id" and ref_id in target_ids:
                builder.graph.add_edge(s["node_id"], ref_id, edge_type=edge_type, weight=weight, rule_id=rule["rule_id"])
                count += 1

    return count


def _handle_field_equals_node_id(builder: GraphBuilder, rule: Dict) -> int:
    """Create edges where a source field equals the target's node_id."""
    cond = rule["condition"]
    src_field = cond["source_field"]
    edge_type = rule["edge_type"]
    weight = rule.get("weight", 1.0)

    count = 0
    sources = [n for n in builder.nodes.values() if _matches_node_type(n, rule["source_node_type"])]
    target_ids = {n["node_id"] for n in builder.nodes.values() if _matches_node_type(n, rule["target_node_type"])}

    for s in sources:
        ref = _resolve_field(s, src_field)
        if ref and ref in target_ids:
            builder.graph.add_edge(s["node_id"], ref, edge_type=edge_type, weight=weight, rule_id=rule["rule_id"])
            count += 1

    return count


def _handle_kanun_atif_parse(builder: GraphBuilder, rule: Dict) -> int:
    """Parse law reference strings (e.g. 'TMK/706') and link to article nodes."""
    cond = rule["condition"]
    src_field = cond["source_field"]
    edge_type = rule["edge_type"]
    weight = rule.get("weight", 1.0)

    count = 0
    sources = [n for n in builder.nodes.values() if _matches_node_type(n, rule["source_node_type"])]
    all_node_ids = set(builder.nodes.keys())

    for s in sources:
        atif_list = _resolve_field(s, src_field)
        if not isinstance(atif_list, list):
            continue
        for atif_str in atif_list:
            target_id = _parse_kanun_atif(atif_str)
            if target_id and target_id in all_node_ids:
                builder.graph.add_edge(
                    s["node_id"], target_id,
                    edge_type=edge_type, weight=weight,
                    rule_id=rule["rule_id"],
                    atif_raw=atif_str,
                )
                count += 1

    return count


def _handle_id_list_reference(builder: GraphBuilder, rule: Dict) -> int:
    """Create edges from references in a source's ID list field."""
    cond = rule["condition"]
    src_field = cond["source_field"]
    edge_type = rule["edge_type"]
    weight = rule.get("weight", 1.0)

    count = 0
    sources = [n for n in builder.nodes.values() if _matches_node_type(n, rule["source_node_type"])]
    target_ids = {n["node_id"] for n in builder.nodes.values() if _matches_node_type(n, rule["target_node_type"])}

    for s in sources:
        ref_list = _resolve_field(s, src_field)
        if not isinstance(ref_list, list):
            continue
        for ref_id in ref_list:
            if ref_id in target_ids:
                builder.graph.add_edge(
                    s["node_id"], ref_id,
                    edge_type=edge_type, weight=weight,
                    rule_id=rule["rule_id"],
                )
                count += 1

    return count


def _handle_cosine_similarity(builder: GraphBuilder, rule: Dict) -> int:
    """Create edges based on embedding cosine similarity."""
    if builder.vector_store is None:
        logger.warning("VectorStore not available; skipping semantic similarity edges.")
        return 0

    cond = rule["condition"]
    threshold = cond.get("threshold", 0.82)
    max_neighbors = cond.get("max_neighbors", 5)
    edge_type = rule["edge_type"]
    bidirectional = rule.get("bidirectional", True)

    pairs = builder.vector_store.find_similar_pairs(
        threshold=threshold,
        max_neighbors=max_neighbors,
    )

    count = 0
    existing_edges = set(builder.graph.edges())
    exclude_existing = cond.get("exclude_existing_edges", True)

    for id_a, id_b, sim in pairs:
        if exclude_existing and (id_a, id_b) in existing_edges:
            continue
        weight = sim if rule.get("weight_from_similarity", False) else rule.get("weight", 1.0)
        builder.graph.add_edge(
            id_a, id_b,
            edge_type=edge_type, weight=weight,
            rule_id=rule["rule_id"],
            similarity=sim,
        )
        count += 1
        if bidirectional:
            if not (exclude_existing and (id_b, id_a) in existing_edges):
                builder.graph.add_edge(
                    id_b, id_a,
                    edge_type=edge_type, weight=weight,
                    rule_id=rule["rule_id"],
                    similarity=sim,
                )
                count += 1

    return count


def _handle_contradictory_decisions(builder: GraphBuilder, rule: Dict) -> int:
    """Create edges between court decisions that address the same legal topic
    but reach opposing outcomes.

    Logic:
      1. Collect all 'karar' nodes.
      2. Normalise each decision's hukuk_dali to a canonical root so that
         'iş hukuku - usul hukuku' and 'iş hukuku' are treated as the same
         legal field.
      3. Within each field, compute pairwise embedding similarity.
      4. If two decisions are semantically close (same topic) **and** their
         outcomes fall into opposing groups, connect them.

    Outcome groups:
      LEHTE  (plaintiff/worker favourable): kabul, kısmen kabul, ONAMA
      ALEYHTE (defendant/employer favourable / reversal): ret, RED, BOZMA,
              KISMI BOZMA, BOZMA (usul)
      Decisions not in either group (e.g. HGK EMSAL KARARI) are skipped.
    """
    if builder.vector_store is None:
        logger.warning("VectorStore not available; skipping contradictory decision edges.")
        return 0

    cond = rule["condition"]
    sim_threshold = cond.get("similarity_threshold", 0.70)
    edge_type = rule["edge_type"]
    weight = rule.get("weight", 0.8)

    # ── Outcome classification ────────────────────────────────────────
    LEHTE = {"kabul", "kısmen kabul", "onama"}
    ALEYHTE = {"ret", "red", "bozma", "kısmi bozma", "bozma (usul)"}

    def _outcome_group(sonuc: str) -> str | None:
        s = sonuc.strip().lower()
        if s in LEHTE:
            return "LEHTE"
        if s in ALEYHTE:
            return "ALEYHTE"
        return None  # unclassifiable

    # ── Collect eligible decisions ────────────────────────────────────
    decisions: list[Dict] = []
    for n in builder.nodes.values():
        if n.get("node_type") != "karar":
            continue
        meta = n.get("metadata", {})
        sonuc = meta.get("sonuc", "")
        grp = _outcome_group(sonuc)
        if grp is None:
            continue
        hukuk = meta.get("hukuk_dali", "")
        # Canonical root: take the part before the first " - "
        hukuk_root = hukuk.split(" - ")[0].strip().lower() if hukuk else ""
        decisions.append({
            "node_id": n["node_id"],
            "hukuk_root": hukuk_root,
            "outcome_group": grp,
        })

    # ── Group by hukuk_dali root ──────────────────────────────────────
    by_field: Dict[str, list] = {}
    for d in decisions:
        by_field.setdefault(d["hukuk_root"], []).append(d)

    count = 0
    existing = set(builder.graph.edges())

    for field, group in by_field.items():
        if len(group) < 2:
            continue
        # Pairwise check within the same legal field
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                # Must have opposing outcomes
                if a["outcome_group"] == b["outcome_group"]:
                    continue
                # Check embedding similarity
                sim = builder.vector_store.similarity(a["node_id"], b["node_id"])
                if sim < sim_threshold:
                    continue
                # Create bidirectional edge
                for src, tgt in [(a["node_id"], b["node_id"]), (b["node_id"], a["node_id"])]:
                    if (src, tgt) not in existing:
                        builder.graph.add_edge(
                            src, tgt,
                            edge_type=edge_type,
                            weight=weight,
                            rule_id=rule["rule_id"],
                            similarity=round(sim, 4),
                            hukuk_dali=field,
                        )
                        existing.add((src, tgt))
                        count += 1

    return count


# Condition type -> handler mapping
_CONDITION_HANDLERS = {
    "metadata_match": _handle_metadata_match,
    "metadata_list_contains": _handle_metadata_list_contains,
    "field_equals_node_id": _handle_field_equals_node_id,
    "kanun_atif_parse": _handle_kanun_atif_parse,
    "id_list_reference": _handle_id_list_reference,
    "cosine_similarity": _handle_cosine_similarity,
    "contradictory_decisions": _handle_contradictory_decisions,
}
