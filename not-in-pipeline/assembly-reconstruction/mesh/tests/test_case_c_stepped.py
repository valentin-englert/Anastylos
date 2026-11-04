"""Case C: asymmetric stepped object on a wide ring + rotation-sense check.

The default stepped scene is asymmetric (top block offset ox=10, oy=-5).
The reconstruction must match the UNMIRRORED orientation: an x-mirrored
copy of the reconstruction fits the ground truth worse.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from trimesh.sample import sample_surface

from anastylos_mesh.carve import VoxelGrid, carve, max_baseline_deg
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


def _reconstruct():
    scene = make_scene("stepped")
    assert scene.params["ox"] != 0, "sense check needs an asymmetric scene"
    fixtures = render_fixtures(scene, n_views=8, width=320, height=240,
                               azimuth_start_deg=22.5)
    grid = _grid_for_scene(scene, resolution=32, margin=0.05)
    result = carve(fixtures.cameras, fixtures.masks, grid, min_observed=2)
    surface = extract_surface(result)
    return scene, fixtures, grid, result, surface


class TestCaseCStepped(unittest.TestCase):
    def test_wide_ring_bbox_and_baseline(self):
        scene, fixtures, grid, result, surface = _reconstruct()
        self.assertGreater(result.occupied_count, 0)
        baseline = max_baseline_deg(fixtures.cameras, grid.roi_center)
        self.assertGreater(baseline, 90.0)  # substantially different views
        report = evaluate(surface.mesh, scene, n_samples=2000, tau_mm=1.0,
                          seed=0)
        rel = [a / g for a, g in zip(report["bbox_dims_abs_err_mm"],
                                     report["bbox_dims_gt_mm"])]
        self.assertLess(max(rel), 0.25)  # observed 0.186
        self.assertLess(report["chamfer_symmetric_mm"], 4.5)  # observed 3.25
        self.assertTrue(surface.watertight)
        self.assertEqual(surface.components, 1)

    def test_rotation_sense_unmirrored_wins(self):
        scene, _, _, _, surface = _reconstruct()
        report = evaluate(surface.mesh, scene, n_samples=2000, tau_mm=1.0,
                          seed=0)
        mirrored = surface.mesh.copy()
        mirrored.vertices[:, 0] = -np.asarray(mirrored.vertices)[:, 0]
        report_mir = evaluate(mirrored, scene, n_samples=2000, tau_mm=1.0,
                              seed=0)
        # Mirrored orientation fits worse (observed gaps: sym +0.80,
        # rec->gt +1.09); the reconstruction captured the true sense.
        self.assertGreater(
            report_mir["chamfer_symmetric_mm"],
            report["chamfer_symmetric_mm"] + 0.5)
        self.assertGreater(
            report_mir["chamfer_rec_to_gt_mm"],
            report["chamfer_rec_to_gt_mm"] + 0.75)
        # Independent sense signal: the top-block (z > 25mm) surface
        # centroid sits near ox=+10, not near the mirrored -10.
        pts, _ = sample_surface(surface.mesh, 5000, seed=11)
        top = np.asarray(pts)[np.asarray(pts)[:, 2] > 25.0]
        self.assertGreater(len(top), 100)
        cx = float(top[:, 0].mean())
        self.assertLess(abs(cx - scene.params["ox"]), 5.0)
        self.assertGreater(abs(-cx - scene.params["ox"]), 10.0)


if __name__ == "__main__":
    unittest.main()
