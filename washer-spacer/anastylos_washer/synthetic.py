"""Deterministic synthetic washer/spacer renderer for regression tests.

Draws idealized top-view rings (flat, split, thick-wall spacer,
hex standoff, hex nut lookalike, solid disc) and side-view bodies
(thin washer, split, spacer with end openings, standoff with flat
bands, solid rod, stacked washers), plus multi-part and touching
compositions. Upright geometry is drawn first; rotation,
perspective, lighting, occlusion, and noise follow deterministically
per seed. Fixtures exercise the pipeline only; they prove nothing
about real-hardware accuracy.

Nominal pixel dimensions live in EXPECTED for measurement tests.
"""

from __future__ import annotations

import cv2
import numpy as np

SIZE = 400
CENTER = (SIZE // 2, SIZE // 2)
BACKGROUND = 190
PART = 55

TOP_KINDS = ("flat_top", "split_top", "spacer_top", "standoff_top",
             "nut_top", "disc_top")
SIDE_KINDS = ("washer_side", "split_side", "spacer_side", "standoff_side",
              "rod_side", "stack_side", "stack_merged_side")
KINDS = TOP_KINDS + SIDE_KINDS

# Nominal pixel geometry: outer_d, inner_d (top); width, height (side).
EXPECTED = {
    "flat_top": {"outer_d": 220.0, "inner_d": 110.0, "gap_deg": 0.0},
    "split_top": {"outer_d": 220.0, "inner_d": 110.0, "gap_deg": 24.0},
    "spacer_top": {"outer_d": 180.0, "inner_d": 60.0, "gap_deg": 0.0},
    "standoff_top": {"outer_d": 200.0, "inner_d": 70.0, "gap_deg": 0.0},
    "nut_top": {"outer_d": 200.0, "inner_d": 80.0, "gap_deg": 0.0},
    "disc_top": {"outer_d": 180.0, "inner_d": 0.0, "gap_deg": 0.0},
    "washer_side": {"width": 240.0, "height": 28.0},
    "split_side": {"width": 240.0, "height": 28.0},
    "spacer_side": {"width": 110.0, "height": 200.0},
    "standoff_side": {"width": 120.0, "height": 200.0},
    "rod_side": {"width": 110.0, "height": 200.0},
    "stack_side": {"width": 240.0, "height": 94.0},
    "stack_merged_side": {"width": 240.0, "height": 84.0},
}


def expected_gap(rotation_deg: float) -> float:
    """Split-gap direction after canvas rotation (clockwise from +x)."""
    return (-rotation_deg) % 360.0


def _hex_points(center, radius, angle_deg=0.0):
    angles = np.deg2rad(np.arange(6) * 60.0 + angle_deg)
    return np.stack([center[0] + radius * np.cos(angles),
                     center[1] + radius * np.sin(angles)], axis=1).astype(int)


def _ring(image, center, outer_r, hole_r, gap_deg=0.0, gap_at=0.0,
          hex_outer=False):
    if hex_outer:
        cv2.fillPoly(image, [_hex_points(center, outer_r)], (PART,) * 3)
    else:
        cv2.circle(image, center, outer_r, (PART,) * 3, -1)
    if hole_r > 0:
        cv2.circle(image, center, hole_r, (BACKGROUND,) * 3, -1)
    if gap_deg > 0:
        # Wedge from the hole past the outer rim breaks the enclosure.
        wedge = np.array([
            center,
            (center[0] + int((outer_r + 4) * np.cos(np.deg2rad(gap_at))),
             center[1] + int((outer_r + 4) * np.sin(np.deg2rad(gap_at)))),
            (center[0] + int((outer_r + 4) * np.cos(
                np.deg2rad(gap_at + gap_deg))),
             center[1] + int((outer_r + 4) * np.sin(
                 np.deg2rad(gap_at + gap_deg)))),
        ])
        cv2.fillPoly(image, [wedge], (BACKGROUND,) * 3)


def _upright_top(image, kind: str) -> None:
    cx, cy = CENTER
    if kind == "flat_top":
        _ring(image, CENTER, 110, 55)
    elif kind == "split_top":
        _ring(image, CENTER, 110, 55, gap_deg=24.0, gap_at=-12.0)
    elif kind == "spacer_top":
        _ring(image, CENTER, 90, 30)
    elif kind == "standoff_top":
        _ring(image, CENTER, 100, 35, hex_outer=True)
    elif kind == "nut_top":
        _ring(image, CENTER, 100, 40, hex_outer=True)
    elif kind == "disc_top":
        cv2.circle(image, CENTER, 90, (PART,) * 3, -1)


def _side_rect(image, width, height, tone=PART):
    cx, cy = CENTER
    cv2.rectangle(image, (cx - width // 2, cy - height // 2),
                  (cx + width // 2, cy + height // 2), (tone,) * 3, -1)
    return cx - width // 2, cy - height // 2, width, height


def _upright_side(image, kind: str) -> None:
    cx, cy = CENTER
    if kind in ("washer_side", "split_side"):
        x, y, w, h = _side_rect(image, 240, 28)
        if kind == "split_side":
            cv2.rectangle(image, (cx + 60, y + 5), (cx + 68, y + h - 5),
                          (BACKGROUND,) * 3, -1)
    elif kind == "spacer_side":
        x, y, w, h = _side_rect(image, 110, 200)
        cv2.ellipse(image, (cx, y + 16), (40, 10), 0, 0, 360, (20,) * 3, -1)
        cv2.ellipse(image, (cx, y + h - 16), (40, 10), 0, 0, 360,
                    (20,) * 3, -1)
    elif kind == "standoff_side":
        x, y, w, h = _side_rect(image, 120, 200)
        third = w // 3
        cv2.rectangle(image, (x, y), (x + third, y + h), (42,) * 3, -1)
        cv2.rectangle(image, (x + 2 * third, y), (x + w, y + h), (68,) * 3, -1)
        cv2.ellipse(image, (cx, y + 16), (42, 10), 0, 0, 360, (20,) * 3, -1)
        cv2.ellipse(image, (cx, y + h - 16), (42, 10), 0, 0, 360,
                    (20,) * 3, -1)
    elif kind == "rod_side":
        _side_rect(image, 110, 200)
    elif kind == "stack_side":
        for i in range(3):
            top = cy - 47 + i * 33
            cv2.rectangle(image, (cx - 120, top), (cx + 120, top + 28),
                          (PART,) * 3, -1)
    elif kind == "stack_merged_side":
        x, y, w, h = _side_rect(image, 240, 84)
        for i in (1, 2):
            seam = y + i * 28
            cv2.rectangle(image, (x + 8, seam), (x + w - 8, seam + 2),
                          (BACKGROUND,) * 3, -1)


def _upright(kind: str) -> np.ndarray:
    image = np.full((SIZE, SIZE, 3), BACKGROUND, np.uint8)
    if kind in TOP_KINDS:
        _upright_top(image, kind)
    elif kind in SIDE_KINDS:
        _upright_side(image, kind)
    else:
        raise ValueError(f"unknown synthetic kind {kind!r}")
    return image


def render(kind: str, rotation_deg: float = 0.0, perspective: float = 0.0,
           brightness: int = 0, glare: bool = False, shadow: float = 0.0,
           occlude: float = 0.0, seed: int = 0) -> np.ndarray:
    """Render one centered part. See module docstring for kinds."""
    if kind not in EXPECTED:
        raise ValueError(f"unknown synthetic kind {kind!r}")
    rng = np.random.RandomState(seed)
    image = _upright(kind)
    if occlude:
        cv2.ellipse(image, (int(CENTER[0] + 95), int(CENTER[1] - 60)),
                    (int(30 + 70 * occlude), int(25 + 45 * occlude)),
                    20, 0, 360, (15, 15, 15), -1)
    if shadow:
        ramp = np.linspace(1 - shadow / 2, 1 + shadow / 2, SIZE,
                           dtype=np.float32)
        image = np.clip(image.astype(np.float32) * ramp[None, :, None],
                        0, 255).astype(np.uint8)
    if brightness:
        image = np.clip(image.astype(np.int32) + brightness, 0,
                        255).astype(np.uint8)
    if perspective:
        amount = min(max(perspective, 0.0), 1.0)
        if kind in TOP_KINDS:
            squash = 1.0 - amount * 0.35
            matrix = cv2.getRotationMatrix2D(CENTER, 0.0, 1.0)
            matrix[1, 1] *= squash
            matrix[1, 2] += CENTER[1] * (1.0 - squash)
            image = cv2.warpAffine(image, matrix, (SIZE, SIZE),
                                   flags=cv2.INTER_LINEAR,
                                   borderValue=(BACKGROUND,) * 3)
        else:
            shift = SIZE * amount * 0.15
            src = np.float32([[0, 0], [SIZE, 0], [SIZE, SIZE], [0, SIZE]])
            dst = np.float32([[shift, 0], [SIZE - shift, 0],
                              [SIZE, SIZE], [0, SIZE]])
            image = cv2.warpPerspective(
                image, cv2.getPerspectiveTransform(src, dst), (SIZE, SIZE),
                borderValue=(BACKGROUND,) * 3)
    if rotation_deg:
        matrix = cv2.getRotationMatrix2D(CENTER, rotation_deg, 1.0)
        image = cv2.warpAffine(image, matrix, (SIZE, SIZE),
                              flags=cv2.INTER_LINEAR,
                              borderValue=(BACKGROUND,) * 3)
    if glare:
        cv2.ellipse(image, (int(SIZE * 0.60), int(SIZE * 0.38)),
                    (18, 26), -20, 0, 360, (255, 255, 255), -1)
    noise = rng.normal(0, 2.0, image.shape).astype(np.float32)
    return np.clip(image.astype(np.float32) + noise, 0, 255).astype(np.uint8)


def _finish(image: np.ndarray, seed: int) -> np.ndarray:
    rng = np.random.RandomState(seed)
    noise = rng.normal(0, 2.0, image.shape).astype(np.float32)
    return np.clip(image.astype(np.float32) + noise, 0, 255).astype(np.uint8)


def render_grid(seed: int = 0) -> np.ndarray:
    """Three separated flat washers of different sizes (top view)."""
    image = np.full((SIZE, SIZE, 3), BACKGROUND, np.uint8)
    _ring(image, (105, 130), 62, 30)
    _ring(image, (290, 120), 80, 40)
    _ring(image, (200, 300), 55, 26)
    return _finish(image, seed)


def render_touching(seed: int = 0) -> np.ndarray:
    """Two tangent flat washers that share one contour (top view)."""
    image = np.full((SIZE, SIZE, 3), BACKGROUND, np.uint8)
    _ring(image, (140, 200), 62, 30)
    _ring(image, (264, 200), 62, 30)
    return _finish(image, seed)


def render_pair_side(seed: int = 0) -> np.ndarray:
    """Thin washer beside a tall spacer (side view)."""
    image = np.full((SIZE, SIZE, 3), BACKGROUND, np.uint8)
    cv2.rectangle(image, (40, 100), (200, 126), (PART,) * 3, -1)
    cv2.rectangle(image, (260, 110), (340, 300), (PART,) * 3, -1)
    cv2.ellipse(image, (300, 126), (30, 8), 0, 0, 360, (20,) * 3, -1)
    cv2.ellipse(image, (300, 284), (30, 8), 0, 0, 360, (20,) * 3, -1)
    return _finish(image, seed)
