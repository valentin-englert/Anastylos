"""Top-view measurement checks on deterministic synthetic renders.

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
from anastylos_screw_measure.compare import compare_measurements
from anastylos_screw_measure.synthetic import PIXELS_PER_MM, blank as blank_image, render_top
from anastylos_screw_measure.vision import analyze

CAL = {"valid": True, "pixels_per_mm": PIXELS_PER_MM, "pixels_per_mm_std": 0.02,
       "frame": "surface_a3", "provenance": "synthetic test", "reasons": []}


def diameter(result):
    return next(m for m in result["measurements"] if m["name"] == "visible_head_diameter")


class NominalTest(unittest.TestCase):
    def test_nominal_diameter_is_ok_and_covers_truth(self):
        image, truth = render_top(seed=1)
        result = analyze(image, mode="top", calibration=CAL)
        entry = diameter(result)
        self.assertEqual(result["verdict"], "ok")
        self.assertEqual(entry["status"], "ok")
        self.assertAlmostEqual(entry["value_px"], truth["diameter_px"], delta=4.0)
        self.assertAlmostEqual(entry["value_mm"], truth["diameter_mm"], delta=0.4)
        lo, hi = entry["mm_interval"]
        self.assertLessEqual(lo, truth["diameter_mm"])
        self.assertGreaterEqual(hi, truth["diameter_mm"])

    def test_scales_stay_ok(self):
        for size in (120.0, 140.0, 260.0):
            with self.subTest(size=size):
                image, truth = render_top(diameter_px=size, seed=3)
                result = analyze(image, mode="top", calibration=CAL)
                self.assertEqual(result["verdict"], "ok")
                self.assertAlmostEqual(diameter(result)["value_px"],
                                       truth["diameter_px"], delta=4.0)

    def test_hex_reports_shape_and_across_flats(self):
        image, truth = render_top(head="hex", rotation_deg=20.0, seed=2)
        result = analyze(image, mode="top", calibration=CAL)
        self.assertEqual(result["verdict"], "ok")
        self.assertEqual(result["geometry"]["head_shape"], "hex")
        self.assertAlmostEqual(diameter(result)["value_px"],
                               truth["across_flats_px"], delta=5.0)

    def test_hex_rotations_keep_shape(self):
        for deg in [float(d) for d in range(0, 60, 5)]:
            with self.subTest(deg=deg):
                image, truth = render_top(head="hex", rotation_deg=deg, seed=11)
                result = analyze(image, mode="top", calibration=CAL)
                self.assertEqual(result["geometry"]["head_shape"], "hex")
                self.assertEqual(result["verdict"], "ok")
                self.assertAlmostEqual(diameter(result)["value_px"],
                                       truth["across_flats_px"], delta=6.0)

    def test_center_matches_truth(self):
        image, truth = render_top(seed=1)
        result = analyze(image, mode="top", calibration=CAL)
        cx, cy = result["geometry"]["center_xy"]
        self.assertAlmostEqual(cx, truth["center_xy"][0], delta=5.0)
        self.assertAlmostEqual(cy, truth["center_xy"][1], delta=5.0)

    def test_never_estimates_forbidden_quantities(self):
        image, _ = render_top(seed=1)
        result = analyze(image, mode="top", calibration=CAL)
        for key in ("nominal_specification", "thread_pitch", "thread_type", "drive_size"):
            self.assertIn(key, result["not_estimated"])
        shaft = next(m for m in result["measurements"]
                     if m["name"] == "visible_shaft_length")
        self.assertEqual(shaft["status"], "unsupported")
        self.assertIsNone(shaft["value_px"])
        self.assertIsNone(shaft["value_mm"])

    def test_result_is_json_serializable(self):
        image, _ = render_top(seed=1)
        json.dumps(analyze(image, mode="top", calibration=CAL))


class DegradedTest(unittest.TestCase):
    def test_mild_tilt_stays_ok_near_warped_truth(self):
        image, truth = render_top(tilt=0.2, seed=1)
        result = analyze(image, mode="top", calibration=CAL)
        warped_mean = sum(truth["warped_axes_px"]) / 2
        self.assertEqual(result["verdict"], "ok")
        self.assertAlmostEqual(diameter(result)["value_px"], warped_mean, delta=6.0)

    def test_severe_tilt_is_never_confident_and_spans_axes(self):
        image, truth = render_top(tilt=1.0, seed=1)
        result = analyze(image, mode="top", calibration=CAL)
        self.assertIn(result["verdict"], ("ambiguous", "unknown"))
        entry = diameter(result)
        if entry["value_px"] is not None:
            minor, major = truth["warped_axes_px"]
            measured = result["geometry"]["ellipse_axes_px"]
            self.assertAlmostEqual(measured[0], minor, delta=8.0)
            self.assertAlmostEqual(measured[1], major, delta=8.0)
            if entry["mm_interval"]:
                self.assertLessEqual(entry["mm_interval"][0], major / PIXELS_PER_MM)
                self.assertGreaterEqual(entry["mm_interval"][1], minor / PIXELS_PER_MM)

    def test_glare_and_shadow_stay_ok(self):
        for kwargs in ({"glare": True}, {"shadow": 0.6}):
            with self.subTest(kwargs=kwargs):
                image, truth = render_top(seed=4, **kwargs)
                result = analyze(image, mode="top", calibration=CAL)
                self.assertEqual(result["verdict"], "ok")
                self.assertAlmostEqual(diameter(result)["value_px"],
                                       truth["diameter_px"], delta=4.0)

    def test_touching_object_stays_accurate(self):
        image, truth = render_top(touching=True, seed=5)
        result = analyze(image, mode="top", calibration=CAL)
        self.assertIn(result["verdict"], ("ok", "ambiguous"))
        self.assertAlmostEqual(diameter(result)["value_px"],
                               truth["diameter_px"], delta=5.0)

    def test_partial_occlusion_keeps_diameter(self):
        image, truth = render_top(occlude=0.3, seed=6)
        result = analyze(image, mode="top", calibration=CAL)
        self.assertIn(result["verdict"], ("ok", "ambiguous"))
        self.assertAlmostEqual(diameter(result)["value_px"],
                               truth["diameter_px"], delta=5.0)

    def test_heavy_occlusion_is_flagged_or_accurate(self):
        image, truth = render_top(occlude=1.0, seed=6)
        result = analyze(image, mode="top", calibration=CAL)
        entry = diameter(result)
        accurate = (entry["value_px"] is not None
                    and abs(entry["value_px"] - truth["diameter_px"]) <= 6.0)
        self.assertTrue(result["verdict"] != "ok" or accurate)

    def test_truncated_crop_is_ambiguous(self):
        image, _ = render_top(seed=1)
        result = analyze(image, mode="top", roi=(100, 100, 200, 200), calibration=CAL)
        self.assertEqual(result["verdict"], "ambiguous")
        self.assertTrue(result["quality"]["touches_border"])
        self.assertTrue(any("border" in r for r in result["reasons"]))


class CalibrationHandlingTest(unittest.TestCase):
    def test_pixel_only_without_calibration(self):
        image, truth = render_top(seed=1)
        result = analyze(image, mode="top", calibration=None)
        entry = diameter(result)
        self.assertEqual(result["verdict"], "ok")
        self.assertAlmostEqual(entry["value_px"], truth["diameter_px"], delta=4.0)
        self.assertIsNone(entry["value_mm"])
        self.assertIsNone(entry["mm_interval"])

    def test_invalid_calibration_disables_millimetres(self):
        image, truth = render_top(seed=1)
        result = analyze(image, mode="top",
                         calibration=blank("pixels_per_mm must be positive"))
        entry = diameter(result)
        self.assertIsNone(entry["value_mm"])
        self.assertTrue(entry["reasons"])
        self.assertAlmostEqual(entry["value_px"], truth["diameter_px"], delta=4.0)


class NegativeTest(unittest.TestCase):
    def test_blank_is_unknown(self):
        result = analyze(blank_image(), mode="top", calibration=CAL)
        self.assertEqual(result["verdict"], "unknown")
        self.assertIsNone(result["geometry"])

    def test_bad_mode_raises(self):
        with self.assertRaises(ValueError):
            analyze(blank_image(), mode="front", calibration=CAL)

    def test_outside_roi_raises(self):
        image, _ = render_top(seed=1)
        with self.assertRaises(ValueError):
            analyze(image, mode="top", roi=(300, 300, 200, 200), calibration=CAL)

    def test_roi_offsets_geometry(self):
        canvas = np.full((600, 600, 3), 200, np.uint8)
        canvas[100:500, 100:500] = render_top(seed=1)[0]
        result = analyze(canvas, mode="top", roi=(100, 100, 400, 400), calibration=CAL)
        self.assertEqual(result["verdict"], "ok")
        cx, cy = result["geometry"]["center_xy"]
        self.assertAlmostEqual(cx, 300, delta=5)
        self.assertAlmostEqual(cy, 300, delta=5)


class AdapterTest(unittest.TestCase):
    def test_custom_segmenter_is_used(self):
        seen = {}

        def segmenter(gray, context):
            seen["mode"] = context["mode"]
            return None

        image, _ = render_top(seed=1)
        result = analyze(image, mode="top", calibration=CAL, segmenter=segmenter)
        self.assertEqual(seen["mode"], "top")
        self.assertEqual(result["verdict"], "ok")

    def test_bad_segmenter_mask_raises(self):
        def segmenter(gray, context):
            return np.zeros((10, 10), np.uint8)

        image, _ = render_top(seed=1)
        with self.assertRaises(ValueError):
            analyze(image, mode="top", calibration=CAL, segmenter=segmenter)

    def test_pipeline_is_deterministic(self):
        image, _ = render_top(tilt=0.2, seed=9)
        first = analyze(image, mode="top", calibration=CAL)
        second = analyze(image.copy(), mode="top", calibration=CAL)
        self.assertEqual(first, second)


class CompareTest(unittest.TestCase):
    def test_same_screw_is_consistent(self):
        left = analyze(render_top(seed=1)[0], mode="top", calibration=CAL)
        right = analyze(render_top(seed=2)[0], mode="top", calibration=CAL)
        report = compare_measurements(left, right)
        self.assertEqual(report["verdict"], "consistent")
        self.assertTrue(report["mm_overlap"])

    def test_different_screws_differ(self):
        left = analyze(render_top(seed=1)[0], mode="top", calibration=CAL)
        right = analyze(render_top(diameter_px=260, seed=3)[0], mode="top", calibration=CAL)
        report = compare_measurements(left, right)
        self.assertEqual(report["verdict"], "different")
        self.assertGreater(report["delta_mm"], 4.0)

    def test_pixel_only_comparison_is_uncertain(self):
        left = analyze(render_top(seed=1)[0], mode="top", calibration=CAL)
        right = analyze(render_top(seed=1)[0], mode="top", calibration=None)
        report = compare_measurements(left, right)
        self.assertEqual(report["verdict"], "uncertain")
        self.assertIsNone(report["delta_mm"])

    def test_mixed_modes_are_uncertain(self):
        from anastylos_screw_measure.synthetic import render_side
        left = analyze(render_top(seed=1)[0], mode="top", calibration=CAL)
        right = analyze(render_side(seed=1)[0], mode="side", calibration=CAL)
        self.assertEqual(compare_measurements(left, right)["verdict"], "uncertain")


if __name__ == "__main__":
    unittest.main()
