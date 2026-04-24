"""
Regex-based citation extractor for Turkish legal documents.

Two-phase extraction:
  1. Header matching  — identifies the citing court and produces a normalized
                        `daire` string that matches the PostgreSQL `documents.daire`
                        field format (e.g. "Yargıtay 11. Hukuk Dairesi",
                        "Adana 1. Asliye Ticaret Mahkemesi").
  2. E/K pair scan    — within a bounded window after each header, extracts every
                        (esas_no, karar_no) tuple — including multiple pairs under
                        the same header, lone-E, lone-K, merged E.K, and AYM
                        individual-application "B. No:" forms.

Does NOT resolve citations to doc_ids — that is handled by resolver.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

SNIPPET_WINDOW = 120   # chars before/after the header start for the audit snippet
EK_WINDOW = 300        # chars after a header that we search for E/K pairs


@dataclass
class RawCitation:
    source_doc_id: str
    raw_text: str
    daire: str                  # normalized to DB `daire` field format
    esas_no: str | None         # canonical "YYYY/NNNN" or None
    karar_no: str | None        # canonical "YYYY/NNNN" or None
    snippet: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snippet(text: str, start: int) -> str:
    s = max(0, start - SNIPPET_WINDOW)
    e = min(len(text), start + SNIPPET_WINDOW)
    return text[s:e].strip()


# Turkish noun-suffix glue after a court-name token (e.g. "Dairesinin",
# "Mahkemesi'nin"). Matches an optional apostrophe + word.
_SUF = r"(?:['’]?[A-Za-zçğıöşüÇĞİÖŞÜ]+)?"

# City name: a single capitalised Turkish word (İstanbul, Gölbaşı, Ankara).
_CITY = r"[A-ZÇĞİÖŞÜ][A-Za-zçğıöşüÇĞİÖŞÜ]+"

# Optional first-instance region suffix (İstanbul *Anadolu* 1. Asliye...).
_REGION = r"(?:Anadolu|Batı|Doğu|Kuzey|Güney)"

# Optional parenthetical between city and daire number ("Gölbaşı (Ankara) 2. ...").
_PAREN = r"(?:\s*\([^)]{1,40}\))?"


# ---------------------------------------------------------------------------
# Header rules
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _HeaderRule:
    pattern: re.Pattern
    normalize: Callable[[re.Match], str | None]


_HEADER_RULES: list[_HeaderRule] = []


def _rule(pat: str, norm: Callable[[re.Match], str | None]) -> None:
    _HEADER_RULES.append(_HeaderRule(re.compile(pat, re.UNICODE), norm))


# --- Yargıtay special bodies (listed before the generic chamber rule) ---

_rule(
    r"(?:Yargıtay\s+)?Hukuk\s+Genel\s+Kurulu" + _SUF,
    lambda m: "Yargıtay Hukuk Genel Kurulu",
)
_rule(
    r"(?:Yargıtay\s+)?Ceza\s+Genel\s+Kurulu" + _SUF,
    lambda m: "Yargıtay Ceza Genel Kurulu",
)
_rule(
    r"(?:Yargıtay\s+)?Hukuk\s+Daireleri\s+Başkanlar\s+Kurulu" + _SUF,
    lambda m: "Yargıtay Hukuk Daireleri Başkanlar Kurulu",
)
_rule(
    r"(?:Yargıtay\s+)?Ceza\s+Daireleri\s+Başkanlar\s+Kurulu" + _SUF,
    lambda m: "Yargıtay Ceza Daireleri Başkanlar Kurulu",
)
_rule(
    r"(?:Yargıtay\s+)?İçtihadı\s+Birleştirme\s+Büyük\s+Genel\s+Kurulu" + _SUF,
    lambda m: "Yargıtay YIBBGK",
)
_rule(
    r"(?:Yargıtay\s+)?İçtihadı\s+Birleştirme\s+Hukuk\s+Genel\s+Kurulu" + _SUF,
    lambda m: "Yargıtay IBHGK",
)

# Yargıtay N. Hukuk/Ceza Dairesi (full, abbreviated H.D./C.D./HD/CD).
_rule(
    r"(?:Yargıtay|Y\.)\s*(\d{1,2})\s*\.\s*"
    r"(?:(Hukuk|Ceza)\s+Dairesi" + _SUF + r"|(H\.?D\.?|C\.?D\.?))",
    lambda m: (
        f"Yargıtay {m.group(1)}. "
        f"{m.group(2) or ('Hukuk' if m.group(3).upper().startswith('H') else 'Ceza')}"
        f" Dairesi"
    ),
)

# --- Danıştay ---

_rule(
    r"Danıştay" + _SUF + r"\s+(?:İdari?\s+Dava\s+Daireleri\s+Kurulu|İDDK|İDDGK)",
    lambda m: "Danıştay İdare Dava Daireleri Kurulu",
)
_rule(
    r"Danıştay" + _SUF + r"\s+(?:Vergi\s+Dava\s+Daireleri\s+Kurulu|VDDK)",
    lambda m: "Danıştay Vergi Dava Daireleri Kurulu",
)
_rule(
    r"Danıştay" + _SUF + r"\s+İçtihadları?\s+Birleştirme(?:\s+Kurulu)?",
    lambda m: "Danıştay IBK",
)
_rule(
    r"Danıştay" + _SUF + r"\s+(\d{1,2})\s*\.\s*Daire(?:si|\s+Başkanlığı)?" + _SUF,
    lambda m: f"Danıştay {m.group(1)}. Daire",
)

# --- Anayasa Mahkemesi ---

_rule(
    r"Anayasa\s+Mahkemesi" + _SUF,
    lambda m: "Anayasa Mahkemesi",
)

# --- Sayıştay ---

_rule(
    r"Sayıştay" + _SUF + r"\s+(\d{1,2})\s*\.\s*Daire(?:si)?" + _SUF,
    lambda m: f"Sayıştay {m.group(1)}. Dairesi",
)

# --- Uyuşmazlık Mahkemesi ---

_rule(
    r"Uyuşmazlık\s+Mahkemesi" + _SUF
    + r"(?:\s+(Hukuk|Ceza|İdari|Idari)\s+Bölümü)?",
    lambda m: (
        "Uyuşmazlık Mahkemesi"
        + (f" {m.group(1)} Bölümü" if m.group(1) else "")
    ),
)


# --- BAM (Bölge Adliye Mahkemesi) ---

def _norm_bam(m: re.Match) -> str:
    city = m.group(1)
    num = m.group(2)
    full = m.group(3)
    abbr = m.group(4)
    if num is None:
        return f"{city} Bölge Adliye Mahkemesi"
    chamber = full or ("Hukuk" if abbr.upper().startswith("H") else "Ceza")
    return f"{city} Bölge Adliye Mahkemesi {num}. {chamber} Dairesi"


_rule(
    rf"({_CITY})\s+(?:Bölge\s+Adliye\s+Mahkemesi|BAM)" + _SUF
    + r"(?:\s+(\d{1,2})\s*\.?\s*"
      r"(?:(Hukuk|Ceza)\s+Dairesi" + _SUF + r"|(HD|CD|H\.D\.?|C\.D\.?)))?",
    _norm_bam,
)


# --- BİM (Bölge İdare Mahkemesi) ---

def _norm_bim(m: re.Match) -> str:
    city = m.group(1)
    num = m.group(2)
    full = m.group(3)
    abbr = m.group(4)
    if num is None:
        return f"{city} Bölge İdare Mahkemesi"
    if abbr:
        suffix = abbr
    else:
        suffix = "VDD" if full.lower().startswith("vergi") else "İDD"
    return f"{city} BİM {num}. {suffix}"


_rule(
    rf"({_CITY})\s+(?:Bölge\s+İdare\s+Mahkemesi|BİM)" + _SUF
    + r"(?:\s+(\d{1,2})\s*\.?\s*"
      r"(?:(Vergi\s+Dava\s+Dairesi|İdar[ei]\s+Dava\s+Dairesi)|(VDD|İDD)))?",
    _norm_bim,
)


# --- First-instance courts ---

_MAHK_TYPES = (
    r"Asliye\s+Ticaret|Asliye\s+Hukuk|Asliye\s+Ceza|"
    r"Sulh\s+Hukuk|Sulh\s+Ceza|Ağır\s+Ceza|"
    r"İş|İcra\s+Hukuk|İdare|Vergi|Tüketici|Aile|Kadastro|"
    r"Fikr[iî]\s+ve\s+Sın[aâî]+\s+Haklar\s+Hukuk"
)


def _norm_first_instance(m: re.Match) -> str:
    city = m.group(1).strip()
    region = m.group(2)
    num = m.group(3)
    type_str = re.sub(r"\s+", " ", m.group(4)).strip()
    prefix = f"{city} {region}" if region else city
    return f"{prefix} {num}. {type_str} Mahkemesi"


_rule(
    rf"({_CITY})(?:\s+({_REGION}))?{_PAREN}\s+(\d{{1,2}})\s*\.\s*"
    rf"({_MAHK_TYPES})\s+Mahkemesi" + _SUF,
    _norm_first_instance,
)


# ---------------------------------------------------------------------------
# E/K pair patterns
# ---------------------------------------------------------------------------

# Canonical "YYYY/NNNN" must not be adjacent to other digits (e.g. inside "30/04/2012").
_N = r"(?<!\d)(\d{4})\s*[/\-]\s*(\d+)(?!\d)"

# pair with suffix tags: "YYYY/NNNN E. YYYY/NNNN K."  /  "... Esas ... Karar"
_PAIR_SUFFIX = re.compile(
    _N + r"\s*(?:Esas|E(?:sas)?\.)"
    + r"[^\d]{0,40}"
    + _N + r"\s*(?:Karar|K(?:arar)?\.)",
    re.UNICODE | re.IGNORECASE,
)

# pair with prefix tags: "E: YYYY/NNNN, K: YYYY/NNNN"  /  "E.YYYY/NNNN K.YYYY/NNNN"
_PAIR_PREFIX = re.compile(
    r"E(?:sas)?\s*[:\.]\s*" + _N
    + r"[^\d]{0,40}"
    + r"K(?:arar)?\s*[:\.]\s*" + _N,
    re.UNICODE | re.IGNORECASE,
)

# AYM merged form: "YYYY/NN-YYYY/NN E.K"
_PAIR_MERGED = re.compile(
    _N + r"\s*[-–]\s*" + _N + r"\s*E\.?\s*K\.?",
    re.UNICODE,
)

# lone E (suffix): "YYYY/NNNN E." / "YYYY/NNNN Esas"
_LONE_E_SUFFIX = re.compile(
    _N + r"\s*(?:Esas|E(?:sas)?\.)",
    re.UNICODE | re.IGNORECASE,
)
# lone E (prefix): "E. YYYY/NNNN"
_LONE_E_PREFIX = re.compile(
    r"E(?:sas)?\s*[:\.]\s*" + _N,
    re.UNICODE | re.IGNORECASE,
)
# lone K (suffix)
_LONE_K_SUFFIX = re.compile(
    _N + r"\s*(?:Karar|K(?:arar)?\.)",
    re.UNICODE | re.IGNORECASE,
)
# lone K (prefix)
_LONE_K_PREFIX = re.compile(
    r"K(?:arar)?\s*[:\.]\s*" + _N,
    re.UNICODE | re.IGNORECASE,
)

# AYM individual application: "B. No: YYYY/NNNN" → stored as esas_no
_AYM_BN = re.compile(
    r"B\.?\s*No\.?\s*[:\.]?\s*" + _N,
    re.UNICODE | re.IGNORECASE,
)


def _extract_pairs(window: str, include_aym_bn: bool = False) -> list[tuple[str | None, str | None, int]]:
    """
    Scan a window for all (esas_no, karar_no) tuples, returning offsets within the window.

    Ordered passes — each consumes its matched span so later (less-specific) passes
    don't double-count:
        1. Pair patterns (suffix tags, prefix tags, merged AYM E.K)
        2. AYM "B. No:" (only when include_aym_bn=True)
        3. Lone E (no K partner)
        4. Lone K (no E partner)
    """
    consumed: list[tuple[int, int]] = []

    def overlaps(a: int, b: int) -> bool:
        return any(not (b <= ca or a >= cb) for ca, cb in consumed)

    results: list[tuple[str | None, str | None, int]] = []

    # 1. pair patterns
    for pat in (_PAIR_SUFFIX, _PAIR_PREFIX, _PAIR_MERGED):
        for m in pat.finditer(window):
            if overlaps(m.start(), m.end()):
                continue
            y1, n1, y2, n2 = m.group(1), m.group(2), m.group(3), m.group(4)
            results.append((f"{y1}/{n1}", f"{y2}/{n2}", m.start()))
            consumed.append((m.start(), m.end()))

    # 2. AYM individual applications
    if include_aym_bn:
        for m in _AYM_BN.finditer(window):
            if overlaps(m.start(), m.end()):
                continue
            results.append((f"{m.group(1)}/{m.group(2)}", None, m.start()))
            consumed.append((m.start(), m.end()))

    # 3. lone E
    for pat in (_LONE_E_SUFFIX, _LONE_E_PREFIX):
        for m in pat.finditer(window):
            if overlaps(m.start(), m.end()):
                continue
            results.append((f"{m.group(1)}/{m.group(2)}", None, m.start()))
            consumed.append((m.start(), m.end()))

    # 4. lone K
    for pat in (_LONE_K_SUFFIX, _LONE_K_PREFIX):
        for m in pat.finditer(window):
            if overlaps(m.start(), m.end()):
                continue
            results.append((None, f"{m.group(1)}/{m.group(2)}", m.start()))
            consumed.append((m.start(), m.end()))

    return results


# ---------------------------------------------------------------------------
# Header search & main entry point
# ---------------------------------------------------------------------------

def _find_all_headers(text: str) -> list[tuple[int, int, str]]:
    """
    Run every header rule in priority order. Earlier (more specific) rules claim
    their span first; later rules skip overlaps. Returns sorted list of
    (start, end, normalized_daire).
    """
    claimed: list[tuple[int, int]] = []
    headers: list[tuple[int, int, str]] = []
    for rule in _HEADER_RULES:
        for m in rule.pattern.finditer(text):
            s, e = m.start(), m.end()
            if any(not (e <= ca or s >= cb) for ca, cb in claimed):
                continue
            daire = rule.normalize(m)
            if not daire:
                continue
            headers.append((s, e, daire))
            claimed.append((s, e))
    headers.sort(key=lambda t: t[0])
    return headers


def extract_citations(doc_id: str, text: str) -> list[RawCitation]:
    """
    Extract all citation references from document text.

    Output is deduplicated by (daire, esas_no, karar_no). A single header can emit
    multiple citations when several (E, K) pairs follow it.
    """
    headers = _find_all_headers(text)
    if not headers:
        return []

    results: list[RawCitation] = []
    seen: set[tuple[str, str | None, str | None]] = set()

    for i, (h_start, h_end, daire) in enumerate(headers):
        # Window is bounded by EK_WINDOW chars or the next header, whichever is closer.
        window_end = min(len(text), h_end + EK_WINDOW)
        if i + 1 < len(headers):
            window_end = min(window_end, headers[i + 1][0])
        window = text[h_end:window_end]

        pairs = _extract_pairs(window, include_aym_bn=(daire == "Anayasa Mahkemesi"))

        for esas, karar, offset in pairs:
            if esas is None and karar is None:
                continue
            key = (daire, esas, karar)
            if key in seen:
                continue
            seen.add(key)

            raw_end = min(len(text), h_end + offset + 40)
            results.append(
                RawCitation(
                    source_doc_id=doc_id,
                    raw_text=text[h_start:raw_end].strip(),
                    daire=daire,
                    esas_no=esas,
                    karar_no=karar,
                    snippet=_snippet(text, h_start),
                )
            )

    return results
