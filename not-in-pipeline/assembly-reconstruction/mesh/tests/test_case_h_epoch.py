"""Case H: object motion (mixed pose_epoch) rejects; no registration path."""

import os
import sys
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from anastylos_mesh.carve import (
    VoxelGrid,
    carve,
    check_pose_epoch_consistency,
)
from anastylos_mesh.geometry import MeshProvenanceError
from anastylos_mesh.synthetic import make_scene, render_fixtures


def _grid_for_scene(scene, resolution, margin):
    """ROI grid mirroring the CLI reconstruct convention (padded gt bounds)."""
    extents = [b[1] - b[0] for b in scene.bounds]
    pad = max(extents) * margin
    bounds = tuple((b[0] - pad, b[1] + pad) for b in scene.bounds)
    voxel = max(b[1] - b[0] for b in bounds) / resolution
    return VoxelGrid(bounds_mm=bounds, voxel_size=voxel)


class TestCaseHEpoch(unittest.TestCase):
    def test_mixed_epochs_reject_in_reconstruct_path(self):
        scene = make_scene("box")
        fixtures = render_fixtures(scene, n_views=2, width=80, height=60)
        grid = _grid_for_scene(scene, resolution=16, margin=0.05)
        moved = replace(fixtures.cameras[1], pose_epoch="moved-object")
        with self.assertRaises(MeshProvenanceError) as ctx:
            carve([fixtures.cameras[0], moved], fixtures.masks, grid,
                  min_observed=2)
        self.assertIn("MIXED_POSE_EPOCH", str(ctx.exception))

    def test_shared_epoch_succeeds(self):
        scene = make_scene("box")
        fixtures = render_fixtures(scene, n_views=2, width=80, height=60)
        grid = _grid_for_scene(scene, resolution=16, margin=0.05)
        result = carve(fixtures.cameras, fixtures.masks, grid,
                       min_observed=2)
        self.assertGreater(result.occupied_count, 0)
        self.assertEqual(check_pose_epoch_consistency(fixtures.cameras),
                         "synthetic")


if __name__ == "__main__":
    unittest.main()
