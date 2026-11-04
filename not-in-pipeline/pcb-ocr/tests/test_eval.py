"""Eval-harness checks with stub backends. Needs the venv (cv2).

Proves the ground-truth workflow scores correctly on fixtures; says
nothing about real-photo accuracy.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import cv2

from anastylos_pcb.eval import run_eval
from test_pipeline import DotBackend, make_crop


class EvalTest(unittest.TestCase):
    def test_perfect_score(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            images = folder / "images"
            images.mkdir()
            cv2.imwrite(str(images / "a.png"), make_crop())
            (folder / "labels.json").write_text(json.dumps({"images": [{
                "file": "a.png",
                "labels": [{"text": "R12", "bbox_xywh": [10, 10, 12, 12]}],
            }]}), encoding="utf-8")
            report = run_eval(folder / "labels.json", images, folder / "out", DotBackend())
            self.assertEqual((report["hits"], report["labels"], report["predicted"]), (1, 1, 1))
            self.assertEqual((report["recall"], report["precision"]), (1.0, 1.0))
            self.assertTrue((folder / "out" / "eval_report.json").is_file())
            self.assertTrue((folder / "out" / "a_annotated.png").is_file())

    def test_miss_and_false_positive(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            images = folder / "images"
            images.mkdir()
            cv2.imwrite(str(images / "a.png"), make_crop())
            (folder / "labels.json").write_text(json.dumps({"images": [{
                "file": "a.png",
                "labels": [{"text": "R12"}, {"text": "C5"}],
            }]}), encoding="utf-8")
            report = run_eval(folder / "labels.json", images, folder / "out",
                              DotBackend(text="R12"))
            self.assertEqual(report["hits"], 1)
            self.assertEqual(report["recall"], 0.5)
            self.assertEqual(report["precision"], 1.0)
            self.assertEqual(report["rows"][0]["labels"][1]["hit"], None)


if __name__ == "__main__":
    unittest.main()
