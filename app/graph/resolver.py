"""
Resolves RawCitation objects against the PostgreSQL documents table.

Matching rule:
    daire (mandatory)  AND  (esas_no OR karar_no)

Daire comparison is done on a normalized form (Turkish-lowercased, diacritic-
stripped, non-alphanumerics removed) so the extractor output and the DB value
match despite minor formatting differences (e.g. "Fikrî" vs "Fikri",
"1.Fikrî" vs "1. Fikrî").

Confidence:
    1.0  — daire + esas + karar all match
    0.8  — daire + exactly one of (esas, karar) matches
    else — logged as UnresolvedCitation
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.graph.citation_extractor import RawCitation


@dataclass
class ResolvedCitation:
    source_doc_id: str
    target_doc_id: str
    daire: str
    esas_no: str
    karar_no: str
    snippet: str
    confidence: float


@dataclass
class UnresolvedCitation:
    source_doc_id: str
    raw_text: str
    daire: str | None
    esas_no: str | None
    karar_no: str | None
    reason: str
    # reason ∈ {
    #   "no_daire_extracted",
    #   "no_esas_or_karar",
    #   "daire_not_in_corpus",
    #   "no_ek_match_in_daire",
    #   "ambiguous_match",
    # }


# ---------------------------------------------------------------------------
# Daire normalization
# ---------------------------------------------------------------------------

_TURKISH_LOWER = str.maketrans({
    "İ": "i", "I": "ı",
    "Ç": "ç", "Ğ": "ğ", "Ö": "ö", "Ş": "ş", "Ü": "ü",
})
_ASCII_FOLD = str.maketrans("çğıöşüâîû", "cgiosuaiu")


def _normalize_daire(s: str | None) -> str:
    if not s:
        return ""
    s = s.translate(_TURKISH_LOWER).lower()
    s = s.translate(_ASCII_FOLD)
    return re.sub(r"[^a-z0-9]+", "", s)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def resolve_citations(
    raw_citations: list[RawCitation],
    conn,
) -> tuple[list[ResolvedCitation], list[UnresolvedCitation]]:
    """
    Resolve RawCitations against the documents table.

    Self-citations (source == target) are silently dropped.
    """
    resolved: list[ResolvedCitation] = []
    unresolved: list[UnresolvedCitation] = []

    # Load entire documents table once and build daire-normalized index.
    with conn.cursor() as cur:
        cur.execute("SELECT doc_id, daire, esas_no, karar_no FROM documents")
        rows = cur.fetchall()

    by_daire: dict[str, list[tuple[str, str, str]]] = {}
    for doc_id, daire, esas_no, karar_no in rows:
        key = _normalize_daire(daire)
        if not key:
            continue
        by_daire.setdefault(key, []).append((doc_id, esas_no or "", karar_no or ""))

    for c in raw_citations:
        if not c.daire:
            unresolved.append(_unresolved(c, "no_daire_extracted"))
            continue
        if not c.esas_no and not c.karar_no:
            unresolved.append(_unresolved(c, "no_esas_or_karar"))
            continue

        candidates = by_daire.get(_normalize_daire(c.daire), [])
        if not candidates:
            unresolved.append(_unresolved(c, "daire_not_in_corpus"))
            continue

        # Score candidates: (esas match? 1 : 0) + (karar match? 1 : 0)
        scored: list[tuple[str, int, str, str]] = []
        for doc_id, esas, karar in candidates:
            if doc_id == c.source_doc_id:
                continue
            esas_ok = bool(c.esas_no) and c.esas_no == esas
            karar_ok = bool(c.karar_no) and c.karar_no == karar
            score = (1 if esas_ok else 0) + (1 if karar_ok else 0)
            if score > 0:
                scored.append((doc_id, score, esas, karar))

        if not scored:
            unresolved.append(_unresolved(c, "no_ek_match_in_daire"))
            continue

        scored.sort(key=lambda x: -x[1])
        top = [s for s in scored if s[1] == scored[0][1]]
        if len(top) > 1:
            unresolved.append(_unresolved(c, "ambiguous_match"))
            continue

        target_doc_id, score, target_esas, target_karar = top[0]
        confidence = 1.0 if score == 2 else 0.8

        resolved.append(
            ResolvedCitation(
                source_doc_id=c.source_doc_id,
                target_doc_id=target_doc_id,
                daire=c.daire,
                esas_no=c.esas_no or target_esas,
                karar_no=c.karar_no or target_karar,
                snippet=c.snippet,
                confidence=confidence,
            )
        )

    return resolved, unresolved


def _unresolved(c: RawCitation, reason: str) -> UnresolvedCitation:
    return UnresolvedCitation(
        source_doc_id=c.source_doc_id,
        raw_text=c.raw_text,
        daire=c.daire or None,
        esas_no=c.esas_no,
        karar_no=c.karar_no,
        reason=reason,
    )
