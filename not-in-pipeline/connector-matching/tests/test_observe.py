"""Observation checks on deterministic synthetic renders. Needs the venv.

Fixtures exercise extraction only; they prove nothing about
real-hardware matching.

Run from the connector-matching directory:

    ..\\.venv\\Scripts\\python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np

from anastylos_connectors.adapters import AssistantProvider
from anastylos_connectors.observe import (
    OBSERVATION_VERSION,
    housing_mask,
    observe,
    validate_frame_meta,
)
from anastylos_connectors.synthetic import render

NOW = "2026-01-01T00:00:00Z"
META = {"px_per_mm": 4.0, "view": "surface_a4", "origin_frame_mm": [52, 42]}


class NoteTaker(AssistantProvider):
    name = "note-stub"
    version_text = "3"

    def advise(self, observation):
        return {"contacts_seen": observation["contact_count"]}


class ObserveTest(unittest.TestCase):
    def test_plug_role_count_layout(self):
        record = observe(render("plug", count=4, seed=1), record_id="p4",
                         source={"image": "p4.png"}, now=NOW)
        self.assertEqual(record["observation_version"], OBSERVATION_VERSION)
        self.assertTrue(record["quality"]["usable"])
        self.assertTrue(record["mating_face_visible"])
        self.assertEqual(record["role"], "plug")
        self.assertEqual(record["contact_count"], 4)
        self.assertEqual(len(record["pins"]), 4)
        self.assertEqual(record["cavities"], [])
        self.assertEqual(record["layout"]["rows"], 1)
        self.assertEqual(record["layout"]["per_row"], [4])
        self.assertAlmostEqual(record["layout"]["pitch_px"], 34.0, delta=1.0)
        self.assertEqual(len(record["source"]["crop_sha256"]), 64)
        json.dumps(record)

    def test_socket_role_two_rows(self):
        record = observe(render("socket", rows=2, count=6, seed=1))
        self.assertEqual(record["role"], "socket")
        self.assertEqual(record["contact_count"], 6)
        self.assertEqual(len(record["cavities"]), 6)
        self.assertEqual(record["pins"], [])
        self.assertEqual(record["layout"]["rows"], 2)
        self.assertEqual(record["layout"]["per_row"], [3, 3])

    def test_keying_side_recorded(self):
        left = observe(render("plug", count=4, keying="left", seed=1))
        right = observe(render("plug", count=4, keying="right", seed=1))
        (latch_l,) = left["keying_notches"]
        (latch_r,) = right["keying_notches"]
        self.assertLess(latch_l["cuv"][0], 0.5)
        self.assertGreater(latch_r["cuv"][0], 0.5)

    def test_latch_detected_and_absent(self):
        with_latch = observe(render("plug", count=4, latch=True, seed=1))
        without = observe(render("plug", count=4, latch=False, seed=1))
        self.assertEqual(len(with_latch["latches"]), 1)
        self.assertEqual(without["latches"], [])

    def test_side_view_unknown_role_no_face(self):
        record = observe(render("side", seed=1))
        self.assertTrue(record["quality"]["usable"])
        self.assertFalse(record["mating_face_visible"])
        self.assertEqual(record["role"], "unknown")
        self.assertEqual(record["contact_count"], 0)
        self.assertIn("mating face", record["role_evidence"])

    def test_rotation_keeps_layout(self):
        for deg in (15, 30, 45, 90, 135, 180):
            with self.subTest(deg=deg):
                record = observe(render("plug", count=4, rotation_deg=deg, seed=30))
                self.assertEqual(record["role"], "plug")
                self.assertEqual(record["contact_count"], 4)
                self.assertEqual(record["layout"]["rows"], 1)
                self.assertAlmostEqual(record["layout"]["pitch_px"], 34.0, delta=2.0)

    def test_missing_contact_reduces_count(self):
        record = observe(render("plug", count=4, missing=(1,), seed=1))
        self.assertEqual(record["contact_count"], 3)

    def test_bent_contact_detected_off_grid(self):
        straight = observe(render("plug", count=4, seed=1))
        bent = observe(render("plug", count=4, bent=(2,), seed=1))
        self.assertEqual(bent["contact_count"], 4)
        plain = [tuple(blob["cuv"]) for blob in straight["pins"]]
        moved = [tuple(blob["cuv"]) for blob in bent["pins"]]
        self.assertNotEqual(plain, moved)

    def test_calibrated_pitch_in_mm(self):
        record = observe(render("plug", count=4, seed=1), frame_meta=META)
        self.assertAlmostEqual(record["pitch_mm"], 8.5, delta=0.1)
        self.assertIn("valid calibration", record["pitch_note"])

    def test_uncalibrated_pitch_pixels_only(self):
        record = observe(render("plug", count=4, seed=1))
        self.assertIsNotNone(record["layout"]["pitch_px"])
        self.assertIsNone(record["pitch_mm"])
        self.assertIn("not comparable", record["pitch_note"])

    def test_invalid_frame_meta_rejected(self):
        for bad in ({"px_per_mm": 0}, {"px_per_mm": -2}, {"view": "x"}, [4.0]):
            with self.subTest(meta=bad):
                with self.assertRaises(ValueError):
                    validate_frame_meta(bad)
                with self.assertRaises(ValueError):
                    observe(render("plug", seed=1), frame_meta=bad)

    def test_marking_preserved(self):
        record = observe(render("plug", seed=1), marking=" x1 ")
        self.assertEqual(record["text"], {"raw": " x1 ", "normalized": "X1"})

    def test_assistant_notes_advisory(self):
        record = observe(render("plug", count=4, seed=1), assistants=[NoteTaker()])
        self.assertEqual(record["assistant_notes"]["note-stub"]["notes"],
                         {"contacts_seen": 4})

    def test_assistant_failure_recorded(self):
        class Broken(NoteTaker):
            def advise(self, observation):
                raise RuntimeError("boom")

        record = observe(render("plug", count=4, seed=1), assistants=[Broken()])
        self.assertIn("boom", record["assistant_notes"]["note-stub"]["notes"]["error"])
        self.assertEqual(record["role"], "plug")

    def test_roi_crops_and_validates(self):
        record = observe(render("plug", seed=1), roi=(60, 60, 240, 240))
        self.assertEqual(record["source"]["roi_xywh"], [60, 60, 240, 240])
        with self.assertRaises(ValueError):
            observe(render("plug", seed=1), roi=(300, 300, 200, 200))

    def test_deterministic_for_same_pixels(self):
        image = render("socket", rows=2, count=6, rotation_deg=20, seed=7)
        first = observe(image.copy(), frame_meta=META, now=NOW)
        second = observe(image.copy(), frame_meta=META, now=NOW)
        self.assertEqual(first, second)


class QualityTest(unittest.TestCase):
    def test_empty_image_rejected(self):
        with self.assertRaises(ValueError):
            observe(np.zeros((0, 0, 3), np.uint8))
        with self.assertRaises(ValueError):
            observe(None)

    def test_uniform_crop_unusable(self):
        record = observe(np.full((200, 200, 3), 128, np.uint8))
        self.assertFalse(record["quality"]["usable"])
        self.assertIn("contrast", record["quality"]["reason"])

    def test_heavy_blur_unusable(self):
        record = observe(render("plug", blur=3.0, seed=1))
        self.assertFalse(record["quality"]["usable"])
        self.assertIn("blurry", record["quality"]["reason"])

    def test_blank_render_unusable(self):
        record = observe(render("blank", seed=1))
        self.assertFalse(record["quality"]["usable"])


class HousingMaskTest(unittest.TestCase):
    def test_mask_marks_housing_not_frame(self):
        mask = housing_mask(render("plug", seed=1))
        fraction = (mask > 0).mean()
        self.assertGreater(fraction, 0.02)
        self.assertLess(fraction, 0.995)

    def test_mask_none_without_housing(self):
        self.assertIsNone(housing_mask(np.full((200, 200, 3), 128, np.uint8)))

    def test_mask_respects_roi_offset(self):
        canvas = np.full((440, 440, 3), 200, np.uint8)
        canvas[40:400, 40:400] = render("plug", seed=1)
        mask = housing_mask(canvas, roi=(40, 40, 360, 360))
        self.assertEqual(mask.shape, (440, 440))
        self.assertFalse((mask[:40] > 0).any())


if __name__ == "__main__":
    unittest.main()
