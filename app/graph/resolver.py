"""
Resolves RawCitation objects against the PostgreSQL documents table.

Matching rule:
    daire (mandatory)  AND  (esas_no OR karar_no)

Daire comparison is done on a normalized form (Turkish-lowercased, diacritic-
stripped, non-alphanumerics removed) so the extractor output and the DB value
match despite minor formatting differences (e.g. "Fikrî" vs "Fikri",
"1.Fikrî" vs "1. Fikrî").

Fuzzy fallback:
    When the exact normalized daire key is not in the index, the resolver
    fuzzy-matches the raw extracted daire string against a list of canonical
    court names (typically sourced from MongoDB court_name values).  Only
    canonical names whose normalized form IS already in the index are
    considered, so a fuzzy match can only promote a citation to a court that
    actually has documents in the corpus.  A SequenceMatcher cutoff of 0.88
    keeps false positives low while catching ASCII / diacritic / dot variants.

Confidence:
    1.0  — daire + esas + karar all match
    0.8  — daire + exactly one of (esas, karar) matches
    else — logged as UnresolvedCitation
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import get_close_matches

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
    s = re.sub(r"[^a-z0-9]+", "", s)
    # Normalise BAM/BİM long-forms so "Bölge Adliye Mahkemesi" == "BAM"
    s = s.replace("bolgeadliyemahkemesi", "bam")
    s = s.replace("bolgeidaremahkemesi", "bim")
    # Unify adjectival/noun forms: "İdari" (adj.) and "İdare" (noun) refer
    # to the same administrative court chambers — extractors mix the two.
    s = s.replace("idari", "idare")
    return s


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def resolve_citations(
    raw_citations: list[RawCitation],
    conn,
    known_daires: list[str] | None = None,
) -> tuple[list[ResolvedCitation], list[UnresolvedCitation]]:
    """
    Resolve RawCitations against the documents table.

    known_daires: optional list of canonical court/daire name strings
        (e.g. from MongoDB court_name field).  Used as the vocabulary for
        fuzzy-matching when the exact normalized daire key is not in the index.

    Self-citations (source == target) are silently dropped.
    """
    resolved: list[ResolvedCitation] = []
    unresolved: list[UnresolvedCitation] = []

    # Load entire documents table once and build daire-normalized index.
    with conn.cursor() as cur:
        cur.execute("SELECT doc_id, court, daire, esas_no, karar_no FROM documents")
        rows = cur.fetchall()

    by_daire: dict[str, list[tuple[str, str, str]]] = {}
    for doc_id, court, daire, esas_no, karar_no in rows:
        entry = (doc_id, esas_no or "", karar_no or "")
        # Index by daire alone ("2. Hukuk Dairesi" → "2hukukdairesi")
        key = _normalize_daire(daire)
        if key:
            by_daire.setdefault(key, []).append(entry)
        # Also index by court+daire so citations that include the court name
        # ("Yargıtay 2. Hukuk Dairesi") match docs that store them separately.
        court_key = _normalize_daire(f"{court} {daire}")
        if court_key and court_key != key:
            by_daire.setdefault(court_key, []).append(entry)

    # Build fuzzy-match vocabulary: canonical name → normalized key.
    # Only keep entries whose normalized key is actually in by_daire so that
    # a fuzzy hit always leads to a corpus document.
    _fuzzy_vocab: dict[str, str] = {}  # canonical_name → norm_key
    for name in (known_daires or []):
        if not name:
            continue
        nk = _normalize_daire(name)
        if nk in by_daire and name not in _fuzzy_vocab:
            _fuzzy_vocab[name] = nk
    # Always include the raw daire values already in the DB as fuzzy candidates.
    _daire_raw: dict[str, str] = {}  # raw_daire_string → norm_key
    for doc_id, court, daire, esas_no, karar_no in rows:
        if daire:
            nk = _normalize_daire(daire)
            if nk in by_daire:
                _daire_raw[daire] = nk
        if court and daire:
            full = f"{court} {daire}"
            nk = _normalize_daire(full)
            if nk in by_daire:
                _daire_raw[full] = nk
    _fuzzy_vocab.update(_daire_raw)
    _fuzzy_names = list(_fuzzy_vocab.keys())

    for c in raw_citations:
        if not c.daire:
            unresolved.append(_unresolved(c, "no_daire_extracted"))
            continue
        if not c.esas_no and not c.karar_no:
            unresolved.append(_unresolved(c, "no_esas_or_karar"))
            continue

        norm_daire = _normalize_daire(c.daire)
        candidates = by_daire.get(norm_daire, [])

        if not candidates:
            # Prefix fallback: handles "bursabam" matching "bursabam4cezadairesi"
            # (citation names general court, document stores specific chamber).
            if len(norm_daire) >= 5:
                candidates = [
                    entry
                    for k, entries in by_daire.items()
                    if k.startswith(norm_daire)
                    for entry in entries
                ]

        if not candidates and _fuzzy_names:
            # Fuzzy fallback: match the raw (un-normalized) extracted daire
            # string against canonical names so that diacritics / dots /
            # ASCII variants are handled at the surface form level.
            close = get_close_matches(c.daire, _fuzzy_names, n=1, cutoff=0.88)
            if close:
                candidates = by_daire.get(_fuzzy_vocab[close[0]], [])

        if not candidates:
            unresolved.append(_unresolved(c, "daire_not_in_corpus"))
            continue

        # Score candidates using set intersection of case numbers.
        # This handles the common case where the extractor stores a number as
        # karar_no but the document records it as esas_no (e.g. AYM başvuru no).
        scored: list[tuple[str, int, str, str]] = []
        for doc_id, esas, karar in candidates:
            if doc_id == c.source_doc_id:
                continue
            citation_numbers = {n for n in [c.esas_no, c.karar_no] if n}
            doc_numbers = {n for n in [esas, karar] if n}
            score = len(citation_numbers & doc_numbers)
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
