"""Scoring helper tests (no network)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anastylos_sam import eval as SC  # noqa: E402


class IoU(unittest.TestCase):
    def test_identical(self):
        box = {"left": 0, "top": 0, "right": 10, "bottom": 10}
        self.assertEqual(SC.iou(box, box), 1.0)

    def test_disjoint(self):
        self.assertEqual(SC.iou({"left": 0, "top": 0, "right": 5, "bottom": 5},
                                {"left": 9, "top": 9, "right": 12, "bottom": 12}),
                         0.0)

    def test_half_overlap(self):
        value = SC.iou({"left": 0, "top": 0, "right": 10, "bottom": 10},
                       {"left": 5, "top": 0, "right": 15, "bottom": 10})
        self.assertAlmostEqual(value, 50 / 150)


class SegmentationScoring(unittest.TestCase):
    def test_hits_fp_misses(self):
        gt = [{"left": 0, "top": 0, "right": 10, "bottom": 10, "label": "a"},
              {"left": 50, "top": 50, "right": 60, "bottom": 60, "label": "b"}]
        preds = [{"box": {"left": 0, "top": 0, "right": 10, "bottom": 10}},
                 {"box": {"left": 200, "top": 200, "right": 210, "bottom": 210}}]
        score = SC.score_segmentation(preds, gt)
        self.assertEqual((score["hits"], score["false_positives"]), (1, 1))
        self.assertEqual(len(score["misses"]), 1)
        self.assertEqual(score["misses"][0]["label"], "b")
        self.assertEqual(score["coverage"], 0.5)


class TrackingScoring(unittest.TestCase):
    def test_coverage_and_occlusion(self):
        desc = {"frames": [0, 1, 2, 5, 6], "segments": 2,
                "gaps": [[3, 4]], "jumps": []}
        score = SC.score_tracking(desc, transport_frames=[0, 1, 2, 3, 4],
                                  occlusion_frames=[5, 6, 7])
        self.assertAlmostEqual(score["transport_coverage"], 0.6)
        self.assertEqual(score["records_during_occlusion"], [5, 6])
        self.assertEqual(score["occlusion_frames_total"], 3)


class CorrespondenceScoring(unittest.TestCase):
    def test_verdicts(self):
        hyps = [{"key": "p1", "verdict": "HYPOTHESIS_SUPPORTED"},
                {"key": "p2", "verdict": "HYPOTHESIS_SUPPORTED"},
                {"key": "p3", "verdict": "UNKNOWN"},
                {"key": "p4", "verdict": "UNKNOWN"}]
        gt = [{"key": "p1", "expected": "same"},
              {"key": "p2", "expected": "different"},
              {"key": "p3", "expected": "same"},
              {"key": "p4", "expected": "unverifiable"}]
        score = SC.score_correspondence(hyps, gt)
        self.assertEqual((score["correct"], score["incorrect"],
                          score["abstain"], score["excluded"]), (1, 1, 1, 1))


if __name__ == "__main__":
    unittest.main()
