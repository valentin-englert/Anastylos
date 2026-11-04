"""Deterministic synthetic screw renderer for measurement tests.

Draws idealized top-view heads (round/hex) and side-view silhouettes
(shaft plus head) with controllable scale, rotation, perspective,
shadow, glare, touching objects, and occlusion. Every render returns
the exact pixel ground truth it was drawn from; degraded renders mark
`metric_valid: False` where the degradation, not the pipeline, moved
the truth. Fixtures exercise the pipeline only and say nothing about
real-hardware accuracy.
"""

from __future__ import annotations

import cv2
import numpy as np

SIZE = 400
CENTER = (SIZE // 2, SIZE // 2)
METAL = 150
BACKGROUND = 200
PIXELS_PER_MM = 10.0


def _finish(image: np.ndarray, rng: np.random.RandomState) -> np.ndarray:
    noise = rng.normal(0, 2.0, image.shape).astype(np.float32)
    return np.clip(image.astype(np.float32) + noise, 0, 255).astype(np.uint8)


def _tilt_matrix(tilt: float) -> np.ndarray:
    shift = SIZE * tilt * 0.12
    src = np.float32([[0, 0], [SIZE, 0], [SIZE, SIZE], [0, SIZE]])
    dst = np.float32([[shift, 0], [SIZE - shift, shift * 0.4],
                      [SIZE - shift, SIZE], [shift, SIZE - shift * 0.4]])
    return cv2.getPerspectiveTransform(src, dst)


def _apply_tilt(single: np.ndarray, tilt: float) -> np.ndarray:
    return cv2.warpPerspective(single, _tilt_matrix(tilt),
                               (SIZE, SIZE), borderValue=BACKGROUND)


def _warp_points(points: np.ndarray, rotation_deg: float, tilt: float) -> np.ndarray:
    warped = points.astype(float)
    if rotation_deg:
        matrix = cv2.getRotationMatrix2D(CENTER, rotation_deg, 1.0)
        warped = (matrix[:, :2] @ warped.T + matrix[:, 2:3]).T
    if tilt:
        homography = _tilt_matrix(tilt)
        lifted = np.column_stack([warped, np.ones(len(warped))])
        projected = (homography @ lifted.T).T
        warped = projected[:, :2] / projected[:, 2:3]
    return warped


def _apply_shadow(single: np.ndarray, shadow: float) -> np.ndarray:
    ramp = np.linspace(1 - shadow / 2, 1 + shadow / 2, SIZE, dtype=np.float32)
    return np.clip(single.astype(np.float32) * ramp[None, :], 0, 255).astype(np.uint8)


def _apply_glare(image: np.ndarray, at=(0.60, 0.38)) -> np.ndarray:
    overlay = image.copy()
    center = (int(SIZE * at[0]), int(SIZE * at[1]))
    axes = (int(SIZE * 0.09), int(SIZE * 0.13))
    cv2.ellipse(overlay, center, axes, -20, 0, 360, (255, 255, 255), -1)
    overlay = cv2.GaussianBlur(overlay, (0, 0), 15)
    mask = np.zeros((SIZE, SIZE), np.uint8)
    cv2.ellipse(mask, center, axes, -20, 0, 360, 255, -1)
    blend = cv2.GaussianBlur(mask, (0, 0), 15).astype(np.float32) / 255 * 0.8
    return (overlay.astype(np.float32) * blend[:, :, None]
            + image.astype(np.float32) * (1 - blend[:, :, None])).astype(np.uint8)


def _hex_points(center, radius: float, angle_deg: float) -> np.ndarray:
    angles = np.deg2rad(np.arange(6) * 60.0 + angle_deg)
    return np.stack([center[0] + radius * np.cos(angles),
                     center[1] + radius * np.sin(angles)], axis=1).astype(int)


def render_top(head: str = "round", diameter_px: float = 200.0,
               rotation_deg: float = 0.0, tilt: float = 0.0,
               glare: bool = False, shadow: float = 0.0,
               occlude: float = 0.0, touching: bool = False,
               seed: int = 0) -> tuple[np.ndarray, dict]:
    """Render a top-view head; truth holds exact pixel geometry."""
    rng = np.random.RandomState(seed)
    radius = diameter_px / 2.0
    yy, xx = np.mgrid[0:SIZE, 0:SIZE].astype(float)
    dist = np.sqrt((xx - CENTER[0]) ** 2 + (yy - CENTER[1]) ** 2)
    single = np.full((SIZE, SIZE), BACKGROUND, np.uint8)
    if head == "hex":
        mask = np.zeros((SIZE, SIZE), np.uint8)
        cv2.fillPoly(mask, [_hex_points(CENTER, radius, rotation_deg)], 255)
        shade = np.clip(METAL + 30 - dist / radius * 20, 0, 255).astype(np.uint8)
        single[mask > 0] = shade[mask > 0]
        cv2.polylines(single, [_hex_points(CENTER, radius, rotation_deg)], True, 40, 2)
    else:
        shade = np.clip(METAL + (1 - dist / radius) * 45 - dist / radius * 25, 0, 255)
        disc = dist <= radius
        single[disc] = shade[disc].astype(np.uint8)
        cv2.circle(single, CENTER, int(round(radius)), 40, 2)
    if touching:
        other = (int(CENTER[0] + radius * 1.55), CENTER[1])
        cv2.circle(single, other, int(round(radius * 0.8)), METAL + 20, -1)
        cv2.circle(single, other, int(round(radius * 0.8)), 40, 2)
    if occlude:
        w = int(radius * 1.4 * occlude + 10)
        cv2.rectangle(single, (int(CENTER[0] + radius * 0.15), int(CENTER[1] - radius - 6)),
                      (int(CENTER[0] + radius * 0.15) + w, int(CENTER[1] + radius + 6)), 15, -1)
    if shadow:
        single = _apply_shadow(single, shadow)
    if tilt:
        single = _apply_tilt(single, tilt)
    image = cv2.cvtColor(single, cv2.COLOR_GRAY2BGR)
    if glare:
        image = _apply_glare(image)
    rim = _warp_points(np.array([[CENTER[0] - radius, CENTER[1]],
                                 [CENTER[0] + radius, CENTER[1]],
                                 [CENTER[0], CENTER[1] - radius],
                                 [CENTER[0], CENTER[1] + radius]]), 0.0, tilt)
    warped_axes = sorted([float(np.linalg.norm(rim[0] - rim[1])),
                          float(np.linalg.norm(rim[2] - rim[3]))])
    truth = {"diameter_px": round(diameter_px, 2),
             "diameter_mm": round(diameter_px / PIXELS_PER_MM, 3),
             "warped_axes_px": [round(v, 2) for v in warped_axes],
             "across_flats_px": round(diameter_px * 0.8660254, 2),
             "center_xy": [float(CENTER[0]), float(CENTER[1])],
             "head_shape": head, "metric_valid": tilt < 0.6 and occlude < 0.9}
    return _finish(image, rng), truth


def render_side(length_px: float = 260.0, shaft_d_px: float = 60.0,
                head_d_px: float = 110.0, head_len_px: float = 55.0,
                rotation_deg: float = 0.0, tilt: float = 0.0, taper: float = 0.0,
                glare: bool = False, shadow: float = 0.0,
                occlude: float = 0.0, touching: bool = False,
                seed: int = 0) -> tuple[np.ndarray, dict]:
    """Render a side-view screw; truth holds exact pixel geometry.

    `taper` narrows the shaft toward the tip (one-sided perspective):
    widths converge, which must flag the diameter, never pass silently.
    """
    rng = np.random.RandomState(seed)
    canvas = np.full((SIZE, SIZE), BACKGROUND, np.uint8)
    x0 = int(CENTER[0] - length_px / 2)
    shaft_y0 = int(CENTER[1] - shaft_d_px / 2)
    shaft_y1 = int(CENTER[1] + shaft_d_px / 2)
    head_y0 = int(CENTER[1] - head_d_px / 2)
    head_y1 = int(CENTER[1] + head_d_px / 2)
    head_x1 = int(x0 + head_len_px)
    x1 = int(x0 + length_px)
    tip_y1 = int(CENTER[1] + shaft_d_px / 2 * (1 - taper))
    shaft_poly = np.array([[x0, shaft_y0], [x1, shaft_y0],
                           [x1, tip_y1], [x0, shaft_y1]])
    cv2.fillPoly(canvas, [shaft_poly], METAL)
    cv2.rectangle(canvas, (x0, head_y0), (head_x1, head_y1), METAL + 25, -1)
    cv2.polylines(canvas, [shaft_poly], True, 40, 2)
    cv2.rectangle(canvas, (x0, head_y0), (head_x1, head_y1), 40, 2)
    for yy in range(shaft_y0, shaft_y1 + 1):
        band = int(20 * (1 - abs(yy - CENTER[1]) / max(shaft_d_px / 2, 1)))
        row = canvas[yy, x0:x1].astype(int)
        screw = row < BACKGROUND - 10
        row[screw] = np.clip(row[screw] + band, 0, 255)
        canvas[yy, x0:x1] = row
    if touching:
        cv2.rectangle(canvas, (int(CENTER[0] + 20), shaft_y1 - 4),
                      (int(CENTER[0] + 70), shaft_y1 + 40), METAL + 10, -1)
    if occlude:
        w = int(length_px * 0.35 * occlude + 8)
        cv2.rectangle(canvas, (int(CENTER[0] + 30), 0),
                      (int(CENTER[0] + 30) + w, SIZE), 15, -1)
    if rotation_deg:
        matrix = cv2.getRotationMatrix2D(CENTER, rotation_deg, 1.0)
        canvas = cv2.warpAffine(canvas, matrix, (SIZE, SIZE), borderValue=BACKGROUND)
    if shadow:
        canvas = _apply_shadow(canvas, shadow)
    if tilt:
        canvas = _apply_tilt(canvas, tilt)
    image = cv2.cvtColor(canvas, cv2.COLOR_GRAY2BGR)
    if glare:
        image = _apply_glare(image, at=(0.58, 0.40))
    mid = (x0 + x1) / 2
    warped = _warp_points(np.array([[x0, CENTER[1]], [x1, CENTER[1]],
                                    [mid, shaft_y0], [mid, shaft_y1],
                                    [head_x1, CENTER[1]]]), rotation_deg, tilt)
    truth = {"overall_length_px": round(length_px, 2),
             "overall_length_mm": round(length_px / PIXELS_PER_MM, 3),
             "shaft_length_px": round(length_px - head_len_px, 2),
             "shaft_diameter_px": round(shaft_d_px, 2),
             "shaft_diameter_mm": round(shaft_d_px / PIXELS_PER_MM, 3),
             "head_length_px": round(head_len_px, 2),
             "warped_overall_length_px": round(float(np.linalg.norm(warped[0] - warped[1])), 2),
             "warped_shaft_diameter_px": round(float(np.linalg.norm(warped[2] - warped[3])), 2),
             "warped_shaft_length_px": round(float(np.linalg.norm(warped[4] - warped[1])), 2),
             "metric_valid": tilt < 0.6 and taper < 0.05 and occlude < 0.5}
    return _finish(image, rng), truth


def blank(size: int = SIZE) -> np.ndarray:
    """Flat field with faint noise; must measure nothing."""
    rng = np.random.RandomState(0)
    return _finish(np.full((size, size, 3), BACKGROUND, np.uint8), rng)
