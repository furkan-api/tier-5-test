#!/usr/bin/env python3
"""
Verify that citations extracted by `app.ingestion.llm_process` are actually
present in the source markdown — i.e. that the LLM did not hallucinate them.

For each `<stem>.json` in the extraction output directory, the matching
`<stem>.md` in the corpus directory is loaded and every entry under
`cited_court_decisions` and `cited_law_articles` is checked:

  * Court decision: verified iff its `case_number` OR `decision_number`
    appears in the source as a digits/slash token.
  * Law article: verified iff its `law_number`, a known abbreviation for
    the law, or the full law name appears in the source. (We do NOT check
    the article number — bare integers match too freely to be meaningful.)

Per-document results are written to `<stem>.verification.json` next to
the extraction JSON. With `--strict`, hallucinated entries are dropped
from `<stem>.json` and the original is preserved as
`<stem>.unverified.json`.

Usage:
    python -m app.ingestion.verify_citations
    python -m app.ingestion.verify_citations --extraction-dir DIR --corpus-dir DIR
    python -m app.ingestion.verify_citations --strict        # rewrite JSONs
    python -m app.ingestion.verify_citations <substring>     # filename filter
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from app.core.config import Settings, get_settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


# Mirrors the abbreviation table in
# app/ingestion/prompts/decision_extraction_v2.md. Keep these in sync.
# Each canonical full law name maps to:
#   abbreviations: uppercase tokens we expect to see literally in the source
#                  (matched case-sensitively with word boundaries)
#   law_number:    official act number, or None for treaties / regulations
LAW_ABBREVIATIONS: dict[str, dict[str, Any]] = {
    "Hukuk Muhakemeleri Kanunu":                           {"abbreviations": ["HMK"], "law_number": "6100"},
    "Hukuk Usulü Muhakemeleri Kanunu (mülga)":             {"abbreviations": ["HUMK"], "law_number": "1086"},
    "Türk Ticaret Kanunu":                                 {"abbreviations": ["TTK"], "law_number": "6102"},
    "Türk Ticaret Kanunu (mülga)":                         {"abbreviations": ["eTTK", "TTK"], "law_number": "6762"},
    "Türk Borçlar Kanunu":                                 {"abbreviations": ["TBK"], "law_number": "6098"},
    "Borçlar Kanunu (mülga)":                              {"abbreviations": ["BK"], "law_number": "818"},
    "Türk Medeni Kanunu":                                  {"abbreviations": ["TMK", "MK"], "law_number": "4721"},
    "Türk Kanunu Medenisi (mülga)":                        {"abbreviations": ["eMK", "MK"], "law_number": "743"},
    "Türk Ceza Kanunu":                                    {"abbreviations": ["TCK"], "law_number": "5237"},
    "Türk Ceza Kanunu (mülga)":                            {"abbreviations": ["eTCK", "TCK"], "law_number": "765"},
    "Ceza Muhakemesi Kanunu":                              {"abbreviations": ["CMK"], "law_number": "5271"},
    "Ceza Muhakemeleri Usulü Kanunu (mülga)":              {"abbreviations": ["CMUK"], "law_number": "1412"},
    "İdari Yargılama Usulü Kanunu":                        {"abbreviations": ["İYUK", "IYUK"], "law_number": "2577"},
    "İcra ve İflas Kanunu":                                {"abbreviations": ["İİK", "IIK"], "law_number": "2004"},
    "Türkiye Cumhuriyeti Anayasası":                       {"abbreviations": ["AY", "Anayasa"], "law_number": "2709"},
    "Anayasa Mahkemesinin Kuruluşu ve Yargılama Usulleri Hakkında Kanun":
                                                           {"abbreviations": ["AMKU"], "law_number": "6216"},
    "İnsan Haklarını ve Temel Özgürlükleri Korumaya Dair Sözleşme":
                                                           {"abbreviations": ["AİHS", "AIHS"], "law_number": None},
    "İş Kanunu":                                           {"abbreviations": ["İK", "IK"], "law_number": "4857"},
    "Sosyal Sigortalar ve Genel Sağlık Sigortası Kanunu":  {"abbreviations": ["SGK"], "law_number": "5510"},
    "Tüketicinin Korunması Hakkında Kanun":                {"abbreviations": ["TKHK"], "law_number": "6502"},
    "Tüketicinin Korunması Hakkında Kanun (mülga)":        {"abbreviations": ["eTKHK"], "law_number": "4077"},
    "Kişisel Verilerin Korunması Kanunu":                  {"abbreviations": ["KVKK"], "law_number": "6698"},
    "Fikir ve Sanat Eserleri Kanunu":                      {"abbreviations": ["FSEK"], "law_number": "5846"},
    "Kamu İhale Kanunu":                                   {"abbreviations": ["KİK", "KIK"], "law_number": "4734"},
    "Kamu İhale Sözleşmeleri Kanunu":                      {"abbreviations": ["KSK"], "law_number": "4735"},
    "Vergi Usul Kanunu":                                   {"abbreviations": ["VUK"], "law_number": "213"},
    "Katma Değer Vergisi Kanunu":                          {"abbreviations": ["KDVK"], "law_number": "3065"},
    "Gelir Vergisi Kanunu":                                {"abbreviations": ["GVK"], "law_number": "193"},
    "Kurumlar Vergisi Kanunu":                             {"abbreviations": ["KVK"], "law_number": "5520"},
    "Siyasi Partiler Kanunu":                              {"abbreviations": [], "law_number": "2820"},
    "Kabahatler Kanunu":                                   {"abbreviations": [], "law_number": "5326"},
    "Uyuşmazlık Mahkemesinin Kuruluş ve İşleyişi Hakkında Kanun":
                                                           {"abbreviations": [], "law_number": "2247"},
}


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------
_WS_RE = re.compile(r"\s+")
# Keeps Turkish letters; everything else collapses to a space so that
# accidental punctuation between tokens doesn't break substring search.
_NON_ALNUM_RE = re.compile(r"[^0-9a-zçğıiöşü]+", re.IGNORECASE)


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def normalize_for_substring(s: str) -> str:
    """Lowercase + NFC + collapse whitespace. Used for full-name search."""
    return _WS_RE.sub(" ", _nfc(s).lower()).strip()


def compact_id(s: str) -> str:
    """Strip everything except digits and a few separators, then drop spaces.

    Source markdowns vary in how they punctuate case/decision numbers
    (`2019/366`, `2019 / 366`, `2019/ 366` …). Compacting both the source
    and the citation token lets us substring-match reliably.
    """
    s = _nfc(str(s))
    # Keep only digits and slashes — that's all we need for "YYYY/N" tokens.
    s = re.sub(r"[^0-9/]", "", s)
    return s


def has_word(source: str, token: str) -> bool:
    """Case-sensitive word-boundary search on the original source.

    Used for short uppercase abbreviations (TBK, HMK, AY, …) where a
    case-insensitive substring search would produce false positives.
    """
    if not token:
        return False
    pattern = r"(?<![0-9A-Za-zÇĞİıÖŞÜçğıöşü])" + re.escape(token) + r"(?![0-9A-Za-zÇĞİıÖŞÜçğıöşü])"
    return re.search(pattern, source) is not None


def has_law_number(source: str, law_number: str) -> bool:
    """Match the official law number as an isolated digit run.

    Operates on the original source (not the compacted form) so that
    adjacent numbers like `6098 sayılı TBK'nun 52.maddesinde` keep
    separate borders — i.e. `6098` matches but `609` does not.
    """
    if not law_number:
        return False
    return bool(re.search(rf"(?<!\d){re.escape(str(law_number))}(?!\d)", source))


# ---------------------------------------------------------------------------
# Verification primitives
# ---------------------------------------------------------------------------
@dataclass
class CitationCheck:
    index: int
    kind: str                        # "court_decision" or "law_article"
    citation: dict
    verified: bool
    signals: list[str] = field(default_factory=list)
    reason: str | None = None        # populated when verified is False


def verify_court_decision(
    citation: dict, *, source: str, source_compact_id: str, idx: int,
) -> CitationCheck:
    case_number = citation.get("case_number")
    decision_number = citation.get("decision_number")

    signals = []
    case_token = compact_id(case_number) if case_number else ""
    decision_token = compact_id(decision_number) if decision_number else ""

    if case_token and case_token in source_compact_id:
        signals.append(f"case_number:{case_number}")
    if decision_token and decision_token in source_compact_id:
        signals.append(f"decision_number:{decision_number}")

    verified = bool(signals)
    reason = None
    if not verified:
        if not case_token and not decision_token:
            reason = "citation has no case_number or decision_number to check"
        else:
            reason = "neither case_number nor decision_number found in source"

    return CitationCheck(
        index=idx, kind="court_decision",
        citation=citation, verified=verified,
        signals=signals, reason=reason,
    )


def verify_law_article(
    citation: dict, *, source: str, source_normalized: str, idx: int,
) -> CitationCheck:
    law = citation.get("law")
    law_number = citation.get("law_number")

    signals = []

    # 1) law_number — strongest signal, e.g. "6098 sayılı..."
    if law_number and has_law_number(source, str(law_number)):
        signals.append(f"law_number:{law_number}")

    # 2) abbreviations — short, uppercase tokens like TBK, HMK, İYUK.
    #    Matched against the original (un-normalized) source text.
    spec = LAW_ABBREVIATIONS.get(law) if law else None
    if spec:
        for abbr in spec["abbreviations"]:
            if has_word(source, abbr):
                signals.append(f"abbreviation:{abbr}")
                break
        # Also try the spec's law_number if the citation didn't carry one.
        if not law_number and spec["law_number"] and \
                has_law_number(source, spec["law_number"]):
            signals.append(f"law_number:{spec['law_number']}")

    # 3) full law name — substring on lowercase NFC text. We strip the
    #    "(mülga)" marker because the source rarely repeats it inline.
    if law:
        canonical = normalize_for_substring(re.sub(r"\(mülga\)", "", law))
        if canonical and len(canonical) >= 6 and canonical in source_normalized:
            signals.append("full_name")

    verified = bool(signals)
    reason = None
    if not verified:
        if not law and not law_number:
            reason = "citation has no law name or law_number"
        else:
            reason = "no law_number, abbreviation, or full name found in source"

    return CitationCheck(
        index=idx, kind="law_article",
        citation=citation, verified=verified,
        signals=signals, reason=reason,
    )


# ---------------------------------------------------------------------------
# File-level orchestration
# ---------------------------------------------------------------------------
@dataclass
class FileResult:
    stem: str
    json_path: Path
    source_path: Path
    decisions: list[CitationCheck] = field(default_factory=list)
    laws: list[CitationCheck] = field(default_factory=list)

    def to_dict(self) -> dict:
        def _check_to_dict(c: CitationCheck) -> dict:
            return {
                "index": c.index,
                "verified": c.verified,
                "signals": c.signals,
                "reason": c.reason,
                "citation": c.citation,
            }

        d_total, d_ok = len(self.decisions), sum(1 for c in self.decisions if c.verified)
        l_total, l_ok = len(self.laws), sum(1 for c in self.laws if c.verified)
        return {
            "file": self.stem,
            "summary": {
                "cited_court_decisions": {"total": d_total, "verified": d_ok,
                                          "hallucinated": d_total - d_ok},
                "cited_law_articles": {"total": l_total, "verified": l_ok,
                                       "hallucinated": l_total - l_ok},
            },
            "cited_court_decisions": [_check_to_dict(c) for c in self.decisions],
            "cited_law_articles": [_check_to_dict(c) for c in self.laws],
        }


def verify_file(json_path: Path, source_path: Path) -> FileResult:
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    source = source_path.read_text(encoding="utf-8")
    source_compact_id = compact_id(source)
    source_normalized = normalize_for_substring(source)

    result = FileResult(
        stem=json_path.stem, json_path=json_path, source_path=source_path,
    )

    for i, c in enumerate(payload.get("cited_court_decisions") or []):
        if not isinstance(c, dict):
            continue
        result.decisions.append(verify_court_decision(
            c, source=source, source_compact_id=source_compact_id, idx=i,
        ))

    for i, c in enumerate(payload.get("cited_law_articles") or []):
        if not isinstance(c, dict):
            continue
        result.laws.append(verify_law_article(
            c, source=source, source_normalized=source_normalized, idx=i,
        ))

    return result


def write_verification(result: FileResult, output_dir: Path) -> Path:
    out_path = output_dir / f"{result.stem}.verification.json"
    out_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out_path


def apply_strict(result: FileResult) -> tuple[bool, int]:
    """Drop hallucinated citations from `<stem>.json` in place.

    Returns (rewrote, dropped_count). When something is dropped, the
    original payload is preserved alongside as `<stem>.unverified.json`.
    """
    payload = json.loads(result.json_path.read_text(encoding="utf-8"))
    original_decisions = payload.get("cited_court_decisions") or []
    original_laws = payload.get("cited_law_articles") or []

    keep_d_idx = {c.index for c in result.decisions if c.verified}
    keep_l_idx = {c.index for c in result.laws if c.verified}

    new_decisions = [c for i, c in enumerate(original_decisions) if i in keep_d_idx]
    new_laws = [c for i, c in enumerate(original_laws) if i in keep_l_idx]

    dropped = (len(original_decisions) - len(new_decisions)) + \
              (len(original_laws) - len(new_laws))
    if dropped == 0:
        return False, 0

    backup = result.json_path.with_suffix(".unverified.json")
    if not backup.exists():
        backup.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    payload["cited_court_decisions"] = new_decisions
    payload["cited_law_articles"] = new_laws
    result.json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return True, dropped


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("filter", nargs="?", default=None,
                        help="Only verify files whose name contains this substring.")
    parser.add_argument("--corpus-dir", type=Path, default=None)
    parser.add_argument("--extraction-dir", type=Path, default=None,
                        help="Directory containing the LLM-produced JSONs "
                             "(defaults to llm_extract_output_dir from settings).")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="Where to write `<stem>.verification.json` files. "
                             "Defaults to --extraction-dir.")
    parser.add_argument("--strict", action="store_true",
                        help="Rewrite each `<stem>.json` to drop unverified "
                             "citations. Originals are backed up as "
                             "`<stem>.unverified.json`.")
    parser.add_argument("--summary", type=Path, default=None,
                        help="Optional path for an aggregate summary JSON.")
    parser.add_argument("--limit", type=int, default=None)
    return parser.parse_args(argv)


def select_pairs(
    extraction_dir: Path, corpus_dir: Path, needle: str | None, limit: int | None,
) -> tuple[list[tuple[Path, Path]], list[Path]]:
    """Return (pairs, missing_sources). `pairs` are (json_path, md_path)."""
    json_files = sorted(extraction_dir.glob("*.json"))
    json_files = [p for p in json_files
                  if not p.name.endswith(".verification.json")
                  and not p.name.endswith(".unverified.json")]
    if needle:
        json_files = [p for p in json_files if needle in p.name]

    pairs: list[tuple[Path, Path]] = []
    missing: list[Path] = []
    for jp in json_files:
        md = corpus_dir / f"{jp.stem}.md"
        if md.is_file():
            pairs.append((jp, md))
        else:
            missing.append(jp)
        if limit is not None and len(pairs) >= limit:
            break
    return pairs, missing


def run(
    pairs: Iterable[tuple[Path, Path]],
    output_dir: Path,
    *, strict: bool,
) -> tuple[list[FileResult], dict[str, int]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    totals = {
        "files": 0,
        "decisions_total": 0, "decisions_verified": 0,
        "laws_total": 0, "laws_verified": 0,
        "files_with_hallucinations": 0,
        "files_rewritten": 0, "citations_dropped": 0,
    }
    results: list[FileResult] = []

    for jp, md in pairs:
        try:
            result = verify_file(jp, md)
        except json.JSONDecodeError as exc:
            log.error("[skip] %s: invalid JSON (%s)", jp.name, exc)
            continue
        except OSError as exc:
            log.error("[skip] %s: %s", jp.name, exc)
            continue

        write_verification(result, output_dir)
        totals["files"] += 1

        d_ok = sum(1 for c in result.decisions if c.verified)
        l_ok = sum(1 for c in result.laws if c.verified)
        totals["decisions_total"] += len(result.decisions)
        totals["decisions_verified"] += d_ok
        totals["laws_total"] += len(result.laws)
        totals["laws_verified"] += l_ok

        d_bad = len(result.decisions) - d_ok
        l_bad = len(result.laws) - l_ok
        if d_bad or l_bad:
            totals["files_with_hallucinations"] += 1
            log.warning("[hallucination] %s: %d/%d decisions, %d/%d laws unverified",
                        jp.name, d_bad, len(result.decisions),
                        l_bad, len(result.laws))
        else:
            log.info("[ok] %s: %d decisions, %d laws verified",
                     jp.name, len(result.decisions), len(result.laws))

        if strict:
            rewrote, dropped = apply_strict(result)
            if rewrote:
                totals["files_rewritten"] += 1
                totals["citations_dropped"] += dropped

        results.append(result)

    return results, totals


def resolve_paths(args: argparse.Namespace, settings: Settings) -> tuple[Path, Path, Path]:
    extraction_dir = (args.extraction_dir or settings.llm_extract_output_dir).resolve()
    corpus_dir = (args.corpus_dir or settings.corpus_dir).resolve()
    output_dir = (args.output_dir or extraction_dir).resolve()
    return extraction_dir, corpus_dir, output_dir


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    settings = get_settings()

    extraction_dir, corpus_dir, output_dir = resolve_paths(args, settings)
    if not extraction_dir.is_dir():
        log.error("Extraction directory not found: %s", extraction_dir)
        return 1
    if not corpus_dir.is_dir():
        log.error("Corpus directory not found: %s", corpus_dir)
        return 1

    pairs, missing = select_pairs(extraction_dir, corpus_dir, args.filter, args.limit)
    log.info("Extraction=%s  corpus=%s  to_verify=%d  missing_source=%d",
             extraction_dir, corpus_dir, len(pairs), len(missing))
    for p in missing:
        log.warning("[no-source] %s — no matching .md in corpus", p.name)

    if not pairs:
        log.info("Nothing to do.")
        return 0

    _, totals = run(pairs, output_dir, strict=args.strict)

    d_total = totals["decisions_total"]
    l_total = totals["laws_total"]
    d_pct = 100.0 * totals["decisions_verified"] / d_total if d_total else 100.0
    l_pct = 100.0 * totals["laws_verified"] / l_total if l_total else 100.0
    log.info(
        "Done — files=%d  decisions=%d/%d (%.1f%%)  laws=%d/%d (%.1f%%)  "
        "files_with_hallucinations=%d",
        totals["files"], totals["decisions_verified"], d_total, d_pct,
        totals["laws_verified"], l_total, l_pct,
        totals["files_with_hallucinations"],
    )
    if args.strict:
        log.info("Strict mode — rewrote %d JSON files, dropped %d citations.",
                 totals["files_rewritten"], totals["citations_dropped"])

    if args.summary:
        args.summary.write_text(
            json.dumps(totals, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        log.info("Wrote summary to %s", args.summary)

    return 0


if __name__ == "__main__":
    sys.exit(main())
