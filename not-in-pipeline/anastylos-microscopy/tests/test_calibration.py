"""Calibration adapter: no mm without evidence; per-module spellings preserved."""

from __future__ import annotations

import unittest

from _support import CLOCK, tmpdir, write_png

from anastylos_microscopy import calibration as cal
from anastylos_microscopy.orchestrator import inspect_image


def _screw_top():
    from anastylos_screw_measure.synthetic import render_top
    image, _ = render_top(diameter_px=200.0)
    return image


def _doc(**overrides):
    doc = {"schema": "anastylos-microscopy-calibration/1", "pixels_per_mm": 40.0,
           "pixels_per_mm_std": 0.2, "image_resolution": [400, 400],
           "provenance": "TEST FIXTURE: synthetic scale", "frame": "custom:test"}
    doc.update(overrides)
    return {k: v for k, v in doc.items() if v is not None}


class CalibrationTest(unittest.TestCase):
    def test_missing_calibration_gives_pixels_only(self):
        base = tmpdir(self)
        src = write_png(base / "top.png", _screw_top())
        record, _ = inspect_image(src, base / "out", mode="screw_measure", options={"view": "top"},
                                  clock=CLOCK)
        self.assertEqual(record["calibration"]["status"], "absent")
        measurements = record["recognizers"][0]["native_result"]["measurements"]
        self.assertTrue(all(m["value_mm"] is None for m in measurements))
        self.assertIsNotNone(measurements[0]["value_px"])
        self.assertIn("no calibration: all geometry is pixel-only", record["uncertainty"]["reasons"])

    def test_valid_calibration_uses_reader_specific_spellings(self):
        base = tmpdir(self)
        src = write_png(base / "top.png", _screw_top())
        record, path = inspect_image(src, base / "out", mode="multi",
                                     readers=["screw_measure", "polarity", "fingerprints"],
                                     options={"view": "top", "gallery": str(base / "nogallery")},
                                     calibration_doc=_doc(), clock=CLOCK)
        blocks = {b["reader"]: b for b in record["recognizers"]}
        diameter = blocks["screw_measure"]["native_result"]["measurements"][0]
        self.assertAlmostEqual(diameter["value_mm"], diameter["value_px"] / 40.0, places=1)
        self.assertEqual(blocks["screw_measure"]["native_result"]["calibration"]["provenance"],
                         "TEST FIXTURE: synthetic scale")
        self.assertEqual(blocks["polarity"]["calibration_adaptation"]["spelling"], "pixels_per_mm")
        self.assertEqual(blocks["fingerprints"]["calibration_adaptation"]["spelling"], "px_per_mm")
        self.assertTrue((path.parent / "readers/screw_measure/screw_measurement_calibration.json")
                        .is_file())

    def test_invalid_calibrations_are_rejected(self):
        res = {"width": 400, "height": 400}
        cases = {
            "resolution": _doc(image_resolution=[1920, 1080]),
            "provenance": _doc(provenance=""),
            "conflict": _doc(px_per_mm=50.0),
            "range": _doc(pixels_per_mm=5000.0),
            "no_scale": _doc(pixels_per_mm=None),
        }
        for name, doc in cases.items():
            with self.subTest(name):
                result = cal.resolve(doc, res)
                self.assertEqual(result["status"], "invalid")
                self.assertIsNone(result["pixels_per_mm"])
        agree = cal.resolve(_doc(px_per_mm=40.2), res)
        self.assertTrue(agree["valid"], agree["reasons"])
        ref = cal.resolve(_doc(pixels_per_mm=None, reference={"length_mm": 5.0, "pixels": 200.0}), res)
        self.assertEqual(ref["pixels_per_mm"], 40.0)

    def test_invalid_calibration_degrades_to_pixels(self):
        base = tmpdir(self)
        src = write_png(base / "top.png", _screw_top())
        record, _ = inspect_image(src, base / "out", mode="screw_measure", options={"view": "top"},
                                  calibration_doc=_doc(image_resolution=[1920, 1080]), clock=CLOCK)
        self.assertEqual(record["calibration"]["status"], "invalid")
        native = record["recognizers"][0]["native_result"]
        self.assertFalse(native["calibration"]["valid"])
        self.assertTrue(all(m["value_mm"] is None for m in native["measurements"]))
        self.assertTrue(any("calibration rejected" in r for r in record["uncertainty"]["reasons"]))


if __name__ == "__main__":
    unittest.main()
