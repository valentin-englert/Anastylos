"""Stdlib PNG codec tests (no network, no OpenCV)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anastylos_sam import png as PNG  # noqa: E402


class PngRoundTrip(unittest.TestCase):
    def test_small_pattern(self):
        raster = bytes((0, 0, 1, 1, 0, 1))
        data = PNG.encode_gray8(3, 2, raster)
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        width, height, back = PNG.decode_gray8(data)
        self.assertEqual((width, height, back), (3, 2, raster))

    def test_single_pixel(self):
        for value in (0, 1):
            data = PNG.encode_gray8(1, 1, bytes((value,)))
            self.assertEqual(PNG.decode_gray8(data), (1, 1, bytes((value,))))

    def test_checkerboard_100x57(self):
        width, height = 100, 57
        raster = bytes((x + y) % 2 for y in range(height)
                       for x in range(width))
        data = PNG.encode_gray8(width, height, raster)
        self.assertEqual(PNG.decode_gray8(data), (width, height, raster))

    def test_size_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            PNG.encode_gray8(2, 2, bytes((0, 1, 0)))

    def test_non_png_rejected(self):
        with self.assertRaises(ValueError):
            PNG.decode_gray8(b"not a png at all...........")


if __name__ == "__main__":
    unittest.main()
