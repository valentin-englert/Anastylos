"""SMD eval-harness checks with stub backends. Needs the venv (cv2).

Proves exact/miss/false-confident accounting on fixtures; says nothing
about real-photo accuracy.
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

from anastylos_smd.eval import run_eval
from test_vision import ShapeBackend, make_crop, word


class EvalTest(unittest.TestCase):
    def _run(self, folder: Path, name: str, marking: str, backend, tag: str):
        images = folder / "images"
        images.mkdir(exist_ok=True)
        cv2.imwrite(str(images / name), make_crop())
        labels = folder / f"labels_{tag}.json"
        labels.write_text(json.dumps({"images": [{"file": name, "marking": marking}]}),
                           encoding="utf-8")
        return run_eval(labels, images, folder / f"out_{tag}", backend)

    def test_eval_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            exact = self._run(folder, "ok.png", "472", ShapeBackend(wide_words=[word("472")]), "exact")
            self.assertEqual((exact["exact"], exact["misses"], exact["false_confident"]), (1, 0, 0))
            miss = self._run(folder, "blank.png", "472", ShapeBackend(), "miss")
            self.assertEqual((miss["exact"], miss["misses"], miss["false_confident"]), (0, 1, 0))
            wrong = self._run(folder, "wrong.png", "472",
                              ShapeBackend(wide_words=[word("473")]), "wrong")
            self.assertEqual((wrong["exact"], wrong["misses"], wrong["false_confident"]), (0, 0, 1))
            self.assertTrue((folder / "out_exact" / "eval_report.json").is_file())
            self.assertTrue((folder / "out_exact" / "ok_annotated.png").is_file())

    def test_eval_repairs_count_undecided(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            report = self._run(folder, "fix.png", "801",
                               ShapeBackend(wide_words=[word("B01")]), "fix")
            self.assertEqual(report["undecided"], 1)
            self.assertEqual(report["false_confident"], 0)


if __name__ == "__main__":
    unittest.main()
