"""Deterministic OpenCV/NumPy visual comparison baseline.

Registered before/after differencing with illumination normalization,
candidate region extraction, mounting-hole detection, small-shift
presence checks, and per-region quality indicators. No learned models;
`aligner` and `segmenter` callables may substitute future providers.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

ECC_CRITERIA = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-5)
MIN_IMAGE_STD = 3.0


def _py(value):
    if isinstance(value, (np.floating, np.integer)):
        return float(value) if isinstance(value, np.floating) else int(value)
    if isinstance(value, np.ndarray):
        return [_py(v) for v in value.tolist()]
    if isinstance(value, dict):
        return {k: _py(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_py(v) for v in value]
    return value


def to_gray(image: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image.copy()


def normalize(gray: np.ndarray) -> np.ndarray:
    """Flatten broad illumination by local-mean division."""
    sigma = max(10.0, min(gray.shape) / 10.0)
    local = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), sigma)
    return np.clip(gray.astype(np.float32) / (local + 1.0) * 128.0, 0, 255).astype(np.uint8)


def normalize_masked(gray: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Local-mean division ignoring invalid pixels (warp borders)."""
    sigma = max(10.0, min(gray.shape) / 10.0)
    weights = cv2.GaussianBlur((valid > 0).astype(np.float32), (0, 0), sigma)
    local = cv2.GaussianBlur(gray.astype(np.float32) * (valid > 0), (0, 0), sigma)
    local = local / np.maximum(weights, 1e-3)
    norm = np.clip(gray.astype(np.float32) / (local + 1.0) * 128.0, 0, 255).astype(np.uint8)
    norm[valid == 0] = 128
    return norm


def _erode_valid(valid: np.ndarray) -> np.ndarray:
    return cv2.erode(valid, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))


def image_usable(gray: np.ndarray) -> tuple[bool, str]:
    if gray.size == 0:
        return False, "empty image"
    if float(gray.std()) < MIN_IMAGE_STD:
        return False, f"image is uniform (std below {MIN_IMAGE_STD}); no usable evidence"
    return True, ""


def align_translation(original: np.ndarray, current: np.ndarray,
                      max_shift_px: float, aligner=None) -> dict:
    """Translate current onto original with ECC. Deterministic."""
    if original.shape != current.shape:
        return {"ok": False, "reason": "image dimensions differ; views are not comparable",
                "dx": 0.0, "dy": 0.0, "cc": 0.0, "warped": None, "valid": None}
    if aligner is not None:
        return aligner(original, current, max_shift_px)
    warp = np.eye(2, 3, dtype=np.float32)
    try:
        cc, warp = cv2.findTransformECC(original, current, warp,
                                        cv2.MOTION_TRANSLATION, ECC_CRITERIA, None, 5)
    except cv2.error as exc:
        return {"ok": False, "reason": f"ECC registration failed: {exc}",
                "dx": 0.0, "dy": 0.0, "cc": 0.0, "warped": None, "valid": None}
    dx, dy = float(warp[0, 2]), float(warp[1, 2])
    shift = math.hypot(dx, dy)
    if shift > max_shift_px:
        return {"ok": False, "reason": f"shift {shift:.1f}px exceeds {max_shift_px}px; views differ",
                "dx": dx, "dy": dy, "cc": round(float(cc), 4), "warped": None, "valid": None}
    height, width = original.shape
    warped = cv2.warpAffine(current, warp, (width, height),
                            flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP,
                            borderValue=0)
    valid = cv2.warpAffine(np.full(current.shape, 255, np.uint8), warp, (width, height),
                           flags=cv2.INTER_NEAREST + cv2.WARP_INVERSE_MAP, borderValue=0)
    return {"ok": True, "reason": "", "dx": round(dx, 2), "dy": round(dy, 2),
            "cc": round(float(cc), 4), "warped": warped, "valid": _erode_valid(valid)}


def apply_verified_transform(image: np.ndarray, homography, size: tuple[int, int]) -> dict:
    """Warp current into the original frame with a verified homography."""
    matrix = np.array(homography, dtype=np.float64)
    if abs(np.linalg.det(matrix)) < 1e-12:
        return {"ok": False, "reason": "homography is singular", "warped": None, "valid": None}
    warped = cv2.warpPerspective(image, matrix, size, flags=cv2.INTER_LINEAR, borderValue=0)
    valid = cv2.warpPerspective(np.full(image.shape[:2], 255, np.uint8), matrix, size,
                                flags=cv2.INTER_NEAREST, borderValue=0)
    valid = _erode_valid(valid)
    coverage = float((valid > 0).mean())
    if coverage < 0.5:
        return {"ok": False, "reason": f"transformed overlap covers only {coverage:.0%}",
                "warped": None, "valid": None}
    return {"ok": True, "reason": "", "warped": warped, "valid": valid, "coverage": coverage}


def difference_mask(norm_original: np.ndarray, norm_current: np.ndarray,
                    valid: np.ndarray, threshold: float, min_area_px: float) -> tuple[np.ndarray, dict]:
    blurred_o = cv2.GaussianBlur(norm_original, (0, 0), 1.0)
    blurred_c = cv2.GaussianBlur(norm_current, (0, 0), 1.0)
    diff = cv2.absdiff(blurred_o, blurred_c)
    diff[valid == 0] = 0
    mask = ((diff > threshold) & (valid > 0)).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    mask[valid == 0] = 0
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    kept = np.zeros_like(mask)
    regions = []
    for index in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[index])
        if area < min_area_px:
            continue
        kept[labels == index] = 255
        component = labels == index
        signed = (blurred_c.astype(float) - blurred_o.astype(float))[component]
        regions.append({"bbox": [x, y, w, h],
                        "centroid": [round(float(centroids[index][0]), 1),
                                     round(float(centroids[index][1]), 1)],
                        "area_px": area,
                        "mean_diff": round(float(diff[component].mean()), 2),
                        "max_diff": round(float(diff[component].max()), 2),
                        "median_delta": round(float(np.median(signed)), 2)})
    regions.sort(key=lambda item: -item["area_px"])
    stats_out = {"changed_fraction": round(float((kept > 0).sum() / max((valid > 0).sum(), 1)), 5),
                 "diff_p99": round(float(np.percentile(diff[valid > 0], 99)), 2)
                 if (valid > 0).any() else 0.0}
    return kept, {"regions": regions, "stats": stats_out, "diff": diff}


def polarity(median_delta: float, threshold: float = 5.0) -> str:
    if median_delta > threshold:
        return "brighter_in_current"
    if median_delta < -threshold:
        return "darker_in_current"
    return "no_polarity"


def detect_holes(gray: np.ndarray, min_area: float = 20.0,
                 max_area_px: float = 400.0) -> list[dict]:
    """Dark compact voids: small discs and rings with bright centers.

    The size cap keeps larger present parts out of the void set; rings
    (dark annulus around a bright bore) are matched via contour nesting.
    """
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    level, _ = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    dark = (blurred < min(level, 120)).astype(np.uint8) * 255
    dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    holes = []
    seen: list[tuple[float, float]] = []

    def _record(contour, kind: str):
        area = cv2.contourArea(contour)
        if not min_area <= area <= max_area_px:
            return
        moments = cv2.moments(contour)
        if moments["m00"] <= 0:
            return
        centroid = [round(moments["m10"] / moments["m00"], 1),
                    round(moments["m01"] / moments["m00"], 1)]
        if any(math.hypot(centroid[0] - x, centroid[1] - y) < 4 for x, y in seen):
            return
        seen.append((centroid[0], centroid[1]))
        holes.append({"centroid": centroid, "area_px": int(area), "kind": kind,
                      "bbox": [int(v) for v in cv2.boundingRect(contour)]})

    contours, hierarchy = cv2.findContours(dark, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    hierarchy = hierarchy[0] if hierarchy is not None else []
    for index, contour in enumerate(contours):
        parent = hierarchy[index][3] if len(hierarchy) else -1
        if parent != -1:
            continue
        area = cv2.contourArea(contour)
        perimeter = cv2.arcLength(contour, True)
        if perimeter <= 0:
            continue
        children = [c for c, row in enumerate(hierarchy) if row[3] == index]
        child_area = sum(cv2.contourArea(contours[c]) for c in children)
        if children and child_area >= 0.20 * area:
            _record(contour, "ring")
            continue
        circularity = 4 * math.pi * area / perimeter ** 2
        hull = cv2.convexHull(contour)
        solidity = area / max(cv2.contourArea(hull), 1.0)
        if circularity >= 0.45 and solidity >= 0.80:
            _record(contour, "disc")
    holes.sort(key=lambda item: -item["area_px"])
    return holes


def match_holes(original_holes: list[dict], current_holes: list[dict],
                tolerance_px: float = 6.0) -> list[dict]:
    """Pair holes seen in both states; used pairs are consumed once."""
    pairs = []
    free = list(range(len(current_holes)))
    for hole in original_holes:
        best, best_dist = None, tolerance_px
        for index in free:
            other = current_holes[index]
            dist = math.hypot(hole["centroid"][0] - other["centroid"][0],
                              hole["centroid"][1] - other["centroid"][1])
            ratio = min(hole["area_px"], other["area_px"]) / max(hole["area_px"], other["area_px"])
            if dist <= best_dist and ratio >= 0.5:
                best, best_dist = index, dist
        if best is not None:
            free.remove(best)
            pairs.append({"original": hole, "current": current_holes[best],
                          "distance_px": round(best_dist, 2)})
    return pairs


def ncc_search(original: np.ndarray, current: np.ndarray, center: list[float],
               half_size: int = 20, radius: int = 12) -> dict:
    """Best normalized cross-correlation of an original patch in current."""
    cx, cy = int(round(center[0])), int(round(center[1]))
    x0, y0 = max(cx - half_size, 0), max(cy - half_size, 0)
    x1, y1 = min(cx + half_size, original.shape[1]), min(cy + half_size, original.shape[0])
    template = original[y0:y1, x0:x1].astype(np.float32)
    if template.size == 0 or template.std() < 1.0:
        return {"score": 0.0, "offset": [0, 0], "usable": False}
    template -= template.mean()
    norm = float(np.sqrt((template ** 2).sum()))
    if norm <= 0:
        return {"score": 0.0, "offset": [0, 0], "usable": False}
    template /= norm
    sx0, sy0 = max(x0 - radius, 0), max(y0 - radius, 0)
    sx1, sy1 = min(x1 + radius, current.shape[1]), min(y1 + radius, current.shape[0])
    search = current[sy0:sy1, sx0:sx1].astype(np.float32)
    best, offset = -1.0, [0, 0]
    for dy in range(search.shape[0] - template.shape[0] + 1):
        for dx in range(search.shape[1] - template.shape[1] + 1):
            window = search[dy:dy + template.shape[0], dx:dx + template.shape[1]]
            window = window - window.mean()
            denom = float(np.sqrt((window ** 2).sum()))
            score = float((template * window).sum() / denom) if denom > 0 else 0.0
            if score > best:
                best = score
                offset = [sx0 + dx - x0, sy0 + dy - y0]
    return {"score": round(best, 4), "offset": [int(v) for v in offset], "usable": True}


def region_quality(original: np.ndarray, current: np.ndarray, bbox: list[int]) -> dict:
    """Glare, shadow, blur, uniformity, and border evidence for one box."""
    x, y, w, h = bbox
    height, width = original.shape
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + w, width), min(y + h, height)
    if x1 <= x0 or y1 <= y0:
        return {"glare_original": 0.0, "glare_current": 0.0, "shadow_current": 0.0,
                "blur_original": 0.0, "blur_current": 0.0, "uniform_current": False,
                "touches_border": True, "pixels": 0}
    patch_o = original[y0:y1, x0:x1]
    patch_c = current[y0:y1, x0:x1]
    size = patch_o.size
    blur_o = cv2.Laplacian(patch_o, cv2.CV_64F).var()
    blur_c = cv2.Laplacian(patch_c, cv2.CV_64F).var()
    inner_o = patch_o[patch_o.shape[0] // 4:3 * patch_o.shape[0] // 4,
                      patch_o.shape[1] // 4:3 * patch_o.shape[1] // 4]
    inner_c = patch_c[patch_c.shape[0] // 4:3 * patch_c.shape[0] // 4,
                      patch_c.shape[1] // 4:3 * patch_c.shape[1] // 4]
    occluded = bool(inner_c.size > 0 and float(inner_c.std()) < 4.0
                    and float(np.median(inner_c.astype(float))) < 120.0
                    and float(inner_o.std()) >= 8.0)
    return {"glare_original": round(float((patch_o > 245).sum() / size), 3),
            "glare_current": round(float((patch_c > 245).sum() / size), 3),
            "shadow_current": round(float((patch_c < 25).sum() / size), 3),
            "blur_original": round(float(blur_o), 1),
            "blur_current": round(float(blur_c), 1),
            "uniform_current": occluded,
            "touches_border": bool(x <= 1 or y <= 1 or x + w >= width - 1 or y + h >= height - 1),
            "pixels": int(size)}
