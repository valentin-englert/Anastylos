"""Scoring rules fixed before the first SNES-01 run. Needs the calibration virtualenv.

Run from the custody-ledger directory:

    ..\.venv\Scripts\python -m unittest discover -s tests -v
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import unittest

from anastylos_custody.scoring import score


def detection(event_id, zone, before, after, kind="appeared", zone_kind="tray_cell"):
    return {"id": event_id, "zone": zone, "zone_kind": zone_kind, "kind": kind, "t_before_s": before, "t_after_s": after, "t_confirmed_s": after + 1}


def gt(cell_events, dont_care=(), site_events=()):
    return {"schema": "custody-gt/events@1", "status": "test", "cell_events": list(cell_events), "cell_dont_care": list(dont_care), "site_events": list(site_events)}


class ScoringTests(unittest.TestCase):
    def test_overlap_within_tolerance_matches_once(self):
        truth = gt([{"id": "G1", "cell": 1, "t_before": 20, "t_after": 21, "certain": True}])
        report = score({"events": [detection("E1", "cell-1", 22, 24), detection("E2", "cell-1", 22.5, 24)]}, truth)["u2_tray_cells"]
        self.assertEqual(report["detected_true"], 1)
        self.assertEqual(len(report["false_positives"]), 1)

    def test_uncertain_truth_is_neutral_and_dont_care_is_ignored(self):
        truth = gt([{"id": "G1", "cell": 1, "t_before": 20, "t_after": 21, "certain": False}], dont_care=[{"cell": 5, "t_from": 100, "t_to": 200}])
        report = score({"events": [detection("E1", "cell-1", 20, 21), detection("E2", "cell-5", 120, 130)]}, truth)["u2_tray_cells"]
        self.assertEqual((report["certain_gt"], report["false_positives"], len(report["matched_uncertain"]), len(report["ignored_dont_care"])), (0, [], 1, 1))

    def test_other_zone_at_same_time_is_a_false_positive(self):
        truth = gt([{"id": "G1", "cell": 2, "t_before": 98, "t_after": 103, "certain": True}])
        report = score({"events": [detection("E1", "cell-3", 92, 104)]}, truth)["u2_tray_cells"]
        self.assertEqual(report["missed"], ["G1"])
        self.assertEqual(report["false_positives"][0]["same_time_gt_other_zone"], ["G1"])


if __name__ == "__main__":
    unittest.main()
