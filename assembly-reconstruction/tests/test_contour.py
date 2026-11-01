"""Mask contour extraction tests (stdlib PNG path)."""

from __future__ import annotations

import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anastylos_reconstruction.contour import (  # noqa: E402
    ContourError,
    extract_mask_contour,
    read_grayscale_png,
)


def _write_gray_png(path: Path, width: int, height: int, pixels: list[int]) -> None:
    """Write an uncompressed-filter grayscale 8-bit PNG."""
    assert len(pixels) == width * height
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter None
        raw.extend(pixels[y * width:(y + 1) * width])
    compressed = zlib.compress(bytes(raw), 9)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", compressed)
        + chunk(b"IEND", b"")
    )


class ContourExtraction(unittest.TestCase):
    def test_reads_and_traces_filled_square(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mask.png"
            width, height = 40, 30
            pixels = [0] * (width * height)
            for y in range(8, 20):
                for x in range(5, 17):
                    pixels[y * width + x] = 255
            _write_gray_png(path, width, height, pixels)
            w, h, grid = read_grayscale_png(path)
            self.assertEqual((w, h), (40, 30))
            self.assertEqual(grid[8][5], 255)
            self.assertEqual(grid[0][0], 0)
            contour = extract_mask_contour(path, [5, 8, 12, 12], max_points=32)
            self.assertEqual(contour["kind"], "mask_contour")
            self.assertEqual(contour["method"], "change-detection-mask-boundary-v1")
            self.assertEqual(contour["roi_px"], [5, 8, 12, 12])
            self.assertEqual(contour["coordinate_space"], "rectified_image_px")
            self.assertGreaterEqual(contour["point_count"], 4)
            self.assertTrue(all(5 <= p[0] <= 17 and 8 <= p[1] <= 20
                                for p in contour["points_px"]))
            self.assertEqual(len(contour["source_sha256"]), 64)

    def test_empty_roi_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mask.png"
            _write_gray_png(path, 16, 16, [0] * 256)
            with self.assertRaises(ContourError):
                extract_mask_contour(path, [2, 2, 4, 4])

    def test_bbox_outside_image_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mask.png"
            _write_gray_png(path, 10, 10, [255] * 100)
            with self.assertRaises(ContourError):
                extract_mask_contour(path, [8, 8, 8, 8])


if __name__ == "__main__":
    unittest.main()
