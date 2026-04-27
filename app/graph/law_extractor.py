"""
Extract law article references (kanun maddesi) from Turkish legal text.

Recognised patterns (per section 11.6 of the Turkish law reference):
  1. Abbreviation + m.:  TCK m.302, TBK m.49/1, CMK m.53/1-a, HMK m.100, f.2
  2. Possessive form:    TCK'nın 302. maddesi
  3. Numbered + m.:     5237 sayılı Türk Ceza Kanunu m.302
  4. Numbered + madde:  5237 sayılı ... 302. maddesi
  5. Anayasa m.:        Anayasa m.90 / Anayasa'nın 13. maddesi

Output is deduplicated by (law_code, article) per document — the first-seen
paragraph and subparagraph values are recorded on the reference.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_SNIPPET_WIN = 80


@dataclass
class RawLawReference:
    source_doc_id: str
    law_code: str          # canonical code e.g. "TCK", "TBK"
    law_no: int | None     # numeric law number e.g. 5237
    article: int
    paragraph: int | None = None
    subparagraph: str | None = None
    raw_text: str = ""
    snippet: str = ""


# ---------------------------------------------------------------------------
# Law registry — canonical abbreviation → metadata
# ---------------------------------------------------------------------------

LAW_REGISTRY: dict[str, dict] = {
    "TCK":     {"full_name": "Türk Ceza Kanunu",                        "law_no": 5237, "branch": "ceza"},
    "CMK":     {"full_name": "Ceza Muhakemesi Kanunu",                  "law_no": 5271, "branch": "ceza"},
    "CMUK":    {"full_name": "Ceza Muhakemeleri Usulü Kanunu (eski)",   "law_no": 1412, "branch": "ceza"},
    "TMK":     {"full_name": "Türk Medenî Kanunu",                      "law_no": 4721, "branch": "hukuk"},
    "TBK":     {"full_name": "Türk Borçlar Kanunu",                     "law_no": 6098, "branch": "hukuk"},
    "TTK":     {"full_name": "Türk Ticaret Kanunu",                     "law_no": 6102, "branch": "hukuk"},
    "HMK":     {"full_name": "Hukuk Muhakemeleri Kanunu",               "law_no": 6100, "branch": "hukuk"},
    "HUMK":    {"full_name": "Hukuk Usulü Muhakemeleri Kanunu (eski)",  "law_no": 1086, "branch": "hukuk"},
    "İİK":     {"full_name": "İcra ve İflâs Kanunu",                    "law_no": 2004, "branch": "hukuk"},
    "İK":      {"full_name": "İş Kanunu",                               "law_no": 4857, "branch": "hukuk"},
    "İK_1475": {"full_name": "İş Kanunu (eski 1475, m.14 yürürlükte)", "law_no": 1475, "branch": "hukuk"},
    "İYUK":    {"full_name": "İdari Yargılama Usulü Kanunu",            "law_no": 2577, "branch": "idari"},
    "TKHK":    {"full_name": "Tüketicinin Korunması Hk. Kanun",        "law_no": 6502, "branch": "hukuk"},
    "VUK":     {"full_name": "Vergi Usul Kanunu",                       "law_no":  213, "branch": "vergi"},
    "MK":      {"full_name": "Medeni Kanun (eski 743)",                 "law_no":  743, "branch": "hukuk"},
    "BK":      {"full_name": "Borçlar Kanunu (eski 818)",               "law_no":  818, "branch": "hukuk"},
    "KMK":     {"full_name": "Kat Mülkiyeti Kanunu",                    "law_no":  634, "branch": "hukuk"},
    "AY":      {"full_name": "Türkiye Cumhuriyeti Anayasası",           "law_no": 2709, "branch": "anayasa"},
    "AİHS":    {"full_name": "Avrupa İnsan Hakları Sözleşmesi",        "law_no": None, "branch": "anayasa"},
}

# law_no → canonical code (first registration wins; newer/primary laws preferred)
_NO_TO_CODE: dict[int, str] = {}
for _code, _meta in LAW_REGISTRY.items():
    _n = _meta.get("law_no")
    if _n and _n not in _NO_TO_CODE:
        _NO_TO_CODE[_n] = _code


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Abbreviations in descending length order to prevent prefix collisions.
# İİK must come before İK; TKHK before İK; CMUK before CMK; HUMK before HMK.
_ABBREVS = (
    "TKHK|İYUK|HUMK|CMUK|İİK|TTK|TBK|TMK|HMK|CMK|TCK|KMK|VUK|AİHS|İK|MK|BK|AY"
)

# Core article segment after an abbreviation or law reference:
#   m.302           — basic
#   m.302/1         — with paragraph (slash form)
#   m.302, f.1      — with paragraph (fıkra form)
#   m.302/1-a       — with paragraph and subparagraph (bent)
# Groups: (article, para_slash, para_f, subparagraph)
_ART = (
    r"m\.?\s*"
    r"(\d{1,4})"
    r"(?:[/]\s*(\d{1,3})|,\s*[fF]\.?\s*(\d{1,3}))?"
    r"(?:\s*[-–]\s*([a-z]))?"
)

# Pattern 1: "TCK m.302" / "TBK m.49/1" / "CMK m.53/1-a"
# Groups: 1=code, 2=article, 3=para_slash, 4=para_f, 5=sub
_ABBREV_CITE = re.compile(
    rf"\b({_ABBREVS})\s+{_ART}",
    re.UNICODE,
)

# Pattern 2: "TCK'nın 302. maddesi" / "TBK'nın 49. maddesi"
# Groups: 1=code, 2=article
_ABBREV_MADDE = re.compile(
    rf"\b({_ABBREVS})['‘’ʼ]?\w*\s+(\d{{1,4}})\s*['.]\s*maddes",
    re.UNICODE | re.IGNORECASE,
)

# Pattern 3: "5237 sayılı ... m.302" — numbered law + m. article
# Groups: 1=law_no, 2=article, 3=para_slash, 4=para_f, 5=sub
_NUMBERED_M = re.compile(
    r"(\d{3,5})\s+(?:sayılı|s\.)\s+"
    r"[^\n;]{0,80}?"   # law name (non-greedy, stops at newline/semicolon)
    + _ART,
    re.UNICODE | re.IGNORECASE,
)

# Pattern 4: "5237 sayılı ... 302. maddesi" — numbered law + ordinal madde
# Groups: 1=law_no, 2=article
_NUMBERED_MADDE = re.compile(
    r"(\d{3,5})\s+(?:sayılı|s\.)\s+"
    r"[^\n;]{0,80}?"
    r"(\d{1,4})\s*['.]\s*maddes",
    re.UNICODE | re.IGNORECASE,
)

# Pattern 5: "Anayasa m.13" without the AY abbreviation
_ANAYASA_M = re.compile(
    r"Anayasa['‘’ʼ]?\w*\s+m\.?\s*(\d{1,3})",
    re.UNICODE | re.IGNORECASE,
)

# Pattern 6: "Anayasa'nın 13. maddesi"
_ANAYASA_MADDE = re.compile(
    r"Anayasa['‘’ʼ]?\w*\s+(\d{1,3})\s*['.]\s*maddes",
    re.UNICODE | re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract_law_references(doc_id: str, text: str) -> list[RawLawReference]:
    """
    Extract all law article references from a Turkish legal document.

    Returns one RawLawReference per unique (law_code, article) pair. When the
    same article is cited multiple times, the first-seen paragraph/subparagraph
    is kept.
    """
    results: list[RawLawReference] = []
    seen: set[tuple[str, int]] = set()

    def _snip(pos: int) -> str:
        s = max(0, pos - _SNIPPET_WIN)
        e = min(len(text), pos + _SNIPPET_WIN)
        return text[s:e].strip()

    def _add(
        code: str,
        law_no: int | None,
        article: int,
        para: int | None,
        sub: str | None,
        raw: str,
        pos: int,
    ) -> None:
        key = (code, article)
        if key in seen:
            return
        seen.add(key)
        results.append(RawLawReference(
            source_doc_id=doc_id,
            law_code=code,
            law_no=law_no,
            article=article,
            paragraph=para,
            subparagraph=sub or None,
            raw_text=raw[:200],
            snippet=_snip(pos),
        ))

    def _para(g_slash: str | None, g_f: str | None) -> int | None:
        raw = g_slash or g_f
        return int(raw) if raw else None

    # --- Pattern 1: abbreviation + m. ---
    for m in _ABBREV_CITE.finditer(text):
        code = m.group(1).upper()
        info = LAW_REGISTRY.get(code, {})
        _add(
            code=code,
            law_no=info.get("law_no"),
            article=int(m.group(2)),
            para=_para(m.group(3), m.group(4)),
            sub=m.group(5) or None,
            raw=m.group(0),
            pos=m.start(),
        )

    # --- Pattern 2: abbreviation + possessive + ordinal madde ---
    for m in _ABBREV_MADDE.finditer(text):
        code = m.group(1).upper()
        info = LAW_REGISTRY.get(code, {})
        _add(
            code=code,
            law_no=info.get("law_no"),
            article=int(m.group(2)),
            para=None,
            sub=None,
            raw=m.group(0),
            pos=m.start(),
        )

    # --- Pattern 3: numbered law + m. ---
    for m in _NUMBERED_M.finditer(text):
        law_no = int(m.group(1))
        code = _NO_TO_CODE.get(law_no, f"KANUN_{law_no}")
        _add(
            code=code,
            law_no=law_no,
            article=int(m.group(2)),
            para=_para(m.group(3), m.group(4)),
            sub=m.group(5) or None,
            raw=m.group(0),
            pos=m.start(),
        )

    # --- Pattern 4: numbered law + ordinal madde ---
    for m in _NUMBERED_MADDE.finditer(text):
        law_no = int(m.group(1))
        code = _NO_TO_CODE.get(law_no, f"KANUN_{law_no}")
        _add(
            code=code,
            law_no=law_no,
            article=int(m.group(2)),
            para=None,
            sub=None,
            raw=m.group(0),
            pos=m.start(),
        )

    # --- Patterns 5 & 6: Anayasa without AY abbreviation ---
    for m in _ANAYASA_M.finditer(text):
        _add("AY", 2709, int(m.group(1)), None, None, m.group(0), m.start())

    for m in _ANAYASA_MADDE.finditer(text):
        _add("AY", 2709, int(m.group(1)), None, None, m.group(0), m.start())

    return results
