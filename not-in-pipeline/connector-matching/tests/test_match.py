"""Mating-rank checks: complements, gates, verdicts, ties. Needs the venv.

Run from the connector-matching directory:

    ..\\.venv\\Scripts\\python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np

from anastylos_connectors.match import COMPAT_NOTE, compare, match_gallery, resolve_weights
from anastylos_connectors.observe import observe
from anastylos_connectors.synthetic import render

META = {"px_per_mm": 4.0}

DEFS = {"plug4L": dict(kind="plug", count=4, keying="left"),
        "sock4R": dict(kind="socket", count=4, keying="right"),
        "sock4L": dict(kind="socket", count=4, keying="left"),
        "sock6R": dict(kind="socket", rows=2, count=6, keying="right"),
        "plug6L": dict(kind="plug", rows=2, count=6, keying="left")}


def gallery(names=None, **kwargs):
    names = names or list(DEFS)
    return [observe(render(seed=1, **DEFS[name]), record_id=name, **kwargs)
            for name in names]


def top(result):
    return result["candidates"][0]["id"] if result["candidates"] else None


class MatingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gallery = gallery()

    def test_plug_ranks_mirrored_socket(self):
        query = observe(render(kind="plug", count=4, keying="left", seed=2))
        result = match_gallery(query, self.gallery)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(top(result), "sock4R")

    def test_socket_ranks_mirrored_plug(self):
        query = observe(render(kind="socket", count=4, keying="right", seed=5))
        result = match_gallery(query, self.gallery)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(top(result), "plug4L")

    def test_rotated_queries_rank_mate(self):
        for deg in (15, 30, 45, 90, 135, 180):
            with self.subTest(deg=deg):
                query = observe(render(kind="plug", count=4, keying="left",
                                       rotation_deg=deg, seed=30))
                result = match_gallery(query, self.gallery)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(top(result), "sock4R")

    def test_same_side_keying_gated(self):
        query = observe(render(kind="plug", count=4, keying="left", seed=2))
        rival = next(item for item in self.gallery if item["id"] == "sock4L")
        scored = compare(query, rival)
        self.assertTrue(any(gate["gate"] == "keying_conflict" for gate in scored["gates"]))

    def test_same_role_gated(self):
        query = observe(render(kind="plug", count=4, keying="left", seed=2))
        rival = next(item for item in self.gallery if item["id"] == "plug4L")
        scored = compare(query, rival)
        self.assertTrue(any(gate["gate"] == "same_role" for gate in scored["gates"]))
        self.assertLess(scored["mating_score"], 0.55)

    def test_visual_alone_never_verifies(self):
        query = observe(render(kind="plug", count=4, keying="left", seed=2))
        result = match_gallery(query, self.gallery)
        (candidate,) = [item for item in result["candidates"] if item["id"] == "sock4R"]
        self.assertFalse(candidate["verified_mechanical"])
        self.assertFalse(candidate["verified_electrical"])
        self.assertIn("never", result["compat_note"])
        self.assertEqual(result["compat_note"], COMPAT_NOTE)

    def test_subset_contacts_rank_without_interference(self):
        query = observe(render(kind="plug", count=4, keying="left", missing=(1,), seed=9))
        result = match_gallery(query, self.gallery)
        self.assertEqual(top(result), "sock4R")
        (candidate,) = [item for item in result["candidates"] if item["id"] == "sock4R"]
        self.assertFalse(any(gate["gate"] == "contact_interference"
                             for gate in candidate["gates"]))

    def test_extra_pins_gated_as_interference(self):
        query = observe(render(kind="plug", rows=2, count=6, keying="left", seed=6))
        rival = next(item for item in self.gallery if item["id"] == "sock4R")
        scored = compare(query, rival)
        self.assertTrue(any(gate["gate"] == "contact_interference"
                            for gate in scored["gates"]))

    def test_six_contact_mate_found(self):
        query = observe(render(kind="plug", rows=2, count=6, keying="left", seed=6))
        result = match_gallery(query, self.gallery)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(top(result), "sock6R")

    def test_bent_and_noise_queries_rank_mate(self):
        for kwargs in (dict(kind="plug", count=4, keying="left", bent=(2,), seed=11),
                       dict(kind="plug", count=4, keying="left", noise=1.0, seed=7),
                       dict(kind="plug", count=4, keying="left", scale=0.75, seed=8),
                       dict(kind="plug", count=4, keying="left", occlude=0.6, seed=12)):
            with self.subTest(kwargs=kwargs):
                result = match_gallery(observe(render(**kwargs)), self.gallery)
                self.assertEqual(top(result), "sock4R")

    def test_heavy_glare_never_confident(self):
        query = observe(render(kind="plug", count=4, keying="left", glare=True, seed=7))
        result = match_gallery(query, self.gallery)
        self.assertIn(result["status"], ("unknown", "ambiguous"))


class AmbiguityTest(unittest.TestCase):
    def test_identical_twins_tie(self):
        twins = [observe(render("socket", count=4, keying="right", seed=21), record_id="s-A"),
                 observe(render("socket", count=4, keying="right", seed=22), record_id="s-B")]
        query = observe(render("plug", count=4, keying="left", seed=23))
        result = match_gallery(query, twins)
        self.assertEqual(result["status"], "ambiguous")
        self.assertIn("tie", result["reason"])

    def test_tie_order_deterministic(self):
        first = observe(render("socket", count=4, keying="right", seed=1), record_id="a-sock")
        second = observe(render("socket", count=4, keying="right", seed=1), record_id="b-sock")
        query = observe(render("plug", count=4, keying="left", seed=2))
        left = match_gallery(query, [first, second])["candidates"]
        right = match_gallery(query, [second, first])["candidates"]
        self.assertEqual([item["id"] for item in left], ["a-sock", "b-sock"])
        self.assertEqual(left, right)

    def test_side_view_unknown_for_missing_face(self):
        query = observe(render("side", seed=10))
        result = match_gallery(query, gallery())
        self.assertEqual(result["status"], "unknown")
        self.assertIn("mating-face visibility", result["reason"])

    def test_poor_query_unknown(self):
        query = observe(np.full((200, 200, 3), 128, np.uint8))
        result = match_gallery(query, gallery())
        self.assertEqual(result["status"], "unknown")

    def test_empty_gallery_unknown(self):
        result = match_gallery(observe(render("plug", seed=1)), [])
        self.assertEqual(result["status"], "unknown")


class ComponentTest(unittest.TestCase):
    def test_mate_reports_components(self):
        query = observe(render(kind="plug", count=4, keying="left", seed=2))
        mate = next(item for item in gallery() if item["id"] == "sock4R")
        scored = compare(query, mate)
        self.assertGreater(scored["mating_score"], 0.9)
        for name in ("housing", "count", "layout", "keying", "role"):
            self.assertIsNotNone(scored["components"][name]["score"])
        self.assertEqual(scored["components"]["role"]["score"], 1.0)
        self.assertEqual(scored["gates"], [])
        self.assertIn("layout", scored["used"])

    def test_calibrated_pitch_compares(self):
        query = observe(render(kind="plug", count=4, keying="left", seed=2),
                        frame_meta=META)
        mate = observe(render(kind="socket", count=4, keying="right", seed=1),
                       frame_meta=META, record_id="sock4R")
        scored = compare(query, mate)
        self.assertGreater(scored["components"]["pitch"]["score"], 0.9)

    def test_pitch_unavailable_without_calibration(self):
        query = observe(render(kind="plug", count=4, keying="left", seed=2))
        mate = observe(render(kind="socket", count=4, keying="right", seed=1),
                       frame_meta=META, record_id="sock4R")
        component = compare(query, mate)["components"]["pitch"]
        self.assertIsNone(component["score"])

    def test_text_agreement_and_mismatch(self):
        query = observe(render(kind="plug", count=4, keying="left", seed=2), marking="X1")
        mate = observe(render(kind="socket", count=4, keying="right", seed=1),
                       marking="X1", record_id="sock4R")
        self.assertEqual(compare(query, mate)["components"]["text"]["score"], 1.0)
        other = observe(render(kind="socket", count=4, keying="right", seed=1),
                        marking="X2", record_id="sock4R")
        self.assertEqual(compare(query, other)["components"]["text"]["score"], 0.0)

    def test_custom_weights_respected(self):
        weights = resolve_weights({"layout": 1.0, "housing": 0.0, "count": 0.0,
                                   "keying": 0.0, "role": 0.0, "pitch": 0.0,
                                   "text": 0.0})
        query = observe(render(kind="plug", count=4, keying="left", seed=2))
        mate = next(item for item in gallery() if item["id"] == "sock4R")
        full = compare(query, mate)
        single = compare(query, mate, weights=weights)
        self.assertEqual(single["mating_score"], full["components"]["layout"]["score"])
        self.assertEqual(single["used"], ["layout"])

    def test_unknown_weight_rejected(self):
        with self.assertRaises(ValueError):
            resolve_weights({"telepathy": 1.0})
        with self.assertRaises(ValueError):
            resolve_weights({"layout": -1.0})


if __name__ == "__main__":
    unittest.main()
