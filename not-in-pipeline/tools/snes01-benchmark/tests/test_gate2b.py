"""Gate 2B unit tests: panels, key gating, scoring math, dry-run.

Fully offline: no API calls. Synthetic fixtures only. The synthetic
'sealed key' below is test scaffolding, never presented as truth.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import gate2b_run as run


def _call(verdict):
    return {"parsed": {"verdict": verdict}}


class Panels(unittest.TestCase):
    def test_panel_deterministic_and_labeled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            left = np.full((40, 30, 3), 200, np.uint8)
            right = np.full((60, 20, 3), 50, np.uint8)
            cv2.imwrite(str(root / "a.png"), left)
            cv2.imwrite(str(root / "b.png"), right)
            first = run.build_panel(root / "a.png", root / "b.png",
                                    root / "p1.png")
            second = run.build_panel(root / "a.png", root / "b.png",
                                     root / "p2.png")
            self.assertEqual(first["panel_sha256"],
                             second["panel_sha256"])
            panel = cv2.imread(str(root / "p1.png"))
            self.assertEqual(panel.shape[0], run.PANEL_H)
            self.assertEqual(first["layout"], "A-left|B-right")


class KeyGating(unittest.TestCase):
    def _write(self, root: Path, name: str, doc: dict) -> Path:
        path = root / name
        path.write_text(json.dumps(doc), encoding="utf-8")
        return path

    def test_template_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), "k.json",
                               {"schema": run.KEY_SCHEMA,
                                "status": "template", "annotator": "",
                                "verdicts": []})
            with self.assertRaises(SystemExit):
                run.load_key(path)

    def test_missing_annotator_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), "k.json",
                               {"schema": run.KEY_SCHEMA,
                                "status": "sealed", "annotator": "",
                                "verdicts": []})
            with self.assertRaises(SystemExit):
                run.load_key(path)

    def test_sealed_key_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), "k.json",
                               {"schema": run.KEY_SCHEMA,
                                "status": "sealed",
                                "annotator": "test-op",
                                "verdicts": []})
            key = run.load_key(path)
            self.assertEqual(key["annotator"], "test-op")

    def test_verdict_extraction_invalid(self):
        self.assertEqual(run._model_verdict({}), "INVALID")
        self.assertEqual(run._model_verdict({"parsed": {"verdict": "x"}}),
                         "INVALID")
        self.assertEqual(run._model_verdict(
            {"parsed": {"verdict": "same_instance_candidate"}}),
            "same_instance_candidate")


class ScoringMath(unittest.TestCase):
    def _setup(self, root: Path):
        (root / "pairs.json").write_text(json.dumps(
            {"scored_pairs": ["P1", "P2", "P3"],
             "pairs": [{"id": "P1"}, {"id": "P2"}, {"id": "P3"},
                       {"id": "S1"}]}), encoding="utf-8")
        (root / "key.json").write_text(json.dumps(
            {"schema": run.KEY_SCHEMA, "status": "sealed",
             "annotator": "test-op",
             "verdicts": [
                 {"pair_id": "P1", "verdict": "same"},
                 {"pair_id": "P2", "verdict": "different"},
                 {"pair_id": "P3", "verdict": "ambiguous"},
                 {"pair_id": "S1", "verdict": "same"}]}),
            encoding="utf-8")
        (root / "results.json").write_text(json.dumps(
            {"modes": ["separate", "composite"],
             "results": [
                 {"id": "P1", "calls": {
                     "separate": _call("same_instance_candidate"),
                     "composite": _call("insufficient_evidence")}},
                 {"id": "P2", "calls": {
                     "separate": _call("different_instance_candidate"),
                     "composite": _call("different_instance_candidate")}},
                 {"id": "P3", "calls": {
                     "separate": _call("same_instance_candidate"),
                     "composite": {}}},
                 {"id": "S1", "calls": {
                     "separate": _call("same_instance_candidate"),
                     "composite": _call("same_instance_candidate")}}]}),
            encoding="utf-8")

    def test_confusion_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._setup(root)
            code = run.main(["score", "--results",
                             str(root / "results.json"), "--pairs",
                             str(root / "pairs.json"), "--key",
                             str(root / "key.json"), "--out",
                             str(root / "o")])
            self.assertEqual(code, 0)
            report = json.loads((root / "o" / "score.json").read_text(
                encoding="utf-8"))
            separate = report["scored_modes"]["separate"]
            self.assertEqual(
                (separate["TP"], separate["FP"], separate["TN"],
                 separate["FN_decisive"], separate["abstain_on_same"]),
                (1, 1, 1, 0, 0))
            self.assertEqual(separate["coverage_decisive"], 1.0)
            self.assertEqual(separate["recall_abstain_as_miss"], 1.0)
            composite = report["scored_modes"]["composite"]
            self.assertEqual(
                (composite["TP"], composite["abstain_on_same"],
                 composite["invalid"]),
                (0, 1, 1))
            self.assertEqual(report["separate_vs_composite"]["agree"], 1)
            self.assertEqual(report["separate_vs_composite"]["disagree"],
                             1)
            self.assertEqual(
                report["separate_vs_composite"]["unavailable"], 1)
            # Sanity pair reported but never scored.
            self.assertIn("S1",
                          report["sanity_excluded_from_scoring"])
            self.assertEqual(separate["n_scored"], 3)

    def test_unsealed_key_refuses_scoring(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._setup(root)
            (root / "key.json").write_text(json.dumps(
                {"schema": run.KEY_SCHEMA, "status": "template",
                 "annotator": "", "verdicts": []}), encoding="utf-8")
            with self.assertRaises(SystemExit):
                run.main(["score", "--results",
                          str(root / "results.json"), "--pairs",
                          str(root / "pairs.json"), "--key",
                          str(root / "key.json"), "--out",
                          str(root / "o")])


if __name__ == "__main__":
    unittest.main()
