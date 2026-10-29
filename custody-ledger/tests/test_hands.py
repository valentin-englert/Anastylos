"""Colour-free hand presence (H1 adversarial cases A1, A2, A7) and zone visits.

Run from the custody-ledger directory:

    ..\\.venv\\Scripts\\python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from anastylos_custody.hands import HandTracker, VisitLog  # noqa: E402
from anastylos_custody.signals import SignalTracker  # noqa: E402

SHAPE = (80, 120)
WATCHED = (10, 10, 110, 70)


def scene():
    gray = np.full(SHAPE, 128.0, np.float32)
    gray[30:50, 40:80] = 90.0          # a device
    return gray


def run(tracker, frames):
    masks, previous, t = [], None, 0.0
    for gray in frames:
        motion = np.zeros(SHAPE, bool) if previous is None else np.abs(gray - previous) > 14
        t = round(t + 0.2, 3)
        masks.append(tracker.update(t, gray, motion))
        previous = gray
    return masks


def with_arm(gray, tip_col=60):
    arm = gray.copy()
    arm[10:40, tip_col - 4:tip_col + 4] = 230.0   # enters across the top border and ends over the device
    return arm


class HandTrackerTests(unittest.TestCase):
    def test_arm_from_the_border_is_a_hand(self):
        masks = run(HandTracker(SHAPE, WATCHED), [scene()] * 5 + [with_arm(scene())] * 3)
        self.assertTrue(masks[-1][35, 60])

    def test_resting_hand_stays_a_hand(self):  # A2
        masks = run(HandTracker(SHAPE, WATCHED), [scene()] * 5 + [with_arm(scene())] * 20)
        self.assertTrue(masks[-1][35, 60], "a motionless arm still reaches the border")

    def test_static_skin_coloured_part_inside_is_never_a_hand(self):  # A1, colour does not matter
        part = scene()
        part[35:45, 90:100] = 200.0        # appears inside the sheet, touches no border
        masks = run(HandTracker(SHAPE, WATCHED), [scene()] * 5 + [part] * 10)
        self.assertFalse(masks[-1].any())

    def test_arm_entering_from_outside_the_camera_view_is_a_hand(self):
        visible = np.ones(SHAPE, bool)
        visible[:, :30] = False                            # the sheet's left part is outside the picture
        arm = scene()
        arm[:, :30] = 0.0                                  # unseen pixels never change
        moved = arm.copy()
        moved[34:42, 30:62] = 230.0                        # an arm reaching in from the unseen side
        tracker = HandTracker(SHAPE, WATCHED)
        masks = [tracker.update(round(0.2 * (i + 1), 3), g, np.zeros(SHAPE, bool) if i == 0 else np.abs(g - prev) > 14, visible)
                 for i, (g, prev) in enumerate(zip([arm] * 5 + [moved] * 8, [arm] * 6 + [moved] * 7))]
        self.assertTrue(masks[-1][38, 50])
        self.assertFalse(HandTracker(SHAPE, WATCHED).update(0.2, arm, np.zeros(SHAPE, bool), visible).any())

    def test_hand_covers_only_where_it_is(self):  # A7
        masks = run(HandTracker(SHAPE, WATCHED), [scene()] * 5 + [with_arm(scene(), tip_col=45)] * 3)
        self.assertTrue(masks[-1][35, 45])
        self.assertFalse(masks[-1][35, 75])


class VisitLogTests(unittest.TestCase):
    def test_visits_are_intervals_per_zone(self):
        log = VisitLog({"SITE": (56, 30, 64, 40), "CELL": (90, 30, 100, 40)}, min_overlap_px=5)
        hand = np.zeros(SHAPE, bool)
        for t, covered in [(1.0, False), (1.2, True), (1.4, True), (1.6, False), (2.0, True)]:
            log.update(t, hand | (np.pad(np.ones((10, 8), bool), ((30, 40), (56, 56))) if covered else hand))
        self.assertEqual(log.close(), {"SITE": [[1.2, 1.4], [2.0, 2.0]]})


class SignalHandMaskTests(unittest.TestCase):
    """The hand plane the observers use comes from geometry and time, never from colour (H1)."""

    SIGNAL = {"normalize_sigma_mm": 15.0, "blur_sigma_mm": 0.0, "motion_threshold": 14.0, "change_threshold": 30.0}
    ARMS_BGR = {"light skin": (150, 180, 230), "dark skin": (45, 60, 90), "blue glove": (200, 90, 30)}

    def frames(self, arm_bgr=None, part_bgr=None):
        empty = np.full(SHAPE + (3,), 235, np.uint8)
        empty[30:50, 40:80] = (70, 70, 70)                 # a dark device
        later = empty.copy()
        if arm_bgr is not None:
            later[0:40, 56:64] = arm_bgr                   # enters across the sheet border
        if part_bgr is not None:
            later[35:45, 90:100] = part_bgr                # lies inside the sheet
        return [empty] * 5 + [later] * 6

    def hand_planes(self, frames):
        tracker = SignalTracker(self.SIGNAL, {"intrusion_min_area_mm2": 10.0}, 1.0)
        return [tracker.update(round(0.2 * (i + 1), 3), f).hand for i, f in enumerate(frames)]

    def test_resting_arm_of_any_colour_is_found_over_paper(self):
        arm = np.zeros(SHAPE, bool)
        arm[0:40, 56:64] = True
        over_paper = arm.copy()
        over_paper[30:, :] = False
        for name, bgr in self.ARMS_BGR.items():
            plane = self.hand_planes(self.frames(arm_bgr=bgr))[-1]   # 1.2 s after the arm stopped moving
            self.assertTrue(plane[over_paper].all(), name)
            self.assertFalse((plane & ~arm).any(), name)

    def test_skin_coloured_part_inside_is_not_a_hand(self):
        self.assertFalse(self.hand_planes(self.frames(part_bgr=self.ARMS_BGR["light skin"]))[-1].any())


if __name__ == "__main__":
    unittest.main()
