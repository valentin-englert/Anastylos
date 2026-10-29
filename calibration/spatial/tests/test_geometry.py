"""Geometry unit tests: transforms, projection, crop equivalence, guidance cues.

Run from the calibration/spatial directory:

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anastylos_spatial.geometry import (
    T_CAD_B_ROTATION,
    apply_affine3,
    axis_angle_deg,
    bottom_row_ok,
    brown_conrady,
    camera_center_board,
    center_separation_mm,
    cues_from_delta,
    det3,
    guidance_delta,
    is_valid_rotation,
    k_mapped,
    mat3_mul,
    mat4_apply,
    mat4_identity,
    mat4_inverse_rigid,
    mat4_mul,
    max_abs_diff,
    optical_axis_board,
    ortho_error,
    project_board_point,
    rotation_block,
    rotation_error_deg,
    tcb_from_tbc,
    to_screen_axes,
    transpose3,
)

FROZEN_T = [1.0, 0.0, 0.0, 100.0,
            0.0, 1.0, 0.0, 80.0,
            0.0, 0.0, 1.0, -400.0,
            0.0, 0.0, 0.0, 1.0]
YAW90_T = [0.0, -1.0, 0.0, 100.0,
           1.0, 0.0, 0.0, 80.0,
           0.0, 0.0, 1.0, -400.0,
           0.0, 0.0, 0.0, 1.0]
K_SYNTHETIC = [1000.0, 0.0, 639.5, 0.0, 1000.0, 359.5, 0.0, 0.0, 1.0]
NO_DIST = [0.0, 0.0, 0.0, 0.0, 0.0]


class RigidTransformTest(unittest.TestCase):
    def test_inverse_round_trip(self):
        inv = mat4_inverse_rigid(YAW90_T)
        self.assertLess(max_abs_diff(mat4_mul(YAW90_T, inv), mat4_identity()), 1e-9)
        self.assertLess(max_abs_diff(mat4_inverse_rigid(inv), YAW90_T), 1e-9)

    def test_tcb_tbc_round_trip(self):
        t_c_b = tcb_from_tbc(YAW90_T)
        self.assertLess(max_abs_diff(mat4_inverse_rigid(t_c_b), YAW90_T), 1e-9)

    def test_camera_center_is_translation(self):
        self.assertEqual(camera_center_board(FROZEN_T), (100.0, 80.0, -400.0))

    def test_identity_rotation_valid(self):
        ok, code = is_valid_rotation(rotation_block(FROZEN_T))
        self.assertTrue(ok)
        self.assertIsNone(code)

    def test_reflection_rejected(self):
        ok, code = is_valid_rotation([1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, -1.0])
        self.assertFalse(ok)
        self.assertEqual(code, "REFLECTION")

    def test_non_orthonormal_rejected(self):
        ok, code = is_valid_rotation([1.0, 0.5, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0])
        self.assertFalse(ok)
        self.assertEqual(code, "NOT_ORTHONORMAL")

    def test_scaled_rotation_rejected(self):
        ok, code = is_valid_rotation([2.0, 0.0, 0.0, 0.0, 2.0, 0.0, 0.0, 0.0, 2.0])
        self.assertFalse(ok)
        self.assertIn(code, ("NOT_ORTHONORMAL", "BAD_DETERMINANT"))

    def test_bottom_row(self):
        self.assertTrue(bottom_row_ok(FROZEN_T))
        bad = list(FROZEN_T)
        bad[15] = 2.0
        self.assertFalse(bottom_row_ok(bad))

    def test_ortho_error_zero_for_rotation(self):
        self.assertLess(ortho_error(rotation_block(YAW90_T)), 1e-12)

    def test_rotation_error_degrees(self):
        identity = rotation_block(FROZEN_T)
        self.assertAlmostEqual(rotation_error_deg(identity, identity), 0.0)
        self.assertAlmostEqual(rotation_error_deg(identity, rotation_block(YAW90_T)), 90.0)


class ProjectionTest(unittest.TestCase):
    def test_board_point_under_camera_hits_principal_point(self):
        """Blueprint analytic test: T=translation(100,80,-400), point
        (100,80,0) projects to the principal point."""
        pixel = project_board_point((100.0, 80.0, 0.0), tcb_from_tbc(FROZEN_T),
                                    K_SYNTHETIC, NO_DIST)
        self.assertIsNotNone(pixel)
        self.assertAlmostEqual(pixel[0], 639.5)
        self.assertAlmostEqual(pixel[1], 359.5)

    def test_off_center_projection(self):
        pixel = project_board_point((140.0, 80.0, 0.0), tcb_from_tbc(FROZEN_T),
                                    K_SYNTHETIC, NO_DIST)
        self.assertAlmostEqual(pixel[0], 639.5 + 1000.0 * 40.0 / 400.0)
        self.assertAlmostEqual(pixel[1], 359.5)

    def test_behind_camera_is_none(self):
        away = [1.0, 0.0, 0.0, 100.0,
                0.0, -1.0, 0.0, 80.0,
                0.0, 0.0, -1.0, -400.0,
                0.0, 0.0, 0.0, 1.0]
        self.assertIsNone(project_board_point((100.0, 80.0, 0.0), tcb_from_tbc(away),
                                              K_SYNTHETIC, NO_DIST))

    def test_zero_distortion_is_identity(self):
        self.assertEqual(brown_conrady(0.3, -0.2, NO_DIST), (0.3, -0.2))

    def test_radial_distortion_value(self):
        xd, yd = brown_conrady(0.1, 0.0, [0.1, 0.0, 0.0, 0.0, 0.0])
        self.assertAlmostEqual(xd, 0.1 * 1.001)
        self.assertAlmostEqual(yd, 0.0)

    def test_tangential_distortion_moves_center_line(self):
        xd, yd = brown_conrady(0.0, 0.2, [0.0, 0.0, 0.01, 0.0, 0.0])
        self.assertAlmostEqual(xd, 0.0)
        self.assertNotAlmostEqual(yd, 0.2)


class CropEquivalenceTest(unittest.TestCase):
    def test_reference_and_mapped_k_agree(self):
        """Projecting with K_ref then the pixel-center affine A equals
        projecting directly with K_img = A @ K_ref (frozen mapping)."""
        aff = [0.5, 0.0, -50.25, 0.0, 0.5, -25.25, 0.0, 0.0, 1.0]
        k_img = k_mapped(K_SYNTHETIC, aff)
        dist = [0.05, -0.01, 0.001, -0.002, 0.0]
        for point in [(100.0, 80.0, 0.0), (200.0, 150.0, 0.0), (21.0, 21.0, 0.0)]:
            via_ref = project_board_point(point, tcb_from_tbc(FROZEN_T), K_SYNTHETIC, dist)
            via_img = project_board_point(point, tcb_from_tbc(FROZEN_T), k_img, dist)
            mapped = apply_affine3(aff, via_ref)
            self.assertAlmostEqual(mapped[0], via_img[0], places=9)
            self.assertAlmostEqual(mapped[1], via_img[1], places=9)

    def test_identity_affine_keeps_k(self):
        self.assertEqual(k_mapped(K_SYNTHETIC, [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]),
                         K_SYNTHETIC)

    def test_mirror_affine_has_negative_determinant(self):
        self.assertLess(det3([-1.0, 0.0, 1280.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]), 0.0)


class GuidanceCueTest(unittest.TestCase):
    def test_frozen_case_one(self):
        delta = guidance_delta(FROZEN_T, (120.0, 70.0, -450.0))
        self.assertEqual([round(v, 9) for v in delta], [20.0, -10.0, -50.0])
        self.assertEqual(cues_from_delta(delta), ["right 20", "up 10", "back 50"])

    def test_frozen_case_two(self):
        delta = guidance_delta(FROZEN_T, (100.0, 80.0, -350.0))
        self.assertEqual(cues_from_delta(delta), ["closer 50"])

    def test_display_rotations(self):
        delta = (20.0, -10.0, -50.0)
        self.assertEqual(cues_from_delta(delta, 0), ["right 20", "up 10", "back 50"])
        self.assertEqual(cues_from_delta(delta, 90), ["right 10", "down 20", "back 50"])
        self.assertEqual(cues_from_delta(delta, 180), ["left 20", "down 10", "back 50"])
        self.assertEqual(cues_from_delta(delta, 270), ["left 10", "up 20", "back 50"])

    def test_display_rotation_cycles(self):
        x, y = 20.0, -10.0
        for _ in range(4):
            x, y = to_screen_axes(x, y, 90)
        self.assertAlmostEqual(x, 20.0)
        self.assertAlmostEqual(y, -10.0)

    def test_invalid_display_rotation_raises(self):
        with self.assertRaises(ValueError):
            to_screen_axes(1.0, 2.0, 45)

    def test_yawed_camera_cues(self):
        delta = guidance_delta(YAW90_T, (120.0, 70.0, -450.0))
        self.assertEqual(cues_from_delta(delta), ["left 10", "up 20", "back 50"])

    def test_identity_has_no_cues(self):
        self.assertEqual(cues_from_delta(guidance_delta(FROZEN_T, (100.0, 80.0, -400.0))), [])

    def test_submillimeter_components_omitted(self):
        self.assertEqual(cues_from_delta((0.2, -0.4, 0.49)), [])


class CadAdapterTest(unittest.TestCase):
    def test_adapter_is_proper_rotation(self):
        rotation = list(T_CAD_B_ROTATION)
        self.assertAlmostEqual(det3(rotation), 1.0)
        self.assertLess(ortho_error(rotation), 1e-12)

    def test_adapter_round_trip(self):
        rotation = list(T_CAD_B_ROTATION)
        there = [rotation[0], rotation[1], rotation[2], 0.0,
                 rotation[3], rotation[4], rotation[5], 0.0,
                 rotation[6], rotation[7], rotation[8], 0.0,
                 0.0, 0.0, 0.0, 1.0]
        back = mat4_inverse_rigid(there)
        point = (100.0, 80.0, -400.0)
        again = mat4_apply(back, mat4_apply(there, point))
        for got, want in zip(again, point):
            self.assertAlmostEqual(got, want)

    def test_optical_axis_and_separation(self):
        self.assertEqual(optical_axis_board(FROZEN_T), (0.0, 0.0, 1.0))
        moved = list(FROZEN_T)
        moved[3] += 30.0
        moved[7] += 40.0
        self.assertAlmostEqual(center_separation_mm(FROZEN_T, moved), 50.0)
        self.assertAlmostEqual(axis_angle_deg(optical_axis_board(FROZEN_T),
                                              optical_axis_board(YAW90_T)), 0.0)


if __name__ == "__main__":
    unittest.main()
