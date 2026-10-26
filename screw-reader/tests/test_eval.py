"""Eval-harness checks on a tiny labelled synthetic set. Needs the venv.

Proves the harness wiring (labels in, report plus overlays out), not
recognition accuracy.

Run from the screw-reader directory:

    ..\\.venv\\Scripts\\python -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2

from anastylos_screw.cli import main
from anastylos_screw.eval import run_eval
from anastylos_screw.synthetic import render


def write_set(directory: Path) -> Path:
    images = directory / "images"
    images.mkdir(parents=True)
    entries = []
    for drive in ("slotted", "phillips", "pozidriv", "torx", "hex"):
        name = f"{drive}.png"
        cv2.imwrite(str(images / name), render(drive, seed=21))
        entries.append({"file": name, "class": drive})
    cv2.imwrite(str(images / "blank.png"), render("blank"))
    entries.append({"file": "blank.png", "class": "unknown"})
    labels = directory / "labels.json"
    labels.write_text(json.dumps({"images": entries}), encoding="utf-8")
    return labels


class EvalTest(unittest.TestCase):
    def test_eval_scores_exact_and_miss_without_false_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            labels = write_set(tmpdir)
            report = run_eval(labels, tmpdir / "images", tmpdir / "eval_out")
            self.assertEqual(report["images"], 6)
            self.assertEqual(report["exact"], 5)
            self.assertEqual(report["misses"], 1)
            self.assertEqual(report["false_confident"], 0)
            self.assertEqual(report["undecided"], 0)
            by_file = {row["file"]: row for row in report["rows"]}
            self.assertEqual(by_file["blank.png"]["outcome"], "miss")
            self.assertEqual(by_file["torx.png"]["outcome"], "exact")
            self.assertTrue((tmpdir / "eval_out" / "eval_report.json").exists())
            self.assertTrue((tmpdir / "eval_out" / "hex_annotated.png").exists())

    def test_eval_is_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            labels = write_set(tmpdir)
            first = run_eval(labels, tmpdir / "images", tmpdir / "out_a")
            second = run_eval(labels, tmpdir / "images", tmpdir / "out_b")
            self.assertEqual(first, second)

    def test_eval_missing_image_fails_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            labels = tmpdir / "labels.json"
            labels.write_text(json.dumps({"images": [{"file": "gone.png",
                                                      "class": "hex"}]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                run_eval(labels, tmpdir, tmpdir / "out")

    def test_cli_eval_reports_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            labels = write_set(tmpdir)
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = main(["eval", "--labels", str(labels),
                             "--images-dir", str(tmpdir / "images"),
                             "--out", str(tmpdir / "eval_out")])
            self.assertEqual(code, 0)
            summary = json.loads(buffer.getvalue())
            self.assertEqual(summary["exact"], 5)
            self.assertEqual(summary["false_confident"], 0)


if __name__ == "__main__":
    unittest.main()
