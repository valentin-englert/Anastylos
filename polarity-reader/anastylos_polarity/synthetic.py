"""Deterministic synthetic component renderer for polarity tests.

Draws idealized tight crops with polarity/orientation markers: diode
cathode bands, electrolytic stripes, tantalum end bars, IC pin-1 dots,
package notches, printed top marks, and connector keys. Upright
geometry is drawn first, then the whole canvas is rotated so rotation
tests exercise the pipeline honestly. Degradations (brightness shift,
glare, shadow ramp, occlusion, marker damage) are deterministic per
seed. Fixtures exercise the pipeline only; they prove nothing about
real-hardware accuracy.

Base marker directions (unrotated canvas, image frame: +x right,
+y down, angles clockwise from +x): diode band +x (0 deg),
electrolytic stripe +x (0 deg), tantalum bar +x (0 deg), IC dot
toward the top-left corner (225 deg), IC notch toward the top edge
(270 deg), connector latch toward (321.9 deg, offset from center).
"""

from __future__ import annotations

import cv2
import numpy as np

SIZE = 400
CENTER = (SIZE // 2, SIZE // 2)
BACKGROUND = 190

# Base marker angles in degrees (clockwise from +x, y-down frame).
BASE_ANGLES = {
    "diode": {"band": 0.0},
    "aluminum_electrolytic": {"stripe": 0.0},
    "tantalum": {"band": 0.0},
    "ic": {"dot": 225.0, "notch": 270.0},
    "ic_topmark": {},
    "connector": {"key": 321.9},  # latch offset +60x from body center
    "blank": {},
}

KINDS = tuple(BASE_ANGLES)


def expected_angle(kind: str, marker: str, rotation_deg: float) -> float:
    """Expected detected angle after canvas rotation.

    OpenCV canvas rotation is counter-clockwise for positive angles,
    which decreases clockwise-from-+x angles.
    """
    return (BASE_ANGLES[kind][marker] - rotation_deg) % 360.0


def _body_mask(shape, box) -> np.ndarray:
    mask = np.zeros(shape[:2], np.uint8)
    cv2.drawContours(mask, [np.asarray(box, dtype=np.int32)], 0, 255, -1)
    return mask


def _upright(kind: str) -> tuple[np.ndarray, dict]:
    """Draw the unrotated crop. Returns (image, marker_paint)."""
    image = np.full((SIZE, SIZE, 3), BACKGROUND, np.uint8)
    cx, cy = CENTER
    paint: dict = {"body_value": None, "markers": {}}
    if kind == "diode":
        body, marker, w, h = 40, 225, 240, 92
        cv2.rectangle(image, (cx - w // 2, cy - h // 2),
                      (cx + w // 2, cy + h // 2), (body,) * 3, -1)
        band_w, band_x = 26, cx + 85
        cv2.rectangle(image, (band_x - band_w // 2, cy - h // 2),
                      (band_x + band_w // 2, cy + h // 2), (marker,) * 3, -1)
        paint["body_value"] = body
        paint["markers"]["band"] = {
            "bbox": [band_x - band_w // 2, cy - h // 2, band_w, h],
            "body_value": body, "marker_value": marker}
    elif kind == "aluminum_electrolytic":
        body, marker, w, h = 35, 228, 150, 260
        cv2.rectangle(image, (cx - w // 2, cy - h // 2),
                      (cx + w // 2, cy + h // 2), (body,) * 3, -1)
        stripe_w, stripe_x = 30, cx + w // 2 - 18
        cv2.rectangle(image, (stripe_x - stripe_w // 2, cy - h // 2 + 6),
                      (stripe_x + stripe_w // 2, cy + h // 2 - 6),
                      (marker,) * 3, -1)
        paint["body_value"] = body
        paint["markers"]["stripe"] = {
            "bbox": [stripe_x - stripe_w // 2, cy - h // 2 + 6,
                     stripe_w, h - 12],
            "body_value": body, "marker_value": marker}
    elif kind == "tantalum":
        body, marker, w, h = 160, 40, 170, 110
        cv2.rectangle(image, (cx - w // 2, cy - h // 2),
                      (cx + w // 2, cy + h // 2), (body,) * 3, -1)
        bar_w, bar_x = 24, cx + w // 2 - 20
        cv2.rectangle(image, (bar_x - bar_w // 2, cy - h // 2),
                      (bar_x + bar_w // 2, cy + h // 2), (marker,) * 3, -1)
        paint["body_value"] = body
        paint["markers"]["band"] = {
            "bbox": [bar_x - bar_w // 2, cy - h // 2, bar_w, h],
            "body_value": body, "marker_value": marker}
    elif kind in ("ic", "ic_topmark"):
        body, side = 30, 220
        cv2.rectangle(image, (cx - side // 2, cy - side // 2),
                      (cx + side // 2, cy + side // 2), (body,) * 3, -1)
        paint["body_value"] = body
        if kind == "ic":
            dot = (cx - 70, cy - 70)
            cv2.circle(image, dot, 14, (215,) * 3, -1)
            cv2.circle(image, (cx, cy - side // 2), 22,
                       (BACKGROUND,) * 3, -1)
            paint["markers"]["dot"] = {
                "bbox": [dot[0] - 14, dot[1] - 14, 28, 28],
                "body_value": body, "marker_value": 215}
            paint["markers"]["notch"] = {
                "bbox": [cx - 22, cy - side // 2 - 22, 44, 44],
                "body_value": body, "marker_value": BACKGROUND}
        else:
            for i, text in enumerate(("74HC14", "NXP 2413")):
                cv2.putText(image, text, (cx - 72, cy - 12 + i * 34),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.85, (200,) * 3, 2,
                            cv2.LINE_AA)
    elif kind == "connector":
        body, w, h = 232, 260, 140
        cv2.rectangle(image, (cx - w // 2, cy - h // 2),
                      (cx + w // 2, cy + h // 2), (body,) * 3, -1)
        latch_w, latch_h = 44, 30
        latch = (cx + 60, cy - h // 2 + 8)
        cv2.rectangle(image, (latch[0] - latch_w // 2, latch[1]),
                      (latch[0] + latch_w // 2, latch[1] + latch_h),
                      (55,) * 3, -1)
        paint["body_value"] = body
        paint["markers"]["key"] = {
            "bbox": [latch[0] - latch_w // 2, latch[1], latch_w, latch_h],
            "body_value": body, "marker_value": 55}
    elif kind == "blank":
        body, w, h = 120, 220, 130
        cv2.rectangle(image, (cx - w // 2, cy - h // 2),
                      (cx + w // 2, cy + h // 2), (body,) * 3, -1)
        paint["body_value"] = body
    else:
        raise ValueError(f"unknown synthetic kind {kind!r}")
    return image, paint


def _damage(image: np.ndarray, paint: dict, amount: float,
            rng: np.random.RandomState) -> None:
    """Scuff painted markers with body-colored blotches and speckle."""
    for spec in paint["markers"].values():
        x, y, w, h = spec["bbox"]
        body_value = spec["body_value"]
        x0, y0 = max(x, 0), max(y, 0)
        x1, y1 = min(x + w, SIZE), min(y + h, SIZE)
        blotches = 1 + int(amount * 3)
        for _ in range(blotches):
            center = (int(rng.uniform(x0, x1)), int(rng.uniform(y0, y1)))
            axes = (int(rng.uniform(w * 0.2, w * (0.3 + amount * 0.5))),
                    int(rng.uniform(h * 0.15, h * (0.25 + amount * 0.4))))
            tone = int(rng.normal(body_value, 5))
            cv2.ellipse(image, center, axes,
                        float(rng.uniform(0, 180)), 0, 360,
                        (tone,) * 3, -1)
        region = image[y0:y1, x0:x1].astype(np.int32)
        kill = rng.random(region.shape[:2]) < amount * 0.4
        blotch = rng.normal(body_value, 6, region.shape).clip(0, 255)
        region[kill] = blotch[kill]
        image[y0:y1, x0:x1] = region.astype(np.uint8)


def render(kind: str, rotation_deg: float = 0.0, brightness: int = 0,
           glare: bool = False, shadow: float = 0.0, occlude: float = 0.0,
           damage: float = 0.0, seed: int = 0) -> np.ndarray:
    """Render one component crop. See module docstring for kinds."""
    if kind not in BASE_ANGLES:
        raise ValueError(f"unknown synthetic kind {kind!r}")
    rng = np.random.RandomState(seed)
    image, paint = _upright(kind)
    if damage:
        _damage(image, paint, min(max(damage, 0.0), 1.0), rng)
    if occlude:
        cv2.ellipse(image,
                    (int(CENTER[0] + 90 * occlude), int(CENTER[1] - 70)),
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
    if rotation_deg:
        matrix = cv2.getRotationMatrix2D(CENTER, rotation_deg, 1.0)
        # Borders reuse the mean edge tone so rotation never invents a body.
        image = cv2.warpAffine(image, matrix, (SIZE, SIZE),
                              flags=cv2.INTER_LINEAR,
                              borderValue=(BACKGROUND,) * 3)
    if glare:
        overlay = image.copy()
        cv2.ellipse(overlay, (int(SIZE * 0.60), int(SIZE * 0.38)),
                    (int(SIZE * 0.09), int(SIZE * 0.13)), -20, 0, 360,
                    (255, 255, 255), -1)
        overlay = cv2.GaussianBlur(overlay, (0, 0), 15)
        mask = np.zeros((SIZE, SIZE), np.uint8)
        cv2.ellipse(mask, (int(SIZE * 0.60), int(SIZE * 0.38)),
                    (int(SIZE * 0.09), int(SIZE * 0.13)), -20, 0, 360,
                    255, -1)
        mask = cv2.GaussianBlur(mask, (0, 0), 15).astype(np.float32)
        mask = mask / 255 * 0.8
        image = (overlay.astype(np.float32) * mask[:, :, None]
                 + image.astype(np.float32) * (1 - mask[:, :, None]))
        image = image.astype(np.uint8)
        cv2.ellipse(image, (int(SIZE * 0.60), int(SIZE * 0.38)),
                    (18, 26), -20, 0, 360, (255, 255, 255), -1)
    noise = rng.normal(0, 2.0, image.shape).astype(np.float32)
    return np.clip(image.astype(np.float32) + noise, 0, 255).astype(np.uint8)
