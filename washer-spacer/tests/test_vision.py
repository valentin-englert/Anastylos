"""Washer/spacer vision checks on deterministic renders. Needs the venv.

Fixtures exercise the pipeline only; they prove nothing about
real-hardware accuracy.

Run from the washer-spacer directory:

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

from anastylos_washer.annotate import annotate
from anastylos_washer.cli import main
from anastylos_washer.synthetic import (EXPECTED, TOP_KINDS, expected_gap,
                                        render, render_grid, render_pair_side,
                                        render_touching)
from anastylos_washer.vision import analyze, default_segmenter

HINTS = {"flat_top": "flat_washer", "split_top": "split_washer",
         "spacer_top": "spacer", "standoff_top": "standoff",
         "washer_side": "flat_washer", "split_side": "split_washer",
         "spacer_side": "spacer", "standoff_side": "standoff"}
TOP_OK = {"flat_top": "flat_washer", "split_top": "split_washer",
          "spacer_top": "spacer", "standoff_top": "standoff"}
SIDE_OK = {"washer_side": "flat_washer", "split_side": "split_washer",
           "spacer_side": "spacer", "standoff_side": "standoff"}


def view_of(kind: str) -> str:
    return "top" if kind in TOP_KINDS else "side"


def single(kind: str, **kwargs):
    result = analyze(render(kind, seed=3), view_of(kind), **kwargs)
    assert len(result["observations"]) == 1, kind
    return result["observations"][0]


def dim(observation, key: str) -> float:
    return observation["geometry"][key]["value_px"]


def unc(observation, key: str) -> float:
    return observation["geometry"][key]["uncertainty_px"]


class NominalTopTest(unittest.TestCase):
    def test_each_top_family_reads_ok_with_hint(self):
        for kind, family in TOP_OK.items():
            with self.subTest(kind=kind):
                obs = single(kind, family=family)
                self.assertEqual(obs["status"], "ok")
                self.assertEqual(obs["top_family"], family)

    def test_top_diameters_match_nominal(self):
        for kind in ("flat_top", "spacer_top"):
            with self.subTest(kind=kind):
                obs = single(kind, family=HINTS[kind])
                self.assertAlmostEqual(dim(obs, "outer_d"),
                                       EXPECTED[kind]["outer_d"], delta=3.0)
                self.assertAlmostEqual(dim(obs, "inner_d"),
                                       EXPECTED[kind]["inner_d"], delta=3.0)

    def test_split_gap_measured(self):
        obs = single("split_top", family="split_washer")
        self.assertGreaterEqual(obs["geometry"]["gap_deg"], 12.0)
        self.assertLessEqual(obs["geometry"]["gap_deg"], 35.0)
        self.assertAlmostEqual(dim(obs, "outer_d"), 220.0, delta=4.0)

    def test_hex_calipers_measured(self):
        obs = single("standoff_top", family="standoff")
        self.assertAlmostEqual(dim(obs, "across_corners"), 200.0, delta=4.0)
        self.assertAlmostEqual(dim(obs, "across_flats"), 173.2, delta=5.0)

    def test_concentricity_near_zero(self):
        obs = single("flat_top", family="flat_washer")
        self.assertLess(obs["geometry"]["concentricity"], 0.05)

    def test_uncertainty_covers_nominal_error(self):
        for kind in ("flat_top", "spacer_top", "split_top", "standoff_top"):
            with self.subTest(kind=kind):
                obs = single(kind, family=HINTS[kind])
                error = abs(dim(obs, "outer_d") - EXPECTED[kind]["outer_d"])
                self.assertLessEqual(error, unc(obs, "outer_d"))
                if "inner_d" in obs["geometry"]:
                    error = abs(dim(obs, "inner_d")
                                - EXPECTED[kind]["inner_d"])
                    self.assertLessEqual(error, unc(obs, "inner_d"))


class NominalSideTest(unittest.TestCase):
    def test_each_side_family_reads_ok_with_hint(self):
        for kind, family in SIDE_OK.items():
            with self.subTest(kind=kind):
                obs = single(kind, family=family)
                self.assertEqual(obs["status"], "ok")
                self.assertEqual(obs["top_family"], family)

    def test_side_dimensions_match_nominal(self):
        for kind in SIDE_OK:
            with self.subTest(kind=kind):
                obs = single(kind, family=HINTS[kind])
                long_side = max(EXPECTED[kind]["width"],
                                EXPECTED[kind]["height"])
                short_side = min(EXPECTED[kind]["width"],
                                 EXPECTED[kind]["height"])
                self.assertAlmostEqual(dim(obs, "length_px"), long_side,
                                       delta=4.0)
                self.assertAlmostEqual(dim(obs, "thickness_px"), short_side,
                                       delta=4.0)

    def test_spacer_openings_visible_at_both_ends(self):
        obs = single("spacer_side", family="spacer")
        self.assertEqual(obs["geometry"]["openings_visible"], 2)

    def test_rod_without_openings_stays_ambiguous(self):
        result = analyze(render("rod_side", seed=3), "side")
        obs = result["observations"][0]
        self.assertEqual(obs["status"], "ambiguous")
        self.assertEqual(obs["geometry"]["openings_visible"], 0)
        families = {item["family"] for item in obs["candidates"]}
        self.assertEqual(families, {"unclassified", "spacer"})


class AmbiguityTest(unittest.TestCase):
    def test_top_ring_splits_washer_and_spacer(self):
        for kind in ("flat_top", "spacer_top"):
            with self.subTest(kind=kind):
                result = analyze(render(kind, seed=3), "top")
                obs = result["observations"][0]
                self.assertEqual(obs["status"], "ambiguous")
                families = [item["family"] for item in obs["candidates"]]
                self.assertEqual(families, ["flat_washer", "spacer"])

    def test_hex_splits_standoff_and_unclassified(self):
        for kind in ("standoff_top", "nut_top"):
            with self.subTest(kind=kind):
                result = analyze(render(kind, seed=3), "top")
                obs = result["observations"][0]
                self.assertEqual(obs["status"], "ambiguous")
                families = [item["family"] for item in obs["candidates"]]
                self.assertEqual(families, ["standoff", "unclassified"])

    def test_solid_disc_is_unclassified(self):
        result = analyze(render("disc_top", seed=3), "top")
        obs = result["observations"][0]
        self.assertEqual(obs["status"], "ok")
        self.assertEqual(obs["top_family"], "unclassified")

    def test_wrong_family_hint_marks_unexpected(self):
        result = analyze(render("flat_top", seed=3), "top",
                         family="standoff")
        obs = result["observations"][0]
        self.assertEqual(obs["status"], "ambiguous")
        self.assertEqual(obs["top_family"], "unclassified")
        self.assertLessEqual(obs["candidates"][0]["confidence"], 0.50)


class RotationScaleTest(unittest.TestCase):
    def test_top_rotations_keep_reading(self):
        cases = [("flat_top", 30.0), ("flat_top", 90.0),
                 ("split_top", 30.0), ("spacer_top", 135.0),
                 ("standoff_top", 30.0)]
        for kind, deg in cases:
            with self.subTest(kind=kind, deg=deg):
                result = analyze(render(kind, rotation_deg=deg, seed=11),
                                 "top", family=HINTS[kind])
                obs = result["observations"][0]
                self.assertEqual(obs["status"], "ok")
                if kind == "standoff_top":
                    self.assertAlmostEqual(dim(obs, "across_corners"), 200.0,
                                           delta=5.0)
                else:
                    self.assertAlmostEqual(
                        dim(obs, "outer_d"), EXPECTED[kind]["outer_d"],
                        delta=4.0)

    def test_split_gap_angle_tracks_rotation(self):
        result = analyze(render("split_top", rotation_deg=30.0, seed=11),
                         "top", family="split_washer")
        measured = result["observations"][0]["geometry"]["gap_at_deg"]
        gap = abs((measured - expected_gap(30.0) + 180.0) % 360.0 - 180.0)
        self.assertLess(gap, 12.0)

    def test_side_tilt_stays_ok(self):
        result = analyze(render("spacer_side", rotation_deg=15.0, seed=11),
                         "side", family="spacer")
        obs = result["observations"][0]
        self.assertEqual(obs["status"], "ok")
        self.assertAlmostEqual(dim(obs, "length_px"), 200.0, delta=6.0)

    def test_scale_changes_stay_ok(self):
        small = cv2.resize(render("flat_top", seed=3), None, fx=0.7, fy=0.7)
        result = analyze(small, "top", family="flat_washer")
        self.assertEqual(result["observations"][0]["status"], "ok")
        big = cv2.resize(render("spacer_side", seed=3), None, fx=1.3, fy=1.3)
        result = analyze(big, "side", family="spacer")
        self.assertEqual(result["observations"][0]["status"], "ok")
        self.assertAlmostEqual(dim(result["observations"][0], "length_px"),
                               260.0, delta=8.0)


class LightingPerspectiveTest(unittest.TestCase):
    def test_brightness_and_shadow_stay_ok(self):
        cases = [("flat_top", {"brightness": 40}),
                 ("spacer_side", {"brightness": -40}),
                 ("flat_top", {"shadow": 0.4}),
                 ("spacer_side", {"shadow": 0.3})]
        for kind, kwargs in cases:
            with self.subTest(kind=kind, kwargs=kwargs):
                result = analyze(render(kind, seed=2, **kwargs),
                                 view_of(kind), family=HINTS[kind])
                self.assertEqual(len(result["observations"]), 1)
                self.assertEqual(result["observations"][0]["status"], "ok")

    def test_mild_glare_stays_ok(self):
        for kind in ("flat_top", "disc_top", "spacer_side"):
            with self.subTest(kind=kind):
                hint = HINTS.get(kind)
                result = analyze(render(kind, glare=True, seed=4),
                                 view_of(kind), family=hint)
                self.assertEqual(len(result["observations"]), 1)
                self.assertEqual(result["observations"][0]["status"], "ok")

    def test_mild_top_perspective_flags_but_stays_ok(self):
        result = analyze(render("flat_top", perspective=0.35, seed=3),
                         "top", family="flat_washer")
        obs = result["observations"][0]
        self.assertEqual(obs["status"], "ok")
        self.assertTrue(obs["geometry"]["limits"].get("perspective_suspect"))
        self.assertGreater(obs["geometry"]["outer_eccentricity"], 0.2)

    def test_heavy_top_perspective_is_ambiguous(self):
        result = analyze(render("flat_top", perspective=1.0, seed=3),
                         "top", family="flat_washer")
        self.assertEqual(result["observations"][0]["status"], "ambiguous")

    def test_side_perspective_flags_taper(self):
        mild = analyze(render("spacer_side", perspective=0.5, seed=3),
                       "side", family="spacer")
        self.assertEqual(mild["observations"][0]["status"], "ok")
        strong = analyze(render("spacer_side", perspective=1.0, seed=3),
                         "side", family="spacer")
        obs = strong["observations"][0]
        self.assertEqual(obs["status"], "ok")
        self.assertTrue(obs["geometry"]["limits"].get("perspective_suspect"))
        self.assertLess(obs["geometry"]["edge_ratio"], 0.90)


class DegradedTest(unittest.TestCase):
    def test_partial_occlusion_stays_ok(self):
        result = analyze(render("flat_top", occlude=0.3, seed=6),
                         "top", family="flat_washer")
        self.assertEqual(result["observations"][0]["status"], "ok")

    def test_heavy_occlusion_never_confident(self):
        result = analyze(render("flat_top", occlude=1.0, seed=6),
                         "top", family="flat_washer")
        self.assertIn(result["observations"][0]["status"],
                      ("ambiguous", "unknown"))

    def test_insufficient_resolution_is_unknown(self):
        tiny = cv2.resize(render("flat_top", seed=3), None, fx=0.1, fy=0.1)
        result = analyze(tiny, "top", family="flat_washer")
        self.assertEqual(result["status"], "unknown")

    def test_tiny_crop_is_unavailable(self):
        result = analyze(np.full((16, 16, 3), 190, np.uint8), "top")
        self.assertEqual(result["status"], "unavailable")

    def test_empty_background_is_unknown(self):
        result = analyze(np.full((400, 400, 3), 190, np.uint8), "top")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["observations"], [])


class MultiTest(unittest.TestCase):
    def test_grid_finds_three_stable_observations(self):
        first = analyze(render_grid(), "top", family="flat_washer")
        second = analyze(render_grid(), "top", family="flat_washer")
        self.assertEqual(len(first["observations"]), 3)
        self.assertEqual(first, second)
        ids = [obs["observation_id"] for obs in first["observations"]]
        self.assertEqual(ids, ["o1", "o2", "o3"])
        diameters = sorted(dim(obs, "outer_d")
                           for obs in first["observations"])
        for measured, expected in zip(diameters, (110.0, 124.0, 160.0)):
            self.assertAlmostEqual(measured, expected, delta=4.0)

    def test_touching_parts_group_with_unknown_count(self):
        result = analyze(render_touching(), "top")
        self.assertEqual(len(result["observations"]), 1)
        obs = result["observations"][0]
        self.assertEqual(obs["status"], "ambiguous")
        self.assertIn("count unknown", obs["explanation"])

    def test_side_pair_classifies_both(self):
        result = analyze(render_pair_side(), "side")
        self.assertEqual(len(result["observations"]), 2)
        tops = [obs["top_family"] for obs in result["observations"]]
        self.assertEqual(tops, ["flat_washer", "spacer"])

    def test_separated_stack_marks_adjacency(self):
        result = analyze(render("stack_side", seed=3), "side")
        self.assertEqual(len(result["observations"]), 3)
        for obs in result["observations"]:
            self.assertIn("stacked_with",
                          obs["geometry"]["limits"])

    def test_merged_stack_groups_with_interfaces(self):
        result = analyze(render("stack_merged_side", seed=3), "side")
        self.assertEqual(len(result["observations"]), 1)
        obs = result["observations"][0]
        self.assertEqual(obs["status"], "ambiguous")
        self.assertEqual(obs["geometry"]["band_interfaces"], 2)
        self.assertIn("count unknown", obs["explanation"])


class CalibrationTest(unittest.TestCase):
    def test_valid_calibration_converts_to_mm(self):
        result = analyze(render("flat_top", seed=3), "top",
                         family="flat_washer",
                         frame_metadata={"pixels_per_mm": 10.0})
        converted = result["geometry_mm"]["observations"][0]["dimensions_mm"]
        self.assertAlmostEqual(converted["outer_d"]["value_mm"], 21.9,
                               delta=0.4)
        self.assertIsNone(result["calibration_error"])

    def test_invalid_calibration_disables_mm_not_verdict(self):
        for bad in ({"pixels_per_mm": 0}, {"pixels_per_mm": -2.0},
                    {"pixels_per_mm": "x"},
                    {"pixels_per_mm": float("nan")},
                    {"pixels_per_mm": 9999.0}, {}, [1, 2]):
            with self.subTest(bad=bad):
                result = analyze(render("flat_top", seed=3), "top",
                                 family="flat_washer", frame_metadata=bad)
                self.assertEqual(result["observations"][0]["status"], "ok")
                self.assertIsNone(result["geometry_mm"])
                self.assertIsNotNone(result["calibration_error"])


class ContractTest(unittest.TestCase):
    def test_reject_bad_view(self):
        with self.assertRaises(ValueError):
            analyze(render("flat_top", seed=3), "tilted")

    def test_reject_unknown_family(self):
        with self.assertRaises(ValueError):
            analyze(render("flat_top", seed=3), "top", family="screw")

    def test_reject_outside_roi(self):
        with self.assertRaises(ValueError):
            analyze(render("flat_top", seed=3), "top",
                    roi=(300, 300, 200, 200))

    def test_roi_offsets_boxes(self):
        canvas = np.full((600, 600, 3), 190, np.uint8)
        canvas[100:500, 100:500] = render("flat_top", seed=3)
        result = analyze(canvas, "top", roi=(100, 100, 400, 400),
                         family="flat_washer")
        obs = result["observations"][0]
        self.assertEqual(obs["status"], "ok")
        x, y, _, _ = obs["bbox_xywh"]
        self.assertGreaterEqual(x, 100)
        self.assertGreaterEqual(y, 100)
        ccx, _ = obs["geometry"]["center_xy"]
        self.assertAlmostEqual(ccx, 300, delta=8)

    def test_result_is_json_serializable(self):
        result = analyze(render("spacer_side", seed=3), "side",
                         family="spacer",
                         frame_metadata={"pixels_per_mm": 10.0})
        json.dumps(result)


class AdapterTest(unittest.TestCase):
    def test_custom_segmenter_is_used(self):
        seen = {}

        def segmenter(gray):
            seen["shape"] = gray.shape
            return default_segmenter(gray)

        result = analyze(render("flat_top", seed=3), "top",
                         family="flat_washer", segmenter=segmenter)
        self.assertIn("shape", seen)
        self.assertEqual(result["observations"][0]["status"], "ok")

    def test_missing_segmenter_yields_unavailable(self):
        result = analyze(render("flat_top", seed=3), "top",
                         segmenter=lambda gray: None)
        self.assertEqual(result["status"], "unavailable")

    def test_custom_ranker_replaces_verdict(self):
        def ranker(segments, view, family):
            return [{"observation_id": "o1", "bbox_xywh": [0, 0, 8, 8],
                     "status": "unknown", "reason": "stub veto",
                     "candidates": [], "top_family": None, "geometry": {},
                     "quality": {}, "explanation": "vetoed"}]

        result = analyze(render("flat_top", seed=3), "top", ranker=ranker)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(len(result["observations"]), 1)

    def test_pipeline_is_deterministic(self):
        image = render("standoff_side", rotation_deg=12.0, seed=9)
        first = analyze(image, "side", family="standoff")
        second = analyze(image.copy(), "side", family="standoff")
        self.assertEqual(first, second)


class AnnotateCliTest(unittest.TestCase):
    def test_annotate_marks_observations(self):
        image = render("flat_top", seed=3)
        result = analyze(image, "top", family="flat_washer")
        marked = annotate(image, result)
        self.assertEqual(marked.shape, image.shape)
        self.assertTrue((marked != image).any())

    def test_annotate_handles_unknown_and_unavailable(self):
        flat = np.full((400, 400, 3), 190, np.uint8)
        self.assertEqual(annotate(flat, analyze(flat, "top")).shape,
                         flat.shape)
        tiny = np.full((16, 16, 3), 190, np.uint8)
        self.assertEqual(annotate(tiny, analyze(tiny, "top")).shape,
                         tiny.shape)

    def test_cli_read_writes_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            cv2.imwrite(str(tmpdir / "crop.png"), render("flat_top", seed=3))
            out = tmpdir / "out"
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["read", "--image", str(tmpdir / "crop.png"),
                             "--view", "top", "--family", "flat_washer",
                             "--frame-metadata", '{"pixels_per_mm": 10.0}',
                             "--out", str(out)])
            self.assertEqual(code, 0)
            bundle = json.loads((out / "result.json").read_text(
                encoding="utf-8"))
            self.assertEqual(bundle["status"], "ok")
            self.assertIsNotNone(bundle["geometry_mm"])
            self.assertTrue((out / "annotated.png").exists())

    def test_cli_read_missing_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = main(["read", "--image", str(Path(tmp) / "nope.png"),
                             "--view", "top", "--out", str(Path(tmp) / "o")])
            self.assertEqual(code, 1)

    def test_cli_read_rejects_bad_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            cv2.imwrite(str(tmpdir / "crop.png"), render("flat_top", seed=3))
            image = str(tmpdir / "crop.png")
            cases = [
                ["read", "--image", image, "--view", "top", "--roi",
                 "300,300,200,200", "--out", str(tmpdir / "o1")],
                ["read", "--image", image, "--view", "top", "--family",
                 "screw", "--out", str(tmpdir / "o2")],
                ["read", "--image", image, "--view", "top",
                 "--frame-metadata", "nope", "--out", str(tmpdir / "o3")],
            ]
            for argv in cases:
                with self.subTest(argv=argv):
                    with contextlib.redirect_stdout(io.StringIO()), \
                            contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(main(argv), 2)

    def test_cli_compare_writes_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            cv2.imwrite(str(tmpdir / "a.png"), render("flat_top", seed=3))
            cv2.imwrite(str(tmpdir / "b.png"), render("flat_top", seed=4))
            outs = []
            for name in ("a", "b"):
                out = tmpdir / f"out_{name}"
                with contextlib.redirect_stdout(io.StringIO()):
                    code = main(["read", "--image", str(tmpdir / f"{name}.png"),
                                 "--view", "top", "--family", "flat_washer",
                                 "--frame-metadata",
                                 '{"pixels_per_mm": 10.0}', "--out", str(out)])
                self.assertEqual(code, 0)
                outs.append(out / "result.json")
            with contextlib.redirect_stdout(io.StringIO()) as buffer:
                code = main(["compare", "--baseline", str(outs[0]),
                             "--candidate", str(outs[1]),
                             "--out", str(tmpdir / "cmp")])
            self.assertEqual(code, 0)
            report = json.loads(buffer.getvalue())
            self.assertEqual(len(report["pairs"]), 1)
            self.assertEqual(report["unit"], "mm")
            self.assertTrue((tmpdir / "cmp" / "comparison.json").exists())

    def test_cli_compare_rejects_mixed_units(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            calibrated = analyze(render("flat_top", seed=3), "top",
                                 frame_metadata={"pixels_per_mm": 10.0})
            plain = analyze(render("flat_top", seed=3), "top")
            left, right = tmpdir / "a.json", tmpdir / "b.json"
            left.write_text(json.dumps(calibrated), encoding="utf-8")
            right.write_text(json.dumps(plain), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = main(["compare", "--baseline", str(left),
                             "--candidate", str(right),
                             "--out", str(tmpdir / "cmp")])
            self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
