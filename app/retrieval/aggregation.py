from __future__ import annotations

from collections import defaultdict


def max_score(chunk_results: list[dict], top_k: int = 20) -> list[tuple[str, float]]:
    """Aggregate chunk scores to document level using max chunk score per document."""
    doc_scores = defaultdict(float)
    for chunk in chunk_results:
        doc_id = chunk["doc_id"]
        if chunk["score"] > doc_scores[doc_id]:
            doc_scores[doc_id] = chunk["score"]
    ranked = sorted(doc_scores.items(), key=lambda x: x[1], reverse=True)
    return ranked[:top_k]


def mean_score(chunk_results: list[dict], top_k: int = 20) -> list[tuple[str, float]]:
    """Aggregate chunk scores to document level using mean chunk score per document."""
    doc_scores = defaultdict(list)
    for chunk in chunk_results:
        doc_scores[chunk["doc_id"]].append(chunk["score"])
    ranked = sorted(
        ((doc_id, sum(scores) / len(scores)) for doc_id, scores in doc_scores.items()),
        key=lambda x: x[1],
        reverse=True,
    )
    return ranked[:top_k]


def combsum(chunk_results: list[dict], top_k: int = 20) -> list[tuple[str, float]]:
    """Aggregate chunk scores to document level using sum of chunk scores per document."""
    doc_scores = defaultdict(float)
    for chunk in chunk_results:
        doc_scores[chunk["doc_id"]] += chunk["score"]
    ranked = sorted(doc_scores.items(), key=lambda x: x[1], reverse=True)
    return ranked[:top_k]


STRATEGIES = {
    "max": max_score,
    "mean": mean_score,
    "combsum": combsum,
}
