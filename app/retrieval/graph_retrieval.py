"""
Graph-augmented retrieval: PPR re-scoring + 1-hop citation expansion.

HippoRAG-style Personalized PageRank applied to the legal citation network.
Dense retrieval seeds the PPR; citation neighbors are added as extra candidates.

Public API:
    expand_and_rescore(dense_results, session, conn, ...) -> list[GraphExpandedResult]
    expand_and_rescore_fallback(dense_results)           -> list[GraphExpandedResult]
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import networkx as nx

log = logging.getLogger(__name__)


@dataclass
class GraphExpandedResult:
    doc_id: str
    dense_score: float
    graph_score: float
    final_score: float
    is_seed: bool           # True = came from dense retrieval; False = graph expansion
    hop_distance: int = 0   # 0 = seed, 1 = 1-hop neighbor, etc.


def _get_neighbor_doc_ids(session, seed_doc_ids: list[str], hops: int) -> set[str]:
    """Return flat set of citation neighbors (bidirectional) for the seed docs."""
    from app.graph.neo4j_sync import get_citation_neighbors
    neighbor_map = get_citation_neighbors(session, seed_doc_ids, hops)
    neighbors: set[str] = set()
    for nlist in neighbor_map.values():
        neighbors.update(nlist)
    return neighbors - set(seed_doc_ids)


def compute_ppr_scores(
    seed_ids: list[str],
    all_candidate_ids: list[str],
    conn,
    alpha: float = 0.85,
) -> dict[str, float]:
    """
    Personalised PageRank over the citation subgraph of all_candidate_ids.

    Seeded to the dense-retrieval seed_ids. Returns {doc_id: normalised_score}.
    Falls back to uniform seed scores if no citation edges exist in the subgraph.
    """
    if not all_candidate_ids:
        return {}

    # Fetch citation subgraph for the candidate set from PG
    with conn.cursor() as cur:
        cur.execute(
            "SELECT source_doc_id, target_doc_id FROM citations "
            "WHERE target_doc_id IS NOT NULL "
            "AND (source_doc_id = ANY(%s) OR target_doc_id = ANY(%s))",
            (all_candidate_ids, all_candidate_ids),
        )
        edges = cur.fetchall()

    G = nx.DiGraph()
    for node in all_candidate_ids:
        G.add_node(node)
    for src, tgt in edges:
        if src in G and tgt in G:
            G.add_edge(src, tgt)

    if G.number_of_edges() == 0:
        # No citations found — uniform PPR over seeds
        n = max(len(seed_ids), 1)
        return {doc_id: (1.0 / n if doc_id in seed_ids else 0.0) for doc_id in all_candidate_ids}

    n_seeds = max(len(seed_ids), 1)
    personalization = {
        doc_id: (1.0 / n_seeds if doc_id in seed_ids else 0.0)
        for doc_id in all_candidate_ids
    }

    try:
        scores = nx.pagerank(G, alpha=alpha, personalization=personalization, max_iter=100)
    except nx.PowerIterationFailedConvergence:
        log.warning("PPR did not converge — using uniform seed scores")
        n = max(len(seed_ids), 1)
        return {doc_id: (1.0 / n if doc_id in seed_ids else 0.0) for doc_id in all_candidate_ids}

    max_score = max(scores.values()) if scores else 1.0
    if max_score > 0:
        scores = {k: v / max_score for k, v in scores.items()}

    return scores


def expand_and_rescore(
    dense_results: list[tuple[str, float]],
    neo4j_session,
    conn,
    top_k_seeds: int = 5,
    hops: int = 1,
    graph_weight: float = 0.3,
) -> list[GraphExpandedResult]:
    """
    Expand the dense result set via citation graph and re-score with PPR.

    Steps:
      1. Take top_k_seeds from dense_results as PPR seeds
      2. Expand by hops in citation graph (bidirectional) → get neighbor doc_ids
      3. Build all candidates = dense results ∪ neighbors
      4. Normalize dense scores to [0, 1]
      5. Compute PPR over the candidate subgraph, seeded to dense top-k
      6. final_score = (1-w) * dense_norm + w * ppr
      7. Sort by final_score descending
    """
    if not dense_results:
        return []

    seed_ids = [doc_id for doc_id, _ in dense_results[:top_k_seeds]]

    # Citation-graph expansion
    neighbor_ids: set[str] = set()
    try:
        neighbor_ids = _get_neighbor_doc_ids(neo4j_session, seed_ids, hops)
    except Exception as e:
        log.warning("Citation graph expansion failed: %s", e)

    dense_doc_ids = {doc_id for doc_id, _ in dense_results}
    all_candidate_ids = list(dense_doc_ids | neighbor_ids)

    # Normalise dense scores
    dense_score_map = dict(dense_results)
    max_dense = max(dense_score_map.values()) if dense_score_map else 1.0
    min_dense = min(dense_score_map.values()) if dense_score_map else 0.0
    rng = max_dense - min_dense if max_dense != min_dense else 1.0
    dense_norm = {k: (v - min_dense) / rng for k, v in dense_score_map.items()}

    # PPR
    ppr_scores = compute_ppr_scores(seed_ids, all_candidate_ids, conn, alpha=0.85)

    results: list[GraphExpandedResult] = []
    for doc_id in all_candidate_ids:
        d_score = dense_score_map.get(doc_id, 0.0)
        d_norm = dense_norm.get(doc_id, 0.0)
        g_score = ppr_scores.get(doc_id, 0.0)
        final = (1 - graph_weight) * d_norm + graph_weight * g_score
        results.append(
            GraphExpandedResult(
                doc_id=doc_id,
                dense_score=d_score,
                graph_score=g_score,
                final_score=final,
                is_seed=doc_id in dense_doc_ids,
                hop_distance=0 if doc_id in dense_doc_ids else 1,
            )
        )

    results.sort(key=lambda r: r.final_score, reverse=True)
    return results


def expand_and_rescore_fallback(
    dense_results: list[tuple[str, float]],
) -> list[GraphExpandedResult]:
    """
    Pure passthrough — used when Neo4j is unavailable.
    Returns dense results as GraphExpandedResult with graph_score=0.
    """
    max_score = max((s for _, s in dense_results), default=1.0)
    return [
        GraphExpandedResult(
            doc_id=doc_id,
            dense_score=score,
            graph_score=0.0,
            final_score=score / max_score if max_score > 0 else 0.0,
            is_seed=True,
            hop_distance=0,
        )
        for doc_id, score in dense_results
    ]
