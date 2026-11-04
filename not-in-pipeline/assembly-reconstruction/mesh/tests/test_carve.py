"""Carving checks: box accuracy, min_observed/baseline gates, cavity fill."""

import hashlib
import os
import sys
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from anastylos_mesh.carve import VoxelGrid, carve, max_baseline_deg
from anastylos_mesh.export import build_manifest
from anastylos_mesh.geometry import DegenerateBaselineError, MeshCoverageError
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


class TestCarve(unittest.TestCase):
    def test_box_8view_nonempty_and_bbox_under_5pct(self):
        scene = make_scene("box")
        fixtures = render_fixtures(scene, n_views=8, width=320, height=240)
        grid = _grid_for_scene(scene, resolution=32, margin=0.05)
        result = carve(fixtures.cameras, fixtures.masks, grid, min_observed=2)
        self.assertGreater(result.occupied_count, 0)
        surface = extract_surface(result)
        report = evaluate(surface.mesh, scene, n_samples=2000, tau_mm=1.0,
                          seed=0)
        rel = [abs(r - g) / g for r, g in
               zip(report["bbox_dims_rec_mm"], report["bbox_dims_gt_mm"])]
        self.assertLess(max(rel), 0.05)

    def test_single_view_rejected_by_min_observed(self):
        scene = make_scene("box")
        fixtures = render_fixtures(scene, n_views=2, width=80, height=60)
        grid = _grid_for_scene(scene, resolution=16, margin=0.05)
        with self.assertRaises(MeshCoverageError) as ctx:
            carve(fixtures.cameras[:1], fixtures.masks[:1], grid,
                  min_observed=2, min_views=1)
        self.assertIn("MIN_OBSERVED_EXCEEDS_VIEWS", str(ctx.exception))

    def test_near_identical_viewpoints_rejected_by_baseline(self):
        scene = make_scene("box")
        fixtures = render_fixtures(scene, n_views=2, width=80, height=60)
        grid = _grid_for_scene(scene, resolution=16, margin=0.05)
        shifted = list(fixtures.cameras[0].T_board_camera)
        shifted[3] += 0.001
        dup = replace(fixtures.cameras[0], T_board_camera=tuple(shifted),
                      pose_epoch="synthetic-dup")
        cams = [fixtures.cameras[0], dup]
        self.assertLess(max_baseline_deg(cams, grid.roi_center), 5.0)
        with self.assertRaises(DegenerateBaselineError) as ctx:
            carve(cams, [fixtures.masks[0], fixtures.masks[0]], grid,
                  min_observed=1)
        self.assertIn("DEGENERATE_BASELINE", str(ctx.exception))

    def test_cavity_not_reconstructed_volume_exceeds_and_marked(self):
        scene = make_scene("cavity")
        fixtures = render_fixtures(scene, n_views=8, width=160, height=120)
        grid = _grid_for_scene(scene, resolution=48, margin=0.05)
        result = carve(fixtures.cameras, fixtures.masks, grid, min_observed=2)
        self.assertGreater(result.occupied_count, 0)
        surface = extract_surface(result)

        void_center = np.array([0.0, 0.0, 17.5])
        dist2 = ((grid.centers() - void_center) ** 2).sum(axis=1)
        flat = np.asarray(result.occupancy).reshape(-1)
        self.assertTrue(bool(flat[int(np.argmin(dist2))]))

        self.assertTrue(surface.watertight)
        voxel_volume = result.occupied_count * grid.voxel_size ** 3
        self.assertGreater(voxel_volume, scene.gt_volume)
        self.assertGreater(float(surface.mesh.volume), scene.gt_volume)

        ply = bytes(surface.mesh.export(file_type="ply"))
        stl = bytes(surface.mesh.export(file_type="stl"))
        manifest = build_manifest(
            params={"scene": scene.name}, image_hashes=[c.image_sha256 for c in
                                                        fixtures.cameras],
            mask_hashes=[m.sha256 for m in fixtures.masks],
            pose_epochs=[c.pose_epoch for c in fixtures.cameras],
            mask_provenance=fixtures.masks[0].provenance,
            voxel_size_mm=grid.voxel_size, dims=list(grid.dims),
            bounds_mm=[list(a) for a in grid.bounds_mm],
            timing_ms={"total_ms": 0.0}, metrics=dict(result.metrics),
            outputs={
                "ply": {"path": "mesh.ply",
                        "sha256": hashlib.sha256(ply).hexdigest(),
                        "vertices": surface.vertex_count,
                        "faces": surface.face_count},
                "stl": {"path": "mesh.stl",
                        "sha256": hashlib.sha256(stl).hexdigest(),
                        "vertices": surface.vertex_count,
                        "faces": surface.face_count},
            })
        self.assertEqual(manifest["extra"]["reconstruction_class"],
                         "silhouette-constrained")


if __name__ == "__main__":
    unittest.main()
