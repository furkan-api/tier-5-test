#!/usr/bin/env python3
"""
Score a result folder of LLM-extracted court-decision JSON files against the
gold-standard folder. Companion to `app.ingestion.llm_process`.

Usage:
    python eval/scripts/score_extractions.py <result_folder>
    python eval/scripts/score_extractions.py <result_folder> --gold <gold_dir>
    python eval/scripts/score_extractions.py out1 out2 --names gemini gold

Default gold directory is `eval/llm_extractions_gold/`.

The script computes per-field, per-file, and aggregate scores. It is
dependency-free (standard library only) so it can be run against any
folder of JSON outputs produced by the runners in app/ingestion.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from statistics import mean


class _Tee:
    """Write to multiple streams. Used to capture stdout for file output."""

    def __init__(self, *streams):
        self._streams = streams

    def write(self, s):
        for st in self._streams:
            st.write(s)
        return len(s)

    def flush(self):
        for st in self._streams:
            st.flush()


# ---------------------------------------------------------------------------
# Field configuration
# ---------------------------------------------------------------------------
# Each field has a (kind, weight) tuple. Weights are relative within a file.
# Kinds:
#   exact      – case-insensitive equality after normalization
#   id         – identifier-style normalization (digits + letters only)
#   bool       – True/False/None equality
#   text       – fuzzy text similarity (SequenceMatcher on normalized text)
#   str_list   – set-based F1 on a list of strings
#   obj_list   – matched F1 on a list of objects (see OBJECT_LIST_KEYS)
#   obj        – nested object: average over its sub-fields
#
# Fields not listed are ignored. Adjust weights here if priorities change.

FIELD_CONFIG: dict[str, tuple[str, float]] = {
    "court_type":               ("exact",    1.0),
    "court":                    ("text",     1.0),
    "case_number":              ("id",       1.0),
    "decision_number":          ("id",       1.0),
    "decision_date":            ("exact",    1.0),
    "decision_type":            ("exact",    1.0),
    "is_final":                 ("bool",     1.0),
    "finality_basis":           ("text",     0.5),
    "decision_outcome":         ("exact",    1.5),
    "decision_outcome_raw":     ("text",     0.5),
    "vote_unanimity":           ("exact",    1.0),
    "has_dissent":              ("bool",     1.0),
    "dissent_summary":          ("text",     0.5),
    "appellants":               ("str_list", 1.0),
    "appeal_outcomes_by_role":  ("obj_list", 1.0),
    "subject":                  ("text",     1.5),
    "summary":                  ("text",     2.0),
    "keywords":                 ("str_list", 1.5),
    "legal_issues":             ("str_list", 1.5),
    "legal_concepts":           ("obj_list", 2.0),
    "dispositive_reasoning":    ("obj",      2.0),
    "fact_pattern":             ("obj",      1.5),
    "cited_court_decisions":    ("obj_list", 2.0),
    "cited_law_articles":       ("obj_list", 2.0),
}

# Sub-fields used when kind == "obj"
OBJECT_SUBFIELDS: dict[str, dict[str, tuple[str, float]]] = {
    "dispositive_reasoning": {
        "issue":       ("text", 1.0),
        "rule":        ("text", 1.0),
        "application": ("text", 1.0),
        "conclusion":  ("text", 1.0),
    },
    "fact_pattern": {
        "actor_roles": ("str_list", 1.0),
        "context":     ("text", 1.0),
        "trigger":     ("text", 1.0),
        "claim":       ("text", 1.0),
    },
}

# For obj_list fields: which key(s) to use when matching gold→result items,
# and which value fields to compare (kind + weight) once matched.
OBJECT_LIST_KEYS: dict[str, dict] = {
    "appeal_outcomes_by_role": {
        "match_keys": [("role", "id")],
        "value_fields": {"result": ("exact", 1.0)},
    },
    "legal_concepts": {
        "match_keys": [("concept", "id")],
        "value_fields": {
            "role":                  ("exact", 1.0),
            "context_in_reasoning":  ("text",  1.0),
        },
    },
    "cited_court_decisions": {
        # Match on any non-empty combination of these — strongest first.
        "match_keys": [
            ("case_number", "id"),
            ("decision_number", "id"),
            ("court", "text"),
        ],
        "value_fields": {
            "court":             ("text",  1.0),
            "cited_court_type":  ("exact", 0.5),
            "case_number":       ("id",    1.0),
            "decision_number":   ("id",    1.0),
            "relation":          ("exact", 0.5),
            "outcome":           ("exact", 0.5),
            "treatment":         ("exact", 0.5),
            "context":           ("text",  1.0),
        },
    },
    "cited_law_articles": {
        "match_keys": [
            ("article", "id"),
            ("law_number", "id"),
            ("law", "text"),
        ],
        "value_fields": {
            "law":         ("text",  1.0),
            "law_number":  ("id",    0.5),
            "article":     ("id",    1.0),
            "context":     ("text",  1.0),
        },
    },
}

# Text similarity threshold for considering two text values "equivalent"
# when used as a matching key in obj_list comparisons.
MATCH_THRESHOLD = 0.7


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------
def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def normalize_text(s) -> str:
    if s is None:
        return ""
    s = str(s).lower()
    s = _strip_accents(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_id(s) -> str:
    """Identifier normalization: keep alphanumerics, drop everything else."""
    if s is None:
        return ""
    s = _strip_accents(str(s).lower())
    return re.sub(r"[^a-z0-9]", "", s)


# ---------------------------------------------------------------------------
# Scoring primitives
# ---------------------------------------------------------------------------
def score_exact(gold, result) -> float:
    return 1.0 if normalize_text(gold) == normalize_text(result) else 0.0


def score_id(gold, result) -> float:
    g, r = normalize_id(gold), normalize_id(result)
    if not g and not r:
        return 1.0
    return 1.0 if g == r else 0.0


def score_bool(gold, result) -> float:
    return 1.0 if gold == result else 0.0


def score_text(gold, result) -> float:
    g, r = normalize_text(gold), normalize_text(result)
    if not g and not r:
        return 1.0
    if not g or not r:
        return 0.0
    return SequenceMatcher(None, g, r).ratio()


def score_str_list(gold, result) -> float:
    """Set-based F1 with fuzzy matching (>=0.85 SequenceMatcher counts as a match)."""
    gold = [normalize_text(x) for x in (gold or []) if x]
    result = [normalize_text(x) for x in (result or []) if x]
    if not gold and not result:
        return 1.0
    if not gold or not result:
        return 0.0

    matched_gold = set()
    matched_result = set()
    for gi, g in enumerate(gold):
        for ri, r in enumerate(result):
            if ri in matched_result:
                continue
            if g == r or SequenceMatcher(None, g, r).ratio() >= 0.85:
                matched_gold.add(gi)
                matched_result.add(ri)
                break

    tp = len(matched_gold)
    precision = tp / len(result) if result else 0.0
    recall = tp / len(gold) if gold else 0.0
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _field_score(kind: str, gold, result) -> float:
    if kind == "exact":
        return score_exact(gold, result)
    if kind == "id":
        return score_id(gold, result)
    if kind == "bool":
        return score_bool(gold, result)
    if kind == "text":
        return score_text(gold, result)
    if kind == "str_list":
        return score_str_list(gold, result)
    raise ValueError(f"unknown primitive kind: {kind}")


def _match_score(kind: str, gold, result) -> float:
    """Used when checking whether two obj_list items refer to the same thing."""
    if kind in ("exact", "id", "bool"):
        return _field_score(kind, gold, result)
    if kind == "text":
        return score_text(gold, result)
    return 0.0


def score_obj(gold: dict | None, result: dict | None, subfields: dict) -> tuple[float, dict]:
    gold = gold or {}
    result = result or {}
    detail = {}
    weighted_total = 0.0
    total_weight = 0.0
    for fname, (kind, weight) in subfields.items():
        if kind == "str_list":
            s = score_str_list(gold.get(fname), result.get(fname))
        else:
            s = _field_score(kind, gold.get(fname), result.get(fname))
        detail[fname] = round(s, 4)
        weighted_total += s * weight
        total_weight += weight
    return (weighted_total / total_weight if total_weight else 1.0), detail


def _items_match(gold_item: dict, result_item: dict, match_keys: list) -> bool:
    """True if the two items share at least one matching key value."""
    for fname, kind in match_keys:
        gv, rv = gold_item.get(fname), result_item.get(fname)
        if gv in (None, "", []) or rv in (None, "", []):
            continue
        if _match_score(kind, gv, rv) >= MATCH_THRESHOLD:
            return True
    return False


def _item_value_score(gold_item: dict, result_item: dict,
                      value_fields: dict) -> tuple[float, dict]:
    """Return (weighted overall score, per-field score dict) for an item pair."""
    per_field = {}
    weighted_total = 0.0
    total_weight = 0.0
    for fname, (kind, weight) in value_fields.items():
        s = _field_score(kind, gold_item.get(fname), result_item.get(fname))
        per_field[fname] = s
        weighted_total += s * weight
        total_weight += weight
    overall = weighted_total / total_weight if total_weight else 1.0
    return overall, per_field


def score_obj_list(gold_list, result_list, config: dict) -> tuple[float, dict]:
    """Greedy bipartite matching, then F1 weighted by per-pair value similarity."""
    gold_list = gold_list or []
    result_list = result_list or []
    value_fields = config["value_fields"]
    empty_subscores = {f: None for f in value_fields}

    if not gold_list and not result_list:
        return 1.0, {"precision": 1.0, "recall": 1.0, "f1": 1.0,
                     "matched": 0, "gold_count": 0, "result_count": 0,
                     "avg_value_score": 1.0,
                     "value_field_scores": {f: 1.0 for f in value_fields}}
    if not gold_list or not result_list:
        return 0.0, {"precision": 0.0, "recall": 0.0, "f1": 0.0,
                     "matched": 0, "gold_count": len(gold_list),
                     "result_count": len(result_list), "avg_value_score": 0.0,
                     "value_field_scores": {f: 0.0 for f in value_fields}}

    match_keys = config["match_keys"]

    # Build candidate pairs sorted by value similarity (greedy best-first).
    candidates = []
    for gi, gi_item in enumerate(gold_list):
        for ri, ri_item in enumerate(result_list):
            if _items_match(gi_item, ri_item, match_keys):
                v, per_field = _item_value_score(gi_item, ri_item, value_fields)
                candidates.append((v, gi, ri, per_field))
    candidates.sort(key=lambda x: x[0], reverse=True)

    used_g, used_r, pair_scores, per_field_lists = set(), set(), [], {f: [] for f in value_fields}
    for v, gi, ri, per_field in candidates:
        if gi in used_g or ri in used_r:
            continue
        used_g.add(gi)
        used_r.add(ri)
        pair_scores.append(v)
        for f, s in per_field.items():
            per_field_lists[f].append(s)

    matched = len(pair_scores)
    avg_value = mean(pair_scores) if pair_scores else 0.0
    value_field_scores = {
        f: round(mean(scores), 4) if scores else None
        for f, scores in per_field_lists.items()
    }
    # Quality-weighted precision/recall: an item only counts as a true positive
    # to the extent that its values actually agree.
    weighted_tp = sum(pair_scores)
    precision = weighted_tp / len(result_list)
    recall = weighted_tp / len(gold_list)
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return f1, {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "matched": matched,
        "gold_count": len(gold_list),
        "result_count": len(result_list),
        "avg_value_score": round(avg_value, 4),
        "value_field_scores": value_field_scores,
    }


# ---------------------------------------------------------------------------
# File-level scoring
# ---------------------------------------------------------------------------
def score_file(gold: dict, result: dict) -> dict:
    field_scores = {}
    field_details = {}
    weighted_total = 0.0
    total_weight = 0.0

    for fname, (kind, weight) in FIELD_CONFIG.items():
        gv, rv = gold.get(fname), result.get(fname)
        if kind == "obj":
            s, detail = score_obj(gv, rv, OBJECT_SUBFIELDS[fname])
            field_details[fname] = detail
        elif kind == "obj_list":
            s, detail = score_obj_list(gv, rv, OBJECT_LIST_KEYS[fname])
            field_details[fname] = detail
        else:
            s = _field_score(kind, gv, rv)
        field_scores[fname] = round(s, 4)
        weighted_total += s * weight
        total_weight += weight

    overall = weighted_total / total_weight if total_weight else 0.0
    return {
        "overall": round(overall, 4),
        "fields": field_scores,
        "details": field_details,
    }


# ---------------------------------------------------------------------------
# Folder-level orchestration
# ---------------------------------------------------------------------------
def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def score_folder(result_dir: Path, gold_dir: Path) -> dict:
    gold_files = {p.name: p for p in gold_dir.glob("*.json")}
    result_files = {p.name: p for p in result_dir.glob("*.json")}

    common = sorted(set(gold_files) & set(result_files))
    only_gold = sorted(set(gold_files) - set(result_files))
    only_result = sorted(set(result_files) - set(gold_files))

    per_file = {}
    for name in common:
        try:
            gold = load_json(gold_files[name])
            result = load_json(result_files[name])
        except json.JSONDecodeError as exc:
            per_file[name] = {"error": f"invalid JSON: {exc}", "overall": 0.0}
            continue
        per_file[name] = score_file(gold, result)

    # Aggregates
    overalls = [r["overall"] for r in per_file.values() if "overall" in r]
    coverage = len(common) / len(gold_files) if gold_files else 0.0

    field_aggregate = {}
    for fname in FIELD_CONFIG:
        scores = [r["fields"].get(fname)
                  for r in per_file.values()
                  if "fields" in r and fname in r["fields"]]
        if scores:
            field_aggregate[fname] = round(mean(scores), 4)

    # Subscores aggregation.
    # For obj fields: avg per subfield across files.
    # For obj_list fields: avg of precision/recall/f1/avg_value_score and avg of
    # each value_field_score across files.
    field_subscores: dict[str, dict] = {}
    for fname, (kind, _) in FIELD_CONFIG.items():
        details = [r["details"].get(fname)
                   for r in per_file.values()
                   if "details" in r and fname in r.get("details", {})]
        if not details:
            continue
        if kind == "obj":
            sub_keys = OBJECT_SUBFIELDS[fname].keys()
            field_subscores[fname] = {
                k: round(mean([d[k] for d in details if k in d]), 4)
                for k in sub_keys
                if any(k in d for d in details)
            }
        elif kind == "obj_list":
            agg = {}
            for k in ("precision", "recall", "f1", "avg_value_score"):
                vals = [d[k] for d in details if k in d]
                if vals:
                    agg[k] = round(mean(vals), 4)
            for k in ("matched", "gold_count", "result_count"):
                vals = [d[k] for d in details if k in d]
                if vals:
                    agg[k] = sum(vals)
            vfs_collected: dict[str, list] = {}
            for d in details:
                for sub, val in (d.get("value_field_scores") or {}).items():
                    if val is not None:
                        vfs_collected.setdefault(sub, []).append(val)
            if vfs_collected:
                agg["value_field_scores"] = {
                    sub: round(mean(vals), 4) for sub, vals in vfs_collected.items()
                }
            field_subscores[fname] = agg

    # Final score penalises missing files (treats them as 0).
    files_in_gold = len(gold_files)
    completeness_penalised = (sum(overalls) / files_in_gold) if files_in_gold else 0.0
    mean_on_present = mean(overalls) if overalls else 0.0

    return {
        "summary": {
            "gold_dir": str(gold_dir),
            "result_dir": str(result_dir),
            "files_in_gold": files_in_gold,
            "files_in_result": len(result_files),
            "files_scored": len(common),
            "coverage": round(coverage, 4),
            "mean_score_on_scored_files": round(mean_on_present, 4),
            "mean_score_with_missing_as_zero": round(completeness_penalised, 4),
        },
        "missing_in_result": only_gold,
        "extra_in_result": only_result,
        "per_field_average": field_aggregate,
        "per_field_subscores": field_subscores,
        "per_file": per_file,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _format_pct(x: float) -> str:
    return f"{x * 100:6.2f}%"


def print_human_report(report: dict, verbose: bool) -> None:
    s = report["summary"]
    print(f"Gold:    {s['gold_dir']}")
    print(f"Result:  {s['result_dir']}")
    print(f"Files in gold:    {s['files_in_gold']}")
    print(f"Files in result:  {s['files_in_result']}")
    print(f"Files scored:     {s['files_scored']}  (coverage {_format_pct(s['coverage'])})")
    print(f"Mean score on scored files:        {_format_pct(s['mean_score_on_scored_files'])}")
    print(f"Mean score (missing files = zero): {_format_pct(s['mean_score_with_missing_as_zero'])}")

    if report["missing_in_result"]:
        print(f"\nMissing in result ({len(report['missing_in_result'])}):")
        for n in report["missing_in_result"]:
            print(f"  - {n}")
    if report["extra_in_result"]:
        print(f"\nExtra in result (not in gold) ({len(report['extra_in_result'])}):")
        for n in report["extra_in_result"]:
            print(f"  - {n}")

    subscores = report.get("per_field_subscores", {})
    print("\nPer-field average (across scored files):")
    for fname, score in sorted(report["per_field_average"].items(),
                               key=lambda kv: kv[1]):
        print(f"  {fname:30s} {_format_pct(score)}")
        sub = subscores.get(fname)
        if not sub:
            continue
        kind, _ = FIELD_CONFIG[fname]
        if kind == "obj":
            for sk, sv in sub.items():
                print(f"      └─ {sk:24s} {_format_pct(sv)}")
        elif kind == "obj_list":
            f1 = sub.get("f1"); pr = sub.get("precision"); rc = sub.get("recall")
            avg_v = sub.get("avg_value_score")
            matched = sub.get("matched", 0)
            gold_n = sub.get("gold_count", 0)
            result_n = sub.get("result_count", 0)
            line = (f"      └─ matched {matched}/{gold_n} gold, "
                    f"{matched}/{result_n} result   "
                    f"P={_format_pct(pr or 0)} R={_format_pct(rc or 0)} "
                    f"F1={_format_pct(f1 or 0)}  avg-pair={_format_pct(avg_v or 0)}")
            print(line)
            for sk, sv in (sub.get("value_field_scores") or {}).items():
                print(f"         · {sk:21s} {_format_pct(sv)}")

    print("\nPer-file overall:")
    for name, r in sorted(report["per_file"].items(),
                          key=lambda kv: kv[1].get("overall", 0)):
        if "error" in r:
            print(f"  {name}  ERROR: {r['error']}")
            continue
        print(f"  {name}  {_format_pct(r['overall'])}")
        if not verbose:
            continue
        details = r.get("details", {})
        for fname, score in r["fields"].items():
            print(f"      {fname:28s} {_format_pct(score)}")
            kind, _ = FIELD_CONFIG[fname]
            d = details.get(fname)
            if not d:
                continue
            if kind == "obj":
                for sk, sv in d.items():
                    print(f"          └─ {sk:22s} {_format_pct(sv)}")
            elif kind == "obj_list":
                f1 = d.get("f1"); pr = d.get("precision"); rc = d.get("recall")
                avg_v = d.get("avg_value_score")
                line = (f"          └─ matched {d.get('matched', 0)}/"
                        f"{d.get('gold_count', 0)} gold, "
                        f"{d.get('matched', 0)}/{d.get('result_count', 0)} result   "
                        f"P={_format_pct(pr or 0)} R={_format_pct(rc or 0)} "
                        f"F1={_format_pct(f1 or 0)}  avg-pair={_format_pct(avg_v or 0)}")
                print(line)
                for sk, sv in (d.get("value_field_scores") or {}).items():
                    if sv is None:
                        continue
                    print(f"             · {sk:19s} {_format_pct(sv)}")


# ---------------------------------------------------------------------------
# Multi-folder comparison
# ---------------------------------------------------------------------------
def _label_for(path: Path) -> str:
    """Short, stable label for a result folder column."""
    return path.name.strip()


def _fmt_cell(value, width: int) -> str:
    if value is None:
        return f"{'—':>{width}}"
    return f"{value * 100:>{width - 1}.2f}%"


def print_comparison_report(reports: list[dict], names: list[str],
                            show_per_file: bool) -> None:
    # ------- Summary table -------
    name_w = max(8, max(len(n) for n in names))
    col_w = max(12, name_w + 2)

    print("=" * (28 + col_w * len(names)))
    print("SUMMARY")
    print("=" * (28 + col_w * len(names)))
    headers = ["metric".ljust(28)] + [n.rjust(col_w - 1) for n in names]
    print(" ".join(headers))

    def _row(metric, vals, is_pct=True):
        cells = [metric.ljust(28)]
        for v in vals:
            if v is None:
                cells.append(f"{'—':>{col_w - 1}}")
            elif is_pct:
                cells.append(f"{v * 100:>{col_w - 2}.2f}%")
            else:
                cells.append(f"{v:>{col_w - 1}}")
        print(" ".join(cells))

    _row("files in result",
         [r["summary"]["files_in_result"] for r in reports], is_pct=False)
    _row("files scored vs gold",
         [r["summary"]["files_scored"] for r in reports], is_pct=False)
    _row("coverage",
         [r["summary"]["coverage"] for r in reports])
    _row("mean (scored only)",
         [r["summary"]["mean_score_on_scored_files"] for r in reports])
    _row("mean (missing = 0)",
         [r["summary"]["mean_score_with_missing_as_zero"] for r in reports])

    # ------- Per-field comparison -------
    print()
    print("=" * (32 + col_w * len(names)))
    print("PER-FIELD AVERAGE  (cells are mean across that folder's scored files)")
    print("=" * (32 + col_w * len(names)))
    headers = ["field".ljust(32)] + [n.rjust(col_w - 1) for n in names]
    print(" ".join(headers))

    # Sort by mean across folders (weakest first → fastest path to improvements).
    field_means = {}
    for fname in FIELD_CONFIG:
        vals = [r["per_field_average"].get(fname) for r in reports]
        present = [v for v in vals if v is not None]
        field_means[fname] = mean(present) if present else None

    sorted_fields = sorted(
        FIELD_CONFIG.keys(),
        key=lambda f: (field_means[f] is None, field_means[f] or 0.0),
    )

    for fname in sorted_fields:
        row = [fname.ljust(32)]
        for r in reports:
            v = r["per_field_average"].get(fname)
            row.append(_fmt_cell(v, col_w))
        print(" ".join(row))

        # If this field has subscores in any folder, indent and print them.
        kind, _kw = FIELD_CONFIG[fname]
        sub_keys: list[str] = []
        if kind == "obj":
            sub_keys = list(OBJECT_SUBFIELDS[fname].keys())
        elif kind == "obj_list":
            seen = set()
            for r in reports:
                vfs = (r.get("per_field_subscores", {}).get(fname, {})
                       .get("value_field_scores") or {})
                for k in vfs:
                    if k not in seen:
                        seen.add(k)
                        sub_keys.append(k)

        if kind == "obj_list":
            # Print P / R / F1 / avg-pair as their own indented rows.
            for label, key in (("precision", "precision"), ("recall", "recall"),
                               ("F1", "f1"), ("avg-pair", "avg_value_score")):
                row = [("    " + label).ljust(32)]
                for r in reports:
                    v = r.get("per_field_subscores", {}).get(fname, {}).get(key)
                    row.append(_fmt_cell(v, col_w))
                print(" ".join(row))

        for sk in sub_keys:
            row = [("      · " + sk).ljust(32)]
            for r in reports:
                sub = r.get("per_field_subscores", {}).get(fname, {})
                if kind == "obj":
                    v = sub.get(sk)
                else:
                    v = (sub.get("value_field_scores") or {}).get(sk)
                row.append(_fmt_cell(v, col_w))
            print(" ".join(row))

    # ------- Per-file comparison -------
    if show_per_file:
        all_files = sorted({fname for r in reports for fname in r["per_file"]})
        if all_files:
            print()
            print("=" * (60 + col_w * len(names)))
            print("PER-FILE OVERALL  (— = file not present in that folder)")
            print("=" * (60 + col_w * len(names)))
            file_w = min(60, max(len(f) for f in all_files))
            headers = ["file".ljust(file_w)] + [n.rjust(col_w - 1) for n in names]
            print(" ".join(headers))
            for fname in all_files:
                row = [fname[:file_w].ljust(file_w)]
                for r in reports:
                    pf = r["per_file"].get(fname)
                    if pf is None or "overall" not in pf:
                        row.append(f"{'—':>{col_w - 1}}")
                    else:
                        row.append(f"{pf['overall'] * 100:>{col_w - 2}.2f}%")
                print(" ".join(row))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_dir", nargs="+",
                        help="One or more folders containing result JSON files")
    parser.add_argument(
        "--gold",
        default=str(Path(__file__).resolve().parent.parent / "llm_extractions_gold"),
        help="Gold standard folder (default: eval/llm_extractions_gold)",
    )
    parser.add_argument("--names", nargs="+",
                        help="Optional column labels (one per result_dir)")
    parser.add_argument("--report",
                        help="JSON report path (default: "
                             "<result_dir>_score.json, or comparison_score.json "
                             "for multi-folder mode).")
    parser.add_argument("--text",
                        help="Text report path (default: "
                             "<result_dir>_score.txt, or comparison_score.txt "
                             "for multi-folder mode).")
    parser.add_argument("--no-files", action="store_true",
                        help="Skip writing report files.")
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="Don't print the human report to stdout "
                             "(still writes files unless --no-files).")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Single-folder: per-field scores for every file. "
                             "Multi-folder: also include per-file table.")
    args = parser.parse_args(argv)

    gold_dir = Path(args.gold)
    if not gold_dir.is_dir():
        print(f"error: gold folder not found: {gold_dir}", file=sys.stderr)
        return 2

    result_dirs = [Path(d) for d in args.result_dir]
    for d in result_dirs:
        if not d.is_dir():
            print(f"error: result folder not found: {d}", file=sys.stderr)
            return 2

    if args.names and len(args.names) != len(result_dirs):
        print("error: --names must match the number of result folders",
              file=sys.stderr)
        return 2

    # Default output paths.
    if len(result_dirs) == 1:
        default_stem = result_dirs[0].name.strip().replace(" ", "_") + "_score"
    else:
        default_stem = "comparison_score"
    json_path = Path(args.report) if args.report else Path(default_stem + ".json")
    text_path = Path(args.text) if args.text else Path(default_stem + ".txt")

    # Capture human report to stdout (unless --quiet) and to a string buffer.
    text_buf = io.StringIO()
    if args.quiet:
        out_target = text_buf
    else:
        out_target = _Tee(sys.stdout, text_buf)

    if len(result_dirs) == 1:
        report = score_folder(result_dirs[0], gold_dir)
        with contextlib.redirect_stdout(out_target):
            print_human_report(report, args.verbose)
        json_payload = report
    else:
        reports = [score_folder(d, gold_dir) for d in result_dirs]
        names = args.names or [_label_for(d) for d in result_dirs]
        with contextlib.redirect_stdout(out_target):
            print(f"Gold: {gold_dir}\n")
            print_comparison_report(reports, names, show_per_file=args.verbose)
        json_payload = {n: r for n, r in zip(names, reports)}

    if not args.no_files:
        json_path.write_text(
            json.dumps(json_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        text_path.write_text(text_buf.getvalue(), encoding="utf-8")
        msg = f"\nWrote: {json_path}\nWrote: {text_path}"
        if args.quiet:
            print(msg.lstrip("\n"))
        else:
            print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
