"""Interpreter rule checks on fabricated markers. Standard library only.

Run from the polarity-reader directory:

    ..\\.venv\\Scripts\\python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anastylos_polarity.interpret import interpret


def marker(marker_id="m1", kind="band", strength=1.0, angle=0.0):
    return {"marker_id": marker_id, "marker_type": kind,
            "bbox_xywh": [10, 10, 20, 60],
            "centroid_xy": [20.0, 40.0],
            "orientation_vector_xy": [1.0, 0.0],
            "orientation_angle_deg": angle, "strength": strength,
            "evidence": {}}


BODY = {"center_xy": [0.0, 0.0]}
QUALITY = {"contrast": 0.5}


class HypothesisTest(unittest.TestCase):
    def test_band_without_hint_lists_both_polarities(self):
        result = interpret([marker()], BODY, QUALITY)
        self.assertEqual(result["status"], "ambiguous")
        readings = [item["interpretation"] for item in result["candidates"]]
        self.assertEqual(readings, ["cathode_at_marker", "anode_at_marker"])

    def test_stripe_without_hint_lists_both_polarities(self):
        result = interpret([marker(kind="stripe")], BODY, QUALITY)
        self.assertEqual(result["status"], "ambiguous")
        readings = {item["interpretation"] for item in result["candidates"]}
        self.assertEqual(readings, {"negative_at_marker",
                                    "positive_at_marker"})

    def test_hint_selects_family_reading(self):
        cases = [("band", "diode", "cathode_at_marker"),
                 ("band", "tantalum", "anode_at_marker"),
                 ("stripe", "aluminum_electrolytic", "negative_at_marker"),
                 ("stripe", "tantalum", "positive_at_marker"),
                 ("dot", "ic", "pin1_adjacent"),
                 ("notch", "ic", "pin1_left_of_notch"),
                 ("key", "connector", "keyed_side_at_marker")]
        for kind, hint, reading in cases:
            with self.subTest(kind=kind, hint=hint):
                result = interpret([marker(kind=kind)], BODY, QUALITY,
                                   family=hint)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["candidates"][0]["interpretation"],
                                 reading)
                self.assertEqual(result["candidates"][0]["family"], hint)

    def test_lone_dot_without_hint_stays_ambiguous(self):
        result = interpret([marker(kind="dot", angle=225.0)], BODY, QUALITY)
        self.assertEqual(result["status"], "ambiguous")
        readings = {item["interpretation"] for item in result["candidates"]}
        self.assertEqual(readings, {"pin1_adjacent", "cosmetic_mark"})

    def test_lone_notch_without_hint_stays_ambiguous(self):
        result = interpret([marker(kind="notch", angle=270.0)], BODY, QUALITY)
        self.assertEqual(result["status"], "ambiguous")

    def test_agreeing_dot_and_notch_clear_margin(self):
        markers = [marker("m1", "dot", 0.9, 225.0),
                   marker("m2", "notch", 1.0, 270.0)]
        result = interpret(markers, BODY, QUALITY)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["candidates"][0]["family"], "ic")

    def test_disagreeing_dot_and_notch_stay_ambiguous(self):
        markers = [marker("m1", "dot", 0.9, 45.0),
                   marker("m2", "notch", 1.0, 270.0)]
        result = interpret(markers, BODY, QUALITY)
        self.assertEqual(result["status"], "ambiguous")

    def test_topmark_always_ambiguous(self):
        topmark = marker(kind="topmark", strength=1.0, angle=180.0)
        for hint in (None, "ic"):
            with self.subTest(hint=hint):
                result = interpret([topmark], BODY, QUALITY, family=hint)
                self.assertEqual(result["status"], "ambiguous")
                self.assertEqual(len(result["candidates"]), 2)

    def test_wrong_family_hint_never_confident(self):
        for kind, hint in (("band", "ic"), ("stripe", "diode"),
                           ("dot", "connector"), ("key", "ic")):
            with self.subTest(kind=kind, hint=hint):
                result = interpret([marker(kind=kind)], BODY, QUALITY,
                                   family=hint)
                self.assertEqual(result["status"], "ambiguous")
                top = result["candidates"][0]
                self.assertEqual(top["interpretation"],
                                 "unexpected_marker_kind")
                self.assertLessEqual(top["confidence"], 0.50)

    def test_weak_evidence_below_floor_is_ambiguous(self):
        result = interpret([marker(strength=0.45)], BODY, QUALITY,
                           family="diode")
        self.assertEqual(result["status"], "ambiguous")

    def test_trace_evidence_is_unknown(self):
        result = interpret([marker(strength=0.2)], BODY, QUALITY,
                           family="diode")
        self.assertEqual(result["status"], "unknown")

    def test_no_markers_is_unknown(self):
        result = interpret([], BODY, QUALITY, family="diode")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["candidates"], [])


class InstallationTest(unittest.TestCase):
    def test_no_reference_claims_nothing(self):
        result = interpret([marker()], BODY, QUALITY, family="diode")
        self.assertFalse(result["installation"]["claimed"])

    def test_reference_claims_relative_angle(self):
        markers = [marker(angle=10.0)]
        result = interpret(markers, BODY, QUALITY, family="diode",
                           reference={"kind": "photo", "angle_deg": 350.0})
        installation = result["installation"]
        self.assertTrue(installation["claimed"])
        self.assertAlmostEqual(installation["relative_angle_deg"], 20.0)


if __name__ == "__main__":
    unittest.main()
