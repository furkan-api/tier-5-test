"""
Batch graph metric computation: PageRank, in/out-degree.

Uses networkx for computation (adequate for current corpus scale).
Writes results back to PostgreSQL documents table and Neo4j.
"""

from __future__ import annotations

import logging

import networkx as nx

log = logging.getLogger(__name__)


def compute_pagerank_networkx(
    conn,
    alpha: float = 0.85,
    max_iter: int = 100,
) -> dict[str, float]:
    """
    Compute PageRank over the citation graph.

    Reads citation edges from PG. Adds isolated nodes (no edges) so every
    document gets a score. Normalises to [0, 1].

    Returns {doc_id: normalised_pagerank_score}.
    """
    G = nx.DiGraph()

    # Add all documents as nodes (including isolated ones)
    with conn.cursor() as cur:
        cur.execute("SELECT doc_id FROM documents")
        for (doc_id,) in cur.fetchall():
            G.add_node(doc_id)

    # Add citation edges
    with conn.cursor() as cur:
        cur.execute(
            "SELECT source_doc_id, target_doc_id FROM citations "
            "WHERE target_doc_id IS NOT NULL"
        )
        for source, target in cur.fetchall():
            G.add_edge(source, target)

    log.info(
        "PageRank graph: %d nodes, %d edges", G.number_of_nodes(), G.number_of_edges()
    )

    if G.number_of_edges() == 0:
        log.warning("Citation graph has no edges — all PageRank scores will be equal")
        n = G.number_of_nodes()
        return {node: 1.0 / n for node in G.nodes()} if n else {}

    scores = nx.pagerank(G, alpha=alpha, max_iter=max_iter)

    max_score = max(scores.values()) if scores else 1.0
    if max_score > 0:
        scores = {k: v / max_score for k, v in scores.items()}

    return scores


def write_pagerank_to_postgres(scores: dict[str, float], conn) -> int:
    """Write pagerank_score back to PG documents table. Returns rows updated."""
    with conn.cursor() as cur:
        cur.executemany(
            "UPDATE documents SET pagerank_score = %s WHERE doc_id = %s",
            [(score, doc_id) for doc_id, score in scores.items()],
        )
    conn.commit()
    return len(scores)


def write_pagerank_to_neo4j(scores: dict[str, float], driver) -> int:
    """Write pagerank_score to Neo4j Document nodes. Returns nodes updated."""
    batch = [{"doc_id": doc_id, "score": score} for doc_id, score in scores.items()]
    with driver.session(database="neo4j") as session:
        session.run(
            "UNWIND $batch AS row "
            "MATCH (d:Document {doc_id: row.doc_id}) "
            "SET d.pagerank_score = row.score",
            batch=batch,
        )
    return len(batch)


def compute_in_out_degree(conn) -> dict[str, tuple[int, int]]:
    """
    Compute in-degree and out-degree for each document from PG citations.
    Returns {doc_id: (in_degree, out_degree)}.
    """
    degrees: dict[str, list[int]] = {}

    with conn.cursor() as cur:
        cur.execute("SELECT doc_id FROM documents")
        for (doc_id,) in cur.fetchall():
            degrees[doc_id] = [0, 0]

    with conn.cursor() as cur:
        cur.execute(
            "SELECT target_doc_id, COUNT(*) FROM citations "
            "WHERE target_doc_id IS NOT NULL GROUP BY target_doc_id"
        )
        for doc_id, count in cur.fetchall():
            if doc_id in degrees:
                degrees[doc_id][0] = count

    with conn.cursor() as cur:
        cur.execute(
            "SELECT source_doc_id, COUNT(*) FROM citations GROUP BY source_doc_id"
        )
        for doc_id, count in cur.fetchall():
            if doc_id in degrees:
                degrees[doc_id][1] = count

    return {k: (v[0], v[1]) for k, v in degrees.items()}


def write_degree_to_postgres(degrees: dict[str, tuple[int, int]], conn) -> int:
    """Write in/out-degree to PG documents table. Returns rows updated."""
    with conn.cursor() as cur:
        cur.executemany(
            "UPDATE documents SET citation_in_degree = %s, citation_out_degree = %s "
            "WHERE doc_id = %s",
            [(in_deg, out_deg, doc_id) for doc_id, (in_deg, out_deg) in degrees.items()],
        )
    conn.commit()
    return len(degrees)
