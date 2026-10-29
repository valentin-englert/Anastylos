"""QA-03 (v0.1.2): Core evidence assessment of resistor-reader 'ok' verdicts.

The resistor reader is unchanged; these tests assert both the native result
(still the v0.1.0 misread on the glare fixture) and the Core's handling of it.
All images are SYNTHETIC renders (reader's own renderer); nothing here is
real-microscope validation.
"""

from __future__ import annotations

import copy
import json
import re
import unittest
from pathlib import Path
from unittest import mock

from _support import CLOCK, resistor_image, tmpdir, write_png

from anastylos_microscopy import adapters, assessment, schema
from anastylos_microscopy.orchestrator import inspect_image

PKG = Path(__file__).resolve().parents[1]
GLARE = PKG / "examples" / "synthetic_mic" / "images" / "unknown_glare.png"
BLUR = PKG / "examples" / "synthetic_mic" / "images" / "unknown_blurred_resistor.png"
UNIFORM = PKG / "examples" / "synthetic_mic" / "images" / "unknown_uniform.png"


def _inspect(testcase, image, **kwargs):
    base = tmpdir(testcase)
    src = image if isinstance(image, Path) else write_png(base / "img.png", image)
    record, path = inspect_image(src, base / "out", mode="resistor", clock=CLOCK, **kwargs)
    testcase.assertEqual(schema.validate_named(record, "inspection"), [])
    return record, path


def _block(record):
    return record["recognizers"][0]


class GlareCaseTest(unittest.TestCase):
    def test_original_glare_fixture_is_held_ambiguous_with_evidence_kept(self):
        record, path = _inspect(self, GLARE)
        block = _block(record)
        native = block["native_result"]
        # The unchanged reader still produces the v0.1.0 misread ...
        self.assertEqual(block["native_status"], "ok")
        self.assertEqual(native["candidates"][0]["resistance_ohms"], 22900.0)
        self.assertEqual(native["candidates"][0]["sequence"], ["red", "red", "white", "red", "gold"])
        # ... but the Core no longer presents it as a reliable candidate.
        self.assertEqual(block["status"], "ambiguous")
        self.assertEqual(record["uncertainty"]["overall_status"], "ambiguous")
        ca = block["core_assessment"]
        self.assertEqual(ca["action"], "downgraded")
        self.assertIn("band_geometry", ca["failed_checks"])
        self.assertIn("band_color_fidelity", ca["failed_checks"])
        self.assertTrue(ca["candidate_preserved"])
        self.assertEqual(record["candidates"][0]["value"], "22.9 kΩ")
        self.assertEqual(record["candidates"][0]["core_assessment"], "not_supported_by_band_evidence")
        self.assertFalse(record["candidates"][0]["confirmed"])
        self.assertTrue(any("held as ambiguous" in r for r in record["uncertainty"]["reasons"]))
        self.assertEqual(record["human_review"]["status"], "pending")
        stored = json.loads((path.parent / "readers" / "resistor" / "result.json").read_text("utf-8"))
        self.assertEqual(stored["status"], "ok", "raw reader evidence file is not rewritten")
        flags = [f["flag"] for f in record["quality"]["flags"]]
        self.assertIn("channel_clipping", flags)
        self.assertIn("held as **ambiguous**", (path.parent / "report.md").read_text("utf-8"))


class KnownGoodTest(unittest.TestCase):
    """False-rejection guard: clean, interpretable renders keep their candidate."""

    CASES = {
        "1k": (("brown", "black", "red", "gold"), {}, 1000.0),
        "4k7": (("yellow", "violet", "red", "gold"), {}, 4700.0),
        "1k2": (("brown", "red", "red", "gold"), {}, 1200.0),
        "47k_5band": (("yellow", "violet", "black", "red", "brown"), {}, 47000.0),
        "10k_6band": (("brown", "black", "black", "red", "brown", "red"), {}, 10000.0),
        "rotated_12deg": (("yellow", "violet", "red", "gold"), {"rotation_deg": 12.0}, 4700.0),
        "blue_body": (("brown", "black", "red", "gold"), {"body": "blue"}, 1000.0),
        "noise": (("brown", "black", "red", "gold"), {"noise_sigma": 6.0}, 1000.0),
    }

    def test_known_good_resistors_stay_candidates(self):
        for name, (seq, kwargs, ohms) in self.CASES.items():
            with self.subTest(name):
                record, _ = _inspect(self, resistor_image(seq, **kwargs))
                block = _block(record)
                self.assertEqual(block["native_status"], "ok")
                self.assertEqual(block["native_result"]["candidates"][0]["resistance_ohms"], ohms)
                self.assertEqual(block["core_assessment"]["action"], "kept", block["core_assessment"])
                self.assertEqual(record["uncertainty"]["overall_status"], "candidate")

    def test_low_quality_but_interpretable_stays_candidate(self):
        for name, kwargs in {"dim_gradient_noise": dict(brightness=0.75, gradient=0.3, noise_sigma=4.0,
                                                        seed=7),
                             "mild_glare_normal_exposure": dict(glare=True)}.items():
            with self.subTest(name):
                record, _ = _inspect(self, resistor_image(("yellow", "violet", "red", "gold"), **kwargs))
                self.assertEqual(_block(record)["core_assessment"]["action"], "kept")
                self.assertEqual(record["uncertainty"]["overall_status"], "candidate")

    def test_clipping_in_one_band_is_enough_to_hold_the_interpretation(self):
        image = resistor_image(("brown", "black", "red", "gold"), body="blue",
                               brightness=1.15, seed=7)
        record, _ = _inspect(self, image)
        block = _block(record)

        self.assertEqual(block["native_status"], "ok")
        self.assertEqual(block["status"], "ambiguous")
        self.assertIn("band_color_fidelity", block["core_assessment"]["failed_checks"])
        self.assertTrue(block["core_assessment"]["candidate_preserved"])


class AbstentionTest(unittest.TestCase):
    def test_native_ambiguous_is_left_untouched(self):
        record, _ = _inspect(self, resistor_image(("red", "black", "black", "brown")))
        block = _block(record)
        self.assertEqual((block["native_status"], block["status"]), ("ambiguous", "ambiguous"))
        self.assertEqual(block["core_assessment"]["action"], "none")

    def test_unreadable_or_obscured_samples_abstain(self):
        for image in (BLUR, UNIFORM):
            with self.subTest(image.name):
                record, _ = _inspect(self, image)
                self.assertNotEqual(record["uncertainty"]["overall_status"], "candidate")
                self.assertNotEqual(_block(record)["status"], "ok")
        img = resistor_image()
        img[:, 200:700] = (255, 255, 255)  # body and bands hidden behind a saturated patch
        record, _ = _inspect(self, img)
        self.assertNotEqual(record["uncertainty"]["overall_status"], "candidate")

    def test_ambiguous_color_band_evidence_is_downgraded(self):
        image = resistor_image(("yellow", "violet", "red", "gold"))
        from anastylos_resistor.vision import analyze
        native = analyze(image)
        self.assertEqual(native["status"], "ok")
        weak = copy.deepcopy(native)
        weak["band_boxes"][1]["color_candidates"] = [{"color": "violet", "score": 0.46},
                                                     {"color": "blue", "score": 0.41}]
        result = assessment.assess_resistor(weak, image)
        self.assertFalse(result["supports_ok"])
        self.assertIn("band_color_determinacy", result["failed_checks"])
        swapped = copy.deepcopy(native)
        swapped["band_boxes"][2]["color_candidates"] = [{"color": "orange", "score": 0.7},
                                                        {"color": "red", "score": 0.3}]
        self.assertIn("band_color_determinacy",
                      assessment.assess_resistor(swapped, image)["failed_checks"])


class RoiTest(unittest.TestCase):
    def test_invalid_rois_are_input_errors(self):
        for roi in ([850, 0, 200, 100], [0, 0, 0, 10], "a,b,c,d"):
            with self.subTest(roi=roi):
                record, _ = _inspect(self, resistor_image(), roi=roi)
                self.assertEqual(record["uncertainty"]["overall_status"], "input_error")
                self.assertEqual(record["recognizers"], [])

    def test_roi_missing_the_component_does_not_produce_a_candidate(self):
        record, _ = _inspect(self, resistor_image(), roi=[0, 0, 120, 80])  # background corner
        self.assertNotEqual(record["uncertainty"]["overall_status"], "candidate")

    def test_roi_crop_assessed_in_source_coordinates(self):
        record, _ = _inspect(self, resistor_image(), roi=[100, 60, 700, 200])
        block = _block(record)
        if block["native_status"] == "ok":
            self.assertEqual(block["core_assessment"]["action"], "kept", block["core_assessment"])


class IncompleteEvidenceTest(unittest.TestCase):
    def _with_native(self, mutate):
        real_run = adapters.ResistorAdapter.run

        def patched(adapter, image, roi, ctx):
            native = real_run(adapter, image, roi, ctx)
            mutate(native)
            return native

        with mock.patch.object(adapters.ResistorAdapter, "run", patched):
            record, _ = _inspect(self, resistor_image())
        return record

    def test_incomplete_reader_evidence_is_downgraded(self):
        mutations = {
            "no_band_boxes": lambda n: n.update(band_boxes=[]),
            "band_count_mismatch": lambda n: n["band_boxes"].pop(),
            "box_out_of_bounds": lambda n: n["band_boxes"][0].update(bbox_xywh=[5000, 10, 20, 50]),
            "box_missing": lambda n: n["band_boxes"][0].pop("bbox_xywh"),
            "no_color_candidates": lambda n: n["band_boxes"][1].update(color_candidates=[]),
            "no_sequence": lambda n: n["candidates"][0].pop("sequence"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name):
                record = self._with_native(mutate)
                block = _block(record)
                self.assertEqual(block["native_status"], "ok")
                self.assertEqual(block["status"], "ambiguous")
                self.assertEqual(block["core_assessment"]["failed_checks"], ["evidence_complete"])
                self.assertEqual(record["uncertainty"]["overall_status"], "ambiguous")

    def test_assessment_crash_fails_closed(self):
        with mock.patch.object(assessment, "assess_resistor", side_effect=RuntimeError("boom")), \
                mock.patch.dict("anastylos_microscopy.orchestrator.ASSESSED_READERS",
                                {"resistor": assessment.assess_resistor}):
            record, _ = _inspect(self, resistor_image())
        block = _block(record)
        self.assertEqual(block["status"], "ambiguous")
        self.assertEqual(block["core_assessment"]["failed_checks"], ["assessment_error"])


class AntiOverfittingTest(unittest.TestCase):
    def test_assessment_has_no_fixture_specific_logic(self):
        source = (PKG / "src" / "anastylos_microscopy" / "assessment.py").read_text("utf-8")
        for forbidden in ("unknown_glare", "22.9", "22900", "2.2 k", "2200", "0.1000", "0.926"):
            self.assertNotIn(forbidden, source)
        self.assertIsNone(re.search(r"[0-9a-f]{64}", source), "no image hashes")
        orchestrator = (PKG / "src" / "anastylos_microscopy" / "orchestrator.py").read_text("utf-8")
        self.assertNotIn("unknown_glare", orchestrator)


if __name__ == "__main__":
    unittest.main()
