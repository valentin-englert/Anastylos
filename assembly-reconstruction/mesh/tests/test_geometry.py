"""Geometry checks: pinhole projection, det+1/reflection gates, mat4 inverse."""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from anastylos_mesh.geometry import (
    MeshCalibrationError,
    mat4_inverse_rigid,
    mat4_mul,
    project_points,
    validate_det1,
)

IDENTITY16 = [
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
]


def _rot_z_30(tx=5.0, ty=-7.0, tz=11.0):
    c, s = math.cos(math.radians(30.0)), math.sin(math.radians(30.0))
    return [c, -s, 0.0, tx, s, c, 0.0, ty, 0.0, 0.0, 1.0, tz, 0.0, 0.0, 0.0, 1.0]


class TestGeometry(unittest.TestCase):
    def test_validate_det1_accepts_identity_and_rigid(self):
        self.assertEqual(validate_det1(list(IDENTITY16)), IDENTITY16)
        rigid = _rot_z_30()
        self.assertEqual(validate_det1(list(rigid)), rigid)

    def test_validate_det1_rejects_reflection(self):
        mirror = [
            -1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0,
        ]
        with self.assertRaises(MeshCalibrationError) as ctx:
            validate_det1(mirror)
        self.assertIn("REFLECTION_DET_MINUS_ONE", str(ctx.exception))

    def test_mat4_inverse_round_trip(self):
        m = _rot_z_30()
        inv = mat4_inverse_rigid(m)
        for product in (mat4_mul(m, inv), mat4_mul(inv, m)):
            for got, want in zip(product, IDENTITY16):
                self.assertAlmostEqual(got, want, places=12)

    def test_project_points_matches_analytic_pinhole(self):
        k_ref = [800.0, 0.0, 160.0, 0.0, 800.0, 120.0, 0.0, 0.0, 1.0]
        dist = [0.0, 0.0, 0.0, 0.0, 0.0]
        pts = np.array([[10.0, -6.0, 50.0], [0.0, 0.0, 100.0],
                        [-25.0, 12.5, 200.0]])
        uv, in_front = project_points(pts, list(IDENTITY16), k_ref, dist)
        self.assertTrue(bool(in_front.all()))
        for (x, y, z), (u, v) in zip(pts, uv):
            self.assertAlmostEqual(u, 800.0 * x / z + 160.0, places=9)
            self.assertAlmostEqual(v, 800.0 * y / z + 120.0, places=9)

    def test_project_points_behind_camera_unobserved(self):
        k_ref = [800.0, 0.0, 160.0, 0.0, 800.0, 120.0, 0.0, 0.0, 1.0]
        dist = [0.0, 0.0, 0.0, 0.0, 0.0]
        _, in_front = project_points(np.array([[0.0, 0.0, -5.0]]),
                                     list(IDENTITY16), k_ref, dist)
        self.assertFalse(bool(in_front[0]))


if __name__ == "__main__":
    unittest.main()
