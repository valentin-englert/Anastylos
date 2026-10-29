"""Spatial evidence math: rigid transforms, Brown-Conrady projection, guidance cues.

Conventions (frozen, SPATIAL-TRACKING-DESIGN section 3): column vectors,
right-handed frames, millimetres. ``T_A_B`` maps coordinates from frame B
into frame A, serialized row-major 4x4 with last row [0,0,0,1] and a
rotation block of determinant +1. Board B is x right, y down, **z into
the paper**; a camera above the board has negative B.z. Camera C is the
OpenCV optical frame. Standard library only.
"""

from __future__ import annotations

import math

ORTHO_TOL = 1e-6
DET_TOL = 1e-6
BOTTOM_ROW_TOL = 1e-9
ROUND_TRIP_TOL = 1e-9
CUE_OMIT_MM = 0.5

# CAD Z-up adapter rotation: 180 degrees about x (frozen, tracking section 3).
T_CAD_B_ROTATION = (1.0, 0.0, 0.0, 0.0, -1.0, 0.0, 0.0, 0.0, -1.0)


def mat4_identity() -> list[float]:
    return [1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0]


def mat4_mul(a: list[float], b: list[float]) -> list[float]:
    """Row-major 4x4 product a @ b."""
    return [sum(a[4 * r + k] * b[4 * k + c] for k in range(4))
            for r in range(4) for c in range(4)]


def mat4_apply(m: list[float], xyz: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = xyz
    return (m[0] * x + m[1] * y + m[2] * z + m[3],
            m[4] * x + m[5] * y + m[6] * z + m[7],
            m[8] * x + m[9] * y + m[10] * z + m[11])


def mat4_inverse_rigid(m: list[float]) -> list[float]:
    """Inverse of a rigid row-major 4x4 (rotation block transposed)."""
    r = [m[0], m[4], m[8], m[1], m[5], m[9], m[2], m[6], m[10]]
    t = (m[3], m[7], m[11])
    nt = (-(r[0] * t[0] + r[1] * t[1] + r[2] * t[2]),
          -(r[3] * t[0] + r[4] * t[1] + r[5] * t[2]),
          -(r[6] * t[0] + r[7] * t[1] + r[8] * t[2]))
    return [r[0], r[1], r[2], nt[0],
            r[3], r[4], r[5], nt[1],
            r[6], r[7], r[8], nt[2],
            0.0, 0.0, 0.0, 1.0]


def rotation_block(m: list[float]) -> list[float]:
    return [m[0], m[1], m[2], m[4], m[5], m[6], m[8], m[9], m[10]]


def translation_part(m: list[float]) -> tuple[float, float, float]:
    return (m[3], m[7], m[11])


def det3(r: list[float]) -> float:
    return (r[0] * (r[4] * r[8] - r[5] * r[7])
            - r[1] * (r[3] * r[8] - r[5] * r[6])
            + r[2] * (r[3] * r[7] - r[4] * r[6]))


def transpose3(r: list[float]) -> list[float]:
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


def is_valid_rotation(r: list[float]) -> tuple[bool, str | None]:
    """Orthonormal + determinant +1. Returns (ok, failure_code)."""
    if ortho_error(r) > ORTHO_TOL:
        return False, "NOT_ORTHONORMAL"
    det = det3(r)
    if det < 0:
        return False, "REFLECTION"
    if abs(det - 1.0) > DET_TOL:
        return False, "BAD_DETERMINANT"
    return True, None


def bottom_row_ok(m: list[float]) -> bool:
    return all(abs(v - w) <= BOTTOM_ROW_TOL for v, w in zip(m[12:16], (0.0, 0.0, 0.0, 1.0)))


def max_abs_diff(a: list[float], b: list[float]) -> float:
    return max(abs(x - y) for x, y in zip(a, b))


def tcb_from_tbc(t_b_c: list[float]) -> list[float]:
    """PnP-style object-to-camera transform from the stored camera-to-board one."""
    return mat4_inverse_rigid(t_b_c)


def camera_center_board(t_b_c: list[float]) -> tuple[float, float, float]:
    """Camera center in board coordinates: translation part of T_B_C."""
    return translation_part(t_b_c)


def brown_conrady(x: float, y: float, dist: list[float]) -> tuple[float, float]:
    """Distorted normalized coordinates, OpenCV model [k1,k2,p1,p2,k3]."""
    k1, k2, p1, p2, k3 = dist
    r2 = x * x + y * y
    r4 = r2 * r2
    r6 = r4 * r2
    radial = 1.0 + k1 * r2 + k2 * r4 + k3 * r6
    return (x * radial + 2.0 * p1 * x * y + p2 * (r2 + 2.0 * x * x),
            y * radial + p1 * (r2 + 2.0 * y * y) + 2.0 * p2 * x * y)


def project_board_point(x_b: tuple[float, float, float], t_c_b: list[float],
                        k: list[float], dist: list[float]) -> tuple[float, float] | None:
    """Board point into pixels. None when at or behind the camera (depth <= 0)."""
    xc, yc, zc = mat4_apply(t_c_b, x_b)
    if zc <= 0:
        return None
    xd, yd = brown_conrady(xc / zc, yc / zc, dist)
    fx, _, cx, _, fy, cy, _, _, _ = k
    return (fx * xd + cx, fy * yd + cy)


def mat3_mul(a: list[float], b: list[float]) -> list[float]:
    return [sum(a[3 * r + k] * b[3 * k + c] for k in range(3))
            for r in range(3) for c in range(3)]


def apply_affine3(a: list[float], uv: tuple[float, float]) -> tuple[float, float]:
    u, v = uv
    w = a[6] * u + a[7] * v + a[8]
    return ((a[0] * u + a[1] * v + a[2]) / w, (a[3] * u + a[4] * v + a[5]) / w)


def k_mapped(k_ref: list[float], a: list[float]) -> list[float]:
    """K_image = A @ K_reference (frozen crop/scale mapping, tracking section 4)."""
    return mat3_mul(a, k_ref)


def guidance_delta(t_b_c: list[float], desired_center_b: tuple[float, float, float]
                   ) -> tuple[float, float, float]:
    """Desired-minus-current camera center, expressed in the optical frame.

    e_C = R_C_B (c* - c): +x move right, -x left; +y down, -y up;
    +z forward (closer), -z backward.
    """
    r_b_c = rotation_block(t_b_c)
    r_c_b = transpose3(r_b_c)
    cx, cy, cz = translation_part(t_b_c)
    dx, dy, dz = (desired_center_b[0] - cx, desired_center_b[1] - cy, desired_center_b[2] - cz)
    return (r_c_b[0] * dx + r_c_b[1] * dy + r_c_b[2] * dz,
            r_c_b[3] * dx + r_c_b[4] * dy + r_c_b[5] * dz,
            r_c_b[6] * dx + r_c_b[7] * dy + r_c_b[8] * dz)


def _fmt_mm(value: float) -> str:
    rounded = round(value)
    if abs(value - rounded) < 1e-9:
        return str(int(rounded))
    text = f"{value:.1f}".rstrip("0").rstrip(".")
    return text if text not in ("-0", "") else "0"


def to_screen_axes(ex: float, ey: float, display_rotation_deg: int) -> tuple[float, float]:
    """Camera-buffer x/y into upright screen x/y.

    ``display_rotation_deg`` is the clockwise rotation of the display
    relative to the camera buffer (one of 0/90/180/270). Screen axes
    stay x right, y down, so cue words keep their physical meaning.
    """
    if display_rotation_deg == 0:
        return (ex, ey)
    if display_rotation_deg == 90:
        return (-ey, ex)
    if display_rotation_deg == 180:
        return (-ex, -ey)
    if display_rotation_deg == 270:
        return (ey, -ex)
    raise ValueError(f"display_rotation_deg must be 0/90/180/270, got {display_rotation_deg}")


def cues_from_delta(delta_c: tuple[float, float, float], display_rotation_deg: int = 0) -> list[str]:
    """Cue words in x/y/z order; components below 0.5 mm are omitted."""
    ex, ey, ez = delta_c
    sx, sy = to_screen_axes(ex, ey, display_rotation_deg)
    cues = []
    if abs(sx) >= CUE_OMIT_MM:
        cues.append(f"{'right' if sx > 0 else 'left'} {_fmt_mm(abs(sx))}")
    if abs(sy) >= CUE_OMIT_MM:
        cues.append(f"{'down' if sy > 0 else 'up'} {_fmt_mm(abs(sy))}")
    if abs(ez) >= CUE_OMIT_MM:
        cues.append(f"{'closer' if ez > 0 else 'back'} {_fmt_mm(abs(ez))}")
    return cues


def rotation_error_deg(r_b_c: list[float], r_b_c_star: list[float]) -> float:
    """Angle of R_err = R_B_C^T R_B_C* in degrees (tracking section 7)."""
    r_err = mat3_mul(transpose3(r_b_c), r_b_c_star)
    trace = r_err[0] + r_err[4] + r_err[8]
    return math.degrees(math.acos(max(-1.0, min(1.0, (trace - 1.0) / 2.0))))


def optical_axis_board(t_b_c: list[float]) -> tuple[float, float, float]:
    """Camera forward (+z_C) expressed in board coordinates."""
    r = rotation_block(t_b_c)
    return (r[2], r[5], r[8])


def axis_angle_deg(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    dot = max(-1.0, min(1.0, a[0] * b[0] + a[1] * b[1] + a[2] * b[2]))
    na = math.sqrt(sum(v * v for v in a))
    nb = math.sqrt(sum(v * v for v in b))
    if na == 0 or nb == 0:
        return 0.0
    return math.degrees(math.acos(max(-1.0, min(1.0, dot / (na * nb)))))


def center_separation_mm(t_b_c_a: list[float], t_b_c_b: list[float]) -> float:
    ax, ay, az = translation_part(t_b_c_a)
    bx, by, bz = translation_part(t_b_c_b)
    return math.sqrt((ax - bx) ** 2 + (ay - by) ** 2 + (az - bz) ** 2)
