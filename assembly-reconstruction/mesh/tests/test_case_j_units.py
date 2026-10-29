"""Case J: wrong-unit (mm/m confusion) gate + visual-pass/metric-fail.

Camera and VoxelGrid declare mm-only. A scene declaring meters keeps
identical numbers (the pipeline math is unit-blind), so its metric
claims are invalid: non-strict export records metric_status=invalid,
strict export raises. The last test reuses the J scene for a mesh that
is watertight and plausible yet fails metric validation.
"""

import os
import sys
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from anastylos_mesh.carve import VoxelGrid, carve
from anastylos_mesh.export import build_manifest
from anastylos_mesh.geometry import MeshCalibrationError, MeshCoverageError
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


class TestCaseJUnits(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene = make_scene("box", units="m")
        cls.fixtures = render_fixtures(cls.scene, n_views=8, width=320,
                                       height=240)
        cls.grid = _grid_for_scene(cls.scene, resolution=32, margin=0.05)
        cls.result = carve(cls.fixtures.cameras, cls.fixtures.masks,
                           cls.grid, min_observed=2)
        cls.surface = extract_surface(cls.result)
        cls.report = evaluate(cls.surface.mesh, cls.scene, n_samples=2000,
                              tau_mm=1.0, seed=0)

    def _manifest(self, **over):
        args = {
            "params": {"case": "J", "scene_units": self.scene.units},
            "image_hashes": [c.image_sha256 for c in self.fixtures.cameras],
            "mask_hashes": [m.sha256 for m in self.fixtures.masks],
            "pose_epochs": [c.pose_epoch for c in self.fixtures.cameras],
            "mask_provenance": "synthetic",
            "voxel_size_mm": self.grid.voxel_size,
            "dims": list(self.grid.dims),
            "bounds_mm": [list(a) for a in self.grid.bounds_mm],
            "timing_ms": {"total_ms": 0.0},
            "metrics": dict(self.result.metrics),
            "outputs": {"ply": {"path": "mesh.ply", "sha256": "0" * 64,
                                "vertices": 1, "faces": 1}},
            "declared_units": self.scene.units,
        }
        args.update(over)
        return build_manifest(**args)

    def test_camera_and_grid_declare_mm_only(self):
        self.fixtures.cameras[0].validate()  # default mm passes
        with self.assertRaises(MeshCalibrationError) as ctx:
            replace(self.fixtures.cameras[0], length_unit="m").validate()
        self.assertIn("BAD_LENGTH_UNIT", str(ctx.exception))
        with self.assertRaises(MeshCoverageError) as ctx:
            VoxelGrid(bounds_mm=self.grid.bounds_mm,
                      voxel_size=self.grid.voxel_size, length_unit="m")
        self.assertIn("BAD_GRID_LENGTH_UNIT", str(ctx.exception))
        with self.assertRaises(MeshCalibrationError) as ctx:
            make_scene("box", units="cm")
        self.assertIn("BAD_SCENE_UNITS", str(ctx.exception))

    def test_meters_scene_metric_status_invalid(self):
        manifest = self._manifest()
        self.assertEqual(manifest["extra"]["declared_units"], "m")
        self.assertEqual(manifest["extra"]["metric_status"], "invalid")
        mm_manifest = self._manifest(declared_units="mm")
        self.assertEqual(mm_manifest["extra"]["metric_status"], "valid")

    def test_strict_metric_export_rejects_meters(self):
        with self.assertRaises(MeshCalibrationError) as ctx:
            self._manifest(strict_metric_units=True)
        self.assertIn("WRONG_LENGTH_UNIT", str(ctx.exception))

    def test_millimetre_meter_confusion_magnitude(self):
        # Unit-blind comparison looks merely coarse (observed 0.2375):
        # the numbers match the mm reconstruction exactly.
        naive = [a / g for a, g in zip(self.report["bbox_dims_abs_err_mm"],
                                      self.report["bbox_dims_gt_mm"])]
        self.assertLess(max(naive), 0.35)
        # True error under the declared unit (60 "m" = 60000mm): ~1000x.
        true_rel = [abs(r - g * 1000.0) / (g * 1000.0) for r, g in
                    zip(self.report["bbox_dims_rec_mm"],
                        self.report["bbox_dims_gt_mm"])]
        self.assertGreater(min(true_rel), 0.99)

    def test_visual_pass_metric_fail(self):
        # Same J mesh: watertight and plausible, yet metrically invalid.
        self.assertTrue(self.surface.watertight)
        self.assertGreater(self.result.occupied_count, 0)
        self.assertGreater(self.surface.face_count, 0)
        self.assertGreater(float(self.surface.mesh.volume), 0.0)
        manifest = self._manifest()
        self.assertEqual(manifest["extra"]["metric_status"], "invalid")


if __name__ == "__main__":
    unittest.main()
