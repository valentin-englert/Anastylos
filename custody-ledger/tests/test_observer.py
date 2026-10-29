"""Deterministic zone-observer tests on synthetic signal planes. Needs the calibration virtualenv.

Run from the custody-ledger directory:

    ..\.venv\Scripts\python -m unittest discover -s tests -v
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import unittest

import numpy as np

from anastylos_custody.observer import ObserverParams, ZoneGeometry, ZoneObserver
from anastylos_custody.signals import FrameSignals

SIZE = (60, 60)
PARAMS = ObserverParams(change_threshold=30, quiet_hold_s=0.4, max_motion_fraction=0.01, intrusion_min_area_px=4,
                        min_changed_area_px=20, confirm_hold_s=0.6, max_anchor_changed_fraction=0.12)
CELL = ZoneGeometry(content=(20, 20, 40, 40), guard=(18, 18, 42, 42), context=(10, 10, 50, 50))


def frame(t, *, part=False, hand=False, moving=False, skin_part=False):
    gray = np.full(SIZE, 128.0, np.float32)
    skin = np.zeros(SIZE, bool)
    if part:
        gray[25:33, 25:33] = 20.0
        if skin_part:
            skin[25:33, 25:33] = True
    if hand:
        gray[22:30, 0:35] = 200.0
        skin[22:30, 0:35] = True
    motion = np.zeros(SIZE, bool)
    if moving:
        motion[10:50, 10:50] = True
    return FrameSignals(t, gray, motion, skin)


def run(observer, frames):
    for index, signals in enumerate(frames):
        observer.observe(signals, index)
    return [event for event in observer.events if event["kind"] != "reference_set"]


def timeline(spec):
    """spec: list of (seconds, kwargs) segments sampled at 5 Hz."""
    frames, t = [], 0.0
    for seconds, kwargs in spec:
        for _ in range(int(round(seconds * 5))):
            frames.append(frame(round(t, 3), **kwargs))
            t += 0.2
    return frames


class ObserverTests(unittest.TestCase):
    def test_deposit_seen_after_hand_leaves_becomes_appeared_event(self):
        observer = ZoneObserver("cell-1", "tray_cell", CELL, PARAMS)
        events = run(observer, timeline([(2, {}), (1, {"hand": True, "moving": True}), (1, {"hand": True, "part": True}), (3, {"part": True})]))
        self.assertEqual([event["kind"] for event in events], ["appeared"])
        self.assertLess(events[0]["t_before_s"], 2.0, "before must be the last clean sample before the hand arrived")
        self.assertGreaterEqual(events[0]["t_after_s"], 4.0, "after must be the first clean sample once the hand left")

    def test_resting_hand_is_never_read_as_a_change(self):
        observer = ZoneObserver("cell-1", "tray_cell", CELL, PARAMS)
        events = run(observer, timeline([(2, {}), (6, {"hand": True})]))
        self.assertEqual(events, [])
        self.assertGreater(observer.not_clean_s, 5.0)

    def test_skin_coloured_part_inside_zone_is_not_a_hand(self):
        observer = ZoneObserver("cell-8", "tray_cell", CELL, PARAMS)
        events = run(observer, timeline([(2, {}), (1, {"hand": True, "moving": True}), (3, {"part": True, "skin_part": True})]))
        self.assertEqual([event["kind"] for event in events], ["appeared"])

    def test_motion_without_content_change_produces_no_event(self):
        observer = ZoneObserver("cell-2", "tray_cell", CELL, PARAMS)
        events = run(observer, timeline([(2, {}), (2, {"moving": True}), (2, {})]))
        self.assertEqual(events, [])

    def test_cell_declared_occupied_reports_vacated_then_appeared(self):
        observer = ZoneObserver("cell-4", "tray_cell", CELL, PARAMS, initially_occupied=True)
        events = run(observer, timeline([(2, {"part": True}), (1, {"hand": True, "moving": True}), (3, {}),
                                         (1, {"hand": True, "moving": True}), (3, {"part": True})]))
        self.assertEqual([event["kind"] for event in events], ["vacated", "appeared"])
        self.assertEqual(observer.summary()["initially"], "occupied")

    def test_site_declared_empty_reports_filled_then_emptied(self):
        observer = ZoneObserver("W-RED", "site", CELL, PARAMS, initially_occupied=False)
        events = run(observer, timeline([(2, {}), (1, {"hand": True, "moving": True}), (3, {"part": True}),
                                         (1, {"hand": True, "moving": True}), (3, {})]))
        self.assertEqual([event["kind"] for event in events], ["site_filled", "site_emptied"])

    def test_undeclared_zones_keep_v1_0_event_names(self):
        site = ZoneObserver("H1", "site", CELL, PARAMS)
        events = run(site, timeline([(2, {"part": True}), (1, {"hand": True, "moving": True}), (3, {})]))
        self.assertEqual([event["kind"] for event in events], ["site_changed"])
        self.assertNotIn("initially", site.summary())

    def test_unstable_candidate_is_not_confirmed(self):
        frames = timeline([(2, {})])
        for index in range(10):
            frames.append(frame(2.0 + 0.2 * index, part=index % 2 == 0))
        observer = ZoneObserver("cell-3", "tray_cell", CELL, PARAMS)
        self.assertEqual(run(observer, frames), [])


PAPER = ObserverParams(**{**PARAMS.__dict__, "cell_occupancy": "paper_model", "print_stroke_px": 3, "min_part_area_px": 20})


def cell_frame(t, *, part_at=None, hand=False, moving=False, printed_line=True):
    signals = frame(t, hand=hand, moving=moving)
    if printed_line:
        signals.gray[30, 20:40] = 20.0  # a 1 px printed stroke inside the cell
    if part_at is not None:
        r, c = part_at
        signals.gray[r:r + 8, c:c + 8] = 20.0
    return signals


def cell_timeline(spec):
    frames, t = [], 0.0
    for seconds, kwargs in spec:
        for _ in range(int(round(seconds * 5))):
            frames.append(cell_frame(round(t, 3), **kwargs))
            t += 0.2
    return frames


class PaperModelTests(unittest.TestCase):
    """v1.3: cell occupancy judged against paper plus thin print, not against the start image."""

    def test_part_put_back_slightly_shifted_is_not_a_departure(self):
        observer = ZoneObserver("cell-6", "tray_cell", CELL, PAPER)
        events = run(observer, cell_timeline([(2, {"part_at": (24, 24)}), (1, {"hand": True, "moving": True}),
                                              (3, {"part_at": (26, 27)})]))
        self.assertNotIn("vacated", [e["kind"] for e in events])
        self.assertEqual(observer.summary()["initially_measured"], "occupied")

    def test_removal_from_an_undeclared_full_cell_is_vacated(self):
        observer = ZoneObserver("cell-4", "tray_cell", CELL, PAPER)
        events = run(observer, cell_timeline([(2, {"part_at": (24, 24)}), (1, {"hand": True, "moving": True}), (3, {})]))
        self.assertEqual([e["kind"] for e in events], ["vacated"])

    def test_printed_stroke_alone_is_an_empty_cell(self):
        observer = ZoneObserver("cell-2", "tray_cell", CELL, PAPER, initially_occupied=True)
        run(observer, cell_timeline([(3, {})]))
        summary = observer.summary()
        self.assertEqual(summary["initially_measured"], "empty")
        self.assertTrue(summary["declared_contradicts_measurement"])

    def test_default_rule_keeps_start_relative_behaviour(self):
        observer = ZoneObserver("cell-6", "tray_cell", CELL, PARAMS, initially_occupied=True)
        events = run(observer, cell_timeline([(2, {"part_at": (24, 24)}), (1, {"hand": True, "moving": True}),
                                              (3, {"part_at": (26, 27)})]))
        self.assertEqual([e["kind"] for e in events], ["vacated"], "v1.2 behaviour: shifted part reads as a departure")


class _StillDevice:
    """Device whose re-registration always succeeds without moving (the device rests)."""

    region = (0, 0, SIZE[1], SIZE[0])

    def __init__(self):
        self.realignments = []
        self.reference = None

    def initialize(self, t_s, gray, motion, skin):
        self.reference = gray

    def realign(self, t_s, gray, skin):
        self.realignments.append({"t_s": t_s, "accepted": True, "shift_px": 0.0, "rotation_deg": 0.0})
        return True

    def aligned_layers(self, t_s, gray, motion, skin):
        return gray, motion, skin


def ring_part_frame(t, *, ring_part):
    signals = frame(t)
    if ring_part:
        signals.gray[10:18, 10:50] = 20.0  # a strip of the anchor ring, outside guard and content
    return signals


class RingRuleTests(unittest.TestCase):
    """v1.2: what a changed anchor ring means once the device pose is verified."""

    def _site(self, rule):
        ring = np.zeros((40, 40), bool)
        ring[:, :] = True
        ring[8:32, 8:32] = False  # context (10..50) minus the guard (18..42)
        geometry = ZoneGeometry(CELL.content, CELL.guard, CELL.context, ring)
        params = ObserverParams(**{**PARAMS.__dict__, "ring_change_with_verified_pose": rule})
        return ZoneObserver("G-01-01", "site", geometry, params, device=_StillDevice())

    def _run_ring_removal(self, observer):
        frames = [ring_part_frame(round(0.2 * i, 3), ring_part=i < 10) for i in range(40)]
        for index, signals in enumerate(frames):
            observer.observe(signals, index)
        return [event["kind"] for event in observer.events if event["kind"] != "reference_set"]

    def test_default_rule_keeps_pose_break(self):
        observer = self._site("pose_break")
        self.assertIn("pose_epoch_break", self._run_ring_removal(observer))
        self.assertEqual(observer.status, "epoch_broken")

    def test_content_rule_adopts_the_ring_and_keeps_observing(self):
        observer = self._site("content")
        kinds = self._run_ring_removal(observer)
        self.assertIn("anchor_ring_changed", kinds)
        self.assertNotIn("pose_epoch_break", kinds)
        self.assertEqual(observer.status, "observing")


if __name__ == "__main__":
    unittest.main()
