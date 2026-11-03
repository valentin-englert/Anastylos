"""Outlines on a synthetic bright sheet: separate parts, no cable, a shadow bridge does not join them."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

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


class SeparateTest(unittest.TestCase):
    def test_shadow_stays_out_and_a_part_as_pale_as_the_sheet_gets_a_box(self):
        empty = np.full((800, 1200, 3), (205, 212, 208), np.uint8)
        image = empty.copy()
        image[300:520, 120:320] = (empty[300:520, 120:320] * 0.62).astype(np.uint8)  # shadow of the dark part
        cv2.rectangle(image, (100, 280), (300, 500), (30, 30, 30), -1)
        image[440:520, 700:1000] = (empty[440:520, 700:1000] * 0.62).astype(np.uint8)  # shadow of the pale part
        masks, boxes = sheet_outlines.separate(image, empty, (0, 0, 1200, 800), PPM)
        self.assertEqual(len(masks), 1)
        self.assertTrue(masks[0][400, 200])
        self.assertFalse(masks[0][510, 200])
        self.assertEqual(len(boxes), 1)
        self.assertTrue(boxes[0][0] < 700 and boxes[0][2] > 1000)

    def test_shadow_is_taken_off_a_pale_part_or_the_mask_is_refused(self):
        empty = np.full((800, 1200, 3), (205, 212, 208), np.uint8)
        image = empty.copy()
        image[400:500, 300:800] = (empty[400:500, 300:800] * 0.62).astype(np.uint8)
        mask = np.zeros(empty.shape[:2], bool)
        mask[200:500, 300:800] = True  # 200 rows of pale part, 100 rows of shadow
        cleaned = sheet_outlines.without_shadow(mask, image, empty, PPM)
        self.assertTrue(cleaned[300, 500])
        self.assertFalse(cleaned[480, 500])
        self.assertIsNone(sheet_outlines.without_shadow(mask, (empty * 0.62).astype(np.uint8), empty, PPM))

    def test_firm_material_as_dark_as_a_shadow_stays(self):
        empty = np.full((800, 1200, 3), (205, 212, 208), np.uint8)
        image = (empty * 0.62).astype(np.uint8)
        mask = np.zeros(empty.shape[:2], bool)
        mask[200:500, 300:800] = True
        firm = mask.copy()
        cleaned = sheet_outlines.without_shadow(mask, image, empty, PPM, firm)
        self.assertTrue(cleaned[300, 500])

    def test_refine_keeps_the_measured_mask_when_sam_would_lose_it(self):
        empty = np.full((800, 1200, 3), (205, 212, 208), np.uint8)
        image = empty.copy()
        cv2.rectangle(image, (300, 200), (800, 500), (30, 30, 30), -1)
        firm = np.zeros(empty.shape[:2], bool)
        firm[200:500, 300:800] = True
        rectified = source.RectifiedFrame(image, PPM, (0.0, 0.0), np.eye(3), {})
        tiny = np.zeros(empty.shape[:2], bool)
        tiny[200:250, 300:800] = True
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(sheet_outlines.muse_sam, "sam_mask", return_value=(tiny, {})):
            refined, method = sheet_outlines.refine_part(rectified, np.zeros(firm.shape, bool), firm, Path(tmp))
        self.assertTrue((refined == firm).all())
        self.assertIn("difference", method)

    def test_sheet_flood_crosses_a_soft_shadow_but_not_a_sharp_edge(self):
        empty = np.full((800, 1200, 3), (205, 212, 208), np.uint8)
        fade = np.ones((800, 1200, 1), np.float32)
        fade[300:500, 500:800] = 0.62
        fade = cv2.GaussianBlur(fade, (0, 0), 40)[..., None]  # soft cast shadow
        image = (empty * fade).astype(np.uint8)
        cv2.rectangle(image, (100, 100), (300, 300), (90, 95, 92), -1)  # grey as a shadow, but with a sharp edge
        reached = sheet_outlines.reached_by_sheet(image, empty, (0, 0, 1200, 800), PPM)
        self.assertTrue(reached[400, 650])
        self.assertFalse(reached[200, 200])

    def test_refine_fills_what_sam_leaves_out_on_sure_material(self):
        empty = np.full((800, 1200, 3), (205, 212, 208), np.uint8)
        image = empty.copy()
        cv2.rectangle(image, (300, 200), (800, 500), (30, 30, 30), -1)
        firm = np.zeros(empty.shape[:2], bool)
        firm[200:500, 300:800] = True
        notched = firm.copy()
        notched[350:500, 500:600] = False  # a cable SAM followed instead of the board
        rectified = source.RectifiedFrame(image, PPM, (0.0, 0.0), np.eye(3), {})
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(sheet_outlines.muse_sam, "sam_mask", return_value=(notched, {})):
            refined, method = sheet_outlines.refine_part(rectified, np.zeros(firm.shape, bool), firm, Path(tmp))
        self.assertTrue(refined[450, 550])
        self.assertIn("SAM", method)

    def test_an_empty_sheet_gives_nothing(self):
        empty = np.full((800, 1200, 3), (205, 212, 208), np.uint8)
        self.assertEqual(sheet_outlines.separate(empty, empty, (0, 0, 1200, 800), PPM), ([], []))


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
