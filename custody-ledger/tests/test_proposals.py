"""Adaptive discovery pass 1 on synthetic planes: colour-free occluders, merge/split, pose break.

Run from the custody-ledger directory:

    ..\\.venv\\Scripts\\python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from anastylos_custody.proposals import DEFAULTS, ProposalTracker, merge_into_sources  # noqa: E402

PPM = 1.0  # 1 px per mm keeps areas readable
SHAPE = (60, 100)
DEVICE = (slice(20, 50), slice(20, 80))  # device occupies rows 20-49, cols 20-79


def base():
    gray = np.full(SHAPE, 200.0, np.float32)  # paper
    gray[DEVICE] = 100.0
    return gray


def tracker():
    mask = np.zeros(SHAPE, bool)
    mask[DEVICE] = True
    params = {**DEFAULTS, "min_area_mm2": 8.0, "occluder_min_outside_mm2": 4.0}
    return ProposalTracker(mask, base(), PPM, 30.0, params)


def run(track, timeline):
    """timeline: list of (seconds, gray, moving) sampled at 5 Hz from t=0.2."""
    t, previous = 0.0, base()
    for seconds, gray, moving in timeline:
        for _ in range(int(round(seconds * 5))):
            t = round(t + 0.2, 3)
            motion = np.abs(gray - previous) > 14 if not moving else np.ones(SHAPE, bool)
            track.update(t, gray, motion)
            previous = gray
    return track


def removed(*boxes):
    gray = base()
    for rows, cols in boxes:
        gray[rows, cols] = 160.0  # well floor visible after the part left
    return gray


def with_arm(gray):
    arm = gray.copy()
    arm[0:35, 30:40] = 250.0  # arm reaches in from the top edge, outside the device
    return arm


class ProposalTests(unittest.TestCase):
    def test_removal_seen_after_arm_leaves_is_one_proposal(self):
        part = (slice(30, 36), slice(50, 56))
        track = run(tracker(), [(1, base(), False), (1, with_arm(base()), True), (2, with_arm(removed(part)), False),
                                (3, removed(part), False)])
        self.assertEqual(len(track.proposals), 1)
        self.assertTrue(track.proposals[0]["mask"][32, 52])
        self.assertFalse(track.proposals[0]["mask"][25, 35], "the arm must never become a source")

    def test_motionless_arm_from_outside_stays_an_occluder(self):
        track = run(tracker(), [(1, base(), False), (1, with_arm(base()), True), (8, with_arm(base()), False)])
        self.assertEqual(track.proposals, [])

    def test_object_inside_device_is_judged_by_change_not_colour(self):
        bright = base()
        bright[30:36, 50:56] = 240.0  # any colour: a part that changes inside the device is a candidate
        track = run(tracker(), [(1, base(), False), (3, bright, False)])
        self.assertEqual(len(track.proposals), 1)

    def test_removals_in_separate_intervals_stay_separate_sources(self):
        a, b = (slice(30, 36), slice(30, 36)), (slice(30, 36), slice(60, 66))
        track = run(tracker(), [(1, base(), False), (3, removed(a), False), (3, removed(a, b), False)])
        self.assertEqual(len(merge_into_sources(track.proposals, PPM, DEFAULTS)), 2)


def piece(cols, t_before=1.0, t_confirmed=3.0, rows=slice(30, 36)):
    mask = np.zeros(SHAPE, bool)
    mask[rows, cols] = True
    return {"mask": mask, "t_before_s": t_before, "t_confirmed_s": t_confirmed, "contrast": 50.0}


class ClusteringTests(unittest.TestCase):
    """One fragmented component versus genuinely distinct components."""

    def test_fragments_of_one_change_merge_and_keep_their_submasks(self):
        groups = merge_into_sources([piece(slice(30, 36)), piece(slice(38, 44))], PPM, DEFAULTS)  # 2 mm apart
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]["members"]), 2)
        self.assertEqual(groups[0]["joins"][0]["gap_mm"], 2.0)

    def test_same_time_alone_never_merges_distant_components(self):
        groups = merge_into_sources([piece(slice(22, 28)), piece(slice(60, 66))], PPM, DEFAULTS)  # 32 mm apart
        self.assertEqual(len(groups), 2)

    def test_neighbours_beyond_their_size_scale_stay_separate(self):
        groups = merge_into_sources([piece(slice(30, 36)), piece(slice(41, 47))], PPM, DEFAULTS)  # gap 5 > 0.5 * 6
        self.assertEqual(len(groups), 2)

    def test_touching_components_from_different_intervals_stay_separate(self):
        groups = merge_into_sources([piece(slice(30, 36)), piece(slice(36, 42), t_before=5.0, t_confirmed=7.0)], PPM, DEFAULTS)
        self.assertEqual(len(groups), 2)


class PoseTests(unittest.TestCase):
    def test_whole_device_change_ends_pose_epoch_without_proposals(self):
        shifted = base()
        shifted[DEVICE] = 160.0  # the whole device looks different at once (flipped or moved away)
        track = run(tracker(), [(1, base(), False), (3, shifted, False)])
        self.assertIsNotNone(track.pose_epoch_end_s)
        self.assertEqual(track.proposals, [])



class EpochSequenceTests(unittest.TestCase):
    """proposals 0.3.0: a pose change ends an epoch; the next quiet scene re-acquires the device."""

    def test_reacquired_device_gets_new_sources_after_a_pose_change(self):
        from anastylos_custody.proposals import EpochSequence
        mask = np.zeros(SHAPE, bool)
        mask[DEVICE] = True
        sequence = EpochSequence(PPM, 30.0, {**DEFAULTS, "min_area_mm2": 8.0})
        opened = base()
        opened[DEVICE] = 160.0                      # the whole device looks different (shell lifted)
        later = opened.copy()
        later[30:36, 50:56] = 60.0                  # then a part is removed from the opened device
        t, previous = 0.0, base()
        for seconds, gray in [(1, base()), (3, opened), (3, later)]:
            for _ in range(int(seconds * 5)):
                t = round(t + 0.2, 3)
                sequence.update(t, gray, np.abs(gray - previous) > 14, lambda: mask)
                previous = gray
        self.assertEqual(len(sequence.epochs), 2)
        self.assertIsNotNone(sequence.epochs[0]["tracker"].pose_epoch_end_s)
        self.assertEqual(sequence.epochs[0]["tracker"].proposals, [])
        self.assertEqual(len(sequence.epochs[1]["tracker"].proposals), 1)

    def test_without_reacquire_the_first_epoch_is_the_last(self):
        from anastylos_custody.proposals import EpochSequence
        mask = np.zeros(SHAPE, bool)
        mask[DEVICE] = True
        sequence = EpochSequence(PPM, 30.0, {**DEFAULTS, "reacquire": False})
        opened = base()
        opened[DEVICE] = 160.0
        t, previous = 0.0, base()
        for seconds, gray in [(1, base()), (4, opened)]:
            for _ in range(int(seconds * 5)):
                t = round(t + 0.2, 3)
                sequence.update(t, gray, np.abs(gray - previous) > 14, lambda: mask)
                previous = gray
        self.assertEqual(len(sequence.epochs), 1)


if __name__ == "__main__":
    unittest.main()
