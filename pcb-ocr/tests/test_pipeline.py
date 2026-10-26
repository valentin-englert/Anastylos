"""Pipeline checks with deterministic stub backends. Needs the venv (cv2).

Stubs key off image geometry and brightness, so rotation search, polarity
handling, and box mapping are exercised for real; recognition quality
itself is the backend's job and is covered by the eval workflow.

Run from the pcb-ocr directory:

    ..\\.venv\\Scripts\\python -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2
import numpy as np

from anastylos_pcb.annotate import annotate
from anastylos_pcb.cli import main
from anastylos_pcb.pipeline import analyze


def make_crop(width=200, height=100, bg=100, dot=255, dot_xy=(10, 10), dot_size=12):
    image = np.full((height, width, 3), bg, np.uint8)
    if dot is not None:
        x, y = dot_xy
        image[y:y + dot_size, x:x + dot_size] = dot
    return image


def _bright_blob(variant):
    mask = variant > 200
    frac = float(mask.mean())
    if frac == 0.0 or frac > 0.05:
        return None
    ys, xs = np.nonzero(mask)
    box = [int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)]
    if frac * variant.size / (box[2] * box[3]) < 0.5:
        return None
    return box


class DotBackend:
    """Stub that 'reads' whatever bright blob it finds; orientation-agnostic."""

    name = "dot-stub"

    def __init__(self, text="R12", confidence=95.0):
        self.text = text
        self.confidence = confidence

    def available(self):
        return True

    def version(self):
        return "stub-1"

    def recognize(self, variant):
        box = _bright_blob(variant)
        if box is None:
            return []
        return [{"text": self.text, "confidence": self.confidence, "box_xywh": box}]


QUADRANT_TO_ANGLE = {0: 0, 1: 90, 2: 270, 3: 180}


def _received_angle(variant, box):
    height, width = variant.shape[:2]
    cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
    return QUADRANT_TO_ANGLE[(0 if cx < width / 2 else 1) + (0 if cy < height / 2 else 2)]


class CannedBackend:
    """Stub returning fixed words, but only at chosen orientations.

    words may be a list (same every firing angle) or a dict angle -> list.
    """

    name = "canned-stub"

    def __init__(self, words, fire_orientations=(0,)):
        self.words = words
        self.fire_orientations = set(fire_orientations)

    def available(self):
        return True

    def version(self):
        return "stub-1"

    def recognize(self, variant):
        box = _bright_blob(variant)
        if box is None:
            return []
        angle = _received_angle(variant, box)
        if angle not in self.fire_orientations:
            return []
        words = self.words.get(angle, []) if isinstance(self.words, dict) else self.words
        return [dict(word) for word in words]


class DotTextBackend:
    """Stub reading the positioning dot itself, gated by orientation."""

    name = "dot-text-stub"

    def __init__(self, text="R12", confidence=95.0, fire_orientations=(0, 90, 180, 270)):
        self.text = text
        self.confidence = confidence
        self.fire_orientations = set(fire_orientations)

    def available(self):
        return True

    def version(self):
        return "stub-1"

    def recognize(self, variant):
        box = _bright_blob(variant)
        if box is None or _received_angle(variant, box) not in self.fire_orientations:
            return []
        return [{"text": self.text, "confidence": self.confidence, "box_xywh": box}]


class UnavailableBackend:
    name = "missing-stub"

    def available(self):
        return False

    def version(self):
        return None

    def recognize(self, variant):
        raise AssertionError("must not be called")


class PipelineTest(unittest.TestCase):
    def test_straight_read_ok(self):
        backend = CannedBackend([{"text": "R12", "confidence": 95.0, "box_xywh": [25, 25, 100, 50]}])
        result = analyze(make_crop(), backend)
        self.assertEqual(result["status"], "ok")
        (candidate,) = result["candidates"]
        self.assertEqual(candidate["normalized"], "R12")
        self.assertEqual(candidate["bbox_xywh"], [10, 10, 40, 20])
        self.assertEqual(candidate["suggested_type"], "resistor")
        self.assertEqual(candidate["orientation"], 0)
        self.assertEqual(candidate["verdict"], "ok")

    def test_rotation_search_maps_boxes(self):
        for angle in (0, 90, 180, 270):
            backend = DotTextBackend(fire_orientations=(angle,))
            result = analyze(make_crop(), backend)
            self.assertEqual(result["status"], "ok", angle)
            (candidate,) = result["candidates"]
            self.assertEqual(candidate["normalized"], "R12", angle)
            for got, want in zip(candidate["bbox_xywh"], [10, 10, 12, 12]):
                self.assertAlmostEqual(got, want, delta=1, msg=angle)
            self.assertEqual(candidate["orientations_seen"], [angle], angle)

    def test_same_ink_corroboration(self):
        result = analyze(make_crop(), DotBackend())
        (candidate,) = result["candidates"]
        self.assertEqual(candidate["orientations_seen"], [0, 90, 180, 270])
        self.assertEqual(candidate["occurrences"], 4)

    def test_same_ink_tie_prefers_upright(self):
        # Both boxes map to source [10, 10, 40, 20]: the 90-degree box is the
        # same ink seen sideways, verified by the shared mapped position.
        # Equal corrections and confidence, so the upright reading wins.
        backend = CannedBackend({
            0: [{"text": "R12", "confidence": 60.0, "box_xywh": [25, 25, 100, 50]}],
            90: [{"text": "C5", "confidence": 60.0, "box_xywh": [174, 25, 50, 100]}],
        }, fire_orientations=(0, 90))
        result = analyze(make_crop(), backend)
        (candidate,) = result["candidates"]
        self.assertEqual(candidate["normalized"], "R12")
        self.assertEqual(candidate["bbox_xywh"], [10, 10, 40, 20])

    def test_arbitration_prefers_clean_reading(self):
        backend = CannedBackend({
            0: [{"text": "RIO", "confidence": 95.0, "box_xywh": [25, 25, 100, 50]}],
            90: [{"text": "R12", "confidence": 90.0, "box_xywh": [174, 25, 50, 100]}],
        }, fire_orientations=(0, 90))
        result = analyze(make_crop(), backend)
        (candidate,) = result["candidates"]
        self.assertEqual(candidate["normalized"], "R12")
        self.assertEqual(candidate["orientations_seen"], [90])

    def test_arbitration_falls_back_to_confidence(self):
        backend = CannedBackend({
            0: [{"text": "GND", "confidence": 90.0, "box_xywh": [25, 25, 100, 50]}],
            90: [{"text": "UNO", "confidence": 76.0, "box_xywh": [174, 25, 50, 100]}],
        }, fire_orientations=(0, 90))
        result = analyze(make_crop(), backend)
        self.assertEqual(result["candidates"], [])
        self.assertEqual([t["raw_text"] for t in result["ignored_text"]], ["GND"])

    def test_rotated_conflict_prefers_confidence(self):
        backend = CannedBackend({
            90: [{"text": "C5", "confidence": 50.0, "box_xywh": [174, 25, 50, 100]}],
            270: [{"text": "R12", "confidence": 90.0, "box_xywh": [25, 374, 50, 100]}],
        }, fire_orientations=(90, 270))
        result = analyze(make_crop(), backend)
        (candidate,) = result["candidates"]
        self.assertEqual(candidate["normalized"], "R12")
        self.assertEqual(candidate["bbox_xywh"], [10, 10, 40, 20])
        self.assertEqual(candidate["orientations_seen"], [270])

    def test_inverted_polarity(self):
        result = analyze(make_crop(bg=200, dot=20), DotBackend())
        self.assertEqual(result["status"], "ok")
        (candidate,) = result["candidates"]
        self.assertEqual(candidate["polarities_seen"], ["inverted"])

    def test_unknown_when_no_text(self):
        result = analyze(make_crop(dot=None), DotBackend())
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["candidates"], [])

    def test_unavailable_backend(self):
        result = analyze(make_crop(), UnavailableBackend())
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("not installed", result["reason"])
        self.assertEqual(result["candidates"], [])

    def test_ambiguous_characters(self):
        backend = CannedBackend([{"text": "RIO", "confidence": 90.0, "box_xywh": [25, 25, 100, 50]}])
        result = analyze(make_crop(), backend)
        (candidate,) = result["candidates"]
        self.assertIn("R10", [candidate["normalized"],
                              *[item["normalized"] for item in candidate["alternates"]]])
        self.assertEqual(candidate["verdict"], "ambiguous")
        self.assertEqual(result["status"], "ambiguous")

    def test_ignored_text(self):
        backend = CannedBackend([
            {"text": "R12", "confidence": 95.0, "box_xywh": [25, 25, 100, 50]},
            {"text": "GND", "confidence": 95.0, "box_xywh": [25, 125, 100, 50]},
        ])
        result = analyze(make_crop(), backend)
        self.assertEqual([c["normalized"] for c in result["candidates"]], ["R12"])
        self.assertEqual([t["raw_text"] for t in result["ignored_text"]], ["GND"])

    def test_distant_duplicates_flagged(self):
        backend = CannedBackend([
            {"text": "R12", "confidence": 95.0, "box_xywh": [25, 25, 100, 50]},
            {"text": "R12", "confidence": 92.0, "box_xywh": [25, 150, 100, 50]},
        ])
        result = analyze(make_crop(), backend)
        self.assertEqual(len(result["candidates"]), 2)
        for candidate in result["candidates"]:
            self.assertTrue(candidate["duplicate_label"])
            self.assertEqual(candidate["verdict"], "ambiguous")
        self.assertEqual(result["status"], "ambiguous")

    def test_overlapping_duplicates_merge(self):
        backend = CannedBackend([
            {"text": "R12", "confidence": 95.0, "box_xywh": [25, 25, 100, 50]},
            {"text": "R12", "confidence": 90.0, "box_xywh": [30, 27, 100, 50]},
        ])
        result = analyze(make_crop(), backend)
        (candidate,) = result["candidates"]
        self.assertEqual(candidate["occurrences"], 2)
        self.assertFalse(candidate["duplicate_label"])

    def test_low_confidence_ambiguous(self):
        backend = CannedBackend([{"text": "R12", "confidence": 20.0, "box_xywh": [25, 25, 100, 50]}])
        result = analyze(make_crop(), backend)
        self.assertEqual(result["candidates"][0]["verdict"], "ambiguous")
        self.assertIn("confidence", result["candidates"][0]["verdict_reason"])

    def test_frame_meta_converts_to_mm(self):
        backend = CannedBackend([{"text": "R12", "confidence": 95.0, "box_xywh": [25, 25, 100, 50]}])
        meta = {"view": "surface_a3", "px_per_mm": 2.0, "origin_frame_mm": [10.0, 20.0]}
        result = analyze(make_crop(), backend, frame_meta=meta)
        self.assertEqual(result["candidates"][0]["bbox_mm"], [15.0, 25.0, 20.0, 10.0])
        plain = analyze(make_crop(), backend)
        self.assertIsNone(plain["candidates"][0]["bbox_mm"])
        with self.assertRaises(ValueError):
            analyze(make_crop(), backend, frame_meta={"view": "x", "px_per_mm": 0,
                                                      "origin_frame_mm": [0, 0]})

    def test_roi_offset(self):
        image = np.full((300, 400, 3), 100, np.uint8)
        image[50:62, 60:72] = 255
        result = analyze(image, DotBackend(), roi=(50, 40, 200, 100))
        (candidate,) = result["candidates"]
        for got, want in zip(candidate["bbox_xywh"], [60, 50, 12, 12]):
            self.assertAlmostEqual(got, want, delta=1)

    def test_annotate(self):
        backend = CannedBackend([{"text": "R12", "confidence": 95.0, "box_xywh": [25, 25, 100, 50]}])
        image = make_crop()
        view = annotate(image, analyze(image, backend))
        self.assertEqual(view.shape, image.shape)
        self.assertFalse((view == image).all())

    def test_cli_read_writes_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            cv2.imwrite(str(folder / "pcb.png"), make_crop())
            out = folder / "out"
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = main(["read", "--image", str(folder / "pcb.png"),
                             "--backend", "tesseract", "--out", str(out)])
            self.assertEqual(code, 0)
            stored = json.loads((out / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["backend"]["name"], "tesseract")
            self.assertTrue((out / "annotated.png").is_file())

    def test_cli_unknown_backend(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["read", "--image", "x.png", "--backend", "bogus"]), 2)


if __name__ == "__main__":
    unittest.main()
