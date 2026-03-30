"""
Resolves RawCitation objects against the PostgreSQL documents table.

Match confidence:
  1.0 — exact esas_no + karar_no + court match
  0.9 — exact esas_no + court match
  0.7 — esas_no only (single unambiguous match)
  unresolvable — logged to UnresolvedCitation
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.graph.citation_extractor import RawCitation


@dataclass
class ResolvedCitation:
    source_doc_id: str
    target_doc_id: str
    esas_no: str
    karar_no: str
    snippet: str
    confidence: float


@dataclass
class UnresolvedCitation:
    source_doc_id: str
    raw_text: str
    esas_no: str | None
    reason: str  # "esas_no_not_in_corpus" | "ambiguous_match" | "no_esas_no_extracted"


def _citation_id(source_doc_id: str, esas_no: str, karar_no: str) -> str:
    raw = f"{source_doc_id}|{esas_no}|{karar_no}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def resolve_citations(
    raw_citations: list[RawCitation],
    conn,
) -> tuple[list[ResolvedCitation], list[UnresolvedCitation]]:
    """
    Resolve a list of RawCitations against the documents table in one batch query.

    Returns (resolved, unresolved).
    Self-citations (source == target) are silently dropped.
    """
    resolved: list[ResolvedCitation] = []
    unresolved: list[UnresolvedCitation] = []

    # Separate citations with no esas_no extracted
    extractable = [c for c in raw_citations if c.esas_no]
    for c in raw_citations:
        if not c.esas_no:
            unresolved.append(
                UnresolvedCitation(
                    source_doc_id=c.source_doc_id,
                    raw_text=c.raw_text,
                    esas_no=None,
                    reason="no_esas_no_extracted",
                )
            )

    if not extractable:
        return resolved, unresolved

    # Batch lookup all unique esas_nos in one query
    unique_esas = list({c.esas_no for c in extractable})
    with conn.cursor() as cur:
        cur.execute(
            "SELECT doc_id, esas_no, karar_no, court FROM documents WHERE esas_no = ANY(%s)",
            (unique_esas,),
        )
        rows = cur.fetchall()

    # Build lookup: esas_no → list of (doc_id, karar_no, court)
    by_esas: dict[str, list[tuple[str, str, str]]] = {}
    for doc_id, esas_no, karar_no, court in rows:
        by_esas.setdefault(esas_no, []).append((doc_id, karar_no or "", court or ""))

    for c in extractable:
        candidates = by_esas.get(c.esas_no, [])

        if not candidates:
            unresolved.append(
                UnresolvedCitation(
                    source_doc_id=c.source_doc_id,
                    raw_text=c.raw_text,
                    esas_no=c.esas_no,
                    reason="esas_no_not_in_corpus",
                )
            )
            continue

        target_doc_id = None
        confidence = 0.0

        # Try match strategies in order
        for doc_id, karar_no, court in candidates:
            if doc_id == c.source_doc_id:
                continue  # skip self-citations

            court_match = c.court_hint and c.court_hint.lower() in court.lower()
            karar_match = c.karar_no and karar_no and c.karar_no == karar_no

            if court_match and karar_match:
                target_doc_id = doc_id
                confidence = 1.0
                break
            if court_match and confidence < 0.9:
                target_doc_id = doc_id
                confidence = 0.9
            elif confidence < 0.7:
                target_doc_id = doc_id
                confidence = 0.7

        if target_doc_id is None:
            reason = "ambiguous_match" if len(candidates) > 1 else "esas_no_not_in_corpus"
            unresolved.append(
                UnresolvedCitation(
                    source_doc_id=c.source_doc_id,
                    raw_text=c.raw_text,
                    esas_no=c.esas_no,
                    reason=reason,
                )
            )
            continue

        resolved.append(
            ResolvedCitation(
                source_doc_id=c.source_doc_id,
                target_doc_id=target_doc_id,
                esas_no=c.esas_no,
                karar_no=c.karar_no or "",
                snippet=c.snippet,
                confidence=confidence,
            )
        )

    return resolved, unresolved
