"""Case I: sparse/missing views succeed annotated partial, never complete."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from anastylos_mesh.carve import VoxelGrid, carve, max_baseline_deg
from anastylos_mesh.export import build_manifest
from anastylos_mesh.geometry import MeshCoverageError
from anastylos_mesh.surface import extract_surface
from anastylos_mesh.synthetic import make_scene, render_fixtures


def _grid_for_scene(scene, resolution, margin):
    """ROI grid mirroring the CLI reconstruct convention (padded gt bounds)."""
    extents = [b[1] - b[0] for b in scene.bounds]
    pad = max(extents) * margin
    bounds = tuple((b[0] - pad, b[1] + pad) for b in scene.bounds)
    voxel = max(b[1] - b[0] for b in bounds) / resolution
    return VoxelGrid(bounds_mm=bounds, voxel_size=voxel)


def _manifest(**over):
    args = {
        "params": {"case": "I"},
        "image_hashes": over.pop("image_hashes"),
        "mask_hashes": over.pop("mask_hashes"),
        "pose_epochs": over.pop("pose_epochs"),
        "mask_provenance": "synthetic",
        "voxel_size_mm": over.pop("voxel_size_mm"),
        "dims": over.pop("dims"),
        "bounds_mm": over.pop("bounds_mm"),
        "timing_ms": {"total_ms": 0.0},
        "metrics": over.pop("metrics"),
        "outputs": {"ply": {"path": "mesh.ply", "sha256": "0" * 64,
                            "vertices": 1, "faces": 1}},
    }
    args.update(over)
    return build_manifest(**args)


def _carve_args(cameras, masks, result, grid):
    return {
        "image_hashes": [c.image_sha256 for c in cameras],
        "mask_hashes": [m.sha256 for m in masks],
        "pose_epochs": [c.pose_epoch for c in cameras],
        "voxel_size_mm": grid.voxel_size,
        "dims": list(grid.dims),
        "bounds_mm": [list(a) for a in grid.bounds_mm],
        "metrics": dict(result.metrics),
    }


class TestCaseISparse(unittest.TestCase):
    def test_two_view_wide_baseline_partial(self):
        scene = make_scene("box")
        fixtures = render_fixtures(scene, n_views=2, width=160, height=120)
        grid = _grid_for_scene(scene, resolution=32, margin=0.5)
        baseline = max_baseline_deg(fixtures.cameras, grid.roi_center)
        self.assertGreater(baseline, 90.0)  # wide baseline, opposite views
        result = carve(fixtures.cameras, fixtures.masks, grid,
                       min_observed=2)
        self.assertGreater(result.occupied_count, 0)
        self.assertIn("unknown_voxels", result.metrics)
        self.assertGreater(result.metrics["unknown_voxels"], 0)
        surface = extract_surface(result)
        self.assertGreater(surface.vertex_count, 0)
        self.assertGreater(surface.face_count, 0)

        base = _carve_args(fixtures.cameras, fixtures.masks, result, grid)
        auto = _manifest(**base)
        self.assertEqual(auto["extra"]["coverage_class"], "partial")
        explicit = _manifest(**dict(base), extra={"coverage_class": "partial"})
        self.assertEqual(explicit["extra"]["coverage_class"], "partial")
        for manifest in (auto, explicit):
            self.assertNotEqual(manifest["extra"]["coverage_class"],
                                "complete")
        with self.assertRaises(MeshCoverageError) as ctx:
            _manifest(**dict(base), coverage_class="complete")
        self.assertIn("COVERAGE_OVERCLAIM", str(ctx.exception))

    def test_single_view_missing_views_extreme_partial(self):
        scene = make_scene("box")
        fixtures = render_fixtures(scene, n_views=2, width=80, height=60)
        grid = _grid_for_scene(scene, resolution=16, margin=0.05)
        result = carve(fixtures.cameras[:1], fixtures.masks[:1], grid,
                       min_observed=1, min_views=1, min_baseline_deg=0.0)
        self.assertGreater(result.occupied_count, 0)
        manifest = _manifest(
            **_carve_args(fixtures.cameras[:1], fixtures.masks[:1],
                          result, grid))
        self.assertEqual(manifest["extra"]["coverage_class"], "partial")


if __name__ == "__main__":
    unittest.main()
