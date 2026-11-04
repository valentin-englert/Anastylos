"""Side-view measurement checks on deterministic synthetic renders.

Fixtures exercise the pipeline only; they prove nothing about
real-hardware accuracy.

Run from the screw-measurement directory:

    ..\\.venv\\Scripts\\python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np

from anastylos_screw_measure.calibration import blank
from anastylos_screw_measure.synthetic import PIXELS_PER_MM, blank as blank_image, render_side
from anastylos_screw_measure.vision import analyze

CAL = {"valid": True, "pixels_per_mm": PIXELS_PER_MM, "pixels_per_mm_std": 0.02,
       "frame": "surface_a3", "provenance": "synthetic test", "reasons": []}


def entry(result, name):
    return next(m for m in result["measurements"] if m["name"] == name)


class NominalTest(unittest.TestCase):
    def setUp(self):
        image, self.truth = render_side(seed=1)
        self.result = analyze(image, mode="side", calibration=CAL)

    def test_verdict_ok(self):
        self.assertEqual(self.result["verdict"], "ok")

    def test_overall_length(self):
        got = entry(self.result, "visible_overall_length")
        self.assertEqual(got["status"], "ok")
        self.assertAlmostEqual(got["value_px"], self.truth["overall_length_px"], delta=6.0)
        lo, hi = got["mm_interval"]
        self.assertLessEqual(lo, self.truth["overall_length_mm"])
        self.assertGreaterEqual(hi, self.truth["overall_length_mm"])

    def test_shaft_dimensions(self):
        length = entry(self.result, "visible_shaft_length")
        diameter = entry(self.result, "visible_shaft_diameter")
        head = entry(self.result, "visible_head_length")
        self.assertEqual(length["status"], "ok")
        self.assertEqual(diameter["status"], "ok")
        self.assertAlmostEqual(length["value_px"], self.truth["shaft_length_px"], delta=8.0)
        self.assertAlmostEqual(diameter["value_px"], self.truth["shaft_diameter_px"], delta=6.0)
        self.assertAlmostEqual(head["value_px"], self.truth["head_length_px"], delta=8.0)

    def test_head_boundary_observed(self):
        self.assertIsNotNone(self.result["geometry"]["split_bin"])
        self.assertIsNotNone(self.result["geometry"]["split_xy"])
        self.assertGreaterEqual(self.result["geometry"]["step_ratio"], 1.25)

    def test_result_is_json_serializable(self):
        json.dumps(self.result)

    def test_never_estimates_forbidden_quantities(self):
        for key in ("nominal_specification", "thread_pitch", "thread_type", "drive_size"):
            self.assertIn(key, self.result["not_estimated"])


class RobustnessTest(unittest.TestCase):
    def test_rotation_stays_ok(self):
        image, truth = render_side(rotation_deg=25.0, seed=2)
        result = analyze(image, mode="side", calibration=CAL)
        self.assertEqual(result["verdict"], "ok")
        self.assertAlmostEqual(entry(result, "visible_overall_length")["value_px"],
                               truth["overall_length_px"], delta=8.0)
        self.assertAlmostEqual(entry(result, "visible_shaft_diameter")["value_px"],
                               truth["shaft_diameter_px"], delta=6.0)

    def test_small_screw_stays_ok(self):
        image, truth = render_side(length_px=180.0, shaft_d_px=40.0, seed=3)
        result = analyze(image, mode="side", calibration=CAL)
        self.assertEqual(result["verdict"], "ok")
        self.assertAlmostEqual(entry(result, "visible_overall_length")["value_px"],
                               truth["overall_length_px"], delta=6.0)

    def test_shadow_stays_ok(self):
        image, truth = render_side(shadow=0.6, seed=2)
        result = analyze(image, mode="side", calibration=CAL)
        self.assertEqual(result["verdict"], "ok")
        self.assertAlmostEqual(entry(result, "visible_overall_length")["value_px"],
                               truth["overall_length_px"], delta=6.0)

    def test_mild_glare_keeps_length(self):
        image, truth = render_side(glare=True, seed=4)
        result = analyze(image, mode="side", calibration=CAL)
        self.assertIn(result["verdict"], ("ok", "ambiguous"))
        got = entry(result, "visible_overall_length")
        self.assertIn(got["status"], ("ok", "ambiguous"))
        self.assertAlmostEqual(got["value_px"], truth["overall_length_px"], delta=10.0)

    def test_touching_object_stays_measured(self):
        image, truth = render_side(touching=True, seed=5)
        result = analyze(image, mode="side", calibration=CAL)
        self.assertIn(result["verdict"], ("ok", "ambiguous"))
        self.assertAlmostEqual(entry(result, "visible_overall_length")["value_px"],
                               truth["overall_length_px"], delta=10.0)
        self.assertAlmostEqual(entry(result, "visible_shaft_diameter")["value_px"],
                               truth["shaft_diameter_px"], delta=8.0)

    def test_mild_tilt_matches_warped_truth(self):
        image, truth = render_side(tilt=0.2, seed=1)
        result = analyze(image, mode="side", calibration=CAL)
        self.assertEqual(result["verdict"], "ok")
        self.assertAlmostEqual(entry(result, "visible_overall_length")["value_px"],
                               truth["warped_overall_length_px"], delta=8.0)

    def test_severe_tilt_measures_visible_geometry(self):
        # Uniform foreshortening is invisible in one view: pixels stay
        # honest against the warped image; millimetres need the true plane.
        image, truth = render_side(tilt=1.0, seed=1)
        result = analyze(image, mode="side", calibration=CAL)
        got = entry(result, "visible_overall_length")
        self.assertAlmostEqual(got["value_px"], truth["warped_overall_length_px"], delta=10.0)
        self.assertAlmostEqual(entry(result, "visible_shaft_length")["value_px"],
                               truth["warped_shaft_length_px"], delta=10.0)

    def test_mild_taper_stays_ok(self):
        image, truth = render_side(taper=0.15, seed=1)
        result = analyze(image, mode="side", calibration=CAL)
        self.assertEqual(result["verdict"], "ok")
        self.assertAlmostEqual(entry(result, "visible_shaft_diameter")["value_px"],
                               truth["shaft_diameter_px"], delta=6.0)

    def test_strong_taper_flags_diameter(self):
        image, _ = render_side(taper=0.40, seed=1)
        result = analyze(image, mode="side", calibration=CAL)
        diameter = entry(result, "visible_shaft_diameter")
        self.assertNotEqual(diameter["status"], "ok")
        self.assertTrue(any("converge" in r for r in diameter["reasons"]))

    def test_occlusion_is_never_confident(self):
        for level in (0.5, 1.0):
            with self.subTest(level=level):
                image, _ = render_side(occlude=level, seed=6)
                result = analyze(image, mode="side", calibration=CAL)
                self.assertIn(result["verdict"], ("ambiguous", "unknown"))
                shaft = entry(result, "visible_shaft_diameter")
                self.assertIn(shaft["status"], ("ambiguous", "unknown"))

    def test_truncated_crop_is_partial(self):
        image, _ = render_side(seed=1)
        result = analyze(image, mode="side", roi=(0, 0, 250, 400), calibration=CAL)
        self.assertEqual(result["verdict"], "ambiguous")
        got = entry(result, "visible_overall_length")
        self.assertTrue(got["partial"])
        self.assertTrue(any("border" in r for r in result["reasons"]))


class CalibrationHandlingTest(unittest.TestCase):
    def test_pixel_only_without_calibration(self):
        image, truth = render_side(seed=1)
        result = analyze(image, mode="side", calibration=None)
        self.assertEqual(result["verdict"], "ok")
        got = entry(result, "visible_overall_length")
        self.assertAlmostEqual(got["value_px"], truth["overall_length_px"], delta=6.0)
        self.assertIsNone(got["value_mm"])

    def test_invalid_calibration_disables_millimetres(self):
        image, _ = render_side(seed=1)
        result = analyze(image, mode="side", calibration=blank("no scale"))
        for name in ("visible_overall_length", "visible_shaft_diameter"):
            got = entry(result, name)
            if got["status"] not in ("unknown", "unsupported"):
                self.assertIsNone(got["value_mm"])
                self.assertTrue(got["reasons"])


class NegativeTest(unittest.TestCase):
    def test_blank_is_unknown(self):
        result = analyze(blank_image(), mode="side", calibration=CAL)
        self.assertEqual(result["verdict"], "unknown")
        self.assertIsNone(result["geometry"])

    def test_outside_roi_raises(self):
        image, _ = render_side(seed=1)
        with self.assertRaises(ValueError):
            analyze(image, mode="side", roi=(300, 300, 200, 200), calibration=CAL)

    def test_roi_offsets_geometry(self):
        canvas = np.full((600, 600, 3), 200, np.uint8)
        canvas[100:500, 100:500] = render_side(seed=1)[0]
        result = analyze(canvas, mode="side", roi=(100, 100, 400, 400), calibration=CAL)
        self.assertEqual(result["verdict"], "ok")
        for x, _ in result["geometry"]["endpoints_xy"]:
            self.assertGreaterEqual(x, 100)

    def test_pipeline_is_deterministic(self):
        image, _ = render_side(rotation_deg=25.0, seed=9)
        self.assertEqual(analyze(image, mode="side", calibration=CAL),
                         analyze(image.copy(), mode="side", calibration=CAL))

    def test_custom_segmenter_is_used(self):
        seen = {}

        def segmenter(gray, context):
            seen["mode"] = context["mode"]
            return None

        image, _ = render_side(seed=1)
        result = analyze(image, mode="side", calibration=CAL, segmenter=segmenter)
        self.assertEqual(seen["mode"], "side")
        self.assertEqual(result["verdict"], "ok")


if __name__ == "__main__":
    unittest.main()
