"""Descriptor checks on deterministic synthetic renders. Needs the venv.

Fixtures exercise creation only; they prove nothing about
real-hardware retrieval.

Run from the component-fingerprints directory:

    ..\\.venv\\Scripts\\python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2
import numpy as np

from anastylos_fingerprints.adapters import EmbeddingProvider
from anastylos_fingerprints.describe import (
    DESCRIPTOR_VERSION,
    fingerprint,
    silhouette_mask,
    validate_frame_meta,
)
from anastylos_fingerprints.synthetic import render

NOW = "2026-01-01T00:00:00Z"
META = {"px_per_mm": 4.0, "view": "surface_a4", "origin_frame_mm": [52, 42]}


class MeanProvider(EmbeddingProvider):
    name = "mean-stub"
    version_text = "1"

    def embed(self, crop_bgr):
        return [float(crop_bgr[:, :, i].mean()) for i in range(3)]


class DescribeTest(unittest.TestCase):
    def test_record_carries_version_provenance_and_features(self):
        record = fingerprint(render("ic", seed=1), record_id="ic-01",
                             source={"image": "ic.png"}, now=NOW)
        self.assertEqual(record["descriptor_version"], DESCRIPTOR_VERSION)
        self.assertEqual(record["created_utc"], NOW)
        self.assertEqual(record["source"]["roi_xywh"], [0, 0, 320, 320])
        self.assertEqual(len(record["source"]["crop_sha256"]), 64)
        self.assertEqual(len(record["hu"]), 7)
        self.assertIn("aspect", record["shape"])
        self.assertIn("count", record["holes"])
        self.assertEqual(len(record["thumb"]["pixels"]), 256)
        self.assertGreater(record["orb"]["count"], 0)
        self.assertTrue(record["quality"]["usable"])
        json.dumps(record)

    def test_calibrated_size_in_mm(self):
        record = fingerprint(render("ic", seed=1), frame_meta=META)
        self.assertEqual(record["size_source"], "calibrated")
        width_mm, height_mm = record["size_mm"]
        self.assertAlmostEqual(width_mm, record["shape"]["long_px"] / 4.0, places=2)
        self.assertGreater(width_mm, height_mm)

    def test_uncalibrated_never_reports_mm(self):
        record = fingerprint(render("ic", seed=1))
        self.assertEqual(record["size_source"], "uncalibrated")
        self.assertIsNone(record["size_mm"])

    def test_invalid_frame_meta_rejected(self):
        for bad in ({"px_per_mm": 0}, {"px_per_mm": -2}, {"view": "x"}, "flat"):
            with self.subTest(meta=bad):
                with self.assertRaises(ValueError):
                    validate_frame_meta(bad)
                with self.assertRaises(ValueError):
                    fingerprint(render("ic"), frame_meta=bad)

    def test_marking_preserved_raw_and_normalized(self):
        record = fingerprint(render("ic", seed=1), marking=" 358 ")
        self.assertEqual(record["text"], {"raw": " 358 ", "normalized": "358"})

    def test_blank_marking_is_none(self):
        record = fingerprint(render("ic", seed=1), marking="   ")
        self.assertIsNone(record["text"])

    def test_provider_embeddings_namespaced(self):
        record = fingerprint(render("ic", seed=1), providers=[MeanProvider()])
        self.assertEqual(record["embeddings"]["mean-stub"]["version"], "1")
        self.assertEqual(len(record["embeddings"]["mean-stub"]["vector"]), 3)

    def test_provider_may_decline(self):
        class Shy(MeanProvider):
            name = "shy-stub"

            def embed(self, crop_bgr):
                return None

        record = fingerprint(render("ic", seed=1), providers=[Shy()])
        self.assertEqual(record["embeddings"], {})

    def test_roi_crops_and_validates(self):
        image = render("ic", seed=1)
        record = fingerprint(image, roi=(60, 60, 200, 200))
        self.assertEqual(record["source"]["roi_xywh"], [60, 60, 200, 200])
        self.assertEqual(record["source"]["crop_size_xy"], [200, 200])
        with self.assertRaises(ValueError):
            fingerprint(image, roi=(300, 300, 200, 200))

    def test_deterministic_for_same_pixels(self):
        image = render("resistor", rotation_deg=30, seed=7)
        first = fingerprint(image.copy(), now=NOW)
        second = fingerprint(image.copy(), now=NOW)
        self.assertEqual(first, second)


class QualityTest(unittest.TestCase):
    def test_empty_image_rejected(self):
        with self.assertRaises(ValueError):
            fingerprint(np.zeros((0, 0, 3), np.uint8))
        with self.assertRaises(ValueError):
            fingerprint(None)

    def test_uniform_crop_unusable(self):
        record = fingerprint(np.full((200, 200, 3), 128, np.uint8))
        self.assertFalse(record["quality"]["usable"])
        self.assertIn("contrast", record["quality"]["reason"])

    def test_tiny_crop_unusable(self):
        record = fingerprint(np.full((16, 16, 3), 128, np.uint8))
        self.assertFalse(record["quality"]["usable"])

    def test_heavy_blur_unusable(self):
        record = fingerprint(render("ic", blur=3.0, seed=1))
        self.assertFalse(record["quality"]["usable"])
        self.assertIn("blurry", record["quality"]["reason"])

    def test_blank_render_unusable(self):
        record = fingerprint(render("blank", seed=1))
        self.assertFalse(record["quality"]["usable"])

    def test_quality_metrics_present(self):
        record = fingerprint(render("screw", seed=1))
        for key in ("gray_std", "blur_variance", "glare_fraction",
                    "shadow_fraction", "silhouette_fraction"):
            self.assertIn(key, record["quality"])


class SilhouetteTest(unittest.TestCase):
    def test_mask_marks_component_not_frame(self):
        mask = silhouette_mask(render("ic", seed=1))
        fraction = (mask > 0).mean()
        self.assertGreater(fraction, 0.02)
        self.assertLess(fraction, 0.995)

    def test_mask_none_without_silhouette(self):
        self.assertIsNone(silhouette_mask(np.full((200, 200, 3), 128, np.uint8)))

    def test_mask_respects_roi_offset(self):
        canvas = np.full((400, 400, 3), 200, np.uint8)
        canvas[40:360, 40:360] = render("screw", seed=1)
        mask = silhouette_mask(canvas, roi=(40, 40, 320, 320))
        self.assertEqual(mask.shape, (400, 400))
        self.assertFalse((mask[:40] > 0).any())


if __name__ == "__main__":
    unittest.main()
