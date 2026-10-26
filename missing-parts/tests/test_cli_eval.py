"""CLI, overlay, and eval-harness checks on a tiny synthetic set.

Proves the wiring (pairs in, bundles plus reports out), not
real-world accuracy.

Run from the missing-parts directory:

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

from anastylos_missing import synthetic as S
from anastylos_missing.annotate import annotate, annotate_reconciliation
from anastylos_missing.cli import main
from anastylos_missing.compare import run_comparison
from anastylos_missing.reconcile import run_reconciliation


def write_context(directory: Path) -> Path:
    path = directory / "ctx.json"
    path.write_text(json.dumps({"original": {"role": "original_assembly", "frame": None},
                                "current": {"role": "reassembly_state", "frame": None},
                                "surface": "light"}), encoding="utf-8")
    return path


def write_labels(directory: Path) -> tuple[Path, Path]:
    pairs_dir = directory / "pairs"
    pairs_dir.mkdir()
    context = write_context(directory)
    cases = {"removed": S.removed(), "added": S.added(), "shifted": S.shifted(),
             "still": S.unchanged(), "shade": S.lighting(), "cover": S.occluded()}
    entries = []
    for ident, (original, current, truth) in cases.items():
        cv2.imwrite(str(pairs_dir / f"{ident}_o.png"), original)
        cv2.imwrite(str(pairs_dir / f"{ident}_c.png"), current)
        entries.append({"id": ident, "original": f"{ident}_o.png", "current": f"{ident}_c.png",
                        "context_file": context.name,
                        "truth_absent": [{"bbox": box} for box in truth["absent"]],
                        "truth_occupied": [{"bbox": box} for box in truth.get("occupied", [])],
                        "truth_present": [{"bbox": box} for box in truth.get("present", [])]})
    labels = directory / "labels.json"
    labels.write_text(json.dumps({"pairs": entries}), encoding="utf-8")
    return labels, pairs_dir


class CliCompareTest(unittest.TestCase):
    def test_compare_writes_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            original, current, _ = S.removed()
            opath, cpath = tmpdir / "o.png", tmpdir / "c.png"
            cv2.imwrite(str(opath), original)
            cv2.imwrite(str(cpath), current)
            context = write_context(tmpdir)
            out = tmpdir / "out"
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["compare", "--original", str(opath), "--current", str(cpath),
                             "--context", str(context), "--out", str(out)])
            self.assertEqual(code, 0)
            bundle = json.loads((out / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(bundle["verdict"], "ok")
            self.assertTrue(bundle["comparison_id"].startswith("cmp-"))
            self.assertIn("confirmed_missing", bundle["not_claimed"])
            self.assertTrue((out / "overlay.png").exists())

    def test_compare_neutral_without_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            original, current, _ = S.removed()
            opath, cpath = tmpdir / "o.png", tmpdir / "c.png"
            cv2.imwrite(str(opath), original)
            cv2.imwrite(str(cpath), current)
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["compare", "--original", str(opath), "--current", str(cpath),
                             "--out", str(tmpdir / "out")])
            self.assertEqual(code, 0)
            bundle = json.loads((tmpdir / "out" / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(bundle["mode"], "neutral")

    def test_compare_detection_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            info = S.detection_fixture(tmpdir / "pair_01")
            context = tmpdir / "ctx.json"
            context.write_text(json.dumps(
                {"original": {"role": "a", "frame": "fixture_back"},
                 "current": {"role": "b", "frame": "fixture_back"},
                 "surface": "light"}), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["compare", "--detection", info["result_json"],
                             "--context", str(context), "--out", str(tmpdir / "out")])
            self.assertEqual(code, 0)
            bundle = json.loads((tmpdir / "out" / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(bundle["regions"][0]["classification"], "candidate_absent_region")

    def test_compare_missing_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = main(["compare", "--original", str(Path(tmp) / "o.png"),
                             "--current", str(Path(tmp) / "c.png"),
                             "--out", str(Path(tmp) / "out")])
            self.assertEqual(code, 1)

    def test_compare_bad_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            bad = tmpdir / "ctx.json"
            bad.write_text(json.dumps({"original": {}}), encoding="utf-8")
            original, current, _ = S.unchanged()
            opath, cpath = tmpdir / "o.png", tmpdir / "c.png"
            cv2.imwrite(str(opath), original)
            cv2.imwrite(str(cpath), current)
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = main(["compare", "--original", str(opath), "--current", str(cpath),
                             "--context", str(bad), "--out", str(tmpdir / "out")])
            self.assertEqual(code, 2)

    def test_compare_exclusive_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            info = S.detection_fixture(tmpdir / "pair_01")
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = main(["compare", "--original", "o.png", "--current", "c.png",
                             "--detection", info["result_json"],
                             "--out", str(tmpdir / "out")])
            self.assertEqual(code, 1)


class CliReconcileTest(unittest.TestCase):
    def _comparison(self, tmpdir: Path) -> Path:
        original, current, _ = S.removed()
        opath, cpath = tmpdir / "o.png", tmpdir / "c.png"
        cv2.imwrite(str(opath), original)
        cv2.imwrite(str(cpath), current)
        context = tmpdir / "ctx.json"
        context.write_text(json.dumps(
            {"original": {"role": "a", "frame": "fixture_back"},
             "current": {"role": "b", "frame": "fixture_back"},
             "surface": "light"}), encoding="utf-8")
        meta = tmpdir / "meta.json"
        meta.write_text(json.dumps({"px_per_mm": 4.0, "view": "fixture_back"}), encoding="utf-8")
        out = tmpdir / "cmp"
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(["compare", "--original", str(opath), "--current", str(cpath),
                         "--context", str(context), "--frame-meta", str(meta),
                         "--out", str(out)])
        self.assertEqual(code, 0)
        return out / "result.json"

    def test_reconcile_writes_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            comparison = self._comparison(tmpdir)
            expected = tmpdir / "exp.json"
            expected.write_text(json.dumps(
                {"expected": [{"id": "e1", "bbox_mm": [26.5, 31.5, 7, 7],
                               "frame": "fixture_back", "source": "test"}]}), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["reconcile", "--comparison", str(comparison),
                             "--expected", str(expected), "--out", str(tmpdir / "rec")])
            self.assertEqual(code, 0)
            report = json.loads((tmpdir / "rec" / "reconciliation.json").read_text(encoding="utf-8"))
            self.assertEqual(report["obligations"][0]["status"], "candidate_absent")
            self.assertTrue((tmpdir / "rec" / "reconciliation.png").exists())

    def test_reconcile_with_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            comparison = self._comparison(tmpdir)
            expected = tmpdir / "exp.json"
            expected.write_text(json.dumps(
                {"expected": [{"id": "e1", "bbox_mm": [26.5, 31.5, 7, 7],
                               "frame": "fixture_back", "source": "test"}]}), encoding="utf-8")
            confirmations = tmpdir / "conf.json"
            confirmations.write_text(json.dumps(
                {"confirmations": [{"expected_id": "e1", "confirmed_by": "operator",
                                    "statement": "missing"}]}), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["reconcile", "--comparison", str(comparison),
                             "--expected", str(expected), "--confirmations", str(confirmations),
                             "--out", str(tmpdir / "rec")])
            self.assertEqual(code, 0)
            report = json.loads((tmpdir / "rec" / "reconciliation.json").read_text(encoding="utf-8"))
            self.assertEqual(report["obligations"][0]["status"], "confirmed_missing")

    def test_reconcile_bad_comparison(self):
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = main(["reconcile", "--comparison", str(Path(tmp) / "gone.json"),
                             "--out", str(Path(tmp) / "rec")])
            self.assertEqual(code, 1)

    def test_reconcile_exclusive_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            comparison = self._comparison(tmpdir)
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = main(["reconcile", "--comparison", str(comparison),
                             "--expected", "e.json", "--memory-log", "m.jsonl",
                             "--out", str(tmpdir / "rec")])
            self.assertEqual(code, 2)


class EvalTest(unittest.TestCase):
    def test_eval_scores_recall_without_false_alerts(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            labels, pairs_dir = write_labels(tmpdir)
            from anastylos_missing.eval import run_eval
            report = run_eval(labels, pairs_dir, tmpdir / "eval_out")
            self.assertEqual(report["pairs"], 6)
            self.assertEqual(report["recall"], 1.0)
            self.assertEqual(report["false_alerts"], 0)
            self.assertEqual(report["false_on_present"], 0)
            self.assertGreaterEqual(report["mean_iou"], 0.5)
            self.assertTrue((tmpdir / "eval_out" / "eval_report.json").exists())
            self.assertTrue((tmpdir / "eval_out" / "removed_overlay.png").exists())

    def test_eval_is_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            labels, pairs_dir = write_labels(tmpdir)
            from anastylos_missing.eval import run_eval
            first = run_eval(labels, pairs_dir, tmpdir / "out_a")
            second = run_eval(labels, pairs_dir, tmpdir / "out_b")
            self.assertEqual(first, second)

    def test_eval_missing_labels_fails_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            from anastylos_missing.eval import run_eval
            with self.assertRaises(ValueError):
                run_eval(Path(tmp) / "gone.json", Path(tmp), Path(tmp) / "out")

    def test_cli_eval_reports_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            labels, pairs_dir = write_labels(tmpdir)
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = main(["eval", "--labels", str(labels),
                             "--pairs-dir", str(pairs_dir),
                             "--out", str(tmpdir / "eval_out")])
            self.assertEqual(code, 0)
            summary = json.loads(buffer.getvalue())
            self.assertEqual(summary["pairs"], 6)
            self.assertEqual(summary["recall"], 1.0)

    def test_cli_eval_missing_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = main(["eval", "--labels", str(Path(tmp) / "gone.json"),
                             "--pairs-dir", str(Path(tmp)), "--out", str(Path(tmp) / "out")])
            self.assertEqual(code, 1)


class AnnotateTest(unittest.TestCase):
    def test_annotate_side_by_side(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            original, current, _ = S.removed()
            opath, cpath = tmpdir / "o.png", tmpdir / "c.png"
            cv2.imwrite(str(opath), original)
            cv2.imwrite(str(cpath), current)
            from anastylos_missing.context import load_context
            context = load_context(write_context(tmpdir))
            result = run_comparison(original=str(opath), current=str(cpath), context=context)
            marked = annotate(result, original, current)
            self.assertEqual(marked.shape[0], original.shape[0])
            self.assertEqual(marked.shape[1], original.shape[1] * 2)

    def test_annotate_without_images(self):
        result = {"comparison_id": "cmp-x", "verdict": "unavailable",
                  "reasons": ["registration failed"], "regions": []}
        marked = annotate(result)
        self.assertEqual(marked.shape[2], 3)

    def test_annotate_reconciliation(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            original, current, _ = S.removed()
            opath, cpath = tmpdir / "o.png", tmpdir / "c.png"
            cv2.imwrite(str(opath), original)
            cv2.imwrite(str(cpath), current)
            from anastylos_missing.context import load_context
            context = load_context(write_context(tmpdir))
            result = run_comparison(original=str(opath), current=str(cpath), context=context)
            overlay = annotate(result, original, current)
            recon = run_reconciliation(result, [], {"source": None, "entries": 0})
            marked = annotate_reconciliation(result, overlay, recon)
            self.assertEqual(marked.shape, overlay.shape)
            self.assertTrue((marked != overlay).any())

    def test_annotate_handles_blank(self):
        image = np.full((200, 200, 3), 200, np.uint8)
        result = {"comparison_id": "cmp-x", "verdict": "unknown", "regions": []}
        marked = annotate(result, image, image)
        self.assertEqual(marked.shape[1], 400)


if __name__ == "__main__":
    unittest.main()
