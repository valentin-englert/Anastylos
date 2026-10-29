"""Rigid-transform and Brown-Conrady projection math for mesh carving.

Convention sources mirrored EXACTLY (no axis flips, no silent inverses):

- ``calibration/spatial/anastylos_spatial/geometry.py``:
  mat4 row-major 4x4, ``T_A_B`` maps frame B into frame A, last row
  [0,0,0,1], rotation determinant +1 (lines 32-56);
  ``camera_center_board`` is the translation part of ``T_B_C``
  (lines 112-114); Brown-Conrady OpenCV model [k1,k2,p1,p2,k3]
  (lines 117-125); ``project_board_point`` with depth<=0 rejected,
  distortion applied to normalized coords, K row-major
  (fx=k[0], fy=k[4], cx=k[2], cy=k[5]) (lines 128-136);
  ``k_mapped = A @ K_reference`` and affine apply with homogeneous
  divide (lines 139-152); tolerances ORTHO 1e-6, DET 1e-6,
  BOTTOM_ROW 1e-9 (lines 15-17).
- ``calibration/spatial/anastylos_spatial/validator.py``:
  ``reference_to_image`` 3x3 finite, singular iff |det|<=1e-12,
  mirrored iff det<0 (lines 256-264);
  ``distortion_before_image_transform`` must be True (lines 265-266);
  ``T_board_camera`` units mm-only (lines 313-314); matrix 16 finite,
  bottom row, valid rotation, camera center z<0 (lines 317-333).
- ``calibration/spatial/anastylos_spatial/profiles.py``:
  camera model ``brown_conrady_5`` (line 22);
  ``reference_image_size_px`` [w,h] ints 1..32768 (lines 102-105);
  K 9 finite with k[0]>0 and k[4]>0 (lines 106-110);
  distortion 5 finite (lines 111-113).
- ``calibration/anastylos/layout.py`` + ``calibration-config.json``:
  units mm; board frame x right, y down, z into the plane, origin at
  the board corner (layout lines 36-42, 100).

Pure numpy + stdlib. Deterministic. Column vectors, right-handed
frames, millimetres.
"""

from __future__ import annotations

import math

import numpy as np

ORTHO_TOL = 1e-6
DET_TOL = 1e-6
BOTTOM_ROW_TOL = 1e-9
SINGULAR_DET_TOL = 1e-12
MAX_IMAGE_DIM = 32768


class MeshError(Exception):
    """Base class for mesh visual-hull failures."""


class MeshProvenanceError(MeshError):
    """Evidence linkage failure: hash mismatch, broken image link, bad bytes."""


class MeshCalibrationError(MeshError):
    """Fail-closed calibration/geometry rejection: bad rotation, K, sizes."""


class MeshCoverageError(MeshError):
    """Coverage/capacity failure: grid caps, view count, observation gates."""


class DegenerateBaselineError(MeshCoverageError):
    """Camera baseline about the ROI is below the required separation."""


def _finite_list(values: object, length: int) -> bool:
    if not isinstance(values, (list, tuple)) or len(values) != length:
        return False
    return all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        for v in values
    )


def mat4_mul(a: list[float], b: list[float]) -> list[float]:
    """Row-major 4x4 product a @ b (mirror of spatial geometry)."""
    return [
        sum(a[4 * r + k] * b[4 * k + c] for k in range(4))
        for r in range(4)
        for c in range(4)
    ]


def mat4_apply(m: list[float], xyz: tuple[float, float, float]) -> tuple[float, float, float]:
    """Apply a row-major 4x4 to an (x, y, z) column vector."""
    x, y, z = xyz
    return (
        m[0] * x + m[1] * y + m[2] * z + m[3],
        m[4] * x + m[5] * y + m[6] * z + m[7],
        m[8] * x + m[9] * y + m[10] * z + m[11],
    )


def mat4_inverse_rigid(m: list[float]) -> list[float]:
    """Inverse of a rigid row-major 4x4 (rotation block transposed)."""
    r = [m[0], m[4], m[8], m[1], m[5], m[9], m[2], m[6], m[10]]
    t = (m[3], m[7], m[11])
    nt = (
        -(r[0] * t[0] + r[1] * t[1] + r[2] * t[2]),
        -(r[3] * t[0] + r[4] * t[1] + r[5] * t[2]),
        -(r[6] * t[0] + r[7] * t[1] + r[8] * t[2]),
    )
    return [
        r[0], r[1], r[2], nt[0],
        r[3], r[4], r[5], nt[1],
        r[6], r[7], r[8], nt[2],
        0.0, 0.0, 0.0, 1.0,
    ]


def rotation_block(m: list[float]) -> list[float]:
    """Row-major 3x3 rotation block of a row-major 4x4."""
    return [m[0], m[1], m[2], m[4], m[5], m[6], m[8], m[9], m[10]]


def translation_part(m: list[float]) -> tuple[float, float, float]:
    """Translation part of a row-major 4x4."""
    return (m[3], m[7], m[11])


def det3(r: list[float]) -> float:
    """Determinant of a row-major 3x3."""
    return (
        r[0] * (r[4] * r[8] - r[5] * r[7])
        - r[1] * (r[3] * r[8] - r[5] * r[6])
        + r[2] * (r[3] * r[7] - r[4] * r[6])
    )


def transpose3(r: list[float]) -> list[float]:
    """Transpose of a row-major 3x3."""
    return [r[0], r[3], r[6], r[1], r[4], r[7], r[2], r[5], r[8]]


def ortho_error(r: list[float]) -> float:
    """Max |R^T R - I| entry: 0 for an exact rotation."""
    worst = 0.0
    for i in range(3):
        for j in range(3):
            dot = sum(r[3 * k + i] * r[3 * k + j] for k in range(3))
            want = 1.0 if i == j else 0.0
            worst = max(worst, abs(dot - want))
    return worst


def mat3_mul(a: list[float], b: list[float]) -> list[float]:
    """Row-major 3x3 product a @ b."""
    return [
        sum(a[3 * r + k] * b[3 * k + c] for k in range(3))
        for r in range(3)
        for c in range(3)
    ]


def validate_det1(m: list[float]) -> list[float]:
    """Fail-closed rigid-transform check; returns the validated matrix.

    Requires 16 finite values, bottom row [0,0,0,1] within 1e-9,
    orthonormal rotation within 1e-6, and rotation determinant +1
    within 1e-6 (reflections rejected even when orthonormal).
    Raises MeshCalibrationError on any violation.
    """
    if not _finite_list(m, 16):
        raise MeshCalibrationError("NONFINITE_TRANSFORM")
    values = [float(v) for v in m]
    if not all(
        abs(v - w) <= BOTTOM_ROW_TOL for v, w in zip(values[12:16], (0.0, 0.0, 0.0, 1.0))
    ):
        raise MeshCalibrationError("BAD_TRANSFORM_BOTTOM_ROW")
    r = rotation_block(values)
    if ortho_error(r) > ORTHO_TOL:
        raise MeshCalibrationError("NOT_ORTHONORMAL")
    det = det3(r)
    if det < 0:
        raise MeshCalibrationError("REFLECTION_DET_MINUS_ONE")
    if abs(det - 1.0) > DET_TOL:
        raise MeshCalibrationError("BAD_DETERMINANT")
    return values


def camera_center(t_board_camera: list[float]) -> tuple[float, float, float]:
    """Camera center in board coords: translation part of T_B_C."""
    return translation_part(t_board_camera)


def brown_conrady(x: float, y: float, dist: list[float]) -> tuple[float, float]:
    """Distorted normalized coordinates, OpenCV model [k1,k2,p1,p2,k3]."""
    k1, k2, p1, p2, k3 = dist
    r2 = x * x + y * y
    r4 = r2 * r2
    r6 = r4 * r2
    radial = 1.0 + k1 * r2 + k2 * r4 + k3 * r6
    return (
        x * radial + 2.0 * p1 * x * y + p2 * (r2 + 2.0 * x * x),
        y * radial + p1 * (r2 + 2.0 * y * y) + 2.0 * p2 * x * y,
    )


def apply_reference_to_image(a: list[float], uv: tuple[float, float]) -> tuple[float, float]:
    """Map reference-frame pixels to image pixels via 3x3 (homogeneous divide)."""
    u, v = uv
    w = a[6] * u + a[7] * v + a[8]
    return ((a[0] * u + a[1] * v + a[2]) / w, (a[3] * u + a[4] * v + a[5]) / w)


def validate_reference_to_image(a: list[float]) -> list[float]:
    """Fail-closed 3x3 check: 9 finite, |det|>1e-12, det>0 (no mirrors)."""
    if not _finite_list(a, 9):
        raise MeshCalibrationError("BAD_REFERENCE_TO_IMAGE")
    values = [float(v) for v in a]
    det = det3(values)
    if abs(det) <= SINGULAR_DET_TOL:
        raise MeshCalibrationError("SINGULAR_IMAGE_TRANSFORM")
    if det < 0:
        raise MeshCalibrationError("MIRRORED_IMAGE_TRANSFORM")
    return values


def project_points(
    board_xyz: np.ndarray,
    t_board_camera: list[float],
    k_ref: list[float],
    dist: list[float],
    reference_to_image: list[float] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Project board points (N,3 float, mm) to image pixels.

    Pipeline mirrors spatial ``project_board_point``: transform by
    T_C_B (the rigid inverse of T_B_C, never a silent flip), reject
    depth<=0, distort normalized coords (Brown-Conrady 5), apply the
    reference K, then the 3x3 reference_to_image map (distortion runs
    BEFORE the image transform, per validator). Returns (uv (N,2),
    in_front (N,) bool). Bounds checks against image size are the
    caller's job: outside-image is unobserved, not background.
    """
    pts = np.asarray(board_xyz, dtype=np.float64).reshape(-1, 3)
    t_c_b = np.array(mat4_inverse_rigid(list(t_board_camera)), dtype=np.float64).reshape(4, 4)
    cam = pts @ t_c_b[:3, :3].T + t_c_b[:3, 3]
    zc = cam[:, 2]
    in_front = zc > 0.0
    xn = np.zeros_like(cam[:, 0])
    yn = np.zeros_like(cam[:, 1])
    xn[in_front] = cam[in_front, 0] / zc[in_front]
    yn[in_front] = cam[in_front, 1] / zc[in_front]
    k1, k2, p1, p2, k3 = (float(v) for v in dist)
    r2 = xn * xn + yn * yn
    radial = 1.0 + k1 * r2 + k2 * r2 * r2 + k3 * r2 * r2 * r2
    xd = xn * radial + 2.0 * p1 * xn * yn + p2 * (r2 + 2.0 * xn * xn)
    yd = yn * radial + p1 * (r2 + 2.0 * yn * yn) + 2.0 * p2 * xn * yn
    fx, _, cx, _, fy, cy, _, _, _ = (float(v) for v in k_ref)
    u = fx * xd + cx
    v = fy * yd + cy
    if reference_to_image is not None:
        a = np.array(list(reference_to_image), dtype=np.float64).reshape(3, 3)
        w = a[2, 0] * u + a[2, 1] * v + a[2, 2]
        u = (a[0, 0] * u + a[0, 1] * v + a[0, 2]) / w
        v = (a[1, 0] * (fx * xd + cx) + a[1, 1] * (fy * yd + cy) + a[1, 2]) / w
    uv = np.stack([u, v], axis=1)
    return uv, in_front
