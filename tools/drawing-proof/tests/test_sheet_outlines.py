"""Outlines on a synthetic bright sheet: separate parts, no cable, a shadow bridge does not join them."""

from pathlib import Path
import sys
import tempfile
import unittest

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sheet_outlines
import source

PPM = 4.0


def _sheet(bridge: bool = False) -> np.ndarray:
    image = np.full((800, 1200, 3), (205, 212, 208), np.uint8)
    cv2.rectangle(image, (100, 100), (400, 700), (90, 95, 92), -1)
    cv2.rectangle(image, (550, 100), (1100, 350), (95, 90, 92), -1)
    cv2.rectangle(image, (600, 420), (1000, 650), (40, 70, 130), -1)
    cv2.line(image, (800, 650), (1150, 790), (15, 15, 15), 10)  # a cable leaving the board
    if bridge:
        cv2.rectangle(image, (700, 350), (800, 420), (150, 156, 152), -1)  # a soft shadow between two parts
    blurred = cv2.GaussianBlur(image, (9, 9), 0).astype(np.float32)  # Otsu needs a real histogram, not two exact values
    return np.clip(blurred + np.random.default_rng(0).normal(0, 6, image.shape), 0, 255).astype(np.uint8)


class FindOutlinesTest(unittest.TestCase):
    def test_finds_each_part_without_the_cable(self):
        masks = sheet_outlines.find_outlines(_sheet(), (0, 0, 1200, 800), PPM)
        self.assertEqual(len(masks), 3)
        board = next(m for m in masks if m[500, 800])
        self.assertFalse(board[720, 980])

    def test_shadow_bridge_keeps_parts_apart(self):
        masks = sheet_outlines.find_outlines(_sheet(bridge=True), (0, 0, 1200, 800), PPM)
        self.assertEqual(len(masks), 3)
        self.assertFalse(next(m for m in masks if m[200, 800])[500, 800])


class WriteJobTest(unittest.TestCase):
    def test_job_needs_confirmation_and_never_overwrites(self):
        image = _sheet()
        rectified = source.RectifiedFrame(image, PPM, (0.0, 0.0), np.eye(3), {})
        mask = sheet_outlines.find_outlines(image, (0, 0, 1200, 800), PPM)[0]
        with tempfile.TemporaryDirectory() as tmp:
            name = sheet_outlines.write_job(Path(tmp), rectified, mask, "Back shell", "test")
            self.assertEqual(name, "outline-back-shell")
            job = Path(tmp) / "drawings" / "jobs" / name
            self.assertIn("observed_outline", (job / "drawing.svg").read_text(encoding="utf-8"))
            self.assertIn("NEEDS_CONFIRMATION", (job / "progress.json").read_text(encoding="utf-8"))
            with self.assertRaises(FileExistsError):
                sheet_outlines.write_job(Path(tmp), rectified, mask, "Back shell", "test")


if __name__ == "__main__":
    unittest.main()
