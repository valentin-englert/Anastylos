"""Deterministic synthetic original/current pairs for missing-parts tests.

A fixed plate layout (two identical screws, one component, two empty
holes) with scripted removals, additions, shifts, displacements,
lighting changes, occlusion, and registration failures. Every case
returns exact pixel truth in original-frame coordinates. Fixtures
exercise the pipeline only and say nothing about real-hardware
accuracy.
"""

from __future__ import annotations

import cv2
import numpy as np

SIZE = 400
BACKGROUND = 200
PART = 60
HOLE = 25

SCREW_A = (120, 140, 14)
SCREW_B = (280, 140, 14)
COMPONENT = (100, 250, 60, 30)
HOLE_DISC = (280, 260, 7)
HOLE_RING = (190, 330, 10, 5)
EMPTY_SPOT = (200, 200)


def _disc(image: np.ndarray, center: tuple[int, int], radius: int, value: int):
    cv2.circle(image, center, radius, value, -1)


def _plate(seed: int = 0, holes: bool = True, screws=("A", "B"),
           component: bool = True, extra=()) -> np.ndarray:
    rng = np.random.RandomState(seed)
    image = np.full((SIZE, SIZE), BACKGROUND, np.uint8)
    if "A" in screws:
        _disc(image, SCREW_A[:2], SCREW_A[2], PART)
    if "B" in screws:
        _disc(image, SCREW_B[:2], SCREW_B[2], PART)
    if component:
        x, y, w, h = COMPONENT
        cv2.rectangle(image, (x, y), (x + w, y + h), PART + 10, -1)
    if holes:
        _disc(image, HOLE_DISC[:2], HOLE_DISC[2], HOLE)
        _disc(image, HOLE_RING[:2], HOLE_RING[2], HOLE + 5)
        _disc(image, HOLE_RING[:2], HOLE_RING[3], BACKGROUND)
    for kind, payload in extra:
        if kind == "screw":
            _disc(image, payload[:2], payload[2], PART)
        elif kind == "hole":
            _disc(image, payload[:2], payload[2], HOLE)
    noise = rng.normal(0, 2.0, image.shape).astype(np.float32)
    gray = np.clip(image.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def _bbox_disc(center: tuple[int, int], radius: int) -> list[int]:
    return [center[0] - radius, center[1] - radius, radius * 2, radius * 2]


def unchanged(seed: int = 0):
    """Identical states; no candidates expected."""
    return _plate(seed), _plate(seed + 1), {"absent": [], "occupied": [],
                                           "present": [_bbox_disc(SCREW_A[:2], 14)]}


def removed(seed: int = 0):
    """Screw A gone in the current state."""
    return _plate(seed), _plate(seed + 1, screws=("B",)), \
        {"absent": [_bbox_disc(SCREW_A[:2], 14)], "occupied": [], "present": []}


def added(seed: int = 0):
    """A new screw appears at the empty spot."""
    return _plate(seed), _plate(seed + 1, extra=(("screw", (200, 200, 14)),)), \
        {"absent": [], "occupied": [_bbox_disc(EMPTY_SPOT, 14)], "present": []}


def shifted(seed: int = 0, dx: int = 5, dy: int = 0):
    """Screw A nudged but still present; must not read absent."""
    moved = (SCREW_A[0] + dx, SCREW_A[1] + dy, SCREW_A[2])
    current = _plate(seed + 1, screws=("B",), extra=(("screw", moved),))
    return _plate(seed), current, \
        {"absent": [], "occupied": [],
         "present": [_bbox_disc(SCREW_A[:2], 16)]}


def displaced(seed: int = 0):
    """Screw A moved across the plate; absence and presence pair up."""
    current = _plate(seed + 1, screws=("B",), extra=(("screw", (200, 200, 14)),))
    return _plate(seed), current, \
        {"absent": [_bbox_disc(SCREW_A[:2], 14)],
         "occupied": [_bbox_disc(EMPTY_SPOT, 14)], "present": []}


def repeated(seed: int = 0):
    """Two identical screws, B removed; identity stays ambiguous."""
    return _plate(seed), _plate(seed + 1, screws=("A",)), \
        {"absent": [_bbox_disc(SCREW_B[:2], 14)], "occupied": [],
         "present": [_bbox_disc(SCREW_A[:2], 14)], "identical": True}


def preexisting_hole(seed: int = 0):
    """Empty holes in both states; never a missing part."""
    return _plate(seed), _plate(seed + 1), {"absent": [], "occupied": [],
                                           "present": [],
                                           "holes": [_bbox_disc(HOLE_DISC[:2], 9)]}


def hole_revealed(seed: int = 0):
    """Screw A removed, revealing a mounting hole beneath."""
    current = _plate(seed + 1, screws=("B",),
                     extra=(("hole", (SCREW_A[0], SCREW_A[1], 7)),))
    return _plate(seed), current, \
        {"absent": [_bbox_disc(SCREW_A[:2], 14)], "occupied": [],
         "present": [], "mounting_hole": True}


def lighting(seed: int = 0):
    """Unchanged parts under a shadow blob plus a brightness ramp."""
    original = _plate(seed).astype(np.float32)
    current = _plate(seed + 1).astype(np.float32)
    ramp = np.linspace(0.85, 1.1, SIZE, dtype=np.float32)
    current *= ramp[None, :, None]
    overlay = current.copy()
    cv2.ellipse(overlay, (300, 300), (90, 60), 20, 0, 360, (90, 90, 90), -1)
    overlay = cv2.GaussianBlur(overlay, (0, 0), 25)
    mask = np.zeros((SIZE, SIZE), np.uint8)
    cv2.ellipse(mask, (300, 300), (90, 60), 20, 0, 360, 255, -1)
    blend = cv2.GaussianBlur(mask, (0, 0), 25).astype(np.float32) / 255 * 0.55
    current = overlay * blend[:, :, None] + current * (1 - blend[:, :, None])
    current = np.clip(current, 0, 255).astype(np.uint8)
    return np.clip(original, 0, 255).astype(np.uint8), current, \
        {"absent": [], "occupied": [], "present": [_bbox_disc(SCREW_A[:2], 14)]}


def occluded(seed: int = 0):
    """A dark hand/tool-like bar covers screw A in the current state."""
    current = _plate(seed + 1)
    cv2.rectangle(current, (60, 100), (180, 180), (20, 20, 20), -1)
    return _plate(seed), current, \
        {"absent": [], "occupied": [], "present": [],
         "occluded": [_bbox_disc(SCREW_A[:2], 16)]}


def contradictory(seed: int = 0):
    """Screw A present but flash-brightened; polarity lies, texture stays."""
    current = _plate(seed + 1).astype(np.float32)
    yy, xx = np.mgrid[0:SIZE, 0:SIZE].astype(float)
    dist = np.sqrt((xx - SCREW_A[0]) ** 2 + (yy - SCREW_A[1]) ** 2)
    lift = np.clip(1 - dist / 60.0, 0, 1) * 90.0
    current += lift[:, :, None]
    current = np.clip(current, 0, 255).astype(np.uint8)
    return _plate(seed), current, \
        {"absent": [], "occupied": [],
         "present": [_bbox_disc(SCREW_A[:2], 16)]}


def shifted_view(seed: int = 0, dx: int = 40, dy: int = 10):
    """Current view translated beyond the registration gate."""
    matrix = np.float32([[1, 0, dx], [0, 1, dy]])
    current = cv2.warpAffine(_plate(seed + 1), matrix, (SIZE, SIZE),
                             borderValue=(BACKGROUND,) * 3)
    return _plate(seed), current, {"absent": [], "occupied": [], "present": []}


def translated_pair(seed: int = 0, dx: int = 40, dy: int = 10):
    """Translated view plus the exact inverse homography for the gate test."""
    original, current, truth = shifted_view(seed, dx, dy)
    homography = [[1.0, 0.0, float(-dx)], [0.0, 1.0, float(-dy)], [0.0, 0.0, 1.0]]
    return original, current, truth, homography


def detection_fixture(directory, status: str = "single_change", view: str = "fixture_back",
                      regions: list[dict] | None = None, with_images: bool = True,
                      seed: int = 0) -> dict:
    """Write a fake change-detection pair directory; returns paths and truth."""
    from pathlib import Path
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if regions is None:
        regions = [{"label": "R1", "bbox_px": [106, 126, 28, 28],
                    "bbox_mm": [52.0, 40.5, 7.0, 7.0], "centroid_mm": [55.5, 44.0],
                    "area_px": 620, "area_mm2": 38.75, "mean_diff": 60.0, "max_diff": 120.0,
                    "sign": "brighter_after"}]
    result = {"pair_id": directory.name, "before": "before.jpg", "after": "after.jpg",
              "status": status, "regions": regions if status not in
              ("registration_invalid", "view_mismatch", "input_error", "no_change") else [],
              "warnings": [], "view": view,
              "registration_before": {"valid": True}, "registration_after": {"valid": True},
              "rectified": {"view": view, "px_per_mm": 4.0, "size_px": [400, 400],
                            "origin_frame_mm": [25.5, 8.5]}}
    if status in ("registration_invalid", "view_mismatch", "input_error"):
        result.pop("rectified", None)
        result["registration_before"] = {"valid": False, "reasons": ["synthetic failure"]}
    (directory / "result.json").write_text(__import__("json").dumps(result), encoding="utf-8")
    if with_images:
        original, current, _ = removed(seed)
        cv2.imwrite(str(directory / "before_rectified.jpg"), original)
        cv2.imwrite(str(directory / "after_rectified.jpg"), current)
    return {"result_json": str(directory / "result.json"), "pair_dir": str(directory)}
