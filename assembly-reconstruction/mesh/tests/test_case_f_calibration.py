"""Case F: invalid calibration/transform inputs fail closed."""

import os
import sys
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from anastylos_mesh.geometry import MeshCalibrationError
from anastylos_mesh.synthetic import make_scene, render_fixtures


class TestCaseFCalibration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures = render_fixtures(make_scene("box"), n_views=2,
                                   width=80, height=60)
        cls.cam = fixtures.cameras[0]

    def test_nonpositive_focal_length_rejected(self):
        for bad_fx in (0.0, -100.0):
            k = list(self.cam.K)
            k[0] = bad_fx
            with self.assertRaises(MeshCalibrationError) as ctx:
                replace(self.cam, K=tuple(k)).validate()
            self.assertIn("NONPOSITIVE_FOCAL_LENGTH", str(ctx.exception))

    def test_reflected_rotation_rejected(self):
        t = list(self.cam.T_board_camera)
        t[0], t[4], t[8] = -t[0], -t[4], -t[8]  # flip one column: det +1 -> -1
        with self.assertRaises(MeshCalibrationError) as ctx:
            replace(self.cam, T_board_camera=tuple(t)).validate()
        self.assertIn("REFLECTION_DET_MINUS_ONE", str(ctx.exception))

    def test_non_rigid_bottom_row_rejected(self):
        t = list(self.cam.T_board_camera)
        t[12], t[13], t[14], t[15] = 0.0, 0.0, 0.0, 2.0
        with self.assertRaises(MeshCalibrationError) as ctx:
            replace(self.cam, T_board_camera=tuple(t)).validate()
        self.assertIn("BAD_TRANSFORM_BOTTOM_ROW", str(ctx.exception))

    def test_nan_in_transform_rejected(self):
        t = list(self.cam.T_board_camera)
        t[3] = float("nan")
        with self.assertRaises(MeshCalibrationError) as ctx:
            replace(self.cam, T_board_camera=tuple(t)).validate()
        self.assertIn("NONFINITE_TRANSFORM", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
