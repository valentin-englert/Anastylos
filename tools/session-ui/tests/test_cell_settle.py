"""Storage-cell settle measurement on synthetic frames: a fake rectifier paints cells over an empty sheet (no real video)."""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

UI_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UI_ROOT))

import cell_settle_worker as worker  # noqa: E402

PPM = 1.0
FPS = 10.0
SECONDS = 30
CELLS = {"cell-1": (318, 50, 26, 26), "cell-2": (318, 79, 26, 26), "cell-3": (318, 108, 26, 26), "cell-4": (318, 137, 26, 26)}
# cell -> seconds in which something lies in it (hands passing, then the part staying)
FILLED = {"cell-1": [(12, 31)], "cell-2": [(5, 8), (20, 31)], "cell-4": [(0, 31)]}


def sheet_at(t: float, extra=None) -> np.ndarray:
    image = np.full((297, 420, 3), 200.0, np.float32)
    for cell, spans in FILLED.items():
        if any(a <= t < b for a, b in spans):
            x, y, w, h = CELLS[cell]
            image[y:y + h, x:x + w] = (40.0, 60.0, 200.0)  # a coloured part: strong luminance and chroma change
    if extra:
        extra(image)
    return image


class CellSettleTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.video = self.tmp / "clip.avi"
        writer = cv2.VideoWriter(str(self.video), cv2.VideoWriter_fourcc(*"MJPG"), FPS, (32, 24))
        for i in range(int(FPS * SECONDS)):
            writer.write(np.full((24, 32, 3), i % 256, np.uint8))
        writer.release()
        self.sha = hashlib.sha256(self.video.read_bytes()).hexdigest()
        patcher = mock.patch.dict(os.environ, {"ANASTYLOS_DRAWING_CACHE": str(self.tmp / "cache")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def measure(self, extra=None, failing=lambda t: False, sha=None):
        def rectify(video, index, ppm):
            t = index / FPS
            return None if failing(t) else sheet_at(t, extra)
        return worker.measure(self.video, sha or self.sha, ppm=PPM, rectify=rectify, cell_rects=lambda: CELLS)

    def test_each_cell_settles_when_its_content_last_changed_for_good(self):
        result = self.measure()
        self.assertEqual(result["reference"]["status"], "ok")
        cells = result["cells"]
        self.assertEqual((cells["cell-1"]["occupied"], cells["cell-1"]["settled_s"], cells["cell-1"]["first_s"]), (True, 12.0, 12.0))
        self.assertEqual((cells["cell-2"]["settled_s"], cells["cell-2"]["first_s"]), (20.0, 20.0))  # the hand passing at 5-8 s is not the settle time
        self.assertEqual((cells["cell-3"]["occupied"], cells["cell-3"]["settled_s"]), (False, None))
        self.assertTrue(all(cells[c]["reference_ok"] for c in ("cell-1", "cell-2", "cell-3")))
        self.assertEqual((result["memory_written"], result["model_called"]), (False, False))
        self.assertEqual(result["video"]["sha256"], self.sha)

    def test_a_cell_that_was_not_empty_at_the_start_has_no_settle_time(self):
        cell = self.measure()["cells"]["cell-4"]
        self.assertEqual((cell["reference_ok"], cell["occupied"], cell["settled_s"]), (False, False, None))

    def test_frames_that_do_not_register_are_left_out(self):
        result = self.measure(failing=lambda t: 15 <= t < 18)
        self.assertEqual(result["cells"]["cell-1"]["settled_s"], 12.0)
        self.assertNotIn(16.0, result["times"])

    def test_a_sheet_that_is_not_empty_at_the_start_gives_no_reference(self):
        def part(image):
            image[100:200, 120:260] = (40.0, 60.0, 200.0)
        result = self.measure(extra=part)
        self.assertEqual(result["reference"]["status"], "no_reference")
        self.assertIn("not empty", result["reference"]["reason"])
        self.assertEqual(result["cells"], {})

    def test_too_few_registered_reference_frames_give_no_reference(self):
        result = self.measure(failing=lambda t: t < 4.0)
        self.assertEqual(result["reference"]["status"], "no_reference")
        self.assertIn("fewer than two", result["reference"]["reason"])

    def test_another_video_is_refused(self):
        with self.assertRaisesRegex(ValueError, "differs from the SHA-256"):
            self.measure(sha="0" * 64)

    def test_settle_time_helper_needs_the_run_to_reach_the_last_sample(self):
        times = [0.0, 1.0, 2.0, 3.0, 4.0]
        self.assertEqual(worker.settle_time(times, [0.0, 0.5, 0.0, 0.5, 0.5], 0.25), 3.0)
        self.assertIsNone(worker.settle_time(times, [0.5, 0.5, 0.5, 0.5, 0.0], 0.25))
        self.assertEqual(worker.settle_time(times, [0.0, 0.1, 0.4, 0.5, 0.5], 0.05), 1.0)  # a low limit finds the first of several parts


if __name__ == "__main__":
    unittest.main()
