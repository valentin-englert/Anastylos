"""Determinism: identical inputs produce identical occupancy and mesh bytes.

Manifest timing is wall-clock and excluded; mesh payload bytes are compared.
"""

import hashlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from anastylos_mesh.carve import VoxelGrid, carve
from anastylos_mesh.surface import extract_surface
from anastylos_mesh.synthetic import make_scene, render_fixtures


def _run_box_pipeline():
    scene = make_scene("box")
    fixtures = render_fixtures(scene, n_views=8, width=96, height=72)
    extents = [b[1] - b[0] for b in scene.bounds]
    pad = max(extents) * 0.05
    bounds = tuple((b[0] - pad, b[1] + pad) for b in scene.bounds)
    voxel = max(b[1] - b[0] for b in bounds) / 32
    grid = VoxelGrid(bounds_mm=bounds, voxel_size=voxel)
    result = carve(fixtures.cameras, fixtures.masks, grid, min_observed=2)
    surface = extract_surface(result)
    ply = bytes(surface.mesh.export(file_type="ply"))
    stl = bytes(surface.mesh.export(file_type="stl"))
    return (np.asarray(result.occupancy).copy(),
            hashlib.sha256(ply).hexdigest(),
            hashlib.sha256(stl).hexdigest())


class TestDeterminism(unittest.TestCase):
    def test_same_inputs_twice_identical_sha256(self):
        occ_a, ply_a, stl_a = _run_box_pipeline()
        occ_b, ply_b, stl_b = _run_box_pipeline()
        self.assertTrue(bool((occ_a == occ_b).all()))
        self.assertEqual(ply_a, ply_b)
        self.assertEqual(stl_a, stl_b)


if __name__ == "__main__":
    unittest.main()
