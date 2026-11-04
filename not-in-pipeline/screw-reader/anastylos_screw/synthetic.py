"""Deterministic synthetic screw-head renderer for vision tests.

Draws idealized drive recesses (slot, Phillips, Pozidriv, Torx, hex)
with controllable rotation, scale, perspective, glare, shadow, wear,
and occlusion. Exercises the pipeline deterministically; results say
nothing about real-hardware accuracy.
"""

from __future__ import annotations

import cv2
import numpy as np

SIZE = 400
CENTER = (SIZE // 2, SIZE // 2)
HEAD_R = 140
METAL = 150
RECESS = 25
BACKGROUND = 200


def _disc(shape, center, radius, value):
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    return ((xx - center[0]) ** 2 + (yy - center[1]) ** 2) <= radius ** 2


def _rotated_rect(image, center, length, width, angle_deg, value):
    box = cv2.boxPoints((center, (length, width), angle_deg)).astype(int)
    cv2.fillPoly(image, [box], value)


def _star(image, center, outer, inner, points, angle_deg, value):
    angles = np.deg2rad(np.arange(points * 2) * (180.0 / points) + angle_deg)
    radii = np.where(np.arange(points * 2) % 2 == 0, outer, inner)
    pts = np.stack([center[0] + radii * np.cos(angles),
                    center[1] + radii * np.sin(angles)], axis=1).astype(int)
    cv2.fillPoly(image, [pts], value)


def _polygon(image, center, radius, sides, angle_deg, value):
    angles = np.deg2rad(np.arange(sides) * (360.0 / sides) + angle_deg)
    pts = np.stack([center[0] + radius * np.cos(angles),
                    center[1] + radius * np.sin(angles)], axis=1).astype(int)
    cv2.fillPoly(image, [pts], value)


def _draw_recess(image, drive, radius, angle_deg):
    center = CENTER
    if drive == "slotted":
        _rotated_rect(image, center, radius * 1.5, radius * 0.30, angle_deg, RECESS)
    elif drive in ("phillips", "pozidriv"):
        _rotated_rect(image, center, radius * 1.30, radius * 0.30, angle_deg, RECESS)
        _rotated_rect(image, center, radius * 1.30, radius * 0.30, angle_deg + 90, RECESS)
        cv2.circle(image, center, int(radius * 0.22), RECESS, -1)
        if drive == "pozidriv":
            for k in range(4):
                _rotated_rect(image, (center[0] + int(radius * 0.27 * np.cos(np.deg2rad(angle_deg + 45 + 90 * k))),
                                      center[1] + int(radius * 0.27 * np.sin(np.deg2rad(angle_deg + 45 + 90 * k)))),
                              radius * 0.18, radius * 0.10, angle_deg + 45 + 90 * k, RECESS)
    elif drive == "torx":
        _star(image, center, radius * 0.62, radius * 0.44, 6, angle_deg, RECESS)
    elif drive == "hex":
        _polygon(image, center, radius * 0.58, 6, angle_deg, RECESS)


def render(drive: str, rotation_deg: float = 0.0, scale: float = 1.0,
           tilt: float = 0.0, glare: bool = False, shadow: float = 0.0,
           wear: float = 0.0, occlude: float = 0.0, seed: int = 0) -> np.ndarray:
    """Render one screw head. drive: slotted/phillips/pozidriv/torx/hex/blank."""
    rng = np.random.RandomState(seed)
    image = np.full((SIZE, SIZE, 3), BACKGROUND, np.uint8)
    yy, xx = np.mgrid[0:SIZE, 0:SIZE]
    dist = np.sqrt((xx - CENTER[0]) ** 2 + (yy - CENTER[1]) ** 2)
    shade = np.clip(METAL + (1 - dist / HEAD_R) * 45 - dist / HEAD_R * 25, 0, 255)
    head = dist <= HEAD_R
    image[head] = shade[head][:, None].astype(np.uint8)
    single = image[:, :, 0].copy()
    if drive != "blank":
        _draw_recess(single, drive, HEAD_R * scale, rotation_deg)
    if wear:
        erode = rng.random(single.shape) < wear * 0.25
        recess = single < 100
        single[recess & erode] = np.clip(single[recess & erode].astype(int) + 90, 0, 255)
        edge = (single < 100).astype(np.uint8)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        if rng.random() < 0.5:
            rough = cv2.dilate(edge, kernel, iterations=int(1 + wear * 2))
        else:
            rough = cv2.erode(edge, kernel, iterations=1)
        single[(rough > 0) & head] = np.clip(
            single[(rough > 0) & head].astype(int) - int(20 * wear), 0, 255)
    if occlude:
        cv2.ellipse(single, (int(CENTER[0] + HEAD_R * 0.4), int(CENTER[1] - HEAD_R * 0.2)),
                    (int(HEAD_R * 0.55 * occlude + 20), int(HEAD_R * 0.5 * occlude + 15)),
                    25, 0, 360, 15, -1)
    if shadow:
        ramp = np.linspace(1 - shadow / 2, 1 + shadow / 2, SIZE, dtype=np.float32)
        single = np.clip(single.astype(np.float32) * ramp[None, :], 0, 255).astype(np.uint8)
    if tilt:
        shift = SIZE * tilt * 0.12
        src = np.float32([[0, 0], [SIZE, 0], [SIZE, SIZE], [0, SIZE]])
        dst = np.float32([[shift, 0], [SIZE - shift, shift * 0.4],
                          [SIZE - shift, SIZE], [shift, SIZE - shift * 0.4]])
        single = cv2.warpPerspective(single, cv2.getPerspectiveTransform(src, dst),
                                     (SIZE, SIZE), borderValue=BACKGROUND)
    image = cv2.cvtColor(single, cv2.COLOR_GRAY2BGR)
    if glare:
        overlay = image.copy()
        cv2.ellipse(overlay, (int(SIZE * 0.60), int(SIZE * 0.38)),
                    (int(SIZE * 0.09), int(SIZE * 0.13)), -20, 0, 360, (255, 255, 255), -1)
        overlay = cv2.GaussianBlur(overlay, (0, 0), 15)
        mask = np.zeros((SIZE, SIZE), np.uint8)
        cv2.ellipse(mask, (int(SIZE * 0.60), int(SIZE * 0.38)),
                    (int(SIZE * 0.09), int(SIZE * 0.13)), -20, 0, 360, 255, -1)
        mask = cv2.GaussianBlur(mask, (0, 0), 15).astype(np.float32) / 255 * 0.8
        image = (overlay.astype(np.float32) * mask[:, :, None]
                 + image.astype(np.float32) * (1 - mask[:, :, None])).astype(np.uint8)
    noise = rng.normal(0, 2.0, image.shape).astype(np.float32)
    return np.clip(image.astype(np.float32) + noise, 0, 255).astype(np.uint8)
