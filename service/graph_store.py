"""
Neo4j Graph Store — CRUD, schema setup, and graph operations.

Responsible for:
  - Schema/index creation (constraints, vector index)
  - Node and edge CRUD (batch upsert)
  - Node queries (by ID, by type, neighborhood)
  - Graph statistics
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .config import get_settings
from .neo4j_driver import execute_query, execute_write, get_session

logger = logging.getLogger(__name__)


# ── Schema Setup ──────────────────────────────────────────────────────────────

async def ensure_schema() -> None:
    """Create constraints, indexes, and vector index if they don't exist.

    Should be called once at application startup or during migration.
    """
    s = get_settings()

    # Uniqueness constraint on node_id
    await execute_write(
        "CREATE CONSTRAINT node_id_unique IF NOT EXISTS "
        "FOR (n:LegalNode) REQUIRE n.node_id IS UNIQUE"
    )
    logger.info("Constraint: node_id_unique ensured.")

    # Index on node_type for fast filtering
    await execute_write(
        "CREATE INDEX node_type_idx IF NOT EXISTS "
        "FOR (n:LegalNode) ON (n.node_type)"
    )

    # Composite index for common query patterns
    await execute_write(
        "CREATE INDEX node_law_abbr_idx IF NOT EXISTS "
        "FOR (n:LegalNode) ON (n.law_abbreviation)"
    )

    # Full-text index for text search fallback
    try:
        await execute_write(
            "CREATE FULLTEXT INDEX node_text_ft IF NOT EXISTS "
            "FOR (n:LegalNode) ON EACH [n.embed_text]"
        )
    except Exception as e:
        logger.warning("Fulltext index creation skipped: %s", e)

    # Vector index for semantic search (Neo4j 5.11+)
    try:
        await execute_write(
            "CREATE VECTOR INDEX $index_name IF NOT EXISTS "
            "FOR (n:LegalNode) ON (n.embedding) "
            "OPTIONS { indexConfig: { "
            "  `vector.dimensions`: $dimension, "
            "  `vector.similarity_function`: $sim_fn "
            "}}",
            {
                "index_name": s.vector_index_name,
                "dimension": s.embedding_dimension,
                "sim_fn": s.vector_similarity_function,
            },
        )
        logger.info(
            "Vector index '%s' ensured (dim=%d, fn=%s).",
            s.vector_index_name, s.embedding_dimension,
            s.vector_similarity_function,
        )
    except Exception as e:
        logger.warning("Vector index creation issue: %s", e)

    # Index on BuildMeta
    await execute_write(
        "CREATE CONSTRAINT build_meta_unique IF NOT EXISTS "
        "FOR (m:BuildMeta) REQUIRE m.build_id IS UNIQUE"
    )
    logger.info("Schema setup complete.")


# ── Node Operations ───────────────────────────────────────────────────────────

async def upsert_nodes_batch(
    nodes: list[dict[str, Any]],
    batch_size: int = 500,
) -> int:
    """Batch upsert nodes into Neo4j.

    Each node dict must have: node_id, node_type, embed_text
    Optional: metadata (dict), embedding (list[float])

    Uses UNWIND for efficient batch operations.
    Returns total nodes upserted.
    """
    total = 0
    for i in range(0, len(nodes), batch_size):
        batch = nodes[i : i + batch_size]

        # Prepare batch data: flatten metadata into node properties
        rows = []
        for n in batch:
            row: dict[str, Any] = {
                "node_id": n["node_id"],
                "node_type": n.get("node_type", ""),
                "embed_text": n.get("embed_text", ""),
            }
            # Flatten metadata as top-level properties
            meta = n.get("metadata", {})
            for k, v in meta.items():
                # Neo4j stores lists/primitives natively; skip nested dicts
                if isinstance(v, dict):
                    continue
                row[k] = v

            # Store full metadata as JSON string for complex queries
            import json
            row["metadata_json"] = json.dumps(meta, ensure_ascii=False)

            # Embedding vector (if present)
            if "embedding" in n:
                row["embedding"] = n["embedding"]

            rows.append(row)

        # Dynamic label: add node_type as secondary label
        # LegalNode is the primary label for all nodes
        await execute_write(
            """
            UNWIND $rows AS row
            MERGE (n:LegalNode {node_id: row.node_id})
            SET n += row
            WITH n, row
            CALL apoc.create.addLabels(n, [row.node_type]) YIELD node
            RETURN count(node) AS cnt
            """,
            {"rows": rows},
        )
        total += len(batch)
        logger.debug("Upserted batch %d-%d (%d nodes)", i, i + len(batch), len(batch))

    logger.info("Upserted %d nodes total.", total)
    return total


async def upsert_nodes_batch_simple(
    nodes: list[dict[str, Any]],
    batch_size: int = 500,
) -> int:
    """Batch upsert nodes without APOC dependency.

    Falls back to this if APOC is not installed.
    Uses only the :LegalNode label.
    """
    total = 0
    for i in range(0, len(nodes), batch_size):
        batch = nodes[i : i + batch_size]

        rows = []
        for n in batch:
            import json
            row: dict[str, Any] = {
                "node_id": n["node_id"],
                "node_type": n.get("node_type", ""),
                "embed_text": n.get("embed_text", ""),
                "metadata_json": json.dumps(
                    n.get("metadata", {}), ensure_ascii=False,
                ),
            }
            meta = n.get("metadata", {})
            for k, v in meta.items():
                if isinstance(v, dict):
                    continue
                row[k] = v
            if "embedding" in n:
                row["embedding"] = n["embedding"]
            rows.append(row)

        await execute_write(
            """
            UNWIND $rows AS row
            MERGE (n:LegalNode {node_id: row.node_id})
            SET n += row
            RETURN count(n) AS cnt
            """,
            {"rows": rows},
        )
        total += len(batch)

    logger.info("Upserted %d nodes total (simple mode).", total)
    return total


# ── Edge Operations ───────────────────────────────────────────────────────────

async def create_edges_batch(
    edges: list[dict[str, Any]],
    batch_size: int = 1000,
) -> int:
    """Batch create edges in Neo4j.

    Each edge dict: source, target, edge_type, weight, rule_id, ...
    Uses MERGE to avoid duplicates.
    """
    total = 0
    for i in range(0, len(edges), batch_size):
        batch = edges[i : i + batch_size]

        rows = []
        for e in batch:
            row = {
                "source": e["source"],
                "target": e["target"],
                "edge_type": e.get("edge_type", "RELATED"),
                "weight": e.get("weight", 1.0),
                "rule_id": e.get("rule_id", ""),
            }
            # Copy additional properties
            for k, v in e.items():
                if k not in row and not isinstance(v, (dict, list)):
                    row[k] = v
            rows.append(row)

        # Use dynamic relationship type via APOC or fallback
        # Fallback: create generic RELATED edges with edge_type property
        await execute_write(
            """
            UNWIND $rows AS row
            MATCH (s:LegalNode {node_id: row.source})
            MATCH (t:LegalNode {node_id: row.target})
            MERGE (s)-[r:RELATED {edge_type: row.edge_type}]->(t)
            SET r += row
            RETURN count(r) AS cnt
            """,
            {"rows": rows},
        )
        total += len(batch)

    logger.info("Created %d edges total.", total)
    return total


async def create_typed_edges_batch(
    edges: list[dict[str, Any]],
    batch_size: int = 1000,
) -> int:
    """Create edges with dynamic relationship types (requires APOC).

    This creates actual Neo4j relationship types like :CONTAINS, :REFERENCES
    instead of generic :RELATED with a property.
    """
    total = 0
    for i in range(0, len(edges), batch_size):
        batch = edges[i : i + batch_size]

        rows = []
        for e in batch:
            row = {
                "source": e["source"],
                "target": e["target"],
                "edge_type": e.get("edge_type", "RELATED"),
                "weight": float(e.get("weight", 1.0)),
                "rule_id": e.get("rule_id", ""),
            }
            rows.append(row)

        await execute_write(
            """
            UNWIND $rows AS row
            MATCH (s:LegalNode {node_id: row.source})
            MATCH (t:LegalNode {node_id: row.target})
            CALL apoc.merge.relationship(
                s, row.edge_type, {edge_type: row.edge_type},
                {weight: row.weight, rule_id: row.rule_id}, t, {}
            ) YIELD rel
            RETURN count(rel) AS cnt
            """,
            {"rows": rows},
        )
        total += len(batch)

    logger.info("Created %d typed edges total.", total)
    return total


# ── Query Operations ──────────────────────────────────────────────────────────

async def get_node(node_id: str) -> dict[str, Any] | None:
    """Fetch a single node with all its properties and edges."""
    records = await execute_query(
        """
        MATCH (n:LegalNode {node_id: $nid})
        OPTIONAL MATCH (n)-[r_out]->(t)
        WITH n, collect(DISTINCT {
            target: t.node_id,
            edge_type: coalesce(r_out.edge_type, type(r_out)),
            weight: coalesce(r_out.weight, 1.0)
        }) AS out_edges
        OPTIONAL MATCH (s)-[r_in]->(n)
        WITH n, out_edges, collect(DISTINCT {
            source: s.node_id,
            edge_type: coalesce(r_in.edge_type, type(r_in)),
            weight: coalesce(r_in.weight, 1.0)
        }) AS in_edges
        RETURN n {.*, out_edges: out_edges, in_edges: in_edges} AS node
        """,
        {"nid": node_id},
    )
    if not records:
        return None

    node = records[0]["node"]
    # Parse metadata_json back
    if "metadata_json" in node:
        import json
        try:
            node["metadata"] = json.loads(node["metadata_json"])
        except (json.JSONDecodeError, TypeError):
            node["metadata"] = {}
    else:
        node["metadata"] = {}

    # Filter out null edges (from OPTIONAL MATCH)
    node["out_edges"] = [e for e in node.get("out_edges", []) if e.get("target")]
    node["in_edges"] = [e for e in node.get("in_edges", []) if e.get("source")]

    return node


async def get_neighborhood(
    node_id: str,
    hops: int = 1,
    max_nodes: int = 100,
) -> dict[str, Any] | None:
    """Fetch the neighborhood subgraph around a node using Cypher BFS.

    Much faster than Python-level BFS for large graphs.
    """
    # Check node exists
    exists = await execute_query(
        "MATCH (n:LegalNode {node_id: $nid}) RETURN n.node_id AS nid",
        {"nid": node_id},
    )
    if not exists:
        return None

    # Variable-length path for multi-hop neighborhood
    safe_hops = max(1, min(int(hops), 3))
    records = await execute_query(
        f"""
        MATCH (center:LegalNode {{node_id: $nid}})
        CALL {{
            WITH center
            MATCH path = (center)-[*1..{safe_hops}]-(neighbor:LegalNode)
            RETURN DISTINCT neighbor
            LIMIT $max_nodes
        }}
        WITH center, collect(neighbor) AS neighbors
        UNWIND ([center] + neighbors) AS n
        WITH collect(DISTINCT n) AS all_nodes
        UNWIND all_nodes AS n
        OPTIONAL MATCH (n)-[r]-(m)
        WHERE m IN all_nodes
        WITH all_nodes, collect(DISTINCT {{
            source: startNode(r).node_id,
            target: endNode(r).node_id,
            edge_type: coalesce(r.edge_type, type(r)),
            weight: coalesce(r.weight, 1.0)
        }}) AS all_edges
        UNWIND all_nodes AS n
        RETURN collect(DISTINCT {{
            node_id: n.node_id,
            node_type: n.node_type,
            text_preview: substring(coalesce(n.embed_text, ''), 0, 120)
        }}) AS nodes, all_edges AS edges
        """,
        {"nid": node_id, "max_nodes": max_nodes},
    )

    if not records:
        return None

    data = records[0]
    return {
        "center": node_id,
        "hops": hops,
        "nodes": data["nodes"],
        "edges": [e for e in data["edges"] if e.get("source")],
    }


async def get_graph_stats() -> dict[str, Any]:
    """Return comprehensive graph statistics."""
    stats_records = await execute_query(
        """
        MATCH (n:LegalNode)
        WITH count(n) AS node_count,
             collect(DISTINCT n.node_type) AS node_types
        OPTIONAL MATCH ()-[r]->()
        WITH node_count, node_types, count(r) AS edge_count
        RETURN node_count, edge_count, node_types
        """
    )

    if not stats_records:
        return {"status": "empty", "nodes": 0, "edges": 0}

    s = stats_records[0]

    # Node type distribution
    type_dist = await execute_query(
        """
        MATCH (n:LegalNode)
        RETURN n.node_type AS node_type, count(n) AS cnt
        ORDER BY cnt DESC
        """
    )

    # Edge type distribution
    edge_dist = await execute_query(
        """
        MATCH ()-[r]->()
        RETURN coalesce(r.edge_type, type(r)) AS edge_type, count(r) AS cnt
        ORDER BY cnt DESC
        """
    )

    # Degree stats
    degree_stats = await execute_query(
        """
        MATCH (n:LegalNode)
        WITH n, COUNT { (n)--() } AS deg
        RETURN avg(deg) AS avg_degree,
               max(deg) AS max_degree,
               sum(CASE WHEN deg = 0 THEN 1 ELSE 0 END) AS isolated
        """
    )

    ds = degree_stats[0] if degree_stats else {}

    return {
        "status": "ready",
        "graph": {
            "nodes": s["node_count"],
            "edges": s["edge_count"],
            "avg_degree": round(ds.get("avg_degree", 0) or 0, 2),
            "max_degree": ds.get("max_degree", 0) or 0,
            "isolated_nodes": ds.get("isolated", 0) or 0,
        },
        "node_types": {r["node_type"]: r["cnt"] for r in type_dist},
        "edge_types": {r["edge_type"]: r["cnt"] for r in edge_dist},
    }


# ── Build Metadata ────────────────────────────────────────────────────────────

async def save_build_meta(meta: dict[str, Any]) -> None:
    """Store build metadata as a :BuildMeta node."""
    await execute_write(
        """
        MERGE (m:BuildMeta {build_id: 'latest'})
        SET m += $meta,
            m.updated_at = datetime()
        """,
        {"meta": meta},
    )


async def get_build_meta() -> dict[str, Any] | None:
    """Retrieve the latest build metadata."""
    records = await execute_query(
        "MATCH (m:BuildMeta {build_id: 'latest'}) RETURN m AS meta"
    )
    if not records:
        return None
    return dict(records[0]["meta"])


async def clear_graph() -> int:
    """Delete all nodes and relationships. USE WITH CAUTION.

    Returns count of deleted nodes.
    """
    result = await execute_write(
        """
        MATCH (n)
        WITH n LIMIT 10000
        DETACH DELETE n
        RETURN count(n) AS deleted
        """
    )
    deleted = result[0]["deleted"] if result else 0

    # Keep deleting in batches until empty
    total = deleted
    while deleted > 0:
        result = await execute_write(
            """
            MATCH (n)
            WITH n LIMIT 10000
            DETACH DELETE n
            RETURN count(n) AS deleted
            """
        )
        deleted = result[0]["deleted"] if result else 0
        total += deleted

    logger.info("Cleared graph: %d nodes deleted.", total)
    return total
