"""Gate 1B unit tests: selection, tracklets, evidence rules, gating.

Needs OpenCV + NumPy (shared calibration venv). Synthetic fixtures
only; no video, no labels, no network.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import gate1_run as g1
import gate1b_extract as ext
import gate1b_run as b


def _blob(x, y):
    return {"centroid": [float(x), float(y)], "bbox": [x - 5, y - 5, 10, 10],
            "area": 5000}


def _track(points, duration=2.0):
    path = [{"frame": f"f{i}.jpg", "pts_time_s": float(i),
             "centroid_qpx": [float(x), float(y)]}
            for i, (x, y) in enumerate(points)]
    length = sum(float(np.hypot(b[0] - a[0], b[1] - a[1]))
                 for a, b in zip(points, points[1:]))
    return {"id": "t", "path": path, "path_length_qpx": length,
            "duration_s": duration, "gaps": 0}


class Selection(unittest.TestCase):
    def test_window_range_is_pts_inclusive(self):
        index = {"frames": [{"file": f"f{i}.jpg", "pts_time_s": float(i)}
                            for i in range(10)]}
        got = b.window_frames_10(index, 3.0, 5.0)
        self.assertEqual([f["file"] for f in got],
                         ["f3.jpg", "f4.jpg", "f5.jpg"])

    def test_subsample_takes_every_nth(self):
        entries = [{"file": f"f{i}.jpg"} for i in range(7)]
        self.assertEqual([e["file"] for e in b.subsample(entries, 2)],
                         ["f0.jpg", "f2.jpg", "f4.jpg", "f6.jpg"])

    def test_long_window_capped_deterministically(self):
        doc = {"events": [{"id": "E1", "before_t": 0, "after_t": 200,
                           "before_frame": "a", "after_frame": "b",
                           "persistent_regions": [
                               {"role": "disappearance",
                                "bbox_xywh": [0, 0, 10, 10]}]}]}
        windows = b.select_windows(doc)
        self.assertEqual((windows[0]["start_t"], windows[0]["end_t"]),
                         (0, b.WINDOW_CAP_S))

    def test_events_without_regions_skipped(self):
        doc = {"events": [{"id": "E1", "before_t": 0, "after_t": 5,
                           "before_frame": "a", "after_frame": "b",
                           "persistent_regions": []}]}
        self.assertEqual(b.select_windows(doc), [])

    def test_showinfo_parse_order(self):
        stderr = ("[Parsed_showinfo_2 @ x] n:0 pts:0 pts_time:1.5\n"
                  "[Parsed_showinfo_2 @ x] n:1 pts:1 pts_time:1.6\n"
                  "noise pts_time:9.9 without tag\n")
        self.assertEqual(ext.parse_showinfo(stderr), [1.5, 1.6])


class Tracklets(unittest.TestCase):
    def test_smooth_motion_is_one_track(self):
        blobs = [[_blob(100 + 5 * i, 200)] for i in range(5)]
        tracks = b.link_tracklets(blobs, 0.1)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(len(tracks[0]["observations"]), 5)
        self.assertEqual(tracks[0]["gaps"], 0)

    def test_teleport_splits_track(self):
        blobs = [[_blob(100, 200)], [_blob(100, 200)], [_blob(500, 800)],
                 [_blob(500, 800)]]
        tracks = b.link_tracklets(blobs, 0.1)
        self.assertEqual(len(tracks), 2)

    def test_short_gap_bridged_and_counted(self):
        blobs = [[_blob(100, 200)], [], [_blob(102, 202)]]
        tracks = b.link_tracklets(blobs, 0.1)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]["gaps"], 1)

    def test_long_gap_splits(self):
        blobs = [[_blob(100, 200)], [], [], [], [_blob(102, 202)]]
        tracks = b.link_tracklets(blobs, 0.1)
        self.assertEqual(len(tracks), 2)


class EvidenceRules(unittest.TestCase):
    def test_interaction_needs_consecutive_roi_frames(self):
        roi = [v // 4 for v in g1.ASSEMBLY_ROI]
        inside = [roi[0] + 10, roi[1] + 10]
        track = _track([[0, 0], inside, inside, inside, [0, 0]])
        self.assertTrue(b.interaction(track)["interaction_candidate"])
        track2 = _track([[0, 0], inside, [0, 0], inside, [0, 0]])
        self.assertFalse(b.interaction(track2)["interaction_candidate"])

    def test_transport_needs_inside_out_path(self):
        roi = [v // 4 for v in g1.ASSEMBLY_ROI]
        inside = [roi[0] + 10, roi[1] + 10]
        outside = [roi[0] + 10, roi[1] + roi[3] + 100]
        mid = [(inside[0] + outside[0]) / 2,
               (inside[1] + outside[1]) / 2]
        track = _track([inside, inside, mid, outside, outside])
        self.assertTrue(b.transport(track)["transport_candidate"])
        staying = _track([inside] * 5)
        result = b.transport(staying)
        self.assertFalse(result["transport_candidate"])
        self.assertIn("never leaves assembly ROI", result["reasons"])

    def test_short_fast_track_rejected(self):
        roi = [v // 4 for v in g1.ASSEMBLY_ROI]
        inside = [roi[0] + 10, roi[1] + 10]
        outside = [roi[0] + 200, roi[1] + roi[3] + 100]
        track = _track([inside, outside], duration=0.2)
        result = b.transport(track)
        self.assertFalse(result["transport_candidate"])
        self.assertIn("duration under 1 s", result["reasons"])


class ProposalGating(unittest.TestCase):
    def _transport(self):
        roi = [v // 4 for v in g1.ASSEMBLY_ROI]
        inside = [roi[0] + 10, roi[1] + 10]
        outside = [roi[0] + 10, roi[1] + roi[3] + 100]
        track = _track([inside, inside, outside, outside])
        track["transport"] = b.transport(track)
        return track

    def test_no_tracks_abstains(self):
        ranked, reason = b.proposals([], [], [])
        self.assertEqual(ranked, [])
        self.assertIn("no hand tracklets", reason)

    def test_no_transport_abstains(self):
        track = _track([[0, 0]] * 4)
        track["transport"] = {"transport_candidate": False}
        ranked, reason = b.proposals([track], [], [])
        self.assertEqual(ranked, [])
        self.assertIn("none leaves", reason)

    def test_transport_without_deposit_abstains(self):
        ranked, reason = b.proposals([self._transport()], [], [])
        self.assertEqual(ranked, [])
        self.assertIn("without persistent deposit", reason)

    def test_far_endpoint_abstains(self):
        track = self._transport()
        dep = {"bbox_xywh": [0, 0, 40, 40], "persist_share": 1.0}
        ranked, reason = b.proposals([track], [dep], [])
        self.assertEqual(ranked, [])
        self.assertIn("far from every deposit", reason)

    def test_linked_endpoint_proposes_with_uncertainty(self):
        track = self._transport()
        end = track["path"][-1]["centroid_qpx"]
        dep = {"bbox_xywh": [int(end[0] * 4 - 20), int(end[1] * 4 - 20),
                              40, 40],
               "persist_share": 1.0}
        ranked, reason = b.proposals([track], [dep], [])
        self.assertEqual(len(ranked), 1)
        self.assertEqual(reason, "")
        self.assertIn("not observed", ranked[0]["uncertainty"])
        self.assertFalse(ranked[0]["src_gate1_linked"])


if __name__ == "__main__":
    unittest.main()
