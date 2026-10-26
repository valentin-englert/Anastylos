"""Deterministic synthetic connector renderer for connector tests.

Draws mating-face views (plug pins, socket cavities) and side views
(housing only) with controllable rows, count, keying, latch, color,
rotation, scale, brightness, noise, background, glare, blur, and
occlusion. Fixtures exercise the pipeline only; they say nothing
about real-hardware matching.
"""

from __future__ import annotations

import cv2
import numpy as np

SIZE = 360
CENTER = (SIZE // 2, SIZE // 2)


def _blank(background):
    return np.full((SIZE, SIZE, 3), background, np.uint8)


def _face(kind, rows, count, keying, latch, housing_color):
    image = _blank(200)
    w, h = 200, 150
    x, y = CENTER[0] - w // 2, CENTER[1] - h // 2
    cv2.rectangle(image, (x, y), (x + w, y + h), housing_color, -1)
    cv2.rectangle(image, (x, y), (x + w, y + h), (25, 25, 25), 3)
    face = (15, 15, w - 30, h - 30)
    fx, fy, fw, fh = face[0] + x, face[1] + y, face[2], face[3]
    face_color = (60, 60, 60) if kind == "plug" else (140, 140, 140)
    cv2.rectangle(image, (fx, fy), (fx + fw, fy + fh), face_color, -1)
    per_row = (count + rows - 1) // rows
    positions = []
    for index in range(count):
        row, col = divmod(index, per_row)
        if row >= rows:
            break
        px = fx + int(fw * (col + 1) / (per_row + 1))
        py = fy + int(fh * (row + 1) / (rows + 1))
        positions.append((px, py))
    if kind == "plug":
        for px, py in positions:
            cv2.circle(image, (px, py), 9, (225, 225, 225), -1)
            cv2.circle(image, (px, py), 9, (40, 40, 40), 1)
    else:
        for px, py in positions:
            cv2.circle(image, (px, py), 9, (25, 25, 25), -1)
    if keying:
        kx = x + (30 if keying == "left" else w - 30)
        cv2.rectangle(image, (kx - 12, y - 2), (kx + 12, y + 12), (200, 200, 200), -1)
    if latch:
        cv2.rectangle(image, (x + w // 2 - 20, y - 18), (x + w // 2 + 20, y), (90, 90, 90), -1)
        cv2.rectangle(image, (x + w // 2 - 20, y - 18), (x + w // 2 + 20, y), (25, 25, 25), 2)
    return image


def _side_view(housing_color):
    image = _blank(200)
    x, y, w, h = 80, 140, 200, 80
    cv2.rectangle(image, (x, y), (x + w, y + h), housing_color, -1)
    cv2.rectangle(image, (x, y), (x + w, y + h), (25, 25, 25), 3)
    cv2.line(image, (x, y + h // 2), (x + w, y + h // 2), (25, 25, 25), 2)
    return image


def render(kind="plug", rows=1, count=4, keying="left", latch=True,
           housing_color=(180, 60, 60), rotation_deg=0.0, scale=1.0,
           brightness=0.0, noise=0.0, background=200, glare=False,
           blur=0.0, occlude=0.0, missing=(), bent=(), seed=0):
    """Render one connector view. kind: plug/socket/side/blank."""
    rng = np.random.RandomState(seed)
    if kind == "side":
        canvas = _side_view(housing_color)
    elif kind == "blank":
        canvas = _blank(background)
    else:
        canvas = _face(kind, rows, count, keying, latch, housing_color)
        if missing or bent:
            canvas = _damage(canvas, rows, count, missing, bent)
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
        bar_w = int(SIZE * 0.30 * occlude + 8)
        image[:, SIZE - bar_w:] = 15
    if glare:
        cv2.ellipse(image, (int(SIZE * 0.60), int(SIZE * 0.38)), (36, 48), -20, 0, 360,
                    (255, 255, 255), -1)
    image = np.clip(image, 0, 255).astype(np.uint8)
    if blur:
        size = int(blur * 8) * 2 + 1
        image = cv2.GaussianBlur(image, (size, size), 0)
    if noise:
        grain = rng.normal(0, noise * 12.0, image.shape).astype(np.float32)
        image = np.clip(image.astype(np.float32) + grain, 0, 255).astype(np.uint8)
    return image


def _damage(canvas, rows, count, missing, bent):
    image = canvas.copy()
    w, h = 200, 150
    x, y = CENTER[0] - w // 2, CENTER[1] - h // 2
    fx, fy, fw, fh = x + 15, y + 15, w - 30, h - 30
    per_row = (count + rows - 1) // rows
    for index in range(count):
        row, col = divmod(index, per_row)
        if row >= rows:
            break
        px = fx + int(fw * (col + 1) / (per_row + 1))
        py = fy + int(fh * (row + 1) / (rows + 1))
        if index in missing:
            cv2.circle(image, (px, py), 11, (60, 60, 60), -1)
        elif index in bent:
            cv2.circle(image, (px, py), 11, (60, 60, 60), -1)
            cv2.ellipse(image, (px + 8, py + 4), (9, 5), 30, 0, 360, (225, 225, 225), -1)
    return image
