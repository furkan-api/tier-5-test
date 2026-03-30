"""
Regex-based citation extractor for Turkish legal documents.

Extracts raw citation references (case numbers) from document text.
Does NOT resolve citations to doc_ids — that is handled by resolver.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SNIPPET_WINDOW = 120  # characters before/after match start


@dataclass
class RawCitation:
    source_doc_id: str
    raw_text: str           # full match text
    court_hint: str | None  # e.g. "Yargıtay", "Danıştay"
    daire_hint: str | None  # e.g. "1", "9" (daire number as string)
    esas_no: str | None     # canonical form YYYY/NNNN
    karar_no: str | None    # canonical form YYYY/NNNN
    snippet: str = field(default="")  # surrounding text window


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def _norm_esas(year: str | None, number: str | None) -> str | None:
    if year and number:
        return f"{year.strip()}/{number.strip()}"
    return None


def _extract_snippet(text: str, match_start: int) -> str:
    start = max(0, match_start - SNIPPET_WINDOW)
    end = min(len(text), match_start + SNIPPET_WINDOW)
    return text[start:end].strip()


# ---------------------------------------------------------------------------
# Compiled patterns (most-specific first)
# ---------------------------------------------------------------------------

# Pattern 1: Full Yargıtay named
# e.g. "Yargıtay 1. Hukuk Dairesi'nin ... 2019/3254 Esas, 2020/1876 Karar"
_P1 = re.compile(
    r"Yargıtay\s+(\d+)\.\s*(?:Hukuk|Ceza)\s+Dairesi[^\d]{0,60}"
    r"(\d{4})[/\-](\d+)\s*(?:E(?:sas)?\.?[,\s]|Esas)[^\d]{0,30}"
    r"(\d{4})[/\-](\d+)\s*(?:K(?:arar)?\.?|Karar)",
    re.UNICODE,
)

# Pattern 2: Abbreviated Yargıtay
# e.g. "Y.1.HD", "Yargıtay 1. HD", "Y1HD" + esas/karar
_P2 = re.compile(
    r"(?:Y(?:argıtay)?\.?\s*(\d+)\.?\s*(?:H(?:ukuk)?\.?D(?:airesi)?|HD|CD|C\.D\.))"
    r"[^\d]{0,50}"
    r"(\d{4})[/\-](\d+)\s*E\.?"
    r"(?:[^\d]{0,30}(\d{4})[/\-](\d+)\s*K\.?)?",
    re.UNICODE,
)

# Pattern 3: HGK / CGK
# e.g. "Hukuk Genel Kurulu'nun 2021/234 E., 2022/456 K."
_P3 = re.compile(
    r"(?:Hukuk\s+Genel\s+Kurulu|Ceza\s+Genel\s+Kurulu|HGK|CGK)[^\d]{0,50}"
    r"(\d{4})[/\-](\d+)\s*E\.?"
    r"(?:[^\d]{0,30}(\d{4})[/\-](\d+)\s*K\.?)?",
    re.UNICODE,
)

# Pattern 4: Danıştay
# e.g. "Danıştay 5. Dairesi, E:2019/1234, K:2020/5678"
_P4 = re.compile(
    r"(?:Danıştay\s+(\d+)\.\s*(?:Dairesi|D\.)|D\.(\d+)\.D\.)[^\d]{0,50}"
    r"(?:E[:\.]?\s*)?(\d{4})[/\-](\d+)"
    r"(?:[^\d]{0,30}(?:K[:\.]?\s*)?(\d{4})[/\-](\d+))?",
    re.UNICODE,
)

# Pattern 5: İBK (İçtihadı Birleştirme Kararı)
# e.g. "İçtihadı Birleştirme 1988/3 E., 1990/1 K."
_P5 = re.compile(
    r"İçtihadı\s+Birleştirme[^\d]{0,60}"
    r"(\d{4})[/\-](\d+)\s*E\.?"
    r"(?:[^\d]{0,30}(\d{4})[/\-](\d+)\s*K\.?)?",
    re.UNICODE,
)

# Pattern 6: BAM / BİM
# e.g. "İstanbul Bölge Adliye Mahkemesi 12. Hukuk Dairesi ... 2021/100 E."
_P6 = re.compile(
    r"(?:\w+\s+)?Bölge\s+(?:Adliye|İdare)\s+Mahkemesi(?:\s+\d+\.\s*\w+\s+Dairesi)?"
    r"[^\d]{0,60}"
    r"(\d{4})[/\-](\d+)\s*E\.?"
    r"(?:[^\d]{0,30}(\d{4})[/\-](\d+)\s*K\.?)?",
    re.UNICODE,
)


@dataclass
class _PatternSpec:
    pattern: re.Pattern
    court_hint: str | None
    daire_group: int | None      # group index for daire number (1-based), or None
    esas_year_group: int
    esas_num_group: int
    karar_year_group: int | None
    karar_num_group: int | None


_PATTERNS: list[_PatternSpec] = [
    _PatternSpec(_P1, "Yargıtay",  1, 2, 3, 4, 5),
    _PatternSpec(_P2, "Yargıtay",  1, 2, 3, 4, 5),
    _PatternSpec(_P3, "HGK/CGK",  None, 1, 2, 3, 4),
    _PatternSpec(_P4, "Danıştay",  1, 3, 4, 5, 6),
    _PatternSpec(_P5, "İBK",      None, 1, 2, 3, 4),
    _PatternSpec(_P6, "BAM/BİM",  None, 1, 2, 3, 4),
]


def extract_citations(doc_id: str, text: str) -> list[RawCitation]:
    """
    Extract all citation references from document text.

    Returns deduplicated RawCitation list (deduplication key: court_hint+daire_hint+esas_no).
    """
    seen: set[tuple] = set()
    results: list[RawCitation] = []

    for spec in _PATTERNS:
        for m in spec.pattern.finditer(text):
            groups = m.groups()

            def g(idx: int | None) -> str | None:
                if idx is None:
                    return None
                v = groups[idx - 1]
                return v.strip() if v else None

            daire = g(spec.daire_group)
            esas_no = _norm_esas(g(spec.esas_year_group), g(spec.esas_num_group))
            karar_no = _norm_esas(g(spec.karar_year_group), g(spec.karar_num_group))

            if not esas_no:
                continue

            key = (spec.court_hint, daire, esas_no)
            if key in seen:
                continue
            seen.add(key)

            results.append(
                RawCitation(
                    source_doc_id=doc_id,
                    raw_text=m.group(0),
                    court_hint=spec.court_hint,
                    daire_hint=daire,
                    esas_no=esas_no,
                    karar_no=karar_no,
                    snippet=_extract_snippet(text, m.start()),
                )
            )

    return results
