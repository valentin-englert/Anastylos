"""Matching checks: components, ranking, verdicts, ties. Needs the venv.

Run from the component-fingerprints directory:

    ..\\.venv\\Scripts\\python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np

from anastylos_fingerprints.describe import fingerprint
from anastylos_fingerprints.match import (
    IDENTITY_NOTE,
    compare,
    match_gallery,
    resolve_weights,
)
from anastylos_fingerprints.synthetic import render

META = {"px_per_mm": 4.0}
KINDS = ("ic", "resistor", "screw", "capacitor")


def gallery(seed=1, **kwargs):
    return [fingerprint(render(kind, seed=seed), record_id=kind, **kwargs)
            for kind in KINDS]


def top(result):
    return result["candidates"][0]["id"] if result["candidates"] else None


class VariationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gallery = gallery()

    def test_rotations_match(self):
        for kind in KINDS:
            for deg in (15, 30, 45, 90, 135):
                with self.subTest(kind=kind, deg=deg):
                    query = fingerprint(render(kind, rotation_deg=deg, seed=30))
                    result = match_gallery(query, self.gallery)
                    self.assertEqual(result["status"], "ok")
                    self.assertEqual(top(result), kind)

    def test_scale_lighting_background_noise_match(self):
        cases = [render("ic", scale=0.8, seed=4), render("ic", scale=1.2, seed=4),
                 render("ic", brightness=-0.4, seed=3),
                 render("ic", brightness=0.3, seed=3),
                 render("ic", background=120, seed=6),
                 render("ic", noise=1.0, seed=5)]
        for index, image in enumerate(cases):
            with self.subTest(case=index):
                result = match_gallery(fingerprint(image), self.gallery)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(top(result), "ic")

    def test_background_occlusion_match(self):
        query = fingerprint(render("ic", occlude=0.6, seed=8))
        result = match_gallery(query, self.gallery)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(top(result), "ic")

    def test_heavy_glare_unknown_with_correct_top(self):
        query = fingerprint(render("ic", glare=True, seed=7))
        result = match_gallery(query, self.gallery)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(top(result), "ic")

    def test_different_shapes_score_low(self):
        query = fingerprint(render("ic", seed=1))
        for record in self.gallery:
            score = compare(query, record)["score"]
            if record["id"] == "ic":
                self.assertEqual(score, 1.0)
            else:
                self.assertLess(score, 0.55)

    def test_unknown_object_unknown(self):
        query = fingerprint(render("capacitor", seed=50))
        result = match_gallery(query, [item for item in self.gallery
                                       if item["id"] != "capacitor"])
        self.assertEqual(result["status"], "unknown")


class AmbiguityTest(unittest.TestCase):
    def test_identical_twins_ambiguous(self):
        twins = [fingerprint(render("ic", noise=0.3, seed=21), record_id="ic-A"),
                 fingerprint(render("ic", noise=0.3, seed=22), record_id="ic-B")]
        query = fingerprint(render("ic", noise=0.3, seed=23))
        result = match_gallery(query, twins)
        self.assertEqual(result["status"], "ambiguous")
        self.assertIn("margin", result["reason"])
        self.assertEqual({item["id"] for item in result["candidates"]}, {"ic-A", "ic-B"})

    def test_exact_tie_ambiguous(self):
        first = fingerprint(render("screw", seed=1), record_id="a-screw")
        second = fingerprint(render("screw", seed=1), record_id="b-screw")
        result = match_gallery(fingerprint(render("screw", seed=1)), [first, second])
        self.assertEqual(result["status"], "ambiguous")
        self.assertIn("tie", result["reason"])

    def test_tie_order_deterministic(self):
        first = fingerprint(render("screw", seed=1), record_id="a-screw")
        second = fingerprint(render("screw", seed=1), record_id="b-screw")
        query = fingerprint(render("screw", seed=1))
        left = match_gallery(query, [first, second])["candidates"]
        right = match_gallery(query, [second, first])["candidates"]
        self.assertEqual([item["id"] for item in left], ["a-screw", "b-screw"])
        self.assertEqual(left, right)

    def test_best_score_never_claims_identity(self):
        result = match_gallery(fingerprint(render("ic", seed=1)), gallery())
        self.assertEqual(result["status"], "ok")
        self.assertIn("not proof", result["identity_note"])
        self.assertEqual(result["identity_note"], IDENTITY_NOTE)


class ComponentTest(unittest.TestCase):
    def test_components_reported_per_candidate(self):
        result = match_gallery(fingerprint(render("ic", seed=1)), gallery())
        (candidate,) = [item for item in result["candidates"] if item["id"] == "ic"]
        self.assertEqual(candidate["score"], 1.0)
        for name in ("silhouette", "geometry", "color", "appearance", "local"):
            self.assertEqual(candidate["components"][name]["score"], 1.0)
        self.assertIsNone(candidate["components"]["size"]["score"])
        self.assertIsNone(candidate["components"]["text"]["score"])
        self.assertIn("local", candidate["used"])
        self.assertNotIn("size", candidate["used"])

    def test_calibrated_size_compares(self):
        ref = fingerprint(render("ic", seed=1), frame_meta=META, record_id="ic")
        same = fingerprint(render("ic", seed=1), frame_meta=META)
        scaled = fingerprint(render("ic", scale=0.8, seed=4), frame_meta=META)
        self.assertEqual(compare(same, ref)["components"]["size"]["score"], 1.0)
        smaller = compare(scaled, ref)["components"]["size"]["score"]
        self.assertLess(smaller, 1.0)
        self.assertGreater(smaller, 0.5)

    def test_size_unavailable_without_calibration(self):
        ref = fingerprint(render("ic", seed=1), frame_meta=META, record_id="ic")
        query = fingerprint(render("ic", seed=1))
        component = compare(query, ref)["components"]["size"]
        self.assertIsNone(component["score"])
        self.assertIn("never compared as mm", component["detail"])

    def test_text_agreement_and_mismatch(self):
        ref = fingerprint(render("ic", seed=1), marking="358", record_id="ic")
        same = compare(fingerprint(render("ic", seed=1), marking="358"), ref)
        self.assertEqual(same["components"]["text"]["score"], 1.0)
        other = compare(fingerprint(render("ic", seed=1), marking="555"), ref)
        self.assertEqual(other["components"]["text"]["score"], 0.0)
        missing = compare(fingerprint(render("ic", seed=1)), ref)
        self.assertIsNone(missing["components"]["text"]["score"])

    def test_custom_weights_respected(self):
        weights = resolve_weights({"local": 1.0, "silhouette": 0.0, "geometry": 0.0,
                                   "size": 0.0, "color": 0.0, "appearance": 0.0,
                                   "text": 0.0})
        query = fingerprint(render("ic", rotation_deg=30, seed=2))
        ref = fingerprint(render("ic", seed=1), record_id="ic")
        full = compare(query, ref)
        local_only = compare(query, ref, weights=weights)
        self.assertEqual(local_only["score"], full["components"]["local"]["score"])
        self.assertEqual(local_only["used"], ["local"])

    def test_unknown_weight_rejected(self):
        with self.assertRaises(ValueError):
            resolve_weights({"telepathy": 1.0})
        with self.assertRaises(ValueError):
            resolve_weights({"local": -1.0})


class QualityVerdictTest(unittest.TestCase):
    def test_poor_query_rejected(self):
        query = fingerprint(np.full((200, 200, 3), 128, np.uint8))
        result = match_gallery(query, gallery())
        self.assertEqual(result["status"], "rejected")
        self.assertIn("contrast", result["reason"])

    def test_empty_gallery_unknown(self):
        result = match_gallery(fingerprint(render("ic", seed=1)), [])
        self.assertEqual(result["status"], "unknown")

    def test_excluded_passed_through(self):
        excluded = [{"id": "old-ic", "reason": "descriptor version 0, current is 1"}]
        result = match_gallery(fingerprint(render("ic", seed=1)), gallery(), excluded)
        self.assertEqual(result["excluded"], excluded)


if __name__ == "__main__":
    unittest.main()
