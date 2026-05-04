#!/usr/bin/env python3
"""
Tests for app.ingestion.llm_process.

Covers:
  * argparse defaults and overrides
  * select_files: filtering by substring, --force, --limit, skip-existing
  * process_files: happy path + invalid-JSON path (raw.txt fallback)
  * end-to-end metric scoring of generated outputs vs. gold via
    eval/scripts/score_extractions.py — proves the runner output shape
    is compatible with the scorer.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "eval" / "scripts"))

from app.ingestion import llm_process  # noqa: E402
from app.ingestion.llm_process import (  # noqa: E402
    parse_args, process_files, select_files, write_output, write_raw,
)


class FakeExtractor:
    """Stand-in for GeminiExtractor; returns canned responses by filename."""

    def __init__(self, responses: dict[str, str]):
        self.responses = responses
        self.calls: list[str] = []

    def extract(self, *, filename: str, body: str) -> str:
        self.calls.append(filename)
        if filename not in self.responses:
            raise RuntimeError(f"no canned response for {filename}")
        return self.responses[filename]


def _write_corpus(tmp: Path, items: dict[str, str]) -> Path:
    corpus = tmp / "corpus"
    corpus.mkdir()
    for name, body in items.items():
        (corpus / name).write_text(body, encoding="utf-8")
    return corpus


class ParseArgsTests(unittest.TestCase):
    def test_defaults(self):
        ns = parse_args([])
        self.assertIsNone(ns.filter)
        self.assertIsNone(ns.corpus_dir)
        self.assertIsNone(ns.output_dir)
        self.assertIsNone(ns.limit)
        self.assertFalse(ns.force)

    def test_filter_positional(self):
        ns = parse_args(["danistay"])
        self.assertEqual(ns.filter, "danistay")

    def test_overrides(self):
        ns = parse_args([
            "yargitay", "--corpus-dir", "/tmp/c",
            "--output-dir", "/tmp/o", "--limit", "3", "--force",
            "--model", "gemini-2.5-pro",
        ])
        self.assertEqual(ns.filter, "yargitay")
        self.assertEqual(ns.corpus_dir, Path("/tmp/c"))
        self.assertEqual(ns.output_dir, Path("/tmp/o"))
        self.assertEqual(ns.limit, 3)
        self.assertTrue(ns.force)
        self.assertEqual(ns.model, "gemini-2.5-pro")


class SelectFilesTests(unittest.TestCase):
    def test_filter_and_skip_existing(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            tmp = Path(td)
            corpus = _write_corpus(tmp, {
                "yargitay_a.md": "x",
                "yargitay_b.md": "y",
                "danistay_c.md": "z",
            })
            output = tmp / "out"
            output.mkdir()
            (output / "yargitay_a.json").write_text("{}", encoding="utf-8")

            todo, skipped = select_files(corpus, output, "yargitay",
                                         force=False, limit=None)
            self.assertEqual([p.name for p in todo], ["yargitay_b.md"])
            self.assertEqual([p.name for p in skipped], ["yargitay_a.md"])

    def test_force_reprocesses_existing(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            tmp = Path(td)
            corpus = _write_corpus(tmp, {"a.md": "x", "b.md": "y"})
            output = tmp / "out"; output.mkdir()
            (output / "a.json").write_text("{}", encoding="utf-8")

            todo, skipped = select_files(corpus, output, None,
                                         force=True, limit=None)
            self.assertEqual([p.name for p in todo], ["a.md", "b.md"])
            self.assertEqual(skipped, [])

    def test_limit_caps_result(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            tmp = Path(td)
            corpus = _write_corpus(tmp, {f"f{i}.md": "x" for i in range(5)})
            output = tmp / "out"; output.mkdir()
            todo, _ = select_files(corpus, output, None, force=False, limit=2)
            self.assertEqual(len(todo), 2)


class ProcessFilesTests(unittest.TestCase):
    GOLD = {
        "court_type": "Yargıtay",
        "court": "Yargıtay 1. Hukuk Dairesi",
        "case_number": "2023/1",
        "decision_number": "2023/2",
        "decision_date": "2023-01-01",
        "decision_type": "Esas Karar",
        "is_final": True,
        "finality_basis": None,
        "decision_outcome": "Onama",
        "decision_outcome_raw": "Onanmıştır.",
        "vote_unanimity": "oybirliği",
        "has_dissent": False,
        "dissent_summary": None,
        "appellants": ["davacı"],
        "appeal_outcomes_by_role": [{"role": "davacı", "result": "kabul"}],
        "subject": "Tapu iptali",
        "summary": "Davacı tapu iptali talep etmiştir.",
        "keywords": ["tapu", "muvazaa"],
        "legal_issues": ["Tapu iptali şartları"],
        "legal_concepts": [
            {"concept": "muris muvazaası", "role": "kural",
             "context_in_reasoning": "Mirasçıdan mal kaçırma."},
        ],
        "dispositive_reasoning": {
            "issue": "Tapu iptali geçerli mi?",
            "rule": "TBK 19",
            "application": "Davacı muvazaayı ispat etmiştir.",
            "conclusion": "Onama",
        },
        "fact_pattern": {
            "actor_roles": ["davacı", "davalı"],
            "context": "Tapu devri.",
            "trigger": "Mirasçıdan mal kaçırma.",
            "claim": "Tapu iptali.",
        },
        "cited_court_decisions": [],
        "cited_law_articles": [{"law": "TBK", "law_number": "6098",
                                 "article": "19", "context": "Muvazaa"}],
    }

    def test_writes_json_for_valid_response(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            tmp = Path(td)
            corpus = _write_corpus(tmp, {"x.md": "body"})
            out = tmp / "out"; out.mkdir()

            extractor = FakeExtractor({
                "x.md": json.dumps(dict(self.GOLD, file="x.md"),
                                   ensure_ascii=False),
            })
            stats = process_files(list(corpus.glob("*.md")), extractor, out)
            self.assertEqual(stats, {"ok": 1, "invalid_json": 0, "errors": 0})
            self.assertTrue((out / "x.json").exists())
            written = json.loads((out / "x.json").read_text(encoding="utf-8"))
            self.assertEqual(written["court_type"], "Yargıtay")

    def test_invalid_json_writes_raw_fallback(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            tmp = Path(td)
            corpus = _write_corpus(tmp, {"x.md": "body"})
            out = tmp / "out"; out.mkdir()

            extractor = FakeExtractor({"x.md": "{not json"})
            stats = process_files(list(corpus.glob("*.md")), extractor, out)
            self.assertEqual(stats["invalid_json"], 1)
            self.assertEqual(stats["ok"], 0)
            self.assertFalse((out / "x.json").exists())
            self.assertTrue((out / "x.raw.txt").exists())


class ScoringIntegrationTests(unittest.TestCase):
    """Exercise the full runner-output → score_extractions metric path."""

    def test_perfect_match_scores_100_percent(self):
        from tempfile import TemporaryDirectory
        import score_extractions  # type: ignore  # eval/scripts on sys.path

        gold = ProcessFilesTests.GOLD
        record = dict(gold, file="case_a.md")

        with TemporaryDirectory() as td:
            tmp = Path(td)
            corpus = _write_corpus(tmp, {"case_a.md": "body"})
            out = tmp / "out"; out.mkdir()
            gold_dir = tmp / "gold"; gold_dir.mkdir()

            # Gold: hand-written reference.
            (gold_dir / "case_a.json").write_text(
                json.dumps(record, ensure_ascii=False), encoding="utf-8",
            )

            # Result: produced via the runner machinery (proves shape compat).
            extractor = FakeExtractor({
                "case_a.md": json.dumps(record, ensure_ascii=False),
            })
            stats = process_files(list(corpus.glob("*.md")), extractor, out)
            self.assertEqual(stats["ok"], 1)

            report = score_extractions.score_folder(out, gold_dir)
            summary = report["summary"]
            self.assertEqual(summary["files_scored"], 1)
            self.assertEqual(summary["coverage"], 1.0)
            self.assertAlmostEqual(
                summary["mean_score_on_scored_files"], 1.0, places=4,
            )
            self.assertAlmostEqual(
                summary["mean_score_with_missing_as_zero"], 1.0, places=4,
            )

    def test_partial_mismatch_drops_score(self):
        from tempfile import TemporaryDirectory
        import score_extractions  # type: ignore

        gold = ProcessFilesTests.GOLD
        result = dict(gold, file="case_a.md",
                      court_type="Danıştay",       # exact mismatch
                      decision_outcome="Bozma",    # exact mismatch (high weight)
                      keywords=["tapu"])           # str_list partial

        with TemporaryDirectory() as td:
            tmp = Path(td)
            out = tmp / "out"; out.mkdir()
            gold_dir = tmp / "gold"; gold_dir.mkdir()
            (gold_dir / "case_a.json").write_text(
                json.dumps(dict(gold, file="case_a.md"), ensure_ascii=False),
                encoding="utf-8",
            )
            (out / "case_a.json").write_text(
                json.dumps(result, ensure_ascii=False), encoding="utf-8",
            )

            report = score_extractions.score_folder(out, gold_dir)
            mean_score = report["summary"]["mean_score_on_scored_files"]
            self.assertLess(mean_score, 1.0)
            self.assertGreater(mean_score, 0.5)
            # Both targeted fields should reflect the mismatches:
            field_avg = report["per_field_average"]
            self.assertEqual(field_avg["court_type"], 0.0)
            self.assertEqual(field_avg["decision_outcome"], 0.0)
            # keywords: 1/2 found → F1 = 2*(1/1*1/2)/(1/1+1/2) = 0.6667
            self.assertAlmostEqual(field_avg["keywords"], 0.6667, places=3)


def _import_helper_check():
    """`llm_process` must expose the helpers tests rely on."""
    assert hasattr(llm_process, "GeminiExtractor")
    assert hasattr(llm_process, "process_files")
    assert hasattr(llm_process, "select_files")
    assert hasattr(llm_process, "write_output")
    assert hasattr(llm_process, "write_raw")


if __name__ == "__main__":
    _import_helper_check()
    unittest.main(verbosity=2)
