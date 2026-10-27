"""Deterministic cable-routing regression tests. Needs OpenCV/NumPy.

Fixtures are synthetic renders from anastylos_cable.synthetic; they
exercise the pipeline only and prove nothing about real-photo accuracy.

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

import cv2
import numpy as np

from anastylos_cable import synthetic
from anastylos_cable.annotate import annotate
from anastylos_cable.cables import analyze
from anastylos_cable.cli import main
from anastylos_cable.compare import compare

EXPECTED_FEATURES = {
    "straight": {}, "curve": {}, "branch": {"branch": 1}, "tap": {"tap": 1},
    "crossing": {"crossing": 1}, "colors": {}, "widths": {}, "clip": {},
    "channel": {}, "connector": {}, "occluded": {}, "multi": {},
    "screw_bg": {}, "traces_bg": {}, "blank": {},
}
EXPECTED_DEGREE = {"branch": 3, "tap": 3, "crossing": 4}


def _feature_counts(result):
    counts = {}
    for feature in result["features"]:
        counts[feature["kind"]] = counts.get(feature["kind"], 0) + 1
    return counts


class SyntheticKinds(unittest.TestCase):
    def test_all_kinds_match_expected_counts(self):
        for kind in synthetic.KINDS:
            with self.subTest(kind=kind):
                result = analyze(synthetic.render(kind))
                expected = synthetic.EXPECTED[kind]
                self.assertEqual(len(result["cables"]), expected["cables"])
                self.assertEqual(_feature_counts(result),
                                 EXPECTED_FEATURES[kind])
                for key in ("hypotheses", "clips", "anchors", "channels"):
                    if key in expected:
                        self.assertEqual(len(result[key]), expected[key],
                                         f"{kind} {key}")

    def test_junction_degrees(self):
        for kind in ("branch", "tap", "crossing"):
            result = analyze(synthetic.render(kind))
            self.assertEqual(len(result["features"]), 1)
            self.assertEqual(result["features"][0]["degree"],
                             EXPECTED_DEGREE[kind])

    def test_crossing_carries_no_connection_claim(self):
        result = analyze(synthetic.render("crossing"))
        note = result["features"][0]["note"]
        self.assertIn("no electrical connection", note)
        self.assertIn("depth order", note)

    def test_widths_follow_fixtures(self):
        result = analyze(synthetic.render("straight"))
        width = result["cables"][0]["branches"][0]["width_px"]
        self.assertAlmostEqual(width, 12.0, delta=2.5)
        result = analyze(synthetic.render("widths"))
        measured = sorted(branch["width_px"]
                          for cable in result["cables"]
                          for branch in cable["branches"])
        self.assertEqual(len(measured), 3)
        self.assertAlmostEqual(measured[0], 6.0, delta=2.5)
        self.assertAlmostEqual(measured[1], 12.0, delta=2.5)
        self.assertAlmostEqual(measured[2], 18.0, delta=2.5)

    def test_blank_is_unknown_without_cables(self):
        result = analyze(synthetic.render("blank"))
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["cables"], [])


class Robustness(unittest.TestCase):
    def test_rotation_keeps_straight_run(self):
        for degrees in (30, 90):
            result = analyze(synthetic.render("straight",
                                              rotation_deg=degrees))
            self.assertEqual(len(result["cables"]), 1, degrees)

    def test_rotation_keeps_branch(self):
        result = analyze(synthetic.render("branch", rotation_deg=90))
        self.assertEqual(_feature_counts(result), {"branch": 1})

    def test_rotated_crossing_splits_honestly(self):
        # Known limitation: rotated X blobs split into arm segments.
        # Locked behavior: segments preserved, no invented features.
        result = analyze(synthetic.render("crossing", rotation_deg=30))
        self.assertEqual(result["status"], "ok")
        self.assertGreaterEqual(len(result["cables"]), 2)

    def test_shadow_is_tolerated(self):
        for kind in ("straight", "multi", "channel"):
            result = analyze(synthetic.render(kind, shadow=0.4))
            self.assertEqual(len(result["cables"]),
                             synthetic.EXPECTED[kind]["cables"], kind)

    def test_bright_shift_is_tolerated(self):
        result = analyze(synthetic.render("straight", brightness=40))
        self.assertEqual(len(result["cables"]), 1)

    def test_dark_frame_reports_unknown_without_hallucination(self):
        result = analyze(synthetic.render("straight", brightness=-60))
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["cables"], [])

    def test_glare_splits_but_never_crashes(self):
        result = analyze(synthetic.render("straight", glare=True))
        self.assertEqual(result["status"], "ok")
        self.assertGreaterEqual(len(result["cables"]), 1)

    def test_occluded_pair_links_with_hypothesis(self):
        result = analyze(synthetic.render("occluded"))
        self.assertEqual(len(result["cables"]), 2)
        self.assertEqual(len(result["hypotheses"]), 1)
        hypothesis = result["hypotheses"][0]
        self.assertIn("uncertain", hypothesis["note"])
        self.assertEqual(len(result["occluded_regions"]), 1)


class Calibration(unittest.TestCase):
    def test_valid_metadata_converts(self):
        result = analyze(synthetic.render("straight"),
                         frame_metadata={"pixels_per_mm": 4.0})
        geometry = result["geometry_mm"]
        self.assertIsNotNone(geometry)
        self.assertIsNone(result["calibration_error"])
        width_px = result["cables"][0]["branches"][0]["width_px"]
        width_mm = geometry["cables"][0]["branches_mm"][0]["width_mm"]
        self.assertAlmostEqual(width_mm, width_px / 4.0, places=2)

    def test_invalid_metadata_blocks_mm_only(self):
        for bad in ({"pixels_per_mm": "4"}, {"pixels_per_mm": True},
                    {"pixels_per_mm": float("inf")}, {"pixels_per_mm": 0.01},
                    {"pixels_per_mm": 999.0}, {"other": 1}, [4.0]):
            result = analyze(synthetic.render("straight"),
                             frame_metadata=bad)
            self.assertIsNone(result["geometry_mm"], bad)
            self.assertIsNotNone(result["calibration_error"], bad)
            self.assertEqual(len(result["cables"]), 1, bad)

    def test_absent_metadata_means_pixels_only(self):
        result = analyze(synthetic.render("straight"))
        self.assertIsNone(result["geometry_mm"])
        self.assertIsNone(result["calibration_error"])


class RegionsAndAdapters(unittest.TestCase):
    def test_roi_reports_source_frame_coordinates(self):
        image = synthetic.render("straight")
        result = analyze(image, roi=(0, 150, 400, 100))
        self.assertEqual(len(result["cables"]), 1)
        point = result["cables"][0]["branches"][0]["polyline_xy"][0]
        self.assertTrue(150 <= point[1] <= 250)

    def test_roi_outside_frame_raises(self):
        image = synthetic.render("straight")
        with self.assertRaises(ValueError):
            analyze(image, roi=(0, 0, 999, 999))

    def test_tiny_crop_is_unavailable(self):
        image = synthetic.render("straight")
        result = analyze(image, roi=(190, 190, 20, 20))
        self.assertEqual(result["status"], "unavailable")

    def test_custom_segmenter_masks(self):
        image = synthetic.render("straight")

        def segment(_frame):
            mask = np.zeros(image.shape[:2], np.uint8)
            mask[190:211, 40:361] = 255
            return mask

        result = analyze(image, segmenter=segment)
        self.assertEqual(len(result["cables"]), 1)

    def test_missing_segmenter_is_unavailable(self):
        image = synthetic.render("straight")
        result = analyze(image, segmenter=lambda _frame: None)
        self.assertEqual(result["status"], "unavailable")


class DeterminismAndSerialization(unittest.TestCase):
    def test_repeated_analysis_is_identical(self):
        image = synthetic.render("crossing")
        first = analyze(image)
        second = analyze(image)
        self.assertEqual(first, second)

    def test_result_is_json_serializable(self):
        for kind in synthetic.KINDS:
            result = analyze(synthetic.render(kind))
            text = json.dumps(result)
            self.assertEqual(json.loads(text)["status"], result["status"])


class CompareTests(unittest.TestCase):
    def test_identical_results_match_with_zero_drift(self):
        result = analyze(synthetic.render("straight"))
        compared = compare(result, result)
        self.assertEqual(compared["status"], "ok")
        self.assertEqual(len(compared["matches"]), 1)
        self.assertEqual(compared["matches"][0]["drift"]["mean_px"], 0.0)
        self.assertEqual(compared["added"], [])
        self.assertEqual(compared["removed"], [])

    def test_rotated_cable_matches_with_drift(self):
        before = analyze(synthetic.render("straight"))
        after = analyze(synthetic.render("straight", rotation_deg=90))
        compared = compare(before, after)
        self.assertEqual(len(compared["matches"]), 1)
        self.assertGreater(compared["matches"][0]["drift"]["mean_px"], 0.0)

    def test_added_and_removed(self):
        before = analyze(synthetic.render("straight"))
        after = analyze(synthetic.render("blank"))
        compared = compare(before, after)
        self.assertEqual(compared["removed"], ["c1"])
        self.assertEqual(compare(after, before)["added"], ["c1"])

    def test_frame_mismatch_is_unavailable(self):
        image = synthetic.render("straight")
        before = analyze(image)
        after = analyze(image, roi=(0, 150, 400, 100))
        compared = compare(before, after)
        self.assertEqual(compared["status"], "unavailable")


class AnnotateTests(unittest.TestCase):
    def test_overlay_marks_evidence(self):
        image = synthetic.render("crossing")
        result = analyze(image)
        overlay = annotate(image, result)
        self.assertEqual(overlay.shape, image.shape)
        changed = (overlay.astype(int) - image.astype(int)).any(axis=2)
        self.assertGreater(int(changed.sum()), 100)


class CliTests(unittest.TestCase):
    def _write_image(self, directory, kind="straight"):
        path = Path(directory) / f"{kind}.png"
        cv2.imwrite(str(path), synthetic.render(kind))
        return str(path)

    def test_read_writes_result_and_overlay(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = self._write_image(tmp)
            out = str(Path(tmp) / "out")
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["read", image, "--out", out])
            self.assertEqual(code, 0)
            self.assertTrue((Path(out) / "cable_result.json").is_file())
            self.assertTrue((Path(out) / "cable_overlay.png").is_file())

    def test_read_missing_image_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["read", str(Path(tmp) / "nope.png"),
                             "--out", str(Path(tmp) / "out")])
            self.assertEqual(code, 1)

    def test_compare_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = analyze(synthetic.render("straight"))
            (Path(tmp) / "a.json").write_text(json.dumps(first))
            (Path(tmp) / "b.json").write_text(json.dumps(first))
            with contextlib.redirect_stdout(io.StringIO()) as out:
                code = main(["compare", str(Path(tmp) / "a.json"),
                             str(Path(tmp) / "b.json")])
            self.assertEqual(code, 0)
            self.assertIn("matched", out.getvalue())


if __name__ == "__main__":
    unittest.main()
