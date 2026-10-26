"""Marking pipeline checks with deterministic stub backends. Needs the venv.

Stubs gate on the received orientation via a positioning dot, so the
pcb-ocr rotation search, polarity handling, and box mapping run for
real; recognition quality itself is the engine's job.

Run from the component-markings directory:

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

import numpy as np

from anastylos_markings.annotate import annotate
from anastylos_markings.cli import main
from anastylos_markings.vision import analyze, group_lines, interpret_words


def make_crop(width=200, height=100, bg=100, dot=255, dot_xy=(10, 10), dot_size=12):
    image = np.full((height, width, 3), bg, np.uint8)
    if dot is not None:
        x, y = dot_xy
        image[y:y + dot_size, x:x + dot_size] = dot
    return image


def word(text, conf=95.0, box=(25, 25, 100, 50)):
    return {"text": text, "confidence": conf, "box_xywh": list(box)}


def _bright_blob(variant):
    mask = variant > 200
    frac = float(mask.mean())
    if frac == 0.0 or frac > 0.05:
        return None
    ys, xs = np.nonzero(mask)
    box = [int(xs.min()), int(ys.min()),
           int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)]
    if frac * variant.size / (box[2] * box[3]) < 0.5:
        return None
    return box


QUADRANT_TO_ANGLE = {0: 0, 1: 90, 2: 270, 3: 180}


def _received_angle(variant, box):
    height, width = variant.shape[:2]
    cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
    return QUADRANT_TO_ANGLE[(0 if cx < width / 2 else 1) + (0 if cy < height / 2 else 2)]


class CannedBackend:
    """Stub returning fixed words, but only at chosen orientations."""

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
        return [dict(item) for item in words]


class UnavailableBackend:
    name = "missing-stub"

    def available(self):
        return False

    def version(self):
        return None

    def recognize(self, variant):
        raise AssertionError("must not be called")


def observed(text, conf=95.0, box=(10, 10, 80, 20), orientations=(0,)):
    return {"raw_text": text, "bbox_xywh": list(box), "ocr_confidence": conf,
            "orientations_seen": list(orientations)}


class GroupingTest(unittest.TestCase):
    def test_same_row_words_form_one_line(self):
        lines = group_lines([observed("T", box=(10, 10, 20, 20)),
                             observed("2A", box=(40, 12, 40, 20))])
        self.assertEqual(len(lines), 1)
        self.assertEqual([word["raw_text"] for word in lines[0]], ["T", "2A"])

    def test_rows_split_into_lines_top_first(self):
        lines = group_lines([observed("2413", box=(10, 60, 80, 20)),
                             observed("LM358N", box=(10, 10, 80, 20))])
        self.assertEqual([[word["raw_text"] for word in line] for line in lines],
                         [["LM358N"], ["2413"]])


class EndToEndTest(unittest.TestCase):
    def test_two_line_ic_reads_ok(self):
        backend = CannedBackend([word("LM358N", box=(25, 25, 150, 40)),
                                 word("2413", box=(25, 110, 150, 40))])
        result = analyze(make_crop(), backend, image_name="ic.png")
        self.assertEqual(result["status"], "ok")
        first, second = result["candidates"]
        self.assertEqual(first["match"], "LM358N")
        self.assertEqual(first["identity"]["class"], "part_number")
        self.assertEqual(second["parsed"][0]["kind"], "date_code")
        self.assertEqual((first["line"], second["line"]), (1, 2))
        self.assertLess(first["bbox_xywh"][1], second["bbox_xywh"][1])
        self.assertEqual(first["evidence"], {"image": "ic.png"})
        self.assertTrue(all(item["verified_spec"] is None for item in result["candidates"]))

    def test_fuse_line_reads_current_and_voltage(self):
        backend = CannedBackend([word("T", box=(25, 25, 40, 50)),
                                 word("2A", box=(70, 25, 60, 50)),
                                 word("250V", box=(135, 25, 90, 50))])
        result = analyze(make_crop(), backend)
        self.assertEqual(result["status"], "ok")
        (candidate,) = result["candidates"]
        kinds = [item["kind"] for item in candidate["parsed"]]
        self.assertIn("current", kinds)
        self.assertIn("voltage", kinds)
        self.assertIsNone(candidate["identity"])

    def test_short_code_stays_ambiguous(self):
        backend = CannedBackend([word("1AM")])
        result = analyze(make_crop(), backend)
        self.assertEqual(result["status"], "ambiguous")
        self.assertFalse(result["candidates"][0]["identity"]["resolved"])

    def test_short_code_resolves_with_mapping(self):
        backend = CannedBackend([word("1AM")])
        result = analyze(make_crop(), backend, mapping={"1AM": "MMBT3904"})
        self.assertEqual(result["status"], "ok")
        identity = result["candidates"][0]["identity"]
        self.assertEqual(identity["resolved_identity"], "MMBT3904")

    def test_rotated_reading_preserves_orientation(self):
        backend = CannedBackend({90: [word("R12", box=(25, 80, 100, 50))]},
                                fire_orientations=(90,))
        result = analyze(make_crop(), backend)
        (candidate,) = result["candidates"]
        self.assertEqual(candidate["orientations_seen"], [90])

    def test_low_confidence_word_ambiguous(self):
        backend = CannedBackend([word("LM358N", conf=20.0)])
        result = analyze(make_crop(), backend)
        self.assertEqual(result["status"], "ambiguous")

    def test_noise_is_unknown(self):
        backend = CannedBackend([word("###")])
        result = analyze(make_crop(), backend)
        self.assertEqual(result["status"], "unknown")

    def test_no_words_is_unknown(self):
        backend = CannedBackend([])
        result = analyze(make_crop(), backend)
        self.assertEqual(result["status"], "unknown")

    def test_missing_backend_unavailable(self):
        result = analyze(make_crop(), UnavailableBackend())
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["candidates"], [])

    def test_reject_outside_roi(self):
        with self.assertRaises(ValueError):
            analyze(make_crop(), CannedBackend([]), roi=(150, 80, 100, 50))

    def test_pipeline_is_deterministic(self):
        backend = CannedBackend([word("LM358N", box=(25, 25, 150, 40)),
                                 word("2413", box=(25, 110, 150, 40))])
        image = make_crop()
        self.assertEqual(analyze(image.copy(), backend),
                         analyze(image.copy(), CannedBackend(
                             [word("LM358N", box=(25, 25, 150, 40)),
                              word("2413", box=(25, 110, 150, 40))])))


class InterpretWordsTest(unittest.TestCase):
    def test_orientation_union_sorted(self):
        result = interpret_words([observed("T2A", orientations=(0, 180)),
                                  observed("250V", box=(100, 10, 80, 20),
                                           orientations=(90,))], "fuse.png")
        (candidate,) = result["candidates"]
        self.assertEqual(candidate["orientations_seen"], [0, 90, 180])

    def test_assistant_notes_recorded_verdict_untouched(self):
        words = [observed("LM358N")]
        plain = interpret_words(words, "ic.png")
        noted = interpret_words(words, "ic.png", assistant=lambda seen: {"n": len(seen)})
        self.assertEqual(noted["assistant_notes"], {"n": 1})
        self.assertIsNone(plain["assistant_notes"])
        self.assertEqual(noted["status"], plain["status"])
        self.assertEqual(noted["candidates"], plain["candidates"])

    def test_assistant_failure_noted_not_fatal(self):
        def broken(seen):
            raise RuntimeError("boom")

        result = interpret_words([observed("LM358N")], "ic.png", assistant=broken)
        self.assertEqual(result["status"], "ok")
        self.assertIn("boom", result["assistant_notes"]["error"])

    def test_observations_carry_line_numbers(self):
        result = interpret_words([observed("LM358N"), observed("2413", box=(10, 60, 80, 20))],
                                 "ic.png")
        self.assertEqual([item["line"] for item in result["observations"]], [1, 2])


class AnnotateCliTest(unittest.TestCase):
    def test_annotate_marks_lines_and_verdict(self):
        backend = CannedBackend([word("LM358N")])
        image = make_crop()
        marked = annotate(image, analyze(image, backend))
        self.assertEqual(marked.shape, image.shape)
        self.assertTrue((marked != image).any())

    def test_annotate_handles_unknown(self):
        image = make_crop()
        marked = annotate(image, analyze(image, CannedBackend([])))
        self.assertEqual(marked.shape, image.shape)

    def test_cli_parse_without_engine(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(["parse", "LM358N", "2413"])
        self.assertEqual(code, 0)
        result = json.loads(buffer.getvalue())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["candidates"][0]["match"], "LM358N")

    def test_cli_parse_bad_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mapping.json"
            path.write_text("{}", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as raised:
                main(["parse", "1AM", "--mapping", str(path)])
            self.assertEqual(raised.exception.code, 2)

    def test_cli_read_missing_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = main(["read", "--image", str(Path(tmp) / "nope.png"),
                             "--out", str(Path(tmp) / "out")])
            self.assertEqual(code, 1)

    def test_cli_read_unknown_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = main(["read", "--image", str(Path(tmp) / "x.png"),
                             "--backend", "nope", "--out", str(Path(tmp) / "out")])
            self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
