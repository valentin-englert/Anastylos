"""Deterministic synthetic component renderer for fingerprint tests.

Draws idealized parts (IC, resistor, screw head, capacitor) with
controllable rotation, scale, brightness, noise, background, glare,
blur, and occlusion. Fixtures exercise the pipeline only; they say
nothing about real-hardware retrieval.
"""

from __future__ import annotations

import cv2
import numpy as np

SIZE = 320


def _blank(background):
    return np.full((SIZE, SIZE, 3), background, np.uint8)


def _draw_ic(image, color):
    body = (90, 110, 140, 100)
    x, y, w, h = body
    cv2.rectangle(image, (x, y), (x + w, y + h), color, -1)
    cv2.rectangle(image, (x, y), (x + w, y + h), (20, 20, 20), 2)
    for i in range(4):
        py = y + 12 + i * 24
        cv2.rectangle(image, (x - 16, py), (x, py + 10), (180, 180, 180), -1)
        cv2.rectangle(image, (x + w, py), (x + w + 16, py + 10), (180, 180, 180), -1)
    cv2.circle(image, (x + 18, y + 18), 7, (200, 200, 200), -1)
    cv2.putText(image, "358", (x + 52, y + 58), cv2.FONT_HERSHEY_SIMPLEX,
                0.9, (210, 210, 210), 2)


def _draw_resistor(image, color):
    x, y, w, h = 70, 135, 180, 50
    cv2.rectangle(image, (x + 20, y), (x + w - 20, y + h), color, -1)
    cv2.ellipse(image, (x + 20, y + h // 2), (20, h // 2), 0, 90, 270, color, -1)
    cv2.ellipse(image, (x + w - 20, y + h // 2), (20, h // 2), 0, 270, 90, color, -1)
    for bx, band in ((105, (30, 30, 220)), (130, (30, 30, 30)), (155, (60, 170, 60))):
        cv2.rectangle(image, (bx, y + 3), (bx + 12, y + h - 3), band, -1)
    cv2.line(image, (10, y + h // 2), (x + 8, y + h // 2), (170, 170, 170), 5)
    cv2.line(image, (x + w - 8, y + h // 2), (SIZE - 10, y + h // 2), (170, 170, 170), 5)


def _draw_screw(image, color):
    center = (SIZE // 2, SIZE // 2)
    cv2.circle(image, center, 95, color, -1)
    cv2.circle(image, center, 95, (25, 25, 25), 2)
    cv2.rectangle(image, (center[0] - 62, center[1] - 12),
                  (center[0] + 62, center[1] + 12), (25, 25, 25), -1)


def _draw_capacitor(image, color):
    x, y, w, h = 125, 70, 70, 180
    cv2.rectangle(image, (x, y), (x + w, y + h), color, -1)
    cv2.rectangle(image, (x, y), (x + w, y + h), (20, 20, 20), 2)
    cv2.rectangle(image, (x + w - 16, y + 8), (x + w - 6, y + h - 8), (220, 220, 220), -1)
    cv2.line(image, (x + 14, y + h), (x + 14, y + h + 22), (170, 170, 170), 5)
    cv2.line(image, (x + w - 14, y + h), (x + w - 14, y + h + 22), (170, 170, 170), 5)


DRAW = {"ic": _draw_ic, "resistor": _draw_resistor, "screw": _draw_screw,
        "capacitor": _draw_capacitor}

COLORS = {"ic": (45, 45, 45), "resistor": (190, 170, 130), "screw": (150, 150, 150),
          "capacitor": (40, 90, 200)}


def render(kind, rotation_deg=0.0, scale=1.0, brightness=0.0, noise=0.0,
           background=200, glare=False, blur=0.0, occlude=0.0, seed=0):
    """Render one synthetic component. kind: ic/resistor/screw/capacitor/blank."""
    rng = np.random.RandomState(seed)
    image = _blank(background).astype(np.float32)
    if kind != "blank":
        canvas = _blank(background)
        DRAW[kind](canvas, COLORS[kind])
        if scale != 1.0:
            small = cv2.resize(canvas, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
            canvas = _blank(background)
            oy, ox = (SIZE - small.shape[0]) // 2, (SIZE - small.shape[1]) // 2
            oy, ox = max(oy, 0), max(ox, 0)
            part = small[:SIZE - oy, :SIZE - ox]
            canvas[oy:oy + part.shape[0], ox:ox + part.shape[1]] = part
        if rotation_deg:
            matrix = cv2.getRotationMatrix2D((SIZE / 2, SIZE / 2), rotation_deg, 1.0)
            canvas = cv2.warpAffine(canvas, matrix, (SIZE, SIZE),
                                    borderValue=(background,) * 3)
        image = canvas.astype(np.float32)
    if brightness:
        image = image + brightness * 100
    if occlude:
        bar_w = int(SIZE * 0.28 * occlude + 8)
        image[:, SIZE - bar_w:] = 15
    if glare:
        cv2.ellipse(image, (int(SIZE * 0.62), int(SIZE * 0.36)), (34, 46), -20, 0, 360,
                    (255, 255, 255), -1)
    image = np.clip(image, 0, 255).astype(np.uint8)
    if blur:
        size = int(blur * 8) * 2 + 1
        image = cv2.GaussianBlur(image, (size, size), 0)
    if noise:
        grain = rng.normal(0, noise * 12.0, image.shape).astype(np.float32)
        image = np.clip(image.astype(np.float32) + grain, 0, 255).astype(np.uint8)
    return image
