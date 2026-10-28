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

    def test_unstable_candidate_is_not_confirmed(self):
        frames = timeline([(2, {})])
        for index in range(10):
            frames.append(frame(2.0 + 0.2 * index, part=index % 2 == 0))
        observer = ZoneObserver("cell-3", "tray_cell", CELL, PARAMS)
        self.assertEqual(run(observer, frames), [])


if __name__ == "__main__":
    unittest.main()
