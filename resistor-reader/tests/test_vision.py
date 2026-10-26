"""Synthetic vision checks. Needs the calibration virtualenv (cv2, numpy).

Run from the resistor-reader directory:

    ..\\.venv\\Scripts\\python -m unittest discover -s tests -v

Images are rendered by anastylos_resistor.synthetic with fixed seeds.
Passing here says nothing about real-camera accuracy.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anastylos_resistor.synthetic import render
from anastylos_resistor.vision import analyze, annotate


class VisionTest(unittest.TestCase):
    def test_four_band_nominal(self):
        result = analyze(render(["yellow", "violet", "red", "gold"]))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["bands_detected"], 4)
        (candidate,) = result["candidates"]
        self.assertEqual(candidate["sequence"], ["yellow", "violet", "red", "gold"])
        self.assertEqual(candidate["orientation"], "forward")
        self.assertEqual(candidate["resistance_ohms"], 4700.0)
        self.assertEqual(candidate["tolerance_percent"], 5.0)
        self.assertGreaterEqual(candidate["score"], 0.5)
        for band in result["band_boxes"]:
            self.assertEqual(len(band["bbox_xywh"]), 4)
            self.assertGreaterEqual(len(band["color_candidates"]), 2)

    def test_five_band(self):
        result = analyze(render(["yellow", "violet", "black", "red", "brown"]))
        self.assertEqual(result["status"], "ok")
        (candidate,) = result["candidates"]
        self.assertEqual(candidate["resistance_ohms"], 47000.0)
        self.assertEqual(candidate["tolerance_percent"], 1.0)

    def test_six_band_tcr(self):
        result = analyze(render(["brown", "black", "black", "red", "brown", "red"]))
        self.assertEqual(result["status"], "ok")
        (candidate,) = result["candidates"]
        self.assertEqual(candidate["resistance_ohms"], 10000.0)
        self.assertEqual(candidate["tcr_ppm_k"], 50)

    def test_reversed_orientation(self):
        result = analyze(render(["gold", "red", "violet", "yellow"]))
        self.assertEqual(result["status"], "ok")
        (candidate,) = result["candidates"]
        self.assertEqual(candidate["orientation"], "reversed")
        self.assertEqual(candidate["resistance_ohms"], 4700.0)

    def test_rotation(self):
        result = analyze(render(["yellow", "violet", "red", "gold"], rotation_deg=12.0))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["candidates"][0]["resistance_ohms"], 4700.0)
        self.assertNotEqual(result["diagnostics"]["deskew_angle_deg"], 0.0)

    def test_lighting_variation(self):
        image = render(["yellow", "violet", "red", "gold"], brightness=0.75,
                       gradient=0.3, noise_sigma=4.0, seed=7)
        result = analyze(image)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["candidates"][0]["resistance_ohms"], 4700.0)

    def test_blue_body(self):
        result = analyze(render(["brown", "black", "red", "gold"], body="blue"))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["candidates"][0]["resistance_ohms"], 1000.0)

    def test_glare(self):
        result = analyze(render(["yellow", "violet", "red", "gold"], glare=True))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["candidates"][0]["resistance_ohms"], 4700.0)
        self.assertGreater(result["diagnostics"]["glare_fraction"], 0.0)

    def test_similar_colors_read_correctly(self):
        result = analyze(render(["brown", "red", "red", "gold"]))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["candidates"][0]["resistance_ohms"], 1200.0)
        top_colors = [band["color_candidates"][0]["color"] for band in result["band_boxes"]]
        self.assertEqual(top_colors, ["brown", "red", "red", "gold"])

    def test_symmetric_sequence_is_ambiguous(self):
        result = analyze(render(["red", "black", "black", "brown"]))
        self.assertEqual(result["status"], "ambiguous")
        ohms = sorted(item["resistance_ohms"] for item in result["candidates"])
        self.assertEqual(ohms, [10.0, 20.0])

    def test_invalid_sequence_is_unknown(self):
        result = analyze(render(["gold", "silver", "gold", "silver"]))
        self.assertEqual(result["status"], "unknown")
        self.assertIn("below", result["reason"])

    def test_missing_band_is_unknown(self):
        result = analyze(render(["yellow", "violet", "red"]))
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["bands_detected"], 3)

    def test_blank_body_is_unknown(self):
        image = render(["yellow", "violet", "red", "gold"])
        from anastylos_resistor.synthetic import BODIES
        import cv2
        height, width = image.shape[:2]
        cv2.rectangle(image, (int(width * 0.16), int(height * 0.32)),
                      (int(width * 0.84), int(height * 0.68)), BODIES["tan"], -1)
        result = analyze(image)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["candidates"], [])

    def test_roi_crop_matches_full_frame(self):
        image = render(["yellow", "violet", "red", "gold"])
        full = analyze(image)
        cropped = analyze(image, roi=(100, 80, 700, 180))
        self.assertEqual(cropped["status"], "ok")
        self.assertEqual(cropped["candidates"][0]["resistance_ohms"],
                         full["candidates"][0]["resistance_ohms"])
        with self.assertRaises(ValueError):
            analyze(image, roi=(800, 300, 200, 100))

    def test_annotate_marks_bands(self):
        image = render(["yellow", "violet", "red", "gold"])
        result = analyze(image)
        view = annotate(image, result)
        self.assertEqual(view.shape, image.shape)
        self.assertFalse((view == image).all())


if __name__ == "__main__":
    unittest.main()
