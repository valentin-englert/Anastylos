"""Screw vision checks on deterministic synthetic renders. Needs the venv.

Fixtures exercise the pipeline only; they prove nothing about
real-hardware accuracy.

Run from the screw-reader directory:

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

from anastylos_screw.annotate import annotate
from anastylos_screw.cli import main
from anastylos_screw.synthetic import render
from anastylos_screw.vision import analyze, default_segmenter, localize_head

LOBES = {"slotted": 2, "phillips": 4, "pozidriv": 4, "torx": 6, "hex": 6}


def top(result):
    return result["candidates"][0]["class"] if result["candidates"] else None


class NominalTest(unittest.TestCase):
    def test_each_drive_reads_ok_with_lobe_signature(self):
        for drive, lobes in LOBES.items():
            with self.subTest(drive=drive):
                result = analyze(render(drive, seed=1))
                self.assertEqual(result["status"], "ok")
                self.assertEqual(top(result), drive)
                self.assertEqual(result["features"]["lobes"], lobes)

    def test_rotations_stay_ok(self):
        cases = [("hex", 15.0), ("hex", 44.0), ("hex", 59.0),
                 ("pozidriv", 22.0), ("torx", 17.0), ("phillips", 33.0),
                 ("slotted", 70.0)]
        for drive, deg in cases:
            with self.subTest(drive=drive, deg=deg):
                result = analyze(render(drive, rotation_deg=deg, seed=11))
                self.assertEqual(result["status"], "ok")
                self.assertEqual(top(result), drive)

    def test_phillips_pozidriv_split_on_tick_evidence(self):
        phil = analyze(render("phillips", seed=1))
        pozi = analyze(render("pozidriv", seed=1))
        self.assertEqual(top(phil), "phillips")
        self.assertEqual(top(pozi), "pozidriv")
        self.assertGreater(pozi["features"]["ticks"], phil["features"]["ticks"] + 0.05)

    def test_torx_hex_split_on_convexity(self):
        torx = analyze(render("torx", seed=1))
        hexa = analyze(render("hex", seed=1))
        self.assertEqual(top(torx), "torx")
        self.assertEqual(top(hexa), "hex")
        self.assertGreater(hexa["features"]["solidity"], torx["features"]["solidity"] + 0.05)
        self.assertLess(hexa["features"]["vertices"], torx["features"]["vertices"])

    def test_scale_change_stays_ok(self):
        small = cv2.resize(render("slotted", seed=3), None, fx=0.7, fy=0.7)
        result = analyze(small)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(top(result), "slotted")

    def test_result_is_json_serializable(self):
        result = analyze(render("hex", seed=1))
        json.dumps(result)


class DegradedTest(unittest.TestCase):
    def test_mild_tilt_stays_ok(self):
        for drive in ("torx", "hex", "phillips"):
            with self.subTest(drive=drive):
                result = analyze(render(drive, tilt=0.2, seed=1))
                self.assertEqual(result["status"], "ok")
                self.assertEqual(top(result), drive)

    def test_extreme_tilt_never_confident(self):
        result = analyze(render("torx", tilt=1.0, seed=1))
        self.assertIn(result["status"], ("ambiguous", "unknown"))

    def test_mild_glare_stays_ok(self):
        result = analyze(render("phillips", glare=True, seed=4))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(top(result), "phillips")

    def test_glare_on_torx_is_ambiguous_not_wrong(self):
        result = analyze(render("torx", glare=True))
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(top(result), "torx")

    def test_shadow_stays_ok(self):
        result = analyze(render("hex", shadow=0.6, seed=2))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(top(result), "hex")

    def test_moderate_wear_stays_ok(self):
        result = analyze(render("phillips", wear=0.8, seed=5))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(top(result), "phillips")

    def test_heavy_wear_is_ambiguous(self):
        result = analyze(render("torx", wear=1.0, seed=7))
        self.assertEqual(result["status"], "ambiguous")

    def test_heavy_occlusion_is_ambiguous(self):
        result = analyze(render("hex", occlude=1.0))
        self.assertEqual(result["status"], "ambiguous")

    def test_partial_occlusion_keeps_true_class_on_top(self):
        result = analyze(render("slotted", occlude=0.3, seed=6))
        self.assertIn(result["status"], ("ok", "ambiguous"))
        self.assertEqual(top(result), "slotted")


class NegativeTest(unittest.TestCase):
    def test_blank_head_is_unknown(self):
        result = analyze(render("blank"))
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["candidates"], [])

    def test_flat_image_is_unknown_without_head(self):
        result = analyze(np.full((400, 400, 3), 200, np.uint8))
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["head"])
        self.assertIn("no circular head", result["reason"])

    def test_reject_outside_roi(self):
        with self.assertRaises(ValueError):
            analyze(render("hex"), roi=(300, 300, 200, 200))

    def test_roi_offsets_boxes(self):
        canvas = np.full((600, 600, 3), 200, np.uint8)
        canvas[100:500, 100:500] = render("phillips", seed=1)
        result = analyze(canvas, roi=(100, 100, 400, 400))
        self.assertEqual(result["status"], "ok")
        cx, cy = result["head"]["center_xy"]
        self.assertAlmostEqual(cx, 300, delta=5)
        self.assertAlmostEqual(cy, 300, delta=5)
        x, y, _, _ = result["recess_bbox_xywh"]
        self.assertGreaterEqual(x, 100)
        self.assertGreaterEqual(y, 100)


class AdapterTest(unittest.TestCase):
    def test_custom_segmenter_is_used(self):
        seen = {}

        def segmenter(gray, head):
            seen["head"] = head
            return default_segmenter(gray, head)

        result = analyze(render("hex", seed=1), segmenter=segmenter)
        self.assertIn("head", seen)
        self.assertEqual(top(result), "hex")

    def test_custom_ranker_replaces_verdict(self):
        def ranker(features, quality):
            return {"status": "unknown", "reason": "stub veto", "candidates": []}

        result = analyze(render("hex", seed=1), ranker=ranker)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["reason"], "stub veto")
        self.assertIsNotNone(result["features"])

    def test_pipeline_is_deterministic(self):
        image = render("torx", rotation_deg=17.0, seed=9)
        self.assertEqual(analyze(image), analyze(image.copy()))


class AnnotateCliTest(unittest.TestCase):
    def test_annotate_marks_head_and_verdict(self):
        image = render("phillips", seed=1)
        marked = annotate(image, analyze(image))
        self.assertEqual(marked.shape, image.shape)
        self.assertTrue((marked != image).any())

    def test_annotate_handles_unknown(self):
        image = np.full((400, 400, 3), 200, np.uint8)
        marked = annotate(image, analyze(image))
        self.assertEqual(marked.shape, image.shape)

    def test_cli_read_writes_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            cv2.imwrite(str(tmpdir / "crop.png"), render("pozidriv", seed=1))
            out = tmpdir / "out"
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["read", "--image", str(tmpdir / "crop.png"),
                             "--out", str(out)])
            self.assertEqual(code, 0)
            bundle = json.loads((out / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(bundle["status"], "ok")
            self.assertEqual(bundle["candidates"][0]["class"], "pozidriv")
            self.assertTrue((out / "annotated.png").exists())

    def test_cli_read_missing_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = main(["read", "--image", str(Path(tmp) / "nope.png"),
                             "--out", str(Path(tmp) / "out")])
            self.assertEqual(code, 1)

    def test_localize_head_reports_rim_support(self):
        gray = cv2.cvtColor(render("hex", seed=1), cv2.COLOR_BGR2GRAY)
        head = localize_head(gray)
        self.assertGreaterEqual(head["rim_support"], 0.25)
        self.assertAlmostEqual(head["center_xy"][0], 200, delta=10)


if __name__ == "__main__":
    unittest.main()
