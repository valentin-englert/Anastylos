"""Deterministic evaluation-workflow tests. Needs OpenCV/NumPy.

Labels here are synthetic and hand-verifiable; they test the scoring
machinery, not real-hardware accuracy. Real-photo evaluation follows
the same label schema (see README).

Run from the cable-routing directory:

    ..\\Anastylos\\.venv\\Scripts\\python -m unittest discover -s tests -v
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
sys.path.insert(0, str(ROOT.parent / "assembly-memory"))

import cv2

from anastylos_cable import synthetic
from anastylos_cable.cli import main
from anastylos_cable.eval import evaluate_cables, evaluate_graph


class CableEvalTests(unittest.TestCase):
    def test_perfect_labels_score_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = synthetic.render("straight")
            cv2.imwrite(str(Path(tmp) / "straight.png"), image)
            labels = {"images": [{
                "file": "straight.png",
                "cables": [{"polyline_xy": [[40, 200], [360, 200]],
                            "width_px": 12.0}],
                "features": []}]}
            labels_path = Path(tmp) / "labels.json"
            labels_path.write_text(json.dumps(labels), encoding="utf-8")
            report = evaluate_cables(str(labels_path))
            summary = report["summary"]
            self.assertEqual(summary["matched"], 1)
            self.assertEqual(summary["missed"], 0)
            self.assertEqual(summary["extra"], 0)
            self.assertLess(summary["mean_drift_px"], 3.0)
            self.assertLess(summary["mean_width_err_px"], 2.5)

    def test_feature_labels_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            cv2.imwrite(str(Path(tmp) / "crossing.png"),
                        synthetic.render("crossing"))
            labels = {"images": [{
                "file": "crossing.png",
                "cables": [{"polyline_xy": [[60, 120], [340, 280]],
                            "width_px": 12.0}],
                "features": [{"kind": "crossing", "xy": [200, 200]}]}]}
            labels_path = Path(tmp) / "labels.json"
            labels_path.write_text(json.dumps(labels), encoding="utf-8")
            report = evaluate_cables(str(labels_path))
            summary = report["summary"]
            self.assertEqual(summary["features_hit"], 1)
            self.assertEqual(summary["features_missed"], 0)

    def test_unreadable_image_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            labels = {"images": [{"file": "nope.png", "cables": [],
                                  "features": []}]}
            labels_path = Path(tmp) / "labels.json"
            labels_path.write_text(json.dumps(labels), encoding="utf-8")
            report = evaluate_cables(str(labels_path))
            self.assertEqual(report["images"][0]["error"], "unreadable")

    def test_eval_command_with_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            cv2.imwrite(str(Path(tmp) / "straight.png"),
                        synthetic.render("straight"))
            labels = {"images": [{
                "file": "straight.png",
                "cables": [{"polyline_xy": [[40, 200], [360, 200]],
                            "width_px": 12.0}],
                "features": []}]}
            labels_path = Path(tmp) / "labels.json"
            labels_path.write_text(json.dumps(labels), encoding="utf-8")
            out = str(Path(tmp) / "out")
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["eval", "--labels", str(labels_path),
                             "--out", out])
            self.assertEqual(code, 0)
            self.assertTrue((Path(out) / "eval_report.json").is_file())


class GraphEvalTests(unittest.TestCase):
    def _fixture(self, root, name="chain"):
        fixture = Path(root) / name
        (fixture / "results").mkdir(parents=True)
        (fixture / "results" / "r1.json").write_text(json.dumps({
            "status": "multiple_changes", "pair_id": "p1",
            "rectified": {"view": "surface_a3"},
            "regions": [
                {"label": "R1", "bbox_mm": [0, 0, 20, 20],
                 "centroid_mm": [10, 10]},
                {"label": "R2", "bbox_mm": [5, 5, 10, 10],
                 "centroid_mm": [10, 10]}]}), encoding="utf-8")
        events = [
            {"type": "observation", "id": "obs-0001", "session": "s1",
             "timestamp": "2026-01-01T00:00:00Z", "source_view": "surface_a3",
             "bbox_mm": [0, 0, 20, 20], "centroid_mm": [10, 10],
             "evidence": {"result_json": "results/r1.json", "pair_id": "p1",
                         "region": "R1", "result_sha256": None},
             "supersedes": None},
            {"type": "observation", "id": "obs-0002", "session": "s1",
             "timestamp": "2026-01-01T00:01:00Z", "source_view": "surface_a3",
             "bbox_mm": [5, 5, 10, 10], "centroid_mm": [10, 10],
             "evidence": {"result_json": "results/r1.json", "pair_id": "p1",
                         "region": "R2", "result_sha256": None},
             "supersedes": None}]
        (fixture / "memory.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events) + "\n",
            encoding="utf-8")
        (fixture / "confirmations.json").write_text(json.dumps([
            {"before": "obs-0001", "after": "obs-0002",
             "confirmed_by": "op"}]), encoding="utf-8")
        (fixture / "expected.json").write_text(json.dumps({
            "confirmed": [["obs-0001", "obs-0002"]]}), encoding="utf-8")

    def test_graph_fixture_scores(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._fixture(tmp)
            report = evaluate_graph(tmp)
            summary = report["summary"]
            self.assertEqual(summary["fixtures"], 1)
            self.assertEqual(summary["confirmed_hit"], 1)
            self.assertEqual(summary["confirmed_missed"], 0)
            self.assertEqual(summary["confirmed_extra"], 0)
            self.assertEqual(summary["valid_orders"], 1)

    def test_eval_command_with_graph_fixtures(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._fixture(tmp)
            out = str(Path(tmp) / "out")
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["eval", "--graph-fixtures", tmp,
                             "--out", out])
            self.assertEqual(code, 0)
            self.assertTrue((Path(out) / "eval_report.json").is_file())


if __name__ == "__main__":
    unittest.main()
