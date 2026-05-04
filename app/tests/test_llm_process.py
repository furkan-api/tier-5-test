#!/usr/bin/env python3
"""
Tests for app.ingestion.llm_process (staged extraction pipeline).

Covers:
  * argparse for the new staged CLI surface
  * stage definitions and per-stage config resolution
  * select_files: stage-aware skip-when-intermediate-exists, --force, --limit
  * process_files (legacy single-pass): happy path, invalid JSON, truncation
  * process_stage (per-stage extraction loop): happy path
  * merge: per-stage payloads → canonical merged JSON, key filtering,
    discover_stems, partial merges
  * end-to-end metric scoring of process_files output vs. gold via
    eval/scripts/score_extractions.py — proves the legacy runner-output
    shape is still compatible with the scorer.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "eval" / "scripts"))

from app.ingestion import llm_process  # noqa: E402
from app.ingestion.llm_process import (  # noqa: E402
    ExtractResult, STAGES, STAGE_NAMES, Stage,
    discover_stems, get_stage, merge_all, merge_one_document,
    merge_stage_payloads, parse_args, process_files, process_stage,
    resolve_stage_config, select_files, write_intermediate, write_output,
)


class FakeExtractor:
    """Stand-in for a real extractor; returns canned ExtractResults by filename."""

    def __init__(self, responses: dict[str, str], *, truncated: bool = False):
        self.responses = responses
        self._truncated = truncated
        self.calls: list[str] = []

    def extract(self, *, filename: str, body: str) -> ExtractResult:
        self.calls.append(filename)
        if filename not in self.responses:
            raise RuntimeError(f"no canned response for {filename}")
        return ExtractResult(text=self.responses[filename],
                             truncated=self._truncated)


def _write_corpus(tmp: Path, items: dict[str, str]) -> Path:
    corpus = tmp / "corpus"
    corpus.mkdir()
    for name, body in items.items():
        (corpus / name).write_text(body, encoding="utf-8")
    return corpus


# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------
class ParseArgsTests(unittest.TestCase):
    def test_defaults(self):
        ns = parse_args([])
        self.assertIsNone(ns.filter)
        self.assertIsNone(ns.stage)
        self.assertFalse(ns.merge_only)
        self.assertFalse(ns.no_merge)
        self.assertFalse(ns.force)
        self.assertIsNone(ns.limit)

    def test_filter_positional(self):
        ns = parse_args(["danistay"])
        self.assertEqual(ns.filter, "danistay")

    def test_stage_choice_validated(self):
        ns = parse_args(["--stage", "summary"])
        self.assertEqual(ns.stage, "summary")
        with self.assertRaises(SystemExit):
            parse_args(["--stage", "bogus"])

    def test_merge_only_and_no_merge(self):
        self.assertTrue(parse_args(["--merge-only"]).merge_only)
        self.assertTrue(parse_args(["--no-merge"]).no_merge)

    def test_per_stage_overrides(self):
        ns = parse_args([
            "--stage", "metadata",
            "--model", "gemini-2.5-pro",
            "--base-url", "http://localhost:11434/v1",
            "--api-key", "ollama",
            "--limit", "3", "--force",
        ])
        self.assertEqual(ns.model, "gemini-2.5-pro")
        self.assertEqual(ns.base_url, "http://localhost:11434/v1")
        self.assertEqual(ns.api_key, "ollama")
        self.assertEqual(ns.limit, 3)
        self.assertTrue(ns.force)


# ---------------------------------------------------------------------------
# Stage table sanity
# ---------------------------------------------------------------------------
class StageTableTests(unittest.TestCase):
    def test_four_stages_with_unique_names(self):
        names = [s.name for s in STAGES]
        self.assertEqual(len(names), 4)
        self.assertEqual(len(set(names)), 4)
        self.assertEqual(set(STAGE_NAMES), set(names))

    def test_each_stage_has_distinct_intermediate_suffix(self):
        suffixes = [s.intermediate_suffix for s in STAGES]
        self.assertEqual(len(set(suffixes)), len(suffixes))

    def test_output_keys_are_disjoint_across_stages(self):
        """Two stages must never claim the same output key, otherwise merge
        order would silently determine which one wins."""
        seen: set[str] = set()
        for stage in STAGES:
            overlap = seen & set(stage.output_keys)
            self.assertFalse(
                overlap, f"stage {stage.name} overlaps on {overlap}"
            )
            seen |= set(stage.output_keys)

    def test_get_stage_lookup(self):
        self.assertEqual(get_stage("metadata").name, "metadata")
        with self.assertRaises(ValueError):
            get_stage("nope")


# ---------------------------------------------------------------------------
# Per-stage config resolution
# ---------------------------------------------------------------------------
class ResolveStageConfigTests(unittest.TestCase):
    """`resolve_stage_config` decides which model / base_url / key each
    stage call should use, with three-tier precedence:
      CLI override > per-stage setting > global llm_extract_* fallback.
    The CLI override only applies when --stage matches THIS stage."""

    def _settings(self, **overrides):
        defaults = dict(
            llm_extract_model="GLOBAL_MODEL",
            llm_extract_base_url=None,
            llm_extract_api_key="",
            gemini_api_key="GEMINI_KEY",
            llm_stage_metadata_prompt=Path("/dev/null"),
            llm_stage_metadata_model=None,
            llm_stage_metadata_base_url=None,
            llm_stage_metadata_api_key=None,
            llm_stage_summary_prompt=Path("/dev/null"),
            llm_stage_summary_model=None,
            llm_stage_summary_base_url=None,
            llm_stage_summary_api_key=None,
            llm_stage_citations_decisions_prompt=Path("/dev/null"),
            llm_stage_citations_decisions_model=None,
            llm_stage_citations_decisions_base_url=None,
            llm_stage_citations_decisions_api_key=None,
            llm_stage_citations_laws_prompt=Path("/dev/null"),
            llm_stage_citations_laws_model=None,
            llm_stage_citations_laws_base_url=None,
            llm_stage_citations_laws_api_key=None,
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_falls_back_to_global_when_nothing_set(self):
        stage = get_stage("metadata")
        ns = parse_args([])
        cfg = resolve_stage_config(stage, ns, self._settings())
        self.assertEqual(cfg["model"], "GLOBAL_MODEL")
        self.assertIsNone(cfg["base_url"])

    def test_per_stage_setting_overrides_global(self):
        stage = get_stage("summary")
        ns = parse_args([])
        s = self._settings(llm_stage_summary_model="SUMMARY_MODEL")
        cfg = resolve_stage_config(stage, ns, s)
        self.assertEqual(cfg["model"], "SUMMARY_MODEL")

    def test_cli_override_only_applies_when_stage_matches(self):
        stage_meta = get_stage("metadata")
        stage_sum = get_stage("summary")
        # User runs --stage metadata --model X. Only metadata should pick X up.
        ns = parse_args(["--stage", "metadata", "--model", "CLI_MODEL"])
        s = self._settings(llm_stage_summary_model="SUMMARY_MODEL")
        meta_cfg = resolve_stage_config(stage_meta, ns, s)
        sum_cfg = resolve_stage_config(stage_sum, ns, s)
        self.assertEqual(meta_cfg["model"], "CLI_MODEL")
        self.assertEqual(sum_cfg["model"], "SUMMARY_MODEL")


# ---------------------------------------------------------------------------
# select_files (stage-aware)
# ---------------------------------------------------------------------------
class SelectFilesTests(unittest.TestCase):
    def test_skips_files_with_existing_intermediate(self):
        with TemporaryDirectory() as td:
            tmp = Path(td)
            corpus = _write_corpus(tmp, {
                "yargitay_a.md": "x",
                "yargitay_b.md": "y",
                "danistay_c.md": "z",
            })
            inter = tmp / "stages"; inter.mkdir()
            stage = get_stage("metadata")
            (inter / f"yargitay_a{stage.intermediate_suffix}").write_text(
                "{}", encoding="utf-8",
            )

            todo, skipped = select_files(
                corpus, inter, stage, "yargitay", force=False, limit=None,
            )
            self.assertEqual([p.name for p in todo], ["yargitay_b.md"])
            self.assertEqual([p.name for p in skipped], ["yargitay_a.md"])

    def test_force_reprocesses_existing(self):
        with TemporaryDirectory() as td:
            tmp = Path(td)
            corpus = _write_corpus(tmp, {"a.md": "x", "b.md": "y"})
            inter = tmp / "stages"; inter.mkdir()
            stage = get_stage("metadata")
            (inter / f"a{stage.intermediate_suffix}").write_text("{}", encoding="utf-8")

            todo, skipped = select_files(
                corpus, inter, stage, None, force=True, limit=None,
            )
            self.assertEqual([p.name for p in todo], ["a.md", "b.md"])
            self.assertEqual(skipped, [])

    def test_limit_caps_result(self):
        with TemporaryDirectory() as td:
            tmp = Path(td)
            corpus = _write_corpus(tmp, {f"f{i}.md": "x" for i in range(5)})
            inter = tmp / "stages"; inter.mkdir()
            todo, _ = select_files(
                corpus, inter, get_stage("metadata"),
                None, force=False, limit=2,
            )
            self.assertEqual(len(todo), 2)

    def test_per_stage_isolation(self):
        """An intermediate from one stage must not skip another stage's work."""
        with TemporaryDirectory() as td:
            tmp = Path(td)
            corpus = _write_corpus(tmp, {"x.md": "x"})
            inter = tmp / "stages"; inter.mkdir()
            meta = get_stage("metadata")
            summ = get_stage("summary")
            (inter / f"x{meta.intermediate_suffix}").write_text(
                "{}", encoding="utf-8",
            )

            todo_meta, _ = select_files(corpus, inter, meta, None, False, None)
            todo_sum, _ = select_files(corpus, inter, summ, None, False, None)
            self.assertEqual(todo_meta, [])
            self.assertEqual([p.name for p in todo_sum], ["x.md"])


# ---------------------------------------------------------------------------
# process_files (legacy single-pass, retained for back-compat)
# ---------------------------------------------------------------------------
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
        with TemporaryDirectory() as td:
            tmp = Path(td)
            corpus = _write_corpus(tmp, {"x.md": "body"})
            out = tmp / "out"; out.mkdir()

            extractor = FakeExtractor({
                "x.md": json.dumps(dict(self.GOLD, file="x.md"),
                                   ensure_ascii=False),
            })
            stats = process_files(list(corpus.glob("*.md")), extractor, out)
            self.assertEqual(stats, {"ok": 1, "truncated": 0,
                                     "invalid_json": 0, "errors": 0})
            self.assertTrue((out / "x.json").exists())
            written = json.loads((out / "x.json").read_text(encoding="utf-8"))
            self.assertEqual(written["court_type"], "Yargıtay")

    def test_invalid_json_writes_raw_fallback(self):
        with TemporaryDirectory() as td:
            tmp = Path(td)
            corpus = _write_corpus(tmp, {"x.md": "body"})
            out = tmp / "out"; out.mkdir()

            extractor = FakeExtractor({"x.md": "{not json"})
            stats = process_files(list(corpus.glob("*.md")), extractor, out)
            self.assertEqual(stats["invalid_json"], 1)
            self.assertEqual(stats["truncated"], 0)
            self.assertEqual(stats["ok"], 0)
            self.assertFalse((out / "x.json").exists())
            self.assertTrue((out / "x.raw.txt").exists())

    def test_truncation_flag_routes_to_truncated_bucket(self):
        """Truncated responses must NOT be lumped into invalid_json — the
        whole point of the dedicated bucket is that the raw output happens
        to be invalid JSON only because it was cut off, which is a
        different problem to investigate (output cap, not prompt bugs)."""
        with TemporaryDirectory() as td:
            tmp = Path(td)
            corpus = _write_corpus(tmp, {"x.md": "body"})
            out = tmp / "out"; out.mkdir()
            # Mid-string cutoff: would also fail json.loads, so without
            # the truncated flag this would land in invalid_json.
            extractor = FakeExtractor({"x.md": '{"foo": "bar'},
                                      truncated=True)
            stats = process_files(list(corpus.glob("*.md")), extractor, out)
            self.assertEqual(stats["truncated"], 1)
            self.assertEqual(stats["invalid_json"], 0)
            self.assertEqual(stats["ok"], 0)
            self.assertTrue((out / "x.raw.txt").exists())


# ---------------------------------------------------------------------------
# process_stage (per-stage loop; writes intermediates with per-stage suffix)
# ---------------------------------------------------------------------------
class ProcessStageTests(unittest.TestCase):
    def test_writes_intermediate_with_stage_suffix(self):
        with TemporaryDirectory() as td:
            tmp = Path(td)
            corpus = _write_corpus(tmp, {"x.md": "body"})
            inter = tmp / "stages"; inter.mkdir()
            stage = get_stage("summary")
            extractor = FakeExtractor({
                "x.md": json.dumps({"file": "x.md", "summary": "Özet."},
                                   ensure_ascii=False),
            })
            stats = process_stage(stage, list(corpus.glob("*.md")),
                                  extractor, inter)
            self.assertEqual(stats["ok"], 1)
            out = inter / f"x{stage.intermediate_suffix}"
            self.assertTrue(out.exists())
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(payload["summary"], "Özet.")

    def test_truncated_response_writes_raw_with_stage_suffix(self):
        with TemporaryDirectory() as td:
            tmp = Path(td)
            corpus = _write_corpus(tmp, {"x.md": "body"})
            inter = tmp / "stages"; inter.mkdir()
            stage = get_stage("citations_laws")
            extractor = FakeExtractor({"x.md": '{"cited_law_articles": ['},
                                      truncated=True)
            stats = process_stage(stage, list(corpus.glob("*.md")),
                                  extractor, inter)
            self.assertEqual(stats["truncated"], 1)
            raw = inter / f"x{stage.intermediate_suffix}.raw.txt"
            self.assertTrue(raw.exists())


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------
class MergeStagePayloadsTests(unittest.TestCase):
    def test_combines_disjoint_keys_in_canonical_order(self):
        merged = merge_stage_payloads("doc", {
            "metadata": {
                "file": "doc.md",
                "court_type": "Yargıtay",
                "court": "Yargıtay 1. HD",
                "case_number": "2023/1",
                "decision_number": "2023/2",
                "decision_date": "2023-01-01",
                "decision_type": "Temyiz",
            },
            "summary": {"file": "doc.md", "summary": "Özet."},
            "citations_decisions": {
                "file": "doc.md",
                "cited_court_decisions": [{"court": "X"}],
            },
            "citations_laws": {
                "file": "doc.md",
                "cited_law_articles": [{"law": "TBK"}],
            },
        })
        self.assertEqual(merged["file"], "doc.md")
        self.assertEqual(merged["court_type"], "Yargıtay")
        self.assertEqual(merged["summary"], "Özet.")
        self.assertEqual(len(merged["cited_court_decisions"]), 1)
        self.assertEqual(len(merged["cited_law_articles"]), 1)

        # Canonical key order: file → metadata → summary → citations
        keys = list(merged.keys())
        self.assertEqual(keys[0], "file")
        self.assertLess(keys.index("court_type"), keys.index("summary"))
        self.assertLess(keys.index("summary"),
                        keys.index("cited_court_decisions"))
        self.assertLess(keys.index("cited_court_decisions"),
                        keys.index("cited_law_articles"))

    def test_drops_keys_outside_stage_output_set(self):
        """If the metadata stage hallucinates a `summary` field, the merge
        must NOT pick it up — only the dedicated summary stage owns that key."""
        merged = merge_stage_payloads("doc", {
            "metadata": {
                "file": "doc.md",
                "court_type": "Yargıtay",
                "summary": "BOGUS — should be dropped",
            },
            "summary": {"file": "doc.md", "summary": "Real summary."},
        })
        self.assertEqual(merged["summary"], "Real summary.")

    def test_partial_merge_omits_missing_stages(self):
        merged = merge_stage_payloads("doc", {
            "metadata": {"file": "doc.md", "court_type": "Yargıtay"},
        })
        self.assertEqual(merged["court_type"], "Yargıtay")
        self.assertNotIn("summary", merged)
        self.assertNotIn("cited_court_decisions", merged)
        self.assertNotIn("cited_law_articles", merged)


class DiscoverAndMergeFromDiskTests(unittest.TestCase):
    def _write_intermediates(self, inter: Path, stem: str,
                             stages_to_write: list[str]):
        payloads = {
            "metadata": {"file": f"{stem}.md", "court_type": "Yargıtay",
                         "court": "Yargıtay 1. HD"},
            "summary":  {"file": f"{stem}.md", "summary": "Özet."},
            "citations_decisions": {
                "file": f"{stem}.md", "cited_court_decisions": [],
            },
            "citations_laws": {
                "file": f"{stem}.md",
                "cited_law_articles": [{"law": "TBK", "article": "19"}],
            },
        }
        for name in stages_to_write:
            stage = get_stage(name)
            (inter / f"{stem}{stage.intermediate_suffix}").write_text(
                json.dumps(payloads[name], ensure_ascii=False), encoding="utf-8",
            )

    def test_discover_stems_finds_each_unique_stem(self):
        with TemporaryDirectory() as td:
            inter = Path(td)
            self._write_intermediates(inter, "doc_a",
                                      ["metadata", "summary"])
            self._write_intermediates(inter, "doc_b", ["metadata"])
            self.assertEqual(discover_stems(inter), ["doc_a", "doc_b"])

    def test_merge_one_document_complete(self):
        with TemporaryDirectory() as td:
            tmp = Path(td)
            inter = tmp / "stages"; inter.mkdir()
            out = tmp / "out"; out.mkdir()
            self._write_intermediates(
                inter, "doc",
                ["metadata", "summary", "citations_decisions", "citations_laws"],
            )
            path, detail = merge_one_document("doc", inter, out)
            self.assertIsNotNone(path)
            self.assertEqual(detail["missing"], [])
            merged = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(merged["court_type"], "Yargıtay")
            self.assertEqual(merged["summary"], "Özet.")
            self.assertEqual(merged["cited_law_articles"][0]["law"], "TBK")

    def test_merge_one_document_partial_still_writes(self):
        with TemporaryDirectory() as td:
            tmp = Path(td)
            inter = tmp / "stages"; inter.mkdir()
            out = tmp / "out"; out.mkdir()
            self._write_intermediates(inter, "doc", ["metadata", "summary"])
            path, detail = merge_one_document("doc", inter, out)
            self.assertIsNotNone(path)
            self.assertCountEqual(detail["missing"],
                                  ["citations_decisions", "citations_laws"])
            merged = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("cited_court_decisions", merged)

    def test_merge_all_classifies_complete_vs_partial(self):
        with TemporaryDirectory() as td:
            tmp = Path(td)
            inter = tmp / "stages"; inter.mkdir()
            out = tmp / "out"; out.mkdir()
            self._write_intermediates(
                inter, "doc_a",
                ["metadata", "summary", "citations_decisions", "citations_laws"],
            )
            self._write_intermediates(inter, "doc_b", ["metadata"])
            stats = merge_all(inter, out)
            self.assertEqual(stats["merged"], 2)
            self.assertEqual(stats["complete"], 1)
            self.assertEqual(stats["partial"], 1)


# ---------------------------------------------------------------------------
# End-to-end: legacy process_files output → score_extractions
# ---------------------------------------------------------------------------
class ScoringIntegrationTests(unittest.TestCase):
    """Exercise the full runner-output → score_extractions metric path."""

    def test_perfect_match_scores_100_percent(self):
        import score_extractions  # type: ignore  # eval/scripts on sys.path

        gold = ProcessFilesTests.GOLD
        record = dict(gold, file="case_a.md")

        with TemporaryDirectory() as td:
            tmp = Path(td)
            corpus = _write_corpus(tmp, {"case_a.md": "body"})
            out = tmp / "out"; out.mkdir()
            gold_dir = tmp / "gold"; gold_dir.mkdir()

            (gold_dir / "case_a.json").write_text(
                json.dumps(record, ensure_ascii=False), encoding="utf-8",
            )

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

    def test_staged_merge_output_matches_score_format(self):
        """The merged JSON produced by the staged pipeline must score
        cleanly against a gold file in the same shape — proving the merge
        layout is downstream-compatible."""
        import score_extractions  # type: ignore

        gold = ProcessFilesTests.GOLD
        record = dict(gold, file="case_a.md")

        with TemporaryDirectory() as td:
            tmp = Path(td)
            inter = tmp / "stages"; inter.mkdir()
            out = tmp / "out"; out.mkdir()
            gold_dir = tmp / "gold"; gold_dir.mkdir()
            (gold_dir / "case_a.json").write_text(
                json.dumps(record, ensure_ascii=False), encoding="utf-8",
            )

            # Split the gold record into 4 stage-shaped intermediates and
            # let merge_all re-assemble them.
            for stage in STAGES:
                stage_payload = {"file": "case_a.md"}
                for k in stage.output_keys:
                    if k in record:
                        stage_payload[k] = record[k]
                write_intermediate(inter, "case_a", stage, stage_payload)

            stats = merge_all(inter, out)
            self.assertEqual(stats["complete"], 1)

            report = score_extractions.score_folder(out, gold_dir)
            self.assertAlmostEqual(
                report["summary"]["mean_score_on_scored_files"], 1.0, places=4,
            )


def _import_helper_check():
    """`llm_process` must expose the helpers tests rely on."""
    for name in (
        "GeminiExtractor", "OpenAICompatibleExtractor",
        "build_extractor", "process_files", "process_stage",
        "select_files", "write_output", "write_intermediate",
        "merge_all", "merge_one_document", "merge_stage_payloads",
        "discover_stems", "get_stage", "STAGES", "STAGE_NAMES", "Stage",
        "resolve_stage_config", "ExtractResult",
    ):
        assert hasattr(llm_process, name), f"missing: {name}"


if __name__ == "__main__":
    _import_helper_check()
    unittest.main(verbosity=2)
