"""Declared-removal projection + representation provenance tests."""

from __future__ import annotations

import hashlib
import json
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anastylos_reconstruction.projector import build_state  # noqa: E402
from anastylos_reconstruction.render import render_svg  # noqa: E402
from anastylos_reconstruction.schema import validate_state  # noqa: E402


def _write_gray_png(path: Path, width: int, height: int, pixels: list[int]) -> None:
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw.extend(pixels[y * width:(y + 1) * width])
    compressed = zlib.compress(bytes(raw), 9)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", compressed) + chunk(b"IEND", b"")
    )


def _write_blank_png(path: Path, width: int = 64, height: int = 64) -> None:
    _write_gray_png(path, width, height, [200] * (width * height))


class DeclaredAndContourProjection(unittest.TestCase):
    def test_observation_with_mask_gets_mask_contour(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = root / "before_rectified.png"
            after = root / "after_rectified.png"
            mask = root / "mask.png"
            _write_blank_png(before)
            _write_blank_png(after)
            pixels = [0] * (64 * 64)
            for y in range(10, 30):
                for x in range(10, 30):
                    pixels[y * 64 + x] = 255
            _write_gray_png(mask, 64, 64, pixels)
            result = {
                "output_files": {
                    "before_rectified": "before_rectified.png",
                    "after_rectified": "after_rectified.png",
                    "mask": "mask.png",
                },
                "rectified": {"px_per_mm": 2.0, "size_px": [64, 64],
                              "origin_frame_mm": [0, 0], "view": "v"},
                "regions": [{
                    "label": "R1",
                    "bbox_px": [10, 10, 20, 20],
                    "bbox_mm": [5.0, 5.0, 10.0, 10.0],
                    "centroid_mm": [10.0, 10.0],
                }],
            }
            result_path = root / "result.json"
            result_path.write_text(json.dumps(result) + "\n", encoding="utf-8")
            digest = hashlib.sha256(result_path.read_bytes()).hexdigest()
            log = root / "memory.jsonl"
            events = [
                {"type": "phase", "id": "phase-0001", "session": "s",
                 "label": "P1", "starts_after": None},
                {"type": "observation", "id": "obs-0001", "session": "s",
                 "source_view": "v", "bbox_mm": [5.0, 5.0, 10.0, 10.0],
                 "centroid_mm": [10.0, 10.0],
                 "evidence": {"result_json": str(result_path), "pair_id": "p",
                              "region": "R1", "result_sha256": digest}},
                {"type": "declared_removal", "id": "dec-0001", "session": "s",
                 "operator_label": "shell", "source": "device",
                 "phase": "phase-0001", "declared_by": "owner"},
                {"type": "declared_removal", "id": "dec-0002", "session": "s",
                 "operator_label": "screw", "source": "hole",
                 "phase": "phase-0001", "declared_by": "owner"},
                {"type": "storage_link", "id": "store-0001", "session": "s",
                 "target_id": "dec-0001", "location": "OUT-A",
                 "basis": {"kind": "human", "confirmed_by": "owner"}},
                {"type": "storage_link", "id": "store-0002", "session": "s",
                 "target_id": "dec-0002", "location": "cell-1",
                 "basis": {"kind": "human", "confirmed_by": "owner"}},
                {"type": "compatible_group", "id": "grp-0001", "session": "s",
                 "members": ["dec-0001", "dec-0002"], "label": "pair",
                 "basis": {"kind": "operator_statement", "declared_by": "owner"}},
            ]
            log.write_text("".join(json.dumps(e) + "\n" for e in events),
                           encoding="utf-8")
            state = build_state(str(log))
            validate_state(state)
            self.assertEqual([c["id"] for c in state["components"]],
                             ["obs-0001", "dec-0001", "dec-0002"])
            obs = state["components"][0]
            self.assertEqual(obs["representation"]["kind"], "mask_contour")
            self.assertGreaterEqual(obs["representation"]["point_count"], 4)
            self.assertEqual(obs["representation"]["source_sha256"],
                             hashlib.sha256(mask.read_bytes()).hexdigest())
            for dec in state["components"][1:]:
                self.assertEqual(dec["kind"], "declared_removal")
                self.assertIsNone(dec["physical"]["bbox_mm"])
                self.assertEqual(dec["representation"]["kind"], "declared_card")
                self.assertIn("SUBSTITUTE", dec["representation"]["note"])
            svg = render_svg(state, embed_crops=True)
            self.assertIn('data-event-id="obs-0001"', svg)
            self.assertIn('data-event-id="dec-0001"', svg)
            self.assertIn("SUBSTITUTE", svg)
            self.assertIn("Technical explosion sketch", svg)
            before = {
                path: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (log, result_path, mask, before, after)
            }
            first = render_svg(state, embed_crops=True)
            second = render_svg(state, embed_crops=True)
            self.assertEqual(first, second)
            for path, digest in before.items():
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                                 digest)


if __name__ == "__main__":
    unittest.main()
