"""Synthetic checks for the drawing proof: shadow rejection, simplification bound, holes, uniform scale."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import draw  # noqa: E402
import proof  # noqa: E402
import segment  # noqa: E402

PAPER, PART, SHADOW = (205, 208, 206), (80, 82, 84), (96, 116, 128)  # BGR, close to the measured L*/b*; shadow warm


def _l_shape() -> np.ndarray:
    """Asymmetric L in px: tells a rotation or mirror apart from the original."""
    return np.array([[100, 100], [500, 100], [500, 180], [220, 180], [220, 400], [100, 400]], np.int32)


def _scene(with_shadow: bool) -> np.ndarray:
    image = np.full((520, 640, 3), PAPER, np.uint8)
    if with_shadow:
        cv2.fillPoly(image, [_l_shape() + 22], SHADOW)
        image = cv2.GaussianBlur(image, (21, 21), 0)
    cv2.fillPoly(image, [_l_shape()], PART)
    noise = np.random.default_rng(1).normal(0, 9, image.shape[:2])  # part texture; shadow stays smooth
    part = np.zeros(image.shape[:2], np.uint8)
    cv2.fillPoly(part, [_l_shape()], 1)
    image[part > 0] = np.clip(image[part > 0] + noise[part > 0, None], 0, 255).astype(np.uint8)
    return image


def _truth() -> np.ndarray:
    truth = np.zeros((520, 640), np.uint8)
    cv2.fillPoly(truth, [_l_shape()], 1)
    return truth > 0


SHADOW_RULE = {"texture_window_px": 9, "min_b": 135, "max_sd": 4.0, "band_px": 80}


class OutlineTest(unittest.TestCase):
    def test_threshold_alone_takes_the_shadow_and_rejection_removes_it(self):
        image = _scene(with_shadow=True)
        seed, _ = segment.threshold_mask(image, 15)
        self.assertGreater(segment.mask_agreement(seed, _truth())["only_a_px"], 2000)
        shadow = segment.shadow_candidates(image, seed, SHADOW_RULE)
        mask, _ = segment.grabcut_mask(image, seed & ~shadow, 12, 5, background=shadow)
        self.assertGreater(segment.mask_agreement(mask, _truth())["iou"], 0.97)

    def test_simplification_stays_within_tolerance(self):
        dense = segment.boundary(_truth())
        for tolerance in (0.1, 0.5, 2.0):
            poly, deviation = draw.simplify(dense, 10.0, tolerance)
            self.assertLessEqual(deviation, tolerance + 1e-6)
            self.assertGreaterEqual(len(poly), 6)

    def test_corrections_apply_in_mm(self):
        mask = _truth()
        corrected = segment.apply_corrections(mask, {"force_background": [[[10, 10], [22, 10], [22, 40], [10, 40]]]},
                                              lambda mm: (np.asarray(mm) * 10.0))
        self.assertFalse(corrected[200, 150])
        self.assertTrue(corrected[110, 450])


class RegionTest(unittest.TestCase):
    def test_enclosed_openings_become_holes_and_edge_touching_ones_do_not(self):
        image = np.full((400, 400, 3), PART, np.uint8)
        cv2.rectangle(image, (80, 80), (320, 320), (240, 240, 240), -1)
        cv2.circle(image, (200, 200), 30, (40, 40, 40), -1)   # enclosed contact opening
        cv2.circle(image, (80, 200), 30, (40, 40, 40), -1)    # touches the membrane edge
        mask = np.ones(image.shape[:2], bool)
        rule = {"light_L": 175, "dark_L": 20, "min_area_mm2": 15, "tolerance_mm": 0.25, "dark_min_area_mm2": 100,
                "dark_min_solidity": 0.85, "min_hole_mm2": 8, "min_hole_circularity": 0.6}
        regions = [r for r in draw.tone_regions(image, mask, rule, 10.0) if r["tone"] == "light"]
        self.assertEqual(len(regions), 1)
        self.assertEqual(len(regions[0]["holes_px"]), 1)


class LayoutTest(unittest.TestCase):
    def test_svg_keeps_one_uniform_scale_and_disjoint_boxes(self):
        outline = (_l_shape().astype(float) / 10.0).tolist()   # mm
        geometry = {"outline_mm": outline, "regions": []}
        spec = {"title": "t", "subtitle": "s", "legend": ["a", "b"]}
        svg = proof.render_svg(geometry, spec)
        path = re.search(r'data-line-role="outline" d="([^"]+)"', svg).group(1)
        points = np.array([[float(v) for v in p.split(",")] for p in re.findall(r"[-\d.]+,[-\d.]+", path)])
        source = np.array(outline)
        span_svg, span_mm = points.max(0) - points.min(0), source.max(0) - source.min(0)
        np.testing.assert_allclose(span_svg, span_mm, atol=0.02)  # 1 unit = 1 mm on both axes
        np.testing.assert_allclose(points - points[0], source - source[0], atol=0.02)  # no mirror, no rotation
        layout = proof._layout(geometry, spec)
        part_top = points[:, 1].min()
        part_bottom = points[:, 1].max()
        self.assertGreater(part_top, 15.0)                                     # below the subtitle line
        self.assertLess(part_bottom, layout["page"][1] - layout["legend_h"])  # above the legend


if __name__ == "__main__":
    unittest.main()
