"""Deterministic screw-head pipeline: localize, segment, measure, rank.

OpenCV and NumPy only, no learned models. Head localization, recess
segmentation, and rotation-invariant geometric features feed the pure
rule classifier. `segmenter` and `ranker` are adapter seams: pass a
callable to substitute a future segmentation or classification
provider without touching the pipeline.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from anastylos_screw.classify import classify

MIN_HEAD_FRACTION = 0.05
MAX_HEAD_FRACTION = 0.92
MIN_RIM_SUPPORT = 0.25
MIN_RECESS_FRACTION = 0.005


def _largest_contour(mask: np.ndarray):
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    return max(contours, key=cv2.contourArea)


def _rim_support(edges: np.ndarray, cx: float, cy: float, radius: float) -> float:
    angles = np.deg2rad(np.arange(0, 360, 2))
    xs = np.clip((cx + radius * np.cos(angles)).astype(int), 0, edges.shape[1] - 1)
    ys = np.clip((cy + radius * np.sin(angles)).astype(int), 0, edges.shape[0] - 1)
    hit = 0
    for x, y in zip(xs, ys):
        if edges[max(0, y - 2):y + 3, max(0, x - 2):x + 3].any():
            hit += 1
    return round(hit / len(xs), 3)


def localize_head(gray: np.ndarray) -> dict | None:
    """Find the head circle; pixel units, no dimension claims."""
    height, width = gray.shape
    total = height * width
    small = min(height, width)
    blurred = cv2.GaussianBlur(gray, (9, 9), 0)
    edges = cv2.Canny(blurred, 60, 150)
    found: list[tuple[float, float, float]] = []
    circles = cv2.HoughCircles(blurred, cv2.HOUGH_GRADIENT, 1, small // 3,
                               param1=120, param2=25,
                               minRadius=int(small * 0.12), maxRadius=int(small * 0.60))
    if circles is not None:
        for cx, cy, radius in circles[0]:
            if math.hypot(cx - width / 2, cy - height / 2) < small * 0.25:
                found.append((float(cx), float(cy), float(radius)))
    _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    for mask in (binary, 255 - binary):
        contour = _largest_contour(mask)
        if contour is None:
            continue
        area = cv2.contourArea(contour)
        if not total * MIN_HEAD_FRACTION < area < total * MAX_HEAD_FRACTION:
            continue
        if cv2.pointPolygonTest(contour, (width / 2, height / 2), False) < 0:
            continue
        (cx, cy), radius = cv2.minEnclosingCircle(contour)
        found.append((float(cx), float(cy), float(radius)))
    best = None
    for cx, cy, radius in found:
        support = _rim_support(edges, cx, cy, radius)
        if support >= MIN_RIM_SUPPORT and (best is None or support > best[0]):
            best = (support, cx, cy, radius)
    if best is None:
        return None
    support, cx, cy, radius = best
    return {"center_xy": [round(cx, 1), round(cy, 1)], "radius_px": round(radius, 1),
            "rim_support": support, "area_px": int(math.pi * radius * radius)}


def default_segmenter(gray: np.ndarray, head: dict) -> np.ndarray:
    """Dark recess pixels inside the head circle."""
    cx, cy = head["center_xy"]
    radius = head["radius_px"]
    yy, xx = np.mgrid[0:gray.shape[0], 0:gray.shape[1]]
    inner = ((xx - cx) ** 2 + (yy - cy) ** 2) <= (radius * 0.88) ** 2
    vals = gray[inner].reshape(1, -1)
    level, _ = cv2.threshold(vals, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    dark = np.zeros_like(gray)
    dark[inner & (gray < level)] = 255
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    cleaned = cv2.morphologyEx(dark, cv2.MORPH_OPEN, kernel)
    cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return np.zeros_like(dark)
    biggest = max(contours, key=cv2.contourArea)
    return cv2.drawContours(np.zeros_like(dark), [biggest], -1, 255, -1)


def _symmetry(mask: np.ndarray, center: tuple[float, float], angle_deg: float) -> float:
    matrix = cv2.getRotationMatrix2D(center, angle_deg, 1.0)
    turned = cv2.warpAffine(mask, matrix, (mask.shape[1], mask.shape[0]),
                            flags=cv2.INTER_NEAREST, borderValue=0)
    inter = np.logical_and(mask > 0, turned > 0).sum()
    total = (mask > 0).sum() + (turned > 0).sum()
    return round(float(2 * inter / total) if total else 0.0, 3)


def _eccentricity(contour: np.ndarray) -> float:
    moments = cv2.moments(contour)
    if moments["m00"] <= 0:
        return 0.0
    mu20, mu02, mu11 = moments["mu20"], moments["mu02"], moments["mu11"]
    trace, det = mu20 + mu02, mu20 * mu02 - mu11 * mu11
    disc = max(0.0, trace * trace / 4 - det)
    root = math.sqrt(disc)
    major, minor = trace / 2 + root, max(trace / 2 - root, 1e-9)
    return round(math.sqrt(max(0.0, 1 - minor / major)), 3)


def _radial_lobes(mask: np.ndarray, center: tuple[float, float], radius: float) -> int:
    height, width = mask.shape
    yy, xx = np.mgrid[0:height, 0:width].astype(float)
    dx, dy = xx - center[0], yy - center[1]
    dist = np.sqrt(dx * dx + dy * dy)
    phi = (np.degrees(np.arctan2(dy, dx)) + 360) % 360
    profile = []
    for deg in range(0, 360, 5):
        ring = (np.abs(((phi - deg + 180) % 360) - 180) < 2.5) & (dist < radius * 0.8)
        profile.append(float(dist[ring & (mask > 0)].max()) if (ring & (mask > 0)).any() else 0.0)
    profile = np.array(profile, dtype=float)
    if profile.max() <= 0:
        return 0
    kernel = np.ones(3) / 3.0
    padded = np.concatenate([profile[-1:], profile, profile[:1]])
    smooth = np.convolve(padded, kernel, mode="valid")
    if smooth.max() - smooth.min() < 2.0:
        return 0
    wide = np.concatenate([smooth[-9:], smooth, smooth[:9]])
    trend = np.array([np.median(wide[i:i + 18]) for i in range(len(smooth))])
    residual = smooth - trend
    floor = 0.12 * (residual.max() - residual.min())
    count = len(smooth)
    cands = []
    for i in range(count):
        window = [residual[(i + k) % count] for k in (-2, -1, 0, 1, 2)]
        if residual[i] > floor and residual[i] == max(window):
            cands.append(i)
    peaks = []
    for i in sorted(cands, key=lambda j: -residual[j]):
        if all(min((i - p) % count, (p - i) % count) >= 6 for p in peaks):
            peaks.append(i)
    return len(peaks)


def _tick_energy(mask: np.ndarray, center: tuple[float, float], radius: float) -> float:
    height, width = mask.shape
    yy, xx = np.mgrid[0:height, 0:width].astype(float)
    dx, dy = xx - center[0], yy - center[1]
    dist = np.sqrt(dx * dx + dy * dy)
    phi = (np.degrees(np.arctan2(dy, dx)) + 360) % 360
    ring = (dist > radius * 0.25) & (dist < radius * 0.62) & (mask > 0)
    if not ring.any():
        return 0.0
    arm = np.zeros(72)
    diag = np.zeros(72)
    for base in range(72):
        center_deg = base * 5.0
        in_arm = np.abs(((phi - center_deg + 180) % 360) - 180) < 14
        in_diag = np.abs(((phi - center_deg - 45 + 180) % 360) - 180) < 12
        arm[base] = (ring & in_arm).sum()
        diag[base] = (ring & in_diag).sum()
    best = int(np.argmax(arm + diag * 0.5))
    total = arm[best] + diag[best]
    return round(float(diag[best] / total) if total else 0.0, 3)


def measure(mask: np.ndarray, head: dict, gray: np.ndarray) -> tuple[dict, dict]:
    """Rotation-invariant features plus quality indicators."""
    cx, cy = head["center_xy"]
    radius = head["radius_px"]
    center = (cx, cy)
    contour = _largest_contour(mask)
    if contour is None or cv2.contourArea(contour) < (math.pi * radius * radius) * MIN_RECESS_FRACTION:
        zero = {"sym2": 0.0, "sym4": 0.0, "sym6": 0.0, "eccentricity": 0.0,
                "fill": 0.0, "solidity": 0.0, "vertices": 0, "defects": 0,
                "lobes": 0, "ticks": 0.0}
        return zero, _quality(gray, head, mask, None)
    hull = cv2.convexHull(contour)
    defects = cv2.convexityDefects(contour, cv2.convexHull(contour, returnPoints=False))
    depth_limit = max(2.0, radius * 0.04)
    strong = 0 if defects is None else sum(1 for row in defects[:, 0] if row[3] / 256.0 > depth_limit)
    approx = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
    area = cv2.contourArea(contour)
    features = {
        "sym2": _symmetry(mask, center, 180.0),
        "sym4": max(_symmetry(mask, center, 90.0), _symmetry(mask, center, 270.0)),
        "sym6": max(_symmetry(mask, center, 60.0), _symmetry(mask, center, 120.0)),
        "eccentricity": _eccentricity(contour),
        "fill": round(area / (math.pi * radius * radius), 3),
        "solidity": round(area / max(cv2.contourArea(hull), 1.0), 3),
        "vertices": int(len(approx)),
        "defects": int(strong),
        "lobes": _radial_lobes(mask, center, radius),
        "ticks": _tick_energy(mask, center, radius),
    }
    return features, _quality(gray, head, mask, contour)


def _quality(gray: np.ndarray, head: dict, mask: np.ndarray, contour) -> dict:
    cx, cy = head["center_xy"]
    radius = head["radius_px"]
    yy, xx = np.mgrid[0:gray.shape[0], 0:gray.shape[1]].astype(float)
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    disc = dist <= radius * 0.9
    recess = mask > 0
    rim = disc & ~recess & (gray < 245)
    recess_clean = recess & (gray < 245)
    contrast = 0.0
    if rim.any() and recess_clean.any():
        contrast = round(float(np.median(gray[rim].astype(float)) - np.median(gray[recess_clean].astype(float))) / 255.0, 3)
    blur = cv2.Laplacian(gray, cv2.CV_64F)
    return {"contrast": contrast,
            "glare_fraction": round(float(((gray > 245) & disc).sum() / max(disc.sum(), 1)), 3),
            "shadow_fraction": round(float(((gray < 25) & disc & ~recess).sum() / max(disc.sum(), 1)), 3),
            "blur_variance": round(float(blur[disc].var()), 1) if disc.any() else 0.0,
            "recess_fraction": round(float(recess.sum() / max(disc.sum(), 1)), 3)}


def _recess_box(mask: np.ndarray) -> list[int] | None:
    contour = _largest_contour(mask)
    if contour is None:
        return None
    x, y, w, h = cv2.boundingRect(contour)
    return [int(x), int(y), int(w), int(h)]


def analyze(image: np.ndarray, roi: tuple[int, int, int, int] | None = None,
            segmenter=None, ranker=None) -> dict:
    """Localize, segment, measure, and rank one crop. JSON-serializable."""
    height, width = image.shape[:2]
    x, y, w, h = roi or (0, 0, width, height)
    if not (0 <= x < width and 0 <= y < height and w > 0 and h > 0 and x + w <= width and y + h <= height):
        raise ValueError(f"ROI {(x, y, w, h)} is outside {width}x{height}")
    crop = image[y:y + h, x:x + w]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    head = localize_head(gray)
    if head is None:
        return {"status": "unknown", "reason": "no circular head localized",
                "candidates": [], "features": None, "quality": {"contrast": 0.0},
                "head": None, "recess_bbox_xywh": None}
    segment = segmenter or default_segmenter
    mask = segment(gray, head)
    features, quality = measure(mask, head, gray)
    rank = ranker or classify
    verdict = rank(features, quality)
    cx, cy = head["center_xy"]
    radius = head["radius_px"]
    head_box = [x + int(cx - radius), y + int(cy - radius), int(2 * radius), int(2 * radius)]
    recess = _recess_box(mask)
    if recess is not None:
        recess[0] += x
        recess[1] += y
    return {**verdict, "features": features, "quality": quality,
            "head": {"center_xy": [round(cx + x, 1), round(cy + y, 1)],
                     "radius_px": head["radius_px"], "rim_support": head["rim_support"],
                     "bbox_xywh": head_box},
            "recess_bbox_xywh": recess}
