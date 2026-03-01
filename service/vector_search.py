"""
Vector operations via Neo4j's native vector index.

Replaces the FAISS-based VectorStore with Neo4j's built-in vector
similarity search (available since Neo4j 5.11).

Advantages over FAISS:
  - No separate index file to manage
  - Vector search + graph traversal in a single query
  - Scales with the database
  - Automatic index updates on node upsert
"""

from __future__ import annotations

import logging
from typing import Any

from .config import get_settings
from .neo4j_driver import execute_query, execute_write

logger = logging.getLogger(__name__)


async def vector_search(
    query_vector: list[float],
    top_k: int = 10,
    score_threshold: float = 0.0,
    node_type_filter: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Semantic similarity search using Neo4j vector index.

    Returns list of {node_id, node_type, score, text_preview, ...}
    ordered by descending similarity.
    """
    s = get_settings()

    # Base vector search query
    # Neo4j 5.18+ uses db.index.vector.queryNodes
    if node_type_filter:
        records = await execute_query(
            """
            CALL db.index.vector.queryNodes($index_name, $top_k, $query_vec)
            YIELD node, score
            WHERE score >= $threshold
              AND node.node_type IN $type_filter
            RETURN node.node_id AS node_id,
                   node.node_type AS node_type,
                   score,
                   substring(coalesce(node.embed_text, ''), 0, 200) AS text_preview,
                   node.metadata_json AS metadata_json
            ORDER BY score DESC
            """,
            {
                "index_name": s.vector_index_name,
                "top_k": top_k * 2,  # Over-fetch to account for type filtering
                "query_vec": query_vector,
                "threshold": score_threshold,
                "type_filter": node_type_filter,
            },
        )
        # Trim to requested top_k after filtering
        records = records[:top_k]
    else:
        records = await execute_query(
            """
            CALL db.index.vector.queryNodes($index_name, $top_k, $query_vec)
            YIELD node, score
            WHERE score >= $threshold
            RETURN node.node_id AS node_id,
                   node.node_type AS node_type,
                   score,
                   substring(coalesce(node.embed_text, ''), 0, 200) AS text_preview,
                   node.metadata_json AS metadata_json
            ORDER BY score DESC
            """,
            {
                "index_name": s.vector_index_name,
                "top_k": top_k,
                "query_vec": query_vector,
                "threshold": score_threshold,
            },
        )

    # Parse metadata
    import json
    results = []
    for r in records:
        meta = {}
        if r.get("metadata_json"):
            try:
                meta = json.loads(r["metadata_json"])
            except (json.JSONDecodeError, TypeError):
                pass
        results.append({
            "node_id": r["node_id"],
            "node_type": r["node_type"],
            "score": round(r["score"], 4),
            "text_preview": r["text_preview"],
            "metadata": meta,
        })

    return results


async def vector_search_with_graph_expansion(
    query_vector: list[float],
    top_k: int = 10,
    expand_hops: int = 1,
    max_expanded: int = 50,
    score_threshold: float = 0.0,
    node_type_filter: list[str] | None = None,
) -> dict[str, Any]:
    """Combined vector search + graph expansion in a single Cypher query.

    This is the key advantage of Neo4j over FAISS + NetworkX:
    vector search AND graph traversal happen in the database engine,
    not in Python memory.

    Returns: {
        seed_nodes: [...],
        expanded_nodes: [...],
        edges: [...],
        subgraph_node_count: int,
        subgraph_edge_count: int,
    }
    """
    s = get_settings()

    type_filter_clause = ""
    # hops must be inlined (Neo4j doesn't support parameterised var-length)
    safe_hops = max(1, min(int(expand_hops), 5))
    params: dict[str, Any] = {
        "index_name": s.vector_index_name,
        "top_k": top_k,
        "query_vec": query_vector,
        "threshold": score_threshold,
        "max_expanded": max_expanded,
    }

    if node_type_filter:
        type_filter_clause = "AND seed.node_type IN $type_filter"
        params["type_filter"] = node_type_filter

    records = await execute_query(
        f"""
        // 1. Vector search → seed nodes
        CALL db.index.vector.queryNodes($index_name, $top_k, $query_vec)
        YIELD node AS seed, score
        WHERE score >= $threshold {type_filter_clause}

        // Collect seeds with scores
        WITH collect({{node: seed, score: score}}) AS seeds

        // 2. Graph expansion via variable-length paths
        UNWIND seeds AS s
        WITH s.node AS seed_node, s.score AS seed_score, seeds
        OPTIONAL MATCH path = (seed_node)-[*1..{safe_hops}]-(neighbor:LegalNode)
        WITH seeds,
             collect(DISTINCT neighbor) AS expanded_list

        // 3. Combine seed + expanded nodes
        WITH seeds,
             [s IN seeds | s.node] AS seed_nodes,
             expanded_list
        WITH seeds,
             seed_nodes,
             apoc.coll.toSet(seed_nodes + expanded_list) AS all_nodes

        // Limit expanded
        WITH seeds,
             seed_nodes,
             all_nodes[0..toInteger($max_expanded)] AS all_nodes

        // 4. Get edges within the subgraph
        UNWIND all_nodes AS n
        OPTIONAL MATCH (n)-[r]->(m)
        WHERE m IN all_nodes
        WITH seeds, seed_nodes, all_nodes,
             collect(DISTINCT {{
                 source: startNode(r).node_id,
                 target: endNode(r).node_id,
                 edge_type: coalesce(r.edge_type, type(r)),
                 weight: coalesce(r.weight, 1.0)
             }}) AS edges

        // 5. Build result
        RETURN
          [s IN seeds | {{
              node_id: s.node.node_id,
              node_type: s.node.node_type,
              score: s.score,
              text_preview: substring(coalesce(s.node.embed_text, ''), 0, 200),
              metadata_json: s.node.metadata_json
          }}] AS seed_nodes,
          [n IN all_nodes WHERE NOT n IN seed_nodes | {{
              node_id: n.node_id,
              node_type: n.node_type,
              text_preview: substring(coalesce(n.embed_text, ''), 0, 200),
              metadata_json: n.metadata_json
          }}] AS expanded_nodes,
          edges,
          size(all_nodes) AS subgraph_node_count,
          size(edges) AS subgraph_edge_count
        """,
        params,
    )

    if not records:
        return {
            "seed_nodes": [],
            "expanded_nodes": [],
            "edges": [],
            "subgraph_node_count": 0,
            "subgraph_edge_count": 0,
        }

    data = records[0]

    # Parse metadata in results
    import json

    def _parse_meta(items):
        for item in items:
            if item.get("metadata_json"):
                try:
                    item["metadata"] = json.loads(item["metadata_json"])
                except (json.JSONDecodeError, TypeError):
                    item["metadata"] = {}
            else:
                item["metadata"] = {}
            item.pop("metadata_json", None)
        return items

    return {
        "seed_nodes": _parse_meta(data.get("seed_nodes", [])),
        "expanded_nodes": _parse_meta(data.get("expanded_nodes", [])),
        "edges": [e for e in data.get("edges", []) if e.get("source")],
        "subgraph_node_count": data.get("subgraph_node_count", 0),
        "subgraph_edge_count": data.get("subgraph_edge_count", 0),
    }


async def vector_search_with_graph_expansion_simple(
    query_vector: list[float],
    top_k: int = 10,
    expand_hops: int = 1,
    max_expanded: int = 50,
    score_threshold: float = 0.0,
    node_type_filter: list[str] | None = None,
) -> dict[str, Any]:
    """Fallback combined search without APOC dependency.

    Uses two-step approach: vector search first, then expand.
    """
    # Step 1: Vector search for seed nodes
    seeds = await vector_search(
        query_vector=query_vector,
        top_k=top_k,
        score_threshold=score_threshold,
        node_type_filter=node_type_filter,
    )

    if not seeds:
        return {
            "seed_nodes": seeds,
            "expanded_nodes": [],
            "edges": [],
            "subgraph_node_count": 0,
            "subgraph_edge_count": 0,
        }

    seed_ids = [s["node_id"] for s in seeds]

    # hops must be inlined (Neo4j doesn't support parameterised var-length)
    safe_hops = max(1, min(int(expand_hops), 5))

    # Step 2: Graph expansion
    records = await execute_query(
        f"""
        UNWIND $seed_ids AS sid
        MATCH (seed:LegalNode {{node_id: sid}})
        OPTIONAL MATCH path = (seed)-[*1..{safe_hops}]-(neighbor:LegalNode)
        WITH collect(DISTINCT seed) + collect(DISTINCT neighbor) AS all_raw
        WITH [n IN all_raw WHERE n IS NOT NULL | n] AS all_nodes_raw
        
        // Deduplicate
        UNWIND all_nodes_raw AS n
        WITH collect(DISTINCT n)[0..$max_expanded] AS all_nodes
        
        // Get edges within subgraph
        UNWIND all_nodes AS n
        OPTIONAL MATCH (n)-[r]->(m)
        WHERE m IN all_nodes
        WITH all_nodes,
             collect(DISTINCT {{
                 source: n.node_id,
                 target: m.node_id,
                 edge_type: coalesce(r.edge_type, type(r)),
                 weight: coalesce(r.weight, 1.0)
             }}) AS edges
        
        // Expanded = all minus seeds
        WITH all_nodes, edges, $seed_ids AS seed_ids
        RETURN
          [n IN all_nodes WHERE NOT n.node_id IN seed_ids | {{
              node_id: n.node_id,
              node_type: n.node_type,
              text_preview: substring(coalesce(n.embed_text, ''), 0, 200),
              metadata_json: n.metadata_json
          }}] AS expanded_nodes,
          edges,
          size(all_nodes) AS subgraph_node_count,
          size(edges) AS subgraph_edge_count
        """,
        {
            "seed_ids": seed_ids,
            "max_expanded": max_expanded,
        },
    )

    expanded_nodes = []
    edges = []
    sg_nodes = len(seeds)
    sg_edges = 0

    if records:
        data = records[0]
        import json

        for item in data.get("expanded_nodes", []):
            if item.get("metadata_json"):
                try:
                    item["metadata"] = json.loads(item["metadata_json"])
                except Exception:
                    item["metadata"] = {}
            else:
                item["metadata"] = {}
            item.pop("metadata_json", None)
            expanded_nodes.append(item)

        edges = [e for e in data.get("edges", []) if e.get("source")]
        sg_nodes = data.get("subgraph_node_count", len(seeds))
        sg_edges = data.get("subgraph_edge_count", 0)

    return {
        "seed_nodes": seeds,
        "expanded_nodes": expanded_nodes,
        "edges": edges,
        "subgraph_node_count": sg_nodes,
        "subgraph_edge_count": sg_edges,
    }


async def update_embeddings_batch(
    node_embeddings: list[dict[str, Any]],
    batch_size: int = 200,
) -> int:
    """Update embedding vectors on existing nodes.

    Each dict: {node_id: str, embedding: list[float]}
    """
    total = 0
    for i in range(0, len(node_embeddings), batch_size):
        batch = node_embeddings[i : i + batch_size]
        await execute_write(
            """
            UNWIND $rows AS row
            MATCH (n:LegalNode {node_id: row.node_id})
            SET n.embedding = row.embedding
            """,
            {"rows": batch},
        )
        total += len(batch)

    logger.info("Updated %d embeddings.", total)
    return total
