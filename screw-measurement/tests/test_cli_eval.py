"""CLI, eval-harness, and overlay checks on a tiny synthetic set.

Proves the wiring (images in, bundles plus reports out), not
real-world accuracy.

Run from the screw-measurement directory:

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

from anastylos_screw_measure.annotate import annotate
from anastylos_screw_measure.cli import main
from anastylos_screw_measure.compare import compare_measurements
from anastylos_screw_measure.eval import run_eval
from anastylos_screw_measure.synthetic import PIXELS_PER_MM, render_side, render_top
from anastylos_screw_measure.vision import analyze


def write_calibration(directory: Path) -> Path:
    path = directory / "calib.json"
    path.write_text(json.dumps({"pixels_per_mm": PIXELS_PER_MM,
                                "pixels_per_mm_std": 0.02,
                                "frame": "surface_a3",
                                "provenance": "synthetic test"}), encoding="utf-8")
    return path


def write_set(directory: Path) -> Path:
    images = directory / "images"
    images.mkdir(parents=True)
    cases = [("top_round.png", "top", render_top(seed=1)),
             ("top_hex.png", "top", render_top(head="hex", rotation_deg=20.0, seed=2)),
             ("side_plain.png", "side", render_side(seed=1)),
             ("side_rot.png", "side", render_side(rotation_deg=25.0, seed=2))]
    entries = []
    for name, mode, (image, truth) in cases:
        cv2.imwrite(str(images / name), image)
        if mode == "top":
            key = ("diameter_mm" if truth["head_shape"] == "round"
                   else None)
            truth_mm = {"visible_head_diameter": truth["diameter_mm"]} \
                if key else {"visible_head_diameter": round(truth["across_flats_px"]
                                                            / PIXELS_PER_MM, 3)}
            truth_px = {"visible_head_diameter": truth["diameter_px"]} \
                if key else {"visible_head_diameter": truth["across_flats_px"]}
        else:
            truth_mm = {"visible_overall_length": truth["overall_length_mm"],
                        "visible_shaft_diameter": truth["shaft_diameter_mm"]}
            truth_px = {"visible_overall_length": truth["overall_length_px"],
                        "visible_shaft_diameter": truth["shaft_diameter_px"]}
        entries.append({"file": name, "mode": mode,
                        "truth_mm": truth_mm, "truth_px": truth_px})
    labels = directory / "labels.json"
    labels.write_text(json.dumps({"images": entries}), encoding="utf-8")
    return labels


class CliMeasureTest(unittest.TestCase):
    def test_measure_writes_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            cv2.imwrite(str(tmpdir / "crop.png"), render_top(seed=1)[0])
            calib = write_calibration(tmpdir)
            out = tmpdir / "out"
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["measure", "--image", str(tmpdir / "crop.png"),
                             "--mode", "top", "--calibration", str(calib),
                             "--out", str(out)])
            self.assertEqual(code, 0)
            bundle = json.loads((out / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(bundle["verdict"], "ok")
            self.assertEqual(bundle["mode"], "top")
            self.assertTrue(bundle["calibration"]["valid"])
            self.assertIn("annotated:annotated.png", bundle["evidence_refs"])
            self.assertTrue((out / "annotated.png").exists())

    def test_measure_side_requires_explicit_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            cv2.imwrite(str(tmpdir / "crop.png"), render_side(seed=1)[0])
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    main(["measure", "--image", str(tmpdir / "crop.png")])

    def test_measure_missing_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = main(["measure", "--image", str(Path(tmp) / "nope.png"),
                             "--mode", "top", "--out", str(Path(tmp) / "out")])
            self.assertEqual(code, 1)

    def test_measure_bad_roi(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            cv2.imwrite(str(tmpdir / "crop.png"), render_top(seed=1)[0])
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = main(["measure", "--image", str(tmpdir / "crop.png"),
                             "--mode", "top", "--roi", "300,300,200,200",
                             "--out", str(tmpdir / "out")])
            self.assertEqual(code, 2)

    def test_measure_invalid_calibration_stays_pixel_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            cv2.imwrite(str(tmpdir / "crop.png"), render_top(seed=1)[0])
            bad = tmpdir / "bad.json"
            bad.write_text(json.dumps({"pixels_per_mm": -3}), encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(err):
                code = main(["measure", "--image", str(tmpdir / "crop.png"),
                             "--mode", "top", "--calibration", str(bad),
                             "--out", str(tmpdir / "out")])
            self.assertEqual(code, 0)
            self.assertIn("pixel-only", err.getvalue())
            bundle = json.loads((tmpdir / "out" / "result.json").read_text(encoding="utf-8"))
            self.assertFalse(bundle["calibration"]["valid"])
            head = next(m for m in bundle["measurements"]
                        if m["name"] == "visible_head_diameter")
            self.assertIsNotNone(head["value_px"])
            self.assertIsNone(head["value_mm"])

    def test_measure_missing_calibration_file_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            cv2.imwrite(str(tmpdir / "crop.png"), render_top(seed=1)[0])
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    main(["measure", "--image", str(tmpdir / "crop.png"),
                          "--mode", "top", "--calibration", str(tmpdir / "gone.json"),
                          "--out", str(tmpdir / "out")])
            self.assertEqual(raised.exception.code, 1)


class CliCompareTest(unittest.TestCase):
    def test_compare_cli_reports_consistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            calib = write_calibration(tmpdir)
            from anastylos_screw_measure.calibration import load_calibration
            cal = load_calibration(calib)
            left = analyze(render_top(seed=1)[0], mode="top", calibration=cal)
            right = analyze(render_top(seed=2)[0], mode="top", calibration=cal)
            (tmpdir / "a.json").write_text(json.dumps(left), encoding="utf-8")
            (tmpdir / "b.json").write_text(json.dumps(right), encoding="utf-8")
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = main(["compare", "--a", str(tmpdir / "a.json"),
                             "--b", str(tmpdir / "b.json"),
                             "--out", str(tmpdir / "cmp")])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(buffer.getvalue())["verdict"], "consistent")
            self.assertTrue((tmpdir / "cmp" / "comparison.json").exists())

    def test_compare_missing_input_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = main(["compare", "--a", str(Path(tmp) / "a.json"),
                             "--b", str(Path(tmp) / "b.json"),
                             "--out", str(Path(tmp) / "cmp")])
            self.assertEqual(code, 1)

    def test_compare_function_flags_frame_mismatch(self):
        left = analyze(render_top(seed=1)[0], mode="top",
                       calibration={"valid": True, "pixels_per_mm": 10.0,
                                    "pixels_per_mm_std": 0.0, "frame": "surface_a3",
                                    "provenance": "a", "reasons": []})
        right = analyze(render_top(seed=1)[0], mode="top",
                        calibration={"valid": True, "pixels_per_mm": 10.0,
                                     "pixels_per_mm_std": 0.0, "frame": "surface_a4",
                                     "provenance": "b", "reasons": []})
        report = compare_measurements(left, right)
        self.assertEqual(report["verdict"], "consistent")
        self.assertTrue(any("frames differ" in r for r in report["reasons"]))


class EvalTest(unittest.TestCase):
    def test_eval_scores_small_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            labels = write_set(tmpdir)
            calib = write_calibration(tmpdir)
            from anastylos_screw_measure.calibration import load_calibration
            report = run_eval(labels, tmpdir / "images", tmpdir / "eval_out",
                              load_calibration(calib))
            self.assertEqual(report["images"], 4)
            self.assertTrue(report["calibration_valid"])
            self.assertEqual(report["verdicts"]["ok"], 4)
            head = report["mm_abs_err"]["visible_head_diameter"]
            self.assertLess(head["max"], 0.5)
            length = report["mm_abs_err"]["visible_overall_length"]
            self.assertLess(length["max"], 0.6)
            self.assertTrue((tmpdir / "eval_out" / "eval_report.json").exists())
            self.assertTrue((tmpdir / "eval_out" / "side_plain_annotated.png").exists())
            self.assertTrue((tmpdir / "eval_out" / "side_plain_result.json").exists())

    def test_eval_without_calibration_marks_mm_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            labels = write_set(tmpdir)
            report = run_eval(labels, tmpdir / "images", tmpdir / "eval_out", None)
            self.assertFalse(report["calibration_valid"])
            self.assertEqual(report["mm_abs_err"], {})
            first = report["rows"][0]["errors"]["visible_head_diameter"]
            self.assertEqual(first["abs_err_mm"], "mm_unavailable")
            self.assertLess(first["abs_err_px"], 5.0)

    def test_eval_is_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            labels = write_set(tmpdir)
            first = run_eval(labels, tmpdir / "images", tmpdir / "out_a", None)
            second = run_eval(labels, tmpdir / "images", tmpdir / "out_b", None)
            self.assertEqual(first, second)

    def test_eval_missing_image_fails_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            labels = tmpdir / "labels.json"
            labels.write_text(json.dumps({"images": [{"file": "gone.png", "mode": "top",
                                                      "truth_mm": {}}]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                run_eval(labels, tmpdir, tmpdir / "out", None)

    def test_cli_eval_reports_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            labels = write_set(tmpdir)
            calib = write_calibration(tmpdir)
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = main(["eval", "--labels", str(labels),
                             "--images-dir", str(tmpdir / "images"),
                             "--calibration", str(calib),
                             "--out", str(tmpdir / "eval_out")])
            self.assertEqual(code, 0)
            summary = json.loads(buffer.getvalue())
            self.assertEqual(summary["images"], 4)
            self.assertEqual(summary["verdicts"]["ok"], 4)


class AnnotateTest(unittest.TestCase):
    def test_annotate_marks_top_and_side(self):
        top = analyze(render_top(seed=1)[0], mode="top")
        image = render_top(seed=1)[0]
        marked = annotate(image, top)
        self.assertEqual(marked.shape, image.shape)
        self.assertTrue((marked != image).any())
        side_image = render_side(seed=1)[0]
        marked_side = annotate(side_image, analyze(side_image, mode="side"))
        self.assertTrue((marked_side != side_image).any())

    def test_annotate_handles_unknown(self):
        image = np.full((400, 400, 3), 200, np.uint8)
        marked = annotate(image, analyze(image, mode="top"))
        self.assertEqual(marked.shape, image.shape)


if __name__ == "__main__":
    unittest.main()
