"""Polarity vision checks on deterministic synthetic renders. Needs the venv.

Fixtures exercise the pipeline only; they prove nothing about
real-hardware accuracy.

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
import numpy as np

from anastylos_polarity.annotate import annotate
from anastylos_polarity.cli import main
from anastylos_polarity.synthetic import expected_angle, render
from anastylos_polarity.vision import analyze, default_detector

HINTS = {"diode": "diode",
         "aluminum_electrolytic": "aluminum_electrolytic",
         "tantalum": "tantalum", "ic": "ic", "connector": "connector"}
NOMINAL_MARKER = {"diode": "band", "aluminum_electrolytic": "stripe",
                  "tantalum": "band", "ic": "notch", "connector": "key"}
NOMINAL_READING = {"diode": "cathode_at_marker",
                   "aluminum_electrolytic": "negative_at_marker",
                   "tantalum": "anode_at_marker",
                   "ic": "pin1_left_of_notch",
                   "connector": "keyed_side_at_marker"}


def gap(left: float, right: float) -> float:
    return abs((left - right + 180.0) % 360.0 - 180.0)


def top(result):
    return result["candidates"][0] if result["candidates"] else None


def marker_angles(result):
    return {item["marker_type"]: item["orientation_angle_deg"]
            for item in result["markers"]}


class NominalTest(unittest.TestCase):
    def test_each_family_reads_ok_with_hint(self):
        for kind, hint in HINTS.items():
            with self.subTest(kind=kind):
                result = analyze(render(kind, seed=3), family=hint)
                self.assertEqual(result["status"], "ok")
                candidate = top(result)
                self.assertEqual(candidate["marker_type"],
                                 NOMINAL_MARKER[kind])
                self.assertEqual(candidate["interpretation"],
                                 NOMINAL_READING[kind])
                for marker, expected in (
                        ("band", 0.0), ("stripe", 0.0), ("notch", 270.0),
                        ("dot", 225.0), ("key", 321.9)):
                    if marker in marker_angles(result):
                        self.assertLess(
                            gap(marker_angles(result)[marker], expected), 6.0,
                            f"{kind} {marker} angle")

    def test_ic_dot_and_notch_agree_without_hint(self):
        result = analyze(render("ic", seed=3))
        self.assertEqual(result["status"], "ok")
        kinds = {item["marker_type"] for item in result["markers"]}
        self.assertEqual(kinds, {"dot", "notch"})

    def test_topmark_stays_ambiguous_with_rivals(self):
        result = analyze(render("ic_topmark", seed=3))
        self.assertEqual(result["status"], "ambiguous")
        readings = [item["interpretation"] for item in result["candidates"]]
        self.assertIn("print_upright_0deg", readings)
        self.assertIn("print_upright_180deg", readings)

    def test_result_is_json_serializable(self):
        result = analyze(render("diode", seed=3), family="diode",
                         reference={"kind": "silkscreen", "angle_deg": 90.0},
                         frame_metadata={"pixels_per_mm": 4.0})
        json.dumps(result)

    def test_marker_geometry_uses_source_frame(self):
        result = analyze(render("tantalum", seed=3), family="tantalum")
        marker = result["markers"][0]
        x, y, w, h = marker["bbox_xywh"]
        self.assertTrue(0 <= x < x + w <= 400)
        self.assertTrue(0 <= y < y + h <= 400)
        dx, dy = marker["orientation_vector_xy"]
        self.assertAlmostEqual(dx * dx + dy * dy, 1.0, places=3)
        self.assertIn("origin top-left", result["coordinate_convention"])


class RotationScaleTest(unittest.TestCase):
    def test_rotations_keep_reading_and_angle(self):
        cases = [("diode", "band", 15.0), ("diode", "band", 90.0),
                 ("aluminum_electrolytic", "stripe", 45.0),
                 ("tantalum", "band", 135.0), ("ic", "notch", 33.0),
                 ("ic", "dot", 200.0), ("connector", "key", 90.0),
                 ("connector", "key", 200.0)]
        for kind, marker, deg in cases:
            with self.subTest(kind=kind, deg=deg):
                result = analyze(render(kind, rotation_deg=deg, seed=11),
                                 family=HINTS[kind])
                self.assertEqual(result["status"], "ok")
                self.assertIn(marker, marker_angles(result))
                self.assertLess(gap(marker_angles(result)[marker],
                                    expected_angle(kind, marker, deg)), 8.0)

    def test_scale_change_stays_ok(self):
        small = cv2.resize(render("tantalum", seed=3), None, fx=0.7, fy=0.7)
        result = analyze(small, family="tantalum")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(top(result)["interpretation"], "anode_at_marker")
        big = cv2.resize(render("ic", seed=3), None, fx=1.3, fy=1.3)
        result = analyze(big, family="ic")
        self.assertEqual(result["status"], "ok")


class LightingTest(unittest.TestCase):
    def test_brightness_shift_stays_ok(self):
        for kind, shift in (("diode", 40), ("connector", -40),
                            ("tantalum", 30)):
            with self.subTest(kind=kind, shift=shift):
                result = analyze(render(kind, brightness=shift, seed=2),
                                 family=HINTS[kind])
                self.assertEqual(result["status"], "ok")

    def test_shadow_ramp_stays_ok(self):
        for kind in ("diode", "ic"):
            with self.subTest(kind=kind):
                result = analyze(render(kind, shadow=0.3, seed=2),
                                 family=HINTS[kind])
                self.assertEqual(result["status"], "ok")

    def test_mild_glare_stays_ok(self):
        for kind in ("diode", "ic"):
            with self.subTest(kind=kind):
                result = analyze(render(kind, glare=True, seed=4),
                                 family=HINTS[kind])
                self.assertEqual(result["status"], "ok")
                self.assertEqual(top(result)["interpretation"],
                                 NOMINAL_READING[kind])

    def test_glare_reports_quality_flag(self):
        plain = analyze(render("diode", seed=4), family="diode")
        glared = analyze(render("diode", glare=True, seed=4), family="diode")
        self.assertGreater(glared["quality"]["glare_fraction"],
                           plain["quality"]["glare_fraction"])


class DegradedTest(unittest.TestCase):
    def test_partial_occlusion_keeps_truth_on_top(self):
        result = analyze(render("diode", occlude=0.3, seed=6), family="diode")
        self.assertIn(result["status"], ("ok", "ambiguous"))
        self.assertEqual(top(result)["interpretation"], "cathode_at_marker")
        self.assertLess(gap(top(result)["orientation_angle_deg"], 0.0), 30.0)

    def test_heavy_occlusion_never_confident(self):
        for kind in ("diode", "aluminum_electrolytic"):
            with self.subTest(kind=kind):
                result = analyze(render(kind, occlude=1.0),
                                 family=HINTS[kind])
                self.assertIn(result["status"], ("ambiguous", "unknown"))

    def test_damaged_markers_never_confident(self):
        for kind in ("diode", "aluminum_electrolytic", "tantalum", "ic",
                     "connector"):
            with self.subTest(kind=kind):
                for seed in (7, 8):
                    result = analyze(render(kind, damage=0.65, seed=seed),
                                     family=HINTS[kind])
                    self.assertIn(result["status"], ("ambiguous", "unknown"),
                                    f"{kind} seed {seed} overconfident")

    def test_light_damage_stays_ok(self):
        result = analyze(render("diode", damage=0.3, seed=7), family="diode")
        self.assertEqual(result["status"], "ok")


class AmbiguityTest(unittest.TestCase):
    def test_stripe_never_equates_polarity_without_family(self):
        result = analyze(render("aluminum_electrolytic", seed=3))
        self.assertEqual(result["status"], "ambiguous")
        readings = {item["interpretation"] for item in result["candidates"]}
        self.assertIn("negative_at_marker", readings)
        self.assertIn("positive_at_marker", readings)

    def test_band_splits_diode_and_tantalum_without_hint(self):
        for kind in ("diode", "tantalum"):
            with self.subTest(kind=kind):
                result = analyze(render(kind, seed=3))
                self.assertEqual(result["status"], "ambiguous")
                readings = {item["interpretation"]
                            for item in result["candidates"]}
                self.assertEqual(readings, {"cathode_at_marker",
                                            "anode_at_marker"})

    def test_key_splits_keying_and_damage_without_hint(self):
        result = analyze(render("connector", seed=3))
        self.assertEqual(result["status"], "ambiguous")
        readings = {item["interpretation"] for item in result["candidates"]}
        self.assertIn("keyed_side_at_marker", readings)
        self.assertIn("mechanical_damage", readings)

    def test_wrong_family_hint_marks_marker_unexpected(self):
        result = analyze(render("diode", seed=3), family="ic")
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(top(result)["interpretation"],
                         "unexpected_marker_kind")
        self.assertLessEqual(top(result)["confidence"], 0.50)


class AbsentMarkerTest(unittest.TestCase):
    def test_blank_body_is_unknown_with_body(self):
        result = analyze(render("blank", seed=3))
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["candidates"], [])
        self.assertIsNotNone(result["body"])

    def test_flat_image_is_unknown_without_body(self):
        result = analyze(np.full((400, 400, 3), 200, np.uint8))
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["body"])

    def test_noise_is_unknown(self):
        rng = np.random.RandomState(0)
        noise = rng.normal(200, 3, (400, 400, 3)).clip(0, 255).astype(np.uint8)
        self.assertEqual(analyze(noise)["status"], "unknown")

    def test_background_bar_is_not_a_marker(self):
        image = render("blank", seed=3)
        cv2.rectangle(image, (10, 10), (60, 390), (230,) * 3, -1)
        result = analyze(image)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["candidates"], [])

    def test_tiny_crop_is_unavailable(self):
        result = analyze(np.full((16, 16, 3), 200, np.uint8))
        self.assertEqual(result["status"], "unavailable")


class GeometryTest(unittest.TestCase):
    def test_chamfer_corner_is_a_key(self):
        image = np.full((400, 400, 3), 190, np.uint8)
        cv2.rectangle(image, (70, 130), (330, 270), (30,) * 3, -1)
        cv2.fillPoly(image, [np.array([(70, 130), (104, 130), (70, 164)])],
                     (190,) * 3)
        result = analyze(image, family="connector")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(top(result)["marker_type"], "key")

    def test_roi_offsets_boxes(self):
        canvas = np.full((600, 600, 3), 190, np.uint8)
        canvas[100:500, 100:500] = render("diode", seed=3)
        result = analyze(canvas, roi=(100, 100, 400, 400), family="diode")
        self.assertEqual(result["status"], "ok")
        x, y, _, _ = result["markers"][0]["bbox_xywh"]
        self.assertGreaterEqual(x, 100)
        self.assertGreaterEqual(y, 100)
        ccx, ccy = result["markers"][0]["centroid_xy"]
        self.assertAlmostEqual(ccx, 385, delta=8)
        self.assertAlmostEqual(ccy, 300, delta=8)

    def test_reject_outside_roi(self):
        with self.assertRaises(ValueError):
            analyze(render("diode", seed=3), roi=(300, 300, 200, 200))

    def test_reject_unknown_family(self):
        with self.assertRaises(ValueError):
            analyze(render("diode", seed=3), family="resistor")

    def test_reject_bad_reference(self):
        for bad in ({"kind": "", "angle_deg": 1.0},
                    {"kind": "silkscreen"},
                    {"kind": "x", "angle_deg": float("inf")}, "nope"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    analyze(render("diode", seed=3), reference=bad)


class InstallationCalibrationTest(unittest.TestCase):
    def test_no_reference_claims_no_installation(self):
        result = analyze(render("diode", seed=3), family="diode")
        self.assertFalse(result["installation"]["claimed"])
        self.assertIn("no reference observation",
                      result["installation"]["explanation"])

    def test_reference_claims_relative_orientation(self):
        result = analyze(render("diode", seed=3), family="diode",
                         reference={"kind": "silkscreen", "angle_deg": 90.0})
        installation = result["installation"]
        self.assertTrue(installation["claimed"])
        self.assertEqual(installation["reference_kind"], "silkscreen")
        self.assertAlmostEqual(installation["relative_angle_deg"], 270.0,
                               delta=4.0)

    def test_valid_calibration_converts_to_mm(self):
        result = analyze(render("diode", seed=3), family="diode",
                         frame_metadata={"pixels_per_mm": 4.0})
        geometry = result["geometry_mm"]
        self.assertIsNotNone(geometry)
        self.assertEqual(geometry["pixels_per_mm"], 4.0)
        ccx, _ = result["markers"][0]["centroid_xy"]
        mmx, _ = geometry["markers"][0]["centroid_mm"]
        self.assertAlmostEqual(mmx, ccx / 4.0, places=2)
        self.assertIsNone(result["calibration_error"])

    def test_invalid_calibration_disables_mm_not_verdict(self):
        for bad in ({"pixels_per_mm": 0}, {"pixels_per_mm": -2.0},
                    {"pixels_per_mm": "x"},
                    {"pixels_per_mm": float("nan")},
                    {"pixels_per_mm": 9999.0}, {}, [1, 2]):
            with self.subTest(bad=bad):
                result = analyze(render("diode", seed=3), family="diode",
                                 frame_metadata=bad)
                self.assertEqual(result["status"], "ok")
                self.assertIsNone(result["geometry_mm"])
                self.assertIsNotNone(result["calibration_error"])


class AdapterTest(unittest.TestCase):
    def test_custom_detector_is_used(self):
        seen = {}

        def detector(gray, body, mask):
            seen["body"] = body
            return default_detector(gray, body, mask)

        result = analyze(render("ic", seed=3), family="ic",
                         detector=detector)
        self.assertIn("body", seen)
        self.assertEqual(result["status"], "ok")

    def test_missing_detector_yields_unavailable(self):
        result = analyze(render("ic", seed=3), detector=lambda *args: None)
        self.assertEqual(result["status"], "unavailable")

    def test_custom_ranker_replaces_verdict(self):
        def ranker(markers, body, quality, family, reference):
            return {"status": "unknown", "reason": "stub veto",
                    "candidates": [],
                    "installation": {"claimed": False,
                                     "explanation": "vetoed"},
                    "explanation": "vetoed"}

        result = analyze(render("ic", seed=3), family="ic", ranker=ranker)
        self.assertEqual(result["status"], "unknown")
        self.assertIsNotNone(result["markers"])

    def test_pipeline_is_deterministic(self):
        image = render("connector", rotation_deg=17.0, seed=9)
        self.assertEqual(analyze(image, family="connector"),
                         analyze(image.copy(), family="connector"))


class AnnotateCliTest(unittest.TestCase):
    def test_annotate_marks_markers_and_verdict(self):
        image = render("diode", seed=3)
        marked = annotate(image, analyze(image, family="diode"))
        self.assertEqual(marked.shape, image.shape)
        self.assertTrue((marked != image).any())

    def test_annotate_handles_unknown_and_unavailable(self):
        flat = np.full((400, 400, 3), 200, np.uint8)
        self.assertEqual(annotate(flat, analyze(flat)).shape, flat.shape)
        tiny = np.full((16, 16, 3), 200, np.uint8)
        self.assertEqual(annotate(tiny, analyze(tiny)).shape, tiny.shape)

    def test_cli_read_writes_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            cv2.imwrite(str(tmpdir / "crop.png"), render("diode", seed=3))
            out = tmpdir / "out"
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["read", "--image", str(tmpdir / "crop.png"),
                             "--family", "diode", "--reference-kind",
                             "silkscreen", "--reference-angle", "90",
                             "--frame-metadata", '{"pixels_per_mm": 4.0}',
                             "--out", str(out)])
            self.assertEqual(code, 0)
            bundle = json.loads((out / "result.json").read_text(
                encoding="utf-8"))
            self.assertEqual(bundle["status"], "ok")
            self.assertTrue(bundle["installation"]["claimed"])
            self.assertIsNotNone(bundle["geometry_mm"])
            self.assertTrue((out / "annotated.png").exists())

    def test_cli_read_missing_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = main(["read", "--image", str(Path(tmp) / "nope.png"),
                             "--out", str(Path(tmp) / "out")])
            self.assertEqual(code, 1)

    def test_cli_read_rejects_bad_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            cv2.imwrite(str(tmpdir / "crop.png"), render("diode", seed=3))
            cases = [
                ["read", "--image", str(tmpdir / "crop.png"),
                 "--roi", "300,300,200,200", "--out", str(tmpdir / "o1")],
                ["read", "--image", str(tmpdir / "crop.png"),
                 "--family", "resistor", "--out", str(tmpdir / "o2")],
                ["read", "--image", str(tmpdir / "crop.png"),
                 "--reference-kind", "silkscreen",
                 "--out", str(tmpdir / "o3")],
                ["read", "--image", str(tmpdir / "crop.png"),
                 "--frame-metadata", "nope", "--out", str(tmpdir / "o4")],
            ]
            for argv in cases:
                with self.subTest(argv=argv):
                    with contextlib.redirect_stdout(io.StringIO()), \
                            contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(main(argv), 2)


if __name__ == "__main__":
    unittest.main()
