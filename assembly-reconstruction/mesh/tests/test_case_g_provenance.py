"""Case G: source/mask mismatches rejected with MeshProvenanceError."""

import hashlib
import os
import sys
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from anastylos_mesh.geometry import MeshProvenanceError
from anastylos_mesh.masks import Mask
from anastylos_mesh.synthetic import (
    encode_png_gray,
    make_scene,
    render_fixtures,
)


class TestCaseGProvenance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures = render_fixtures(make_scene("box"), n_views=2,
                                   width=80, height=60)
        cls.cam = fixtures.cameras[0]
        cls.mask = fixtures.masks[0]

    def test_wrong_sha_rejected(self):
        bad = replace(self.mask, sha256="0" * 64)
        with self.assertRaises(MeshProvenanceError) as ctx:
            bad.validate(self.cam)
        self.assertIn("MASK_SHA256_MISMATCH", str(ctx.exception))

    def test_wrong_resolution_rejected(self):
        # Evidence bytes at 40x30 but declared (and camera) at 80x60.
        small = np.zeros((30, 40), dtype=np.uint8)
        png = encode_png_gray(small)
        bad = Mask(
            png_bytes=png, sha256=hashlib.sha256(png).hexdigest(),
            image_sha256=self.cam.image_sha256, provenance="synthetic",
            width=self.cam.width, height=self.cam.height,
            foreground=np.zeros((self.cam.height, self.cam.width),
                                dtype=np.bool_))
        with self.assertRaises(MeshProvenanceError) as ctx:
            bad.validate(self.cam)
        self.assertIn("MASK_PNG_RESOLUTION_MISMATCH", str(ctx.exception))

    def test_wrong_epoch_id_rejected(self):
        bad = replace(self.mask, pose_epoch="other-epoch")
        with self.assertRaises(MeshProvenanceError) as ctx:
            bad.validate(self.cam)
        self.assertIn("MASK_POSE_EPOCH_MISMATCH", str(ctx.exception))

    def test_matching_epoch_id_accepted(self):
        good = replace(self.mask, pose_epoch=self.cam.pose_epoch)
        self.assertIs(good.validate(self.cam), good)


if __name__ == "__main__":
    unittest.main()
