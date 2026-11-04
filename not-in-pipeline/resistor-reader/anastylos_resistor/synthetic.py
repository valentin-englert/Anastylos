"""Deterministic synthetic resistor renderer for vision tests.

Renders axial resistors with known band colors onto a flat background with
optional rotation, lighting variation, noise, and glare. Used only to
exercise the pipeline deterministically; results on these images say nothing
about real-camera accuracy.
"""

from __future__ import annotations

import cv2
import numpy as np

BGR = {
    "black": (10, 10, 10),
    "brown": (25, 65, 130),
    "red": (15, 15, 210),
    "orange": (10, 140, 255),
    "yellow": (10, 225, 235),
    "green": (25, 140, 25),
    "blue": (210, 25, 25),
    "violet": (170, 60, 150),
    "gray": (150, 150, 150),
    "white": (245, 245, 245),
    "gold": (35, 175, 225),
    "silver": (195, 195, 195),
}

BODIES = {"tan": (155, 205, 235), "blue": (190, 70, 30)}
BACKGROUND = (128, 128, 128)
LEAD = (185, 185, 185)


def render(sequence: list[str],
           width: int = 900,
           height: int = 320,
           body: str = "tan",
           rotation_deg: float = 0.0,
           brightness: float = 1.0,
           gradient: float = 0.0,
           noise_sigma: float = 0.0,
           glare: bool = False,
           seed: int = 0) -> np.ndarray:
    rng = np.random.RandomState(seed)
    image = np.full((height, width, 3), BACKGROUND, np.uint8)
    body_y0, body_y1 = int(height * 0.32), int(height * 0.68)
    body_x0, body_x1 = int(width * 0.16), int(width * 0.84)
    cv2.rectangle(image, (0, body_y0 + 8), (body_x0, body_y1 - 8), LEAD, -1)
    cv2.rectangle(image, (body_x1, body_y0 + 8), (width, body_y1 - 8), LEAD, -1)
    cv2.rectangle(image, (body_x0, body_y0), (body_x1, body_y1), BODIES[body], -1)
    span_x0, span_x1 = width * 0.26, width * 0.74
    band_w = int(width * 0.026)
    slots = np.linspace(span_x0, span_x1, len(sequence) + 1)
    pitch = slots[1] - slots[0]
    for color, left in zip(sequence, slots[:-1]):
        center = int(left + pitch / 2)
        cv2.rectangle(image, (center - band_w // 2, body_y0),
                      (center + band_w // 2, body_y1), BGR[color], -1)
    if rotation_deg:
        matrix = cv2.getRotationMatrix2D((width / 2, height / 2), rotation_deg, 1.0)
        image = cv2.warpAffine(image, matrix, (width, height),
                               borderMode=cv2.BORDER_CONSTANT, borderValue=BACKGROUND)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV).astype(np.float32)
    if gradient:
        ramp = np.linspace(1 - gradient / 2, 1 + gradient / 2, width, dtype=np.float32)
        hsv[:, :, 2] *= ramp[None, :]
    hsv[:, :, 2] = np.clip(hsv[:, :, 2] * brightness, 0, 255)
    image = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
    if noise_sigma:
        noise = rng.normal(0, noise_sigma, image.shape).astype(np.float32)
        image = np.clip(image.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    if glare:
        overlay = image.copy()
        cv2.ellipse(overlay, (int(width * 0.55), int(height * 0.40)),
                    (int(width * 0.10), int(height * 0.16)), -20, 0, 360, (255, 255, 255), -1)
        overlay = cv2.GaussianBlur(overlay, (0, 0), 21)
        mask = np.zeros((height, width), np.uint8)
        cv2.ellipse(mask, (int(width * 0.55), int(height * 0.40)),
                    (int(width * 0.10), int(height * 0.16)), -20, 0, 360, 255, -1)
        mask = cv2.GaussianBlur(mask, (0, 0), 21).astype(np.float32) / 255 * 0.75
        image = (overlay.astype(np.float32) * mask[:, :, None]
                 + image.astype(np.float32) * (1 - mask[:, :, None])).astype(np.uint8)
    return image
