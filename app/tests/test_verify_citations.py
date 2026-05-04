#!/usr/bin/env python3
"""Tests for app.ingestion.verify_citations."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.ingestion import verify_citations as v  # noqa: E402


SOURCE_TR = """T.C. ADANA 1. ASLİYE TİCARET MAHKEMESİ
ESAS NO : 2019/366
KARAR NO : 2021/945
6098 sayılı TBK'nun 52.maddesinde düzenlenmiştir.
6100 sayılı HMK.nun 341/1 ve 345 maddeleri gereğince ...
"""


class CourtDecisionTests(unittest.TestCase):
    def test_verifies_when_case_number_present(self):
        c = {"case_number": "2019/366", "decision_number": None}
        result = v.verify_court_decision(
            c, source=SOURCE_TR,
            source_compact_id=v.compact_id(SOURCE_TR), idx=0,
        )
        self.assertTrue(result.verified)
        self.assertIn("case_number:2019/366", result.signals)

    def test_rejects_when_neither_number_present(self):
        c = {"case_number": "2018/9999", "decision_number": "2019/1234"}
        result = v.verify_court_decision(
            c, source=SOURCE_TR,
            source_compact_id=v.compact_id(SOURCE_TR), idx=0,
        )
        self.assertFalse(result.verified)
        self.assertEqual(result.signals, [])
        self.assertIn("neither", result.reason)

    def test_handles_whitespace_in_source(self):
        src = "Esas No: 2019 / 366  -  Karar No: 2021 / 945"
        c = {"case_number": "2019/366", "decision_number": None}
        result = v.verify_court_decision(
            c, source=src, source_compact_id=v.compact_id(src), idx=0,
        )
        self.assertTrue(result.verified)


class LawArticleTests(unittest.TestCase):
    def setUp(self):
        self.compact = v.compact_id(SOURCE_TR)
        self.normalized = v.normalize_for_substring(SOURCE_TR)

    def _check(self, citation):
        return v.verify_law_article(
            citation, source=SOURCE_TR,
            source_normalized=self.normalized, idx=0,
        )

    def test_verifies_via_abbreviation(self):
        result = self._check({"law": "Türk Borçlar Kanunu",
                              "law_number": "6098", "article": "52"})
        self.assertTrue(result.verified)
        self.assertTrue(any(s.startswith("abbreviation:TBK") or
                            s.startswith("law_number:6098")
                            for s in result.signals))

    def test_verifies_via_law_number_alone(self):
        result = self._check({"law": "Türk Borçlar Kanunu",
                              "law_number": "6098", "article": None})
        self.assertTrue(result.verified)
        self.assertIn("law_number:6098", result.signals)

    def test_rejects_unrelated_law(self):
        result = self._check({"law": "Kişisel Verilerin Korunması Kanunu",
                              "law_number": "6698", "article": "5"})
        self.assertFalse(result.verified)
        self.assertEqual(result.signals, [])

    def test_rejects_when_only_article_number_overlaps(self):
        # "52" coincidentally appears in the source as part of TBK Art. 52.
        # Without a law signal, we must NOT verify a TCK citation.
        result = self._check({"law": "Türk Ceza Kanunu",
                              "law_number": "5237", "article": "52"})
        self.assertFalse(result.verified)


class HasWordTests(unittest.TestCase):
    def test_word_boundary_blocks_substring(self):
        # "MK" (Türk Medeni Kanunu shorthand) should NOT match "HMK".
        self.assertFalse(v.has_word("6100 sayılı HMK", "MK"))

    def test_matches_with_punctuation_border(self):
        self.assertTrue(v.has_word("6100 sayılı HMK.nun 341 maddesi", "HMK"))

    def test_does_not_match_when_glued_to_letters(self):
        self.assertFalse(v.has_word("ABCTBKXYZ", "TBK"))


class HasLawNumberTests(unittest.TestCase):
    def test_matches_with_text_borders(self):
        self.assertTrue(v.has_law_number("6098 sayılı TBK", "6098"))

    def test_does_not_match_inside_longer_digit_run(self):
        self.assertFalse(v.has_law_number("160980 sayılı", "6098"))


class FileIntegrationTests(unittest.TestCase):
    def _setup(self, tmp: Path, payload: dict, source: str):
        corpus = tmp / "corpus"; corpus.mkdir()
        out = tmp / "out"; out.mkdir()
        (corpus / "doc.md").write_text(source, encoding="utf-8")
        (out / "doc.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8",
        )
        return corpus, out

    def test_verify_file_writes_report(self):
        with TemporaryDirectory() as td:
            tmp = Path(td)
            payload = {
                "cited_court_decisions": [
                    {"case_number": "2019/366", "decision_number": None},
                    {"case_number": "1999/1", "decision_number": "2000/1"},
                ],
                "cited_law_articles": [
                    {"law": "Türk Borçlar Kanunu", "law_number": "6098",
                     "article": "52"},
                    {"law": "Türk Ceza Kanunu", "law_number": "5237",
                     "article": "86"},
                ],
            }
            corpus, out = self._setup(tmp, payload, SOURCE_TR)

            result = v.verify_file(out / "doc.json", corpus / "doc.md")
            self.assertEqual(len(result.decisions), 2)
            self.assertTrue(result.decisions[0].verified)
            self.assertFalse(result.decisions[1].verified)
            self.assertTrue(result.laws[0].verified)
            self.assertFalse(result.laws[1].verified)

            v.write_verification(result, out)
            report_path = out / "doc.verification.json"
            self.assertTrue(report_path.exists())
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(
                report["summary"]["cited_court_decisions"]["hallucinated"], 1,
            )
            self.assertEqual(
                report["summary"]["cited_law_articles"]["hallucinated"], 1,
            )

    def test_apply_strict_drops_hallucinated_and_backs_up(self):
        with TemporaryDirectory() as td:
            tmp = Path(td)
            payload = {
                "cited_court_decisions": [
                    {"case_number": "9999/1", "decision_number": "9999/2"},
                ],
                "cited_law_articles": [
                    {"law": "Türk Borçlar Kanunu", "law_number": "6098",
                     "article": "52"},
                    {"law": "Türk Ceza Kanunu", "law_number": "5237",
                     "article": "86"},
                ],
            }
            corpus, out = self._setup(tmp, payload, SOURCE_TR)

            result = v.verify_file(out / "doc.json", corpus / "doc.md")
            rewrote, dropped = v.apply_strict(result)

            self.assertTrue(rewrote)
            self.assertEqual(dropped, 2)  # 1 court + 1 law dropped

            cleaned = json.loads((out / "doc.json").read_text(encoding="utf-8"))
            self.assertEqual(cleaned["cited_court_decisions"], [])
            self.assertEqual(len(cleaned["cited_law_articles"]), 1)
            self.assertEqual(
                cleaned["cited_law_articles"][0]["law"], "Türk Borçlar Kanunu",
            )

            backup = out / "doc.unverified.json"
            self.assertTrue(backup.exists())
            original = json.loads(backup.read_text(encoding="utf-8"))
            self.assertEqual(len(original["cited_court_decisions"]), 1)
            self.assertEqual(len(original["cited_law_articles"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
