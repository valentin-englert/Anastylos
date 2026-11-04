"""SMD pipeline checks with stub OCR backends. Needs the venv (cv2/numpy).

Stubs implement the pcb-ocr backend interface and key responses off image
geometry, so the full pcb-ocr search plus SMD interpretation runs for
real; recognition quality itself is the engine's job.

Run from the smd-reader directory:

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

from anastylos_smd.cli import main
from anastylos_smd.vision import analyze, interpret_words


def make_crop(width=200, height=100, value=100):
    return np.full((height, width, 3), value, np.uint8)


def word(text, conf=90.0, box=(25, 25, 150, 75)):
    return {"text": text, "confidence": conf, "box_xywh": list(box)}


class ShapeBackend:
    """Stub keyed off received-geometry: wide attempts vs tall attempts."""

    name = "shape-stub"

    def __init__(self, wide_words=(), tall_words=()):
        self.wide_words = [dict(w) for w in wide_words]
        self.tall_words = [dict(w) for w in tall_words]

    def available(self):
        return True

    def version(self):
        return "stub-1"

    def recognize(self, variant):
        height, width = variant.shape[:2]
        words = self.wide_words if width > height else self.tall_words
        return [dict(item) for item in words]


class MissingBackend:
    name = "missing-stub"

    def available(self):
        return False

    def version(self):
        return None

    def recognize(self, variant):
        raise AssertionError("must not be called")


class VisionTest(unittest.TestCase):
    def test_straight_read_ok(self):
        result = analyze(make_crop(), ShapeBackend(wide_words=[word("472")]))
        self.assertEqual(result["status"], "ok")
        (candidate,) = result["candidates"]
        self.assertEqual(candidate["code"], "472")
        self.assertEqual(candidate["resistance_ohms_exact"], "4700")
        self.assertEqual(candidate["schemes"], ["3-digit"])
        self.assertFalse(candidate["repaired"])
        self.assertEqual(candidate["occurrences"], 2)
        self.assertEqual(candidate["evidence"]["raw_text"], "472")
        self.assertIn("resistor", result["object_hypothesis"]["assumed_object"])

    def test_unknown_when_no_text(self):
        result = analyze(make_crop(), ShapeBackend())
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["candidates"], [])

    def test_unavailable_backend(self):
        result = analyze(make_crop(), MissingBackend())
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("not installed", result["reason"])

    def test_garbage_word_is_unknown_but_listed(self):
        result = analyze(make_crop(), ShapeBackend(wide_words=[word("###")]))
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["observations"][0]["raw_text"], "###")
        self.assertEqual(result["observations"][0]["readings"], [])

    def test_zero_o_confusion(self):
        result = analyze(make_crop(), ShapeBackend(wide_words=[word("O47")]))
        self.assertEqual(result["status"], "ambiguous")
        codes = [c["code"] for c in result["candidates"]]
        self.assertIn("047", codes)
        repaired = next(c for c in result["candidates"] if c["code"] == "047")
        self.assertTrue(repaired["repaired"])
        self.assertEqual(repaired["subs"], [{"position": 0, "from": "O", "to": "0"}])
        self.assertEqual(repaired["evidence"]["raw_text"], "O47")

    def test_one_i_confusion(self):
        result = analyze(make_crop(), ShapeBackend(wide_words=[word("I02")]))
        codes = {c["code"]: c for c in result["candidates"]}
        self.assertIn("102", codes)
        self.assertEqual(codes["102"]["resistance_ohms_exact"], "1000")
        self.assertEqual(result["status"], "ambiguous")

    def test_one_l_confusion(self):
        result = analyze(make_crop(), ShapeBackend(wide_words=[word("L02")]))
        codes = [c["code"] for c in result["candidates"]]
        self.assertIn("102", codes)
        self.assertEqual(result["status"], "ambiguous")

    def test_five_s_confusion(self):
        result = analyze(make_crop(), ShapeBackend(wide_words=[word("S22")]))
        codes = {c["code"]: c for c in result["candidates"]}
        self.assertIn("522", codes)
        self.assertEqual(codes["522"]["resistance_ohms_exact"], "5200")

    def test_eight_b_confusion(self):
        result = analyze(make_crop(), ShapeBackend(wide_words=[word("B01")]))
        codes = {c["code"]: c for c in result["candidates"]}
        self.assertIn("801", codes)
        self.assertEqual(codes["801"]["resistance_ohms_exact"], "800")
        self.assertEqual(result["status"], "ambiguous")

    def test_orientation_ambiguity(self):
        backend = ShapeBackend(wide_words=[word("472")], tall_words=[word("473")])
        result = analyze(make_crop(), backend)
        self.assertEqual(result["status"], "ambiguous")
        ohms = sorted(c["resistance_ohms_exact"] for c in result["candidates"])
        self.assertEqual(ohms, ["4700", "47000"])

    def test_r_notation_and_eia96_through_images(self):
        r_result = analyze(make_crop(), ShapeBackend(wide_words=[word("4R7")]))
        self.assertEqual(r_result["candidates"][0]["resistance_ohms_exact"], "4.7")
        eia = analyze(make_crop(), ShapeBackend(wide_words=[word("01C")]))
        self.assertEqual(eia["status"], "ok")
        self.assertEqual(eia["candidates"][0]["resistance_ohms_exact"], "10000")

    def test_jumper_merges_schemes(self):
        result = analyze(make_crop(), ShapeBackend(wide_words=[word("000")]))
        (candidate,) = result["candidates"]
        self.assertIn("zero-jumper", candidate["schemes"])
        self.assertEqual(candidate["resistance_ohms_exact"], "0")

    def test_interpret_words_unit(self):
        words = [{"raw_text": "472", "bbox_xywh": [10, 10, 40, 20],
                  "ocr_confidence": None, "orientations_seen": [0]}]
        result = interpret_words(words, "crop.png")
        self.assertEqual(result["status"], "ok")
        self.assertIsNone(result["candidates"][0]["ocr_confidence"])
        self.assertEqual(result["candidates"][0]["evidence"]["image"], "crop.png")

    def test_cli_decode(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(["decode", "472"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(buffer.getvalue())["candidates"][0]["resistance_ohms_exact"], "4700")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["decode", "01S", "--aliases", "S=0.1"]), 0)

    def test_cli_read_writes_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            cv2.imwrite(str(folder / "smd.png"), make_crop())
            out = folder / "out"
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = main(["read", "--image", str(folder / "smd.png"),
                             "--backend", "tesseract", "--out", str(out)])
            self.assertEqual(code, 0)
            stored = json.loads((out / "result.json").read_text(encoding="utf-8"))
            self.assertIn(stored["status"], ("ok", "ambiguous", "unknown", "unavailable"))
            self.assertTrue((out / "annotated.png").is_file())

    def test_cli_unknown_backend(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["read", "--image", "x.png", "--backend", "bogus"]), 2)


if __name__ == "__main__":
    unittest.main()
