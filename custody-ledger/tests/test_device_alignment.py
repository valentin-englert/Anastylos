"""Device re-registration on synthetic texture (ECC with SIFT seed). Needs the calibration virtualenv.

Run from the custody-ledger directory:

    ..\.venv\Scripts\python -m unittest discover -s tests -v
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import unittest

import cv2
import numpy as np

from anastylos_custody.device_alignment import AlignmentLimits, DevicePose

LIMITS = AlignmentLimits(max_shift_px=40, max_rotation_deg=30, min_correlation=0.8)


def textured(shape=(161, 241), seed=7):
    rng = np.random.default_rng(seed)
    return cv2.GaussianBlur(rng.uniform(0, 255, shape).astype(np.float32), (0, 0), 2.5) * 2.0


def moved(image, dx, dy, degrees):
    height, width = image.shape
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), degrees, 1.0)
    matrix[:, 2] += (dx, dy)
    return cv2.warpAffine(image, matrix, (width, height), borderMode=cv2.BORDER_REFLECT)


class DevicePoseTests(unittest.TestCase):
    def setUp(self):
        self.reference = textured()
        self.region = (0, 0, self.reference.shape[1], self.reference.shape[0])
        self.empty = np.zeros(self.reference.shape, bool)
        self.pose = DevicePose(self.region, self.empty.copy(), LIMITS)
        self.pose.initialize(0.0, self.reference, self.empty, self.empty)

    def test_recovers_a_small_slide_and_rotation(self):
        current = moved(self.reference, 6, -4, 3.0)
        self.assertTrue(self.pose.realign(1.0, current, self.empty))
        gray, _motion, _skin = self.pose.aligned_layers(1.0, current, self.empty, self.empty)
        inner = (slice(30, -30), slice(30, -30))
        self.assertLess(float(np.abs(gray[inner] - self.reference[inner]).mean()), 6.0)

    def test_rejects_an_unrelated_image(self):
        self.assertFalse(self.pose.realign(1.0, textured(seed=99), self.empty))
        self.assertFalse(self.pose.realignments[-1]["accepted"])

    def test_one_attempt_per_sample(self):
        current = moved(self.reference, 3, 2, 1.0)
        self.pose.realign(1.0, current, self.empty)
        self.pose.realign(1.0, current, self.empty)
        self.assertEqual(len(self.pose.realignments), 1)


if __name__ == "__main__":
    unittest.main()
