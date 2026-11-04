"""Eval-harness and comparison checks on synthetic sets. Needs the venv.

Proves the harness wiring (labels in, report plus overlays out),
not recognition accuracy.

Run from the washer-spacer directory:

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
import numpy as np

from anastylos_washer.cli import main
from anastylos_washer.compare import compare_files
from anastylos_washer.eval import run_eval
from anastylos_washer.synthetic import render, render_grid, render_touching
from anastylos_washer.vision import analyze

SETS = [
    ("flat.png", "top", "flat_washer", "flat_washer",
     {"outer_mm": 22.0, "inner_mm": 11.0}),
    ("split.png", "top", "split_washer", "split_washer", {}),
    ("standoff.png", "top", "standoff", "standoff",
     {"across_corners_mm": 20.0, "across_flats_mm": 17.32}),
    ("spacer.png", "side", "spacer", "spacer",
     {"length_mm": 20.0, "thickness_mm": 11.0}),
    ("washer.png", "side", "flat_washer", "flat_washer",
     {"length_mm": 24.0, "thickness_mm": 2.8}),
    ("nut.png", "top", None, "standoff", {}),
    ("rod.png", "side", None, "unclassified", {}),
]


def write_set(directory: Path) -> Path:
    images = directory / "images"
    images.mkdir(parents=True)
    entries = []
    kinds = {"flat.png": "flat_top", "split.png": "split_top",
             "standoff.png": "standoff_top", "spacer.png": "spacer_side",
             "washer.png": "washer_side", "nut.png": "nut_top",
             "rod.png": "rod_side"}
    for name, view, hint, label, dims in SETS:
        cv2.imwrite(str(images / name), render(kinds[name], seed=21))
        entry = {"file": name, "view": view, "class": label, "count": 1,
                 "pixels_per_mm": 10.0, **dims}
        if hint is not None:
            entry["family"] = hint
        entries.append(entry)
    cv2.imwrite(str(images / "touching.png"), render_touching(seed=21))
    entries.append({"file": "touching.png", "view": "top",
                    "class": "flat_washer", "count": 1})
    cv2.imwrite(str(images / "blank.png"),
                np.full((400, 400, 3), 190, np.uint8))
    entries.append({"file": "blank.png", "view": "top",
                    "class": "unclassified", "count": 0})
    labels = directory / "labels.json"
    labels.write_text(json.dumps({"images": entries}), encoding="utf-8")
    return labels


class EvalTest(unittest.TestCase):
    def test_eval_scores_classes_counts_and_dims(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            labels = write_set(tmpdir)
            report = run_eval(labels, tmpdir / "images", tmpdir / "eval_out")
            self.assertEqual(report["images"], 9)
            self.assertEqual(report["exact"], 5)
            self.assertEqual(report["undecided"], 3)
            self.assertEqual(report["misses"], 1)
            self.assertEqual(report["false_confident"], 0)
            self.assertEqual(report["count_exact"], 9)
            by_file = {row["file"]: row for row in report["rows"]}
            self.assertEqual(by_file["blank.png"]["outcome"], "miss")
            self.assertEqual(by_file["nut.png"]["outcome"], "undecided")
            self.assertTrue(by_file["nut.png"]["truth_kept"])
            self.assertTrue(by_file["touching.png"]["truth_kept"])
            for key, stats in report["dim_errors_mm"].items():
                self.assertLess(stats["mean_abs_mm"], 0.3,
                                f"synthetic {key} drifted")
            self.assertTrue(
                (tmpdir / "eval_out" / "eval_report.json").exists())
            self.assertTrue(
                (tmpdir / "eval_out" / "flat_annotated.png").exists())

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
                                                      "view": "top",
                                                      "class": "flat_washer"}
                                                     ]}), encoding="utf-8")
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


class CompareTest(unittest.TestCase):
    def _results(self, directory: Path):
        left = analyze(render("flat_top", seed=3), "top",
                       frame_metadata={"pixels_per_mm": 10.0})
        same = analyze(render("flat_top", seed=4), "top",
                       frame_metadata={"pixels_per_mm": 10.0})
        other = analyze(render("spacer_top", seed=3), "top",
                        frame_metadata={"pixels_per_mm": 10.0})
        paths = {}
        for name, result in (("a.json", left), ("b.json", same),
                             ("c.json", other)):
            path = directory / name
            path.write_text(json.dumps(result), encoding="utf-8")
            paths[name] = path
        return paths

    def test_identical_parts_score_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._results(Path(tmp))
            report = compare_files(paths["a.json"], paths["b.json"])
            self.assertEqual(len(report["pairs"]), 1)
            self.assertEqual(report["pairs"][0]["similarity_heuristic"], 1.0)
            self.assertEqual(report["unit"], "mm")

    def test_different_parts_score_lower(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._results(Path(tmp))
            report = compare_files(paths["a.json"], paths["c.json"])
            self.assertLess(report["pairs"][0]["similarity_heuristic"], 0.5)
            self.assertIn("never prove",
                          report["disclaimer"])

    def test_mixed_units_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            calibrated = analyze(render("flat_top", seed=3), "top",
                                 frame_metadata={"pixels_per_mm": 10.0})
            plain = analyze(render("flat_top", seed=3), "top")
            left, right = tmpdir / "a.json", tmpdir / "b.json"
            left.write_text(json.dumps(calibrated), encoding="utf-8")
            right.write_text(json.dumps(plain), encoding="utf-8")
            with self.assertRaises(ValueError):
                compare_files(left, right)

    def test_unmatched_observations_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            grid = analyze(render_grid(seed=3), "top")
            one = analyze(render("flat_top", seed=3), "top")
            left, right = tmpdir / "a.json", tmpdir / "b.json"
            left.write_text(json.dumps(grid), encoding="utf-8")
            right.write_text(json.dumps(one), encoding="utf-8")
            report = compare_files(left, right)
            self.assertEqual(len(report["pairs"]), 1)
            self.assertEqual(len(report["unmatched_baseline"]), 2)
            self.assertEqual(report["unmatched_candidate"], [])


if __name__ == "__main__":
    unittest.main()
