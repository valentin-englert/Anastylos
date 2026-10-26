"""Classifier rule checks on fabricated geometry. Standard library only.

Run from the washer-spacer directory:

    ..\\.venv\\Scripts\\python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anastylos_washer.classify import classify


def top_geometry(**overrides):
    geometry = {"outer": {"center_xy": [200.0, 200.0], "long_d": 220.0,
                          "short_d": 220.0, "mean_d": 220.0,
                          "eccentricity": 0.0, "angle_deg": 0.0, "rms": 0.01},
                "hole": {"centroid_xy": [200.0, 200.0], "area_px": 9500,
                         "circularity": 0.9, "diameter_px": 110.0},
                "holes_extra": 0, "concentricity": 0.0,
                "isoperimetric_q": 1.02, "vertices": 8,
                "hexagon_likely": False, "solidity": 0.99, "coverage": 1.0,
                "gap_deg": 0.0, "gap_at_deg": None, "defects": [],
                "defect_separation_deg": None, "area_px": 38000,
                "hex_calipers": {}}
    geometry.update(overrides)
    return geometry


def side_geometry(**overrides):
    geometry = {"length_px": 200.0, "thickness_px": 110.0, "aspect": 1.82,
                "rect_fill": 1.0, "vertices": 4, "edge_ratio": 1.0,
                "band_interfaces": 0, "interfaces_long": 0,
                "interfaces_transverse": 0, "openings": 2,
                "flats_likely": False, "area_px": 22000}
    geometry.update(overrides)
    return geometry


def segment(geometry, truncated=False):
    return {"contour": None, "bbox_xywh": [90, 90, 220, 220],
            "geometry": geometry, "truncated": truncated,
            "quality": {"contrast": 0.5}}


class TopScoreTest(unittest.TestCase):
    def test_ring_splits_washer_and_spacer(self):
        [obs] = classify([segment(top_geometry())], "top")
        self.assertEqual(obs["status"], "ambiguous")
        families = [item["family"] for item in obs["candidates"]]
        self.assertEqual(families, ["flat_washer", "spacer"])

    def test_hint_selects_ring_family(self):
        for hint in ("flat_washer", "spacer"):
            with self.subTest(hint=hint):
                [obs] = classify([segment(top_geometry())], "top",
                                 family=hint)
                self.assertEqual(obs["status"], "ok")
                self.assertEqual(obs["top_family"], hint)

    def test_split_clears_margin(self):
        geometry = top_geometry(hole=None, gap_deg=24.0, gap_at_deg=0.0,
                                coverage=0.93)
        [obs] = classify([segment(geometry)], "top")
        self.assertEqual(obs["status"], "ok")
        self.assertEqual(obs["top_family"], "split_washer")

    def test_hex_splits_standoff_and_unclassified(self):
        geometry = top_geometry(hexagon_likely=True, vertices=6,
                                isoperimetric_q=1.20)
        [obs] = classify([segment(geometry)], "top")
        self.assertEqual(obs["status"], "ambiguous")
        families = [item["family"] for item in obs["candidates"]]
        self.assertEqual(families, ["standoff", "unclassified"])

    def test_solid_disc_is_unclassified(self):
        geometry = top_geometry(hole=None)
        [obs] = classify([segment(geometry)], "top")
        self.assertEqual(obs["status"], "ok")
        self.assertEqual(obs["top_family"], "unclassified")

    def test_touching_lobes_group(self):
        geometry = top_geometry(
            defects=[{"depth": 40.0, "point_xy": [140.0, 200.0]},
                     {"depth": 38.0, "point_xy": [260.0, 200.0]}],
            defect_separation_deg=180.0)
        [obs] = classify([segment(geometry)], "top")
        self.assertEqual(obs["status"], "ambiguous")
        self.assertIn("count unknown", obs["explanation"])

    def test_partial_outline_caps_confidence(self):
        geometry = top_geometry(coverage=0.6)
        [obs] = classify([segment(geometry)], "top", family="flat_washer")
        self.assertEqual(obs["status"], "ambiguous")

    def test_tiny_hole_caps_family_confidence(self):
        hole = {"centroid_xy": [200.0, 200.0], "area_px": 40,
                "circularity": 0.9, "diameter_px": 6.0}
        [obs] = classify([segment(top_geometry(hole=hole))], "top",
                         family="flat_washer")
        self.assertEqual(obs["status"], "ambiguous")

    def test_small_outer_is_unknown(self):
        outer = {"center_xy": [20.0, 20.0], "long_d": 20.0, "short_d": 20.0,
                 "mean_d": 20.0, "eccentricity": 0.0, "angle_deg": 0.0,
                 "rms": 0.05}
        [obs] = classify([segment(top_geometry(outer=outer))], "top",
                         family="flat_washer")
        self.assertEqual(obs["status"], "unknown")

    def test_wrong_hint_never_confident(self):
        [obs] = classify([segment(top_geometry())], "top", family="standoff")
        self.assertEqual(obs["status"], "ambiguous")
        self.assertEqual(obs["top_family"], "unclassified")
        self.assertLessEqual(obs["candidates"][0]["confidence"], 0.50)

    def test_truncation_caps_ok(self):
        [obs] = classify([segment(top_geometry(), truncated=True)], "top",
                         family="flat_washer")
        self.assertEqual(obs["status"], "ambiguous")
        self.assertTrue(obs["geometry"]["limits"].get("truncated"))


class SideScoreTest(unittest.TestCase):
    def test_thin_bar_is_flat_washer(self):
        geometry = side_geometry(length_px=240.0, thickness_px=28.0,
                                 aspect=8.57)
        [obs] = classify([segment(geometry)], "side")
        self.assertEqual(obs["status"], "ok")
        self.assertEqual(obs["top_family"], "flat_washer")

    def test_thin_slit_is_split_washer(self):
        geometry = side_geometry(length_px=240.0, thickness_px=28.0,
                                 aspect=8.57, band_interfaces=1,
                                 interfaces_long=1)
        [obs] = classify([segment(geometry)], "side")
        self.assertEqual(obs["status"], "ok")
        self.assertEqual(obs["top_family"], "split_washer")

    def test_tall_openings_without_flats_is_spacer(self):
        [obs] = classify([segment(side_geometry())], "side")
        self.assertEqual(obs["status"], "ok")
        self.assertEqual(obs["top_family"], "spacer")

    def test_tall_openings_with_flats_is_standoff(self):
        [obs] = classify([segment(side_geometry(flats_likely=True))], "side")
        self.assertEqual(obs["status"], "ok")
        self.assertEqual(obs["top_family"], "standoff")

    def test_single_opening_is_ambiguous(self):
        [obs] = classify([segment(side_geometry(openings=1))], "side")
        self.assertEqual(obs["status"], "ambiguous")

    def test_no_openings_is_ambiguous(self):
        [obs] = classify([segment(side_geometry(openings=0))], "side")
        self.assertEqual(obs["status"], "ambiguous")
        families = {item["family"] for item in obs["candidates"]}
        self.assertEqual(families, {"unclassified", "spacer"})

    def test_stacked_layers_group(self):
        geometry = side_geometry(length_px=240.0, thickness_px=84.0,
                                 aspect=2.86, band_interfaces=2,
                                 interfaces_transverse=2)
        [obs] = classify([segment(geometry)], "side")
        self.assertEqual(obs["status"], "ambiguous")
        self.assertIn("count unknown", obs["explanation"])

    def test_compact_block_is_ambiguous(self):
        geometry = side_geometry(length_px=100.0, thickness_px=95.0,
                                 aspect=1.05)
        [obs] = classify([segment(geometry)], "side")
        self.assertEqual(obs["status"], "ambiguous")

    def test_taper_flags_perspective(self):
        [obs] = classify([segment(side_geometry(edge_ratio=0.80))], "side",
                         family="spacer")
        self.assertTrue(obs["geometry"]["limits"].get("perspective_suspect"))

    def test_thin_profile_limits_openings(self):
        geometry = side_geometry(length_px=240.0, thickness_px=28.0,
                                 aspect=8.57)
        [obs] = classify([segment(geometry)], "side")
        self.assertIn("ends_unresolved", obs["geometry"]["limits"])


class ObservationTest(unittest.TestCase):
    def test_ids_are_stable_and_ordered(self):
        segments = [segment(top_geometry()), segment(top_geometry())]
        obs = classify(segments, "top")
        self.assertEqual([item["observation_id"] for item in obs],
                         ["o1", "o2"])

    def test_uncertainty_grows_with_flag(self):
        plain = classify([segment(top_geometry())], "top",
                         family="flat_washer")[0]
        tilted_outer = dict(top_geometry()["outer"], eccentricity=0.5)
        flagged = classify(
            [segment(top_geometry(outer=tilted_outer))], "top",
            family="flat_washer")[0]
        self.assertGreater(
            flagged["geometry"]["outer_d"]["uncertainty_px"],
            plain["geometry"]["outer_d"]["uncertainty_px"])


if __name__ == "__main__":
    unittest.main()
