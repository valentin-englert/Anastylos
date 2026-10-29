"""Case B: cylinder radius/height recovery + curved-silhouette metrics."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from anastylos_mesh.carve import VoxelGrid, carve
from anastylos_mesh.metrics import evaluate
from anastylos_mesh.surface import extract_surface
from anastylos_mesh.synthetic import make_scene, render_fixtures


def _grid_for_scene(scene, resolution, margin):
    """ROI grid mirroring the CLI reconstruct convention (padded gt bounds)."""
    extents = [b[1] - b[0] for b in scene.bounds]
    pad = max(extents) * margin
    bounds = tuple((b[0] - pad, b[1] + pad) for b in scene.bounds)
    voxel = max(b[1] - b[0] for b in bounds) / resolution
    return VoxelGrid(bounds_mm=bounds, voxel_size=voxel)


class TestCaseBCylinder(unittest.TestCase):
    def test_radius_height_and_curved_metrics(self):
        scene = make_scene("cylinder")  # r=20, h=40
        fixtures = render_fixtures(scene, n_views=8, width=320, height=240)
        grid = _grid_for_scene(scene, resolution=32, margin=0.05)
        result = carve(fixtures.cameras, fixtures.masks, grid, min_observed=2)
        self.assertGreater(result.occupied_count, 0)
        surface = extract_surface(result)
        report = evaluate(surface.mesh, scene, n_samples=2000, tau_mm=1.0,
                          seed=0)
        dx, dy, dz = report["bbox_dims_rec_mm"]
        # Radius from both circular axes (observed err 0.625mm each).
        self.assertLess(abs(dx / 2.0 - 20.0), 1.5)
        self.assertLess(abs(dy / 2.0 - 20.0), 1.5)
        # Circular symmetry: both axes agree within one voxel (obs 0.0).
        self.assertLess(abs(dx - dy), grid.voxel_size)
        # Height is silhouette-weak (no top views); bound it loosely
        # (observed err 4.0mm on 40mm).
        self.assertLess(abs(dz - 40.0), 6.0)
        # Curved-surface fit (observed chamfer 1.36mm, vol err 9.5%).
        self.assertLess(report["chamfer_symmetric_mm"], 2.5)
        self.assertLess(report["volume_rel_err"], 0.15)
        self.assertTrue(surface.watertight)
        self.assertEqual(surface.components, 1)


if __name__ == "__main__":
    unittest.main()
