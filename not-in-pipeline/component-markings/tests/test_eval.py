"""Eval-harness checks with stub backends. Needs the venv (cv2).

Proves exact/miss/false-confident/undecided accounting on fixtures;
says nothing about real-photo accuracy.
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

from anastylos_markings.eval import run_eval
from test_vision import CannedBackend, make_crop, word


class EvalTest(unittest.TestCase):
    def _run(self, folder: Path, name: str, backend, tag: str, **label):
        images = folder / "images"
        images.mkdir(exist_ok=True)
        cv2.imwrite(str(images / name), make_crop())
        entry = {"file": name, **label}
        labels = folder / f"labels_{tag}.json"
        labels.write_text(json.dumps({"images": [entry]}), encoding="utf-8")
        return run_eval(labels, images, folder / f"out_{tag}", backend)

    def test_eval_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            exact = self._run(folder, "ok.png", CannedBackend([word("LM358N")]),
                              "exact", marking="LM358N")
            self.assertEqual((exact["exact"], exact["misses"],
                              exact["false_confident"], exact["undecided"]), (1, 0, 0, 0))
            miss = self._run(folder, "blank.png", CannedBackend([]),
                             "miss", marking="LM358N")
            self.assertEqual((miss["exact"], miss["misses"],
                              miss["false_confident"], miss["undecided"]), (0, 1, 0, 0))
            wrong = self._run(folder, "wrong.png", CannedBackend([word("NE555")]),
                              "wrong", marking="LM358N")
            self.assertEqual((wrong["exact"], wrong["misses"],
                              wrong["false_confident"], wrong["undecided"]), (0, 0, 1, 0))
            undecided = self._run(folder, "short.png", CannedBackend([word("1AM")]),
                                  "undecided", marking="1AM", status="ok")
            self.assertEqual((undecided["exact"], undecided["misses"],
                              undecided["false_confident"], undecided["undecided"]), (0, 0, 0, 1))
            expected_ambiguous = self._run(folder, "short2.png", CannedBackend([word("1AM")]),
                                           "expected", marking="1AM", status="ambiguous")
            self.assertEqual(expected_ambiguous["exact"], 1)
            self.assertTrue((folder / "out_exact" / "eval_report.json").is_file())
            self.assertTrue((folder / "out_exact" / "ok_annotated.png").is_file())

    def test_eval_is_reproducible(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            images = folder / "images"
            images.mkdir()
            cv2.imwrite(str(images / "ok.png"), make_crop())
            labels = folder / "labels.json"
            labels.write_text(json.dumps({"images": [{"file": "ok.png",
                                                      "marking": "LM358N"}]}), encoding="utf-8")
            first = run_eval(labels, images, folder / "out_a", CannedBackend([word("LM358N")]))
            second = run_eval(labels, images, folder / "out_b", CannedBackend([word("LM358N")]))
            self.assertEqual(first, second)

    def test_eval_missing_image_fails_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            labels = tmpdir / "labels.json"
            labels.write_text(json.dumps({"images": [{"file": "gone.png",
                                                      "marking": "LM358N"}]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                run_eval(labels, tmpdir, tmpdir / "out", CannedBackend([]))


if __name__ == "__main__":
    unittest.main()
