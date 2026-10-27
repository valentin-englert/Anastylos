"""Deterministic synthetic cable renderer for regression tests.

Draws idealized tightly framed cable scenes: straight runs, curves,
Y branches, crossings, colored and varying-width cables, clips,
channels, connector blocks, occluders, background screws and faint
traces. Paint order is realistic (occluders cover cables). Fixtures
exercise the pipeline only; they prove nothing about real-photo
accuracy.

EXPECTED records nominal polyline geometry for measurement tests.
"""

from __future__ import annotations

import cv2
import numpy as np

SIZE = 400
BACKGROUND = 190
DARK = (60, 60, 60)
RED = (60, 60, 210)
BLUE = (210, 120, 40)
CLIP_TONE = 225
CONNECTOR = 35

KINDS = ("straight", "curve", "branch", "tap", "crossing", "colors",
         "widths", "clip", "channel", "connector", "occluded", "multi",
         "screw_bg", "traces_bg", "blank")

EXPECTED = {
    "straight": {"cables": 1, "width": 12.0},
    "curve": {"cables": 1, "width": 12.0},
    "branch": {"cables": 1, "width": 10.0, "junctions": 1},
    "tap": {"cables": 1, "width": 10.0, "taps": 1},
    "crossing": {"cables": 1, "width": 12.0, "crossings": 1},
    "colors": {"cables": 3, "width": 10.0},
    "widths": {"cables": 3, "width": None},
    "clip": {"cables": 2, "width": 12.0, "clips": 1, "hypotheses": 1},
    "channel": {"cables": 1, "width": 10.0, "channels": 1},
    "connector": {"cables": 1, "width": 12.0, "anchors": 1},
    "occluded": {"cables": 2, "width": 12.0, "hypotheses": 1},
    "multi": {"cables": 3, "width": 10.0},
    "screw_bg": {"cables": 1, "width": 12.0},
    "traces_bg": {"cables": 1, "width": 12.0},
    "blank": {"cables": 0, "width": None},
}


def _run(image, points, width, color=DARK):
    pts = np.array(points, np.int32).reshape((-1, 1, 2))
    cv2.polylines(image, [pts], False, color, width, cv2.LINE_8)
    for x, y in points[:1] + points[-1:]:
        cv2.circle(image, (int(x), int(y)), width // 2, color, -1)


def _upright(image, kind: str) -> None:
    if kind == "straight":
        _run(image, [(40, 200), (360, 200)], 12)
    elif kind == "curve":
        _run(image, [(40, 320), (140, 320), (260, 120), (360, 120)], 12)
    elif kind == "branch":
        _run(image, [(200, 200), (200, 60)], 10)
        _run(image, [(200, 200), (80, 270)], 10)
        _run(image, [(200, 200), (320, 270)], 10)
    elif kind == "tap":
        _run(image, [(40, 200), (360, 200)], 10)
        _run(image, [(200, 200), (200, 320)], 10)
    elif kind == "crossing":
        _run(image, [(60, 120), (340, 280)], 12)
        _run(image, [(60, 280), (340, 120)], 12)
    elif kind == "colors":
        _run(image, [(30, 100), (370, 100)], 10, RED)
        _run(image, [(30, 200), (370, 200)], 10, BLUE)
        _run(image, [(30, 300), (370, 300)], 10, DARK)
    elif kind == "widths":
        _run(image, [(30, 90), (370, 90)], 6)
        _run(image, [(30, 200), (370, 200)], 12)
        _run(image, [(30, 310), (370, 310)], 18)
    elif kind == "clip":
        _run(image, [(40, 200), (360, 200)], 12)
        cv2.rectangle(image, (188, 168), (212, 232), (CLIP_TONE,) * 3, -1)
    elif kind == "channel":
        cv2.line(image, (30, 150), (370, 150), (165,) * 3, 4)
        cv2.line(image, (30, 250), (370, 250), (165,) * 3, 4)
        _run(image, [(40, 200), (360, 200)], 10)
    elif kind == "connector":
        _run(image, [(120, 200), (360, 200)], 12)
        cv2.rectangle(image, (40, 170), (120, 230), (CONNECTOR,) * 3, -1)
    elif kind == "occluded":
        _run(image, [(40, 200), (360, 200)], 12)
        cv2.rectangle(image, (170, 150), (230, 250), (175,) * 3, -1)
    elif kind == "multi":
        _run(image, [(30, 90), (370, 130)], 10)
        _run(image, [(30, 200), (370, 200)], 10)
        _run(image, [(30, 310), (370, 270)], 10)
    elif kind == "screw_bg":
        _run(image, [(40, 300), (360, 300)], 12)
        cv2.circle(image, (320, 90), 26, (70,) * 3, -1)
        cv2.line(image, (300, 90), (340, 90), (200,) * 3, 6)
    elif kind == "traces_bg":
        _run(image, [(40, 300), (360, 300)], 12)
        for y in (60, 90, 120):
            cv2.line(image, (40, y), (360, y), (170,) * 3, 2)
    elif kind == "blank":
        pass
    else:
        raise ValueError(f"unknown synthetic kind {kind!r}")


def render(kind: str, rotation_deg: float = 0.0, brightness: int = 0,
           glare: bool = False, shadow: float = 0.0, seed: int = 0
           ) -> np.ndarray:
    """Render one cable scene. See module docstring for kinds."""
    if kind not in EXPECTED:
        raise ValueError(f"unknown synthetic kind {kind!r}")
    rng = np.random.RandomState(seed)
    image = np.full((SIZE, SIZE, 3), BACKGROUND, np.uint8)
    _upright(image, kind)
    if shadow:
        ramp = np.linspace(1 - shadow / 2, 1 + shadow / 2, SIZE,
                           dtype=np.float32)
        image = np.clip(image.astype(np.float32) * ramp[None, :, None],
                        0, 255).astype(np.uint8)
    if brightness:
        image = np.clip(image.astype(np.int32) + brightness, 0,
                        255).astype(np.uint8)
    if rotation_deg:
        matrix = cv2.getRotationMatrix2D((SIZE // 2, SIZE // 2),
                                         rotation_deg, 1.0)
        image = cv2.warpAffine(image, matrix, (SIZE, SIZE),
                              flags=cv2.INTER_LINEAR,
                              borderValue=(BACKGROUND,) * 3)
    if glare:
        cv2.ellipse(image, (int(SIZE * 0.60), int(SIZE * 0.38)),
                    (60, 80), -20, 0, 360, (255, 255, 255), -1)
    noise = rng.normal(0, 2.0, image.shape).astype(np.float32)
    return np.clip(image.astype(np.float32) + noise, 0, 255).astype(np.uint8)
