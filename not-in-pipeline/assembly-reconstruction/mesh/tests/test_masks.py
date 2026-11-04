"""Mask evidence checks: dimension/hash gates and empty/full diagnostics."""

import hashlib
import os
import sys
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from anastylos_mesh.geometry import MeshCalibrationError, MeshProvenanceError
from anastylos_mesh.masks import Mask
from anastylos_mesh.synthetic import encode_png_gray, make_scene, render_fixtures


class TestMasks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures = render_fixtures(make_scene("box"), n_views=2,
                                   width=80, height=60)
        cls.cam = fixtures.cameras[0]
        cls.mask = fixtures.masks[0]

    def test_dim_mismatch_rejected(self):
        bad_size = replace(self.mask, width=self.mask.width + 1)
        with self.assertRaises(MeshCalibrationError) as ctx:
            bad_size.validate(self.cam)
        self.assertIn("MASK_CAMERA_SIZE_MISMATCH", str(ctx.exception))

        bad_shape = replace(self.mask,
                            foreground=self.mask.foreground[:-1, :])
        with self.assertRaises(MeshCalibrationError) as ctx:
            bad_shape.validate(self.cam)
        self.assertIn("MASK_FOREGROUND_SHAPE_MISMATCH", str(ctx.exception))

    def test_hash_link_mismatch_rejected(self):
        bad_link = replace(self.mask, image_sha256="0" * 64)
        with self.assertRaises(MeshProvenanceError) as ctx:
            bad_link.validate(self.cam)
        self.assertIn("MASK_IMAGE_LINK_MISMATCH", str(ctx.exception))

        bad_sha = replace(self.mask, sha256="0" * 64)
        with self.assertRaises(MeshProvenanceError) as ctx:
            bad_sha.validate(self.cam)
        self.assertIn("MASK_SHA256_MISMATCH", str(ctx.exception))

    def _linked_mask(self, gray_value):
        gray = np.full((self.cam.height, self.cam.width), gray_value,
                       dtype=np.uint8)
        png = encode_png_gray(gray)
        return Mask(png_bytes=png, sha256=hashlib.sha256(png).hexdigest(),
                    image_sha256=self.cam.image_sha256, provenance="synthetic",
                    width=self.cam.width, height=self.cam.height,
                    foreground=(gray >= 127).astype(np.bool_))

    def test_empty_full_flags(self):
        empty = self._linked_mask(0).validate(self.cam)
        diag = empty.diagnostics()
        self.assertTrue(diag["is_empty"])
        self.assertFalse(diag["is_full"])
        self.assertEqual(diag["foreground_fraction"], 0.0)

        full = self._linked_mask(255).validate(self.cam)
        diag = full.diagnostics()
        self.assertTrue(diag["is_full"])
        self.assertFalse(diag["is_empty"])
        self.assertEqual(diag["foreground_fraction"], 1.0)

        diag = self.mask.validate(self.cam).diagnostics()
        self.assertFalse(diag["is_empty"])
        self.assertFalse(diag["is_full"])
        self.assertGreater(diag["foreground_fraction"], 0.0)
        self.assertLess(diag["foreground_fraction"], 1.0)


if __name__ == "__main__":
    unittest.main()
