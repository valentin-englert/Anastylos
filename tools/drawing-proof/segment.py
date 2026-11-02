"""Part masks on a rectified sheet image, and how well a mask boundary sits on image edges.

Method A (``threshold``): the sheet is bright and neutral; the part is darker. Otsu on L* inside the part
region, closing, hole filling, largest component. A soft cast shadow is lighter than the grey part and falls
on the paper side of the Otsu split.
Method B (``grabcut``): GrabCut seeded from A with a probable band around A's boundary, so it can move the
edge by a few pixels toward the strongest colour boundary.
Manual corrections (``force_background`` / ``force_foreground`` polygons in sheet mm) are applied last and
recorded; an empty list means no manual step.
"""

from __future__ import annotations

import cv2
import numpy as np


def _largest_component(mask: np.ndarray) -> np.ndarray:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    if count <= 1:
        return np.zeros_like(mask, bool)
    best = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return labels == best


def fill_holes(mask: np.ndarray) -> np.ndarray:
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    filled = np.zeros(mask.shape, np.uint8)
    cv2.drawContours(filled, contours, -1, 1, thickness=cv2.FILLED)
    return filled.astype(bool)


def threshold_mask(image: np.ndarray, close_px: int) -> tuple[np.ndarray, dict]:
    lightness = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)[:, :, 0]
    level, dark = cv2.threshold(cv2.GaussianBlur(lightness, (5, 5), 0), 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_px, close_px))
    closed = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, kernel)
    mask = fill_holes(_largest_component(closed > 0))
    return mask, {"method": "threshold", "otsu_L": float(level), "close_px": close_px}


def shadow_candidates(image: np.ndarray, seed: np.ndarray, rule: dict) -> np.ndarray:
    """Cast shadow on paper inside the seed's outer band: warm (b*) and nearly textureless.

    Measured on FULL-01-P3 f300: shadow b* 137-138, local sd 0.9-2.7; shell rim b* 128-135, sd 6-25.
    """
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    k = (rule["texture_window_px"], rule["texture_window_px"])
    sd = np.sqrt(np.maximum(cv2.blur(gray * gray, k) - cv2.blur(gray, k) ** 2, 0))
    depth = cv2.distanceTransform(seed.astype(np.uint8), cv2.DIST_L2, 5)
    shadow = seed & (lab[:, :, 2] >= rule["min_b"]) & (sd <= rule["max_sd"]) & (depth <= rule["band_px"])
    return cv2.morphologyEx(shadow.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)) > 0


def grabcut_mask(image: np.ndarray, seed: np.ndarray, band_px: int, iterations: int,
                 background: np.ndarray | None = None) -> tuple[np.ndarray, dict]:
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * band_px + 1, 2 * band_px + 1))
    inner = cv2.erode(seed.astype(np.uint8), kernel) > 0
    outer = cv2.dilate(seed.astype(np.uint8), kernel) > 0
    labels = np.full(seed.shape, cv2.GC_BGD, np.uint8)
    labels[outer] = cv2.GC_PR_BGD
    labels[seed] = cv2.GC_PR_FGD
    labels[inner] = cv2.GC_FGD
    if background is not None:
        labels[background] = cv2.GC_BGD
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    cv2.grabCut(image, labels, None, bgd, fgd, iterations, cv2.GC_INIT_WITH_MASK)
    raw = (labels == cv2.GC_FGD) | (labels == cv2.GC_PR_FGD)
    mask = fill_holes(_largest_component(raw))
    return mask, {"method": "grabcut", "seed": "threshold", "band_px": band_px, "iterations": iterations}


def apply_corrections(mask: np.ndarray, corrections: dict, mm_to_px) -> np.ndarray:
    out = mask.astype(np.uint8)
    for key, value in (("force_background", 0), ("force_foreground", 1)):
        for polygon_mm in corrections.get(key, []):
            polygon = np.round(mm_to_px(np.array(polygon_mm, float))).astype(np.int32)
            cv2.fillPoly(out, [polygon], value)
    return out.astype(bool)


def boundary(mask: np.ndarray) -> np.ndarray:
    """Dense outer boundary (N, 2) in pixel coordinates, no simplification."""
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    return max(contours, key=cv2.contourArea).reshape(-1, 2).astype(float)


def edge_fit(image: np.ndarray, contour_px: np.ndarray, edge_low: int, edge_high: int) -> dict:
    """Distance from each boundary point to the nearest Canny edge pixel (px)."""
    gray = cv2.GaussianBlur(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (3, 3), 0)
    edges = cv2.Canny(gray, edge_low, edge_high)
    distance = cv2.distanceTransform((edges == 0).astype(np.uint8), cv2.DIST_L2, 5)
    xs = np.clip(np.round(contour_px[:, 0]).astype(int), 0, image.shape[1] - 1)
    ys = np.clip(np.round(contour_px[:, 1]).astype(int), 0, image.shape[0] - 1)
    d = distance[ys, xs]
    return {"median_px": float(np.median(d)), "p90_px": float(np.percentile(d, 90)), "max_px": float(d.max()),
            "share_within_2px": float(np.mean(d <= 2.0))}


def mask_agreement(a: np.ndarray, b: np.ndarray) -> dict:
    inter, union = np.logical_and(a, b).sum(), np.logical_or(a, b).sum()
    return {"iou": float(inter / union) if union else 0.0, "only_a_px": int((a & ~b).sum()), "only_b_px": int((b & ~a).sum())}
