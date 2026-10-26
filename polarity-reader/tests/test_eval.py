"""Eval-harness checks on a tiny labelled synthetic set. Needs the venv.

Proves the harness wiring (labels in, report plus overlays out), not
recognition accuracy.

Run from the polarity-reader directory:

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

from anastylos_polarity.cli import main
from anastylos_polarity.eval import run_eval
from anastylos_polarity.synthetic import render

SETS = [("diode.png", "diode", "diode", "band", 0.0),
        ("electrolytic.png", "aluminum_electrolytic",
         "aluminum_electrolytic", "stripe", 0.0),
        ("tantalum.png", "tantalum", "tantalum", "band", 0.0),
        ("ic.png", "ic", "ic", "notch", 270.0),
        ("connector.png", "connector", "connector", "key", 321.9),
        ("topmark.png", "ic_topmark", None, "topmark", None),
        ("blank.png", "blank", None, "none", None)]


def write_set(directory: Path) -> Path:
    images = directory / "images"
    images.mkdir(parents=True)
    entries = []
    for name, kind, hint, marker, angle in SETS:
        cv2.imwrite(str(images / name), render(kind, seed=21))
        entry = {"file": name, "marker": marker}
        if hint is not None:
            entry["family"] = hint
        if angle is not None:
            entry["angle_deg"] = angle
        entries.append(entry)
    labels = directory / "labels.json"
    labels.write_text(json.dumps({"images": entries}), encoding="utf-8")
    return labels


class EvalTest(unittest.TestCase):
    def test_eval_scores_exact_undecided_and_miss(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            labels = write_set(tmpdir)
            report = run_eval(labels, tmpdir / "images", tmpdir / "eval_out")
            self.assertEqual(report["images"], 7)
            self.assertEqual(report["exact"], 5)
            self.assertEqual(report["undecided"], 1)
            self.assertEqual(report["misses"], 1)
            self.assertEqual(report["false_confident"], 0)
            by_file = {row["file"]: row for row in report["rows"]}
            self.assertEqual(by_file["blank.png"]["outcome"], "miss")
            self.assertEqual(by_file["topmark.png"]["outcome"], "undecided")
            self.assertTrue(by_file["topmark.png"]["truth_kept"])
            self.assertEqual(by_file["ic.png"]["outcome"], "exact")
            for name, _, _, _, _ in SETS:
                stem = Path(name).stem
                self.assertTrue(
                    (tmpdir / "eval_out" / f"{stem}_annotated.png").exists())
            self.assertTrue(
                (tmpdir / "eval_out" / "eval_report.json").exists())

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
                                                      "marker": "band"}]}),
                              encoding="utf-8")
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
