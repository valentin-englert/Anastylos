"""Calibration checks: validated scale in, pixel-only out when invalid.

Run from the screw-measurement directory:

    ..\\.venv\\Scripts\\python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anastylos_screw_measure.calibration import blank, load_calibration, to_millimetres


def write_calibration(directory: Path, payload) -> Path:
    path = directory / "calib.json"
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class ValidTest(unittest.TestCase):
    def test_direct_scale_loads_with_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_calibration(Path(tmp), {"pixels_per_mm": 12.5,
                                                 "pixels_per_mm_std": 0.05,
                                                 "frame": "surface_a3",
                                                 "provenance": "rectified run 4"})
            cal = load_calibration(path)
        self.assertTrue(cal["valid"])
        self.assertEqual(cal["pixels_per_mm"], 12.5)
        self.assertEqual(cal["frame"], "surface_a3")
        self.assertEqual(cal["reasons"], [])

    def test_reference_derives_scale(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_calibration(Path(tmp), {"reference": {"length_mm": 20.0,
                                                               "pixels": 250.0,
                                                               "label": "check line H"},
                                                 "frame": "surface_a3",
                                                 "provenance": "ruler in plane"})
            cal = load_calibration(path)
        self.assertTrue(cal["valid"])
        self.assertAlmostEqual(cal["pixels_per_mm"], 12.5)

    def test_custom_frame_is_kept_verbatim(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_calibration(Path(tmp), {"pixels_per_mm": 8.0,
                                                 "frame": "bench_cam_2",
                                                 "provenance": "grid photo"})
            cal = load_calibration(path)
        self.assertTrue(cal["valid"])
        self.assertEqual(cal["frame"], "custom:bench_cam_2")

    def test_conversion_combines_contour_and_scale_uncertainty(self):
        cal = {"valid": True, "pixels_per_mm": 10.0, "pixels_per_mm_std": 0.0,
               "frame": "surface_a3", "provenance": "test", "reasons": []}
        value, interval, reasons = to_millimetres(200.0, 1.0, cal)
        self.assertEqual(value, 20.0)
        self.assertEqual(reasons, [])
        self.assertAlmostEqual(interval[0], 19.8)
        self.assertAlmostEqual(interval[1], 20.2)
        loose = dict(cal, pixels_per_mm_std=0.5)
        _, wide, _ = to_millimetres(200.0, 1.0, loose)
        self.assertLess(wide[0], interval[0])
        self.assertGreater(wide[1], interval[1])


class InvalidTest(unittest.TestCase):
    def test_missing_file_raises(self):
        with self.assertRaises(ValueError):
            load_calibration(Path("no-such-calibration.json"))

    def test_unparseable_file_is_pixel_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            cal = load_calibration(write_calibration(Path(tmp), "{not json"))
        self.assertFalse(cal["valid"])
        self.assertIsNone(cal["pixels_per_mm"])

    def test_nonpositive_scale_is_pixel_only(self):
        for ppm in (0, -4.0, "ten", None):
            with self.subTest(ppm=ppm), tempfile.TemporaryDirectory() as tmp:
                cal = load_calibration(write_calibration(
                    Path(tmp), {"pixels_per_mm": ppm, "provenance": "x"}))
                self.assertFalse(cal["valid"])
                self.assertIsNone(cal["pixels_per_mm"])

    def test_missing_provenance_is_pixel_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            cal = load_calibration(write_calibration(Path(tmp), {"pixels_per_mm": 10.0}))
        self.assertFalse(cal["valid"])
        self.assertTrue(any("provenance" in r for r in cal["reasons"]))

    def test_conflicting_sources_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            cal = load_calibration(write_calibration(
                Path(tmp), {"pixels_per_mm": 10.0,
                            "reference": {"length_mm": 20.0, "pixels": 250.0,
                                          "label": "check line"},
                            "provenance": "x"}))
        self.assertFalse(cal["valid"])
        self.assertTrue(any("conflict" in r for r in cal["reasons"]))

    def test_bad_reference_is_pixel_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            cal = load_calibration(write_calibration(
                Path(tmp), {"reference": {"length_mm": 0, "pixels": 5, "label": ""},
                            "provenance": "x"}))
        self.assertFalse(cal["valid"])

    def test_blank_disables_conversion(self):
        cal = blank("no calibration supplied; pixel-only output")
        value, interval, reasons = to_millimetres(100.0, 1.0, cal)
        self.assertIsNone(value)
        self.assertIsNone(interval)
        self.assertTrue(reasons)


if __name__ == "__main__":
    unittest.main()
