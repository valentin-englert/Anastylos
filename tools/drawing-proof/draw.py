"""Technical line drawing of one part from its verified mask, in sheet millimetres.

Geometry classes (each drawn and listed separately):
- ``observed_outline``: the mask boundary, Douglas-Peucker simplified with a stated tolerance in mm.
- ``observed_regions``: outlines of tone regions inside the part (light membranes, dark rubber) taken from the
  same image; they are image regions, not interpreted features.
- ``unknown``: nothing is completed here; hidden or unmeasured properties are listed as text (height, far side).
No independent width/height fit: one uniform scale from sheet mm to drawing units.
"""

from __future__ import annotations

import cv2
import numpy as np


def simplify(contour_px: np.ndarray, px_per_mm: float, tolerance_mm: float) -> tuple[np.ndarray, float]:
    """Douglas-Peucker in px; returns the polygon and its measured max deviation from the dense boundary (mm)."""
    poly = cv2.approxPolyDP(contour_px.astype(np.float32).reshape(-1, 1, 2), tolerance_mm * px_per_mm, True).reshape(-1, 2)
    return poly.astype(float), max_deviation_mm(contour_px, poly, px_per_mm)


def max_deviation_mm(dense: np.ndarray, poly: np.ndarray, px_per_mm: float) -> float:
    a, b = poly, np.roll(poly, -1, axis=0)
    ab = b - a
    t = np.clip(((dense[:, None, :] - a[None]) * ab[None]).sum(-1) / np.maximum((ab * ab).sum(-1), 1e-9)[None], 0, 1)
    nearest = a[None] + t[..., None] * ab[None]
    return float(np.sqrt(((dense[:, None, :] - nearest) ** 2).sum(-1)).min(1).max() / px_per_mm)


def tone_regions(image: np.ndarray, mask: np.ndarray, rule: dict, px_per_mm: float) -> list[dict]:
    lightness = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)[:, :, 0]
    inner = cv2.erode(mask.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    regions = []
    for tone, selector in (("light", lightness >= rule["light_L"]), ("dark", lightness <= rule["dark_L"])):
        raw = (selector & inner).astype(np.uint8)
        raw = cv2.morphologyEx(raw, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        raw = cv2.morphologyEx(raw, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        contours, hierarchy = cv2.findContours(raw, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
        for index, contour in enumerate(contours):
            if hierarchy[0][index][3] != -1:
                continue  # inner contour: handled as a hole of its parent below
            area_mm2 = cv2.contourArea(contour) / px_per_mm ** 2
            if area_mm2 < rule["min_area_mm2"]:
                continue
            # Shadowed shell floor is as dark as the rubber pad but frayed; the pad is compact.
            hull_area = cv2.contourArea(cv2.convexHull(contour))
            if tone == "dark" and (area_mm2 < rule["dark_min_area_mm2"] or cv2.contourArea(contour) / max(hull_area, 1) < rule["dark_min_solidity"]):
                continue
            dense = contour.reshape(-1, 2).astype(float)
            poly, deviation = simplify(dense, px_per_mm, rule["tolerance_mm"])
            holes = []
            child = hierarchy[0][index][2]
            while child != -1:
                opening = contours[child]
                area, perimeter = cv2.contourArea(opening), cv2.arcLength(opening, True)
                # Openings where the membrane is absent; round ones only (scratches and glare are not holes).
                if area / px_per_mm ** 2 >= rule["min_hole_mm2"] and 4 * np.pi * area / max(perimeter ** 2, 1e-9) >= rule["min_hole_circularity"]:
                    holes.append(simplify(opening.reshape(-1, 2).astype(float), px_per_mm, rule["tolerance_mm"])[0])
                child = hierarchy[0][child][0]
            regions.append({"tone": tone, "area_mm2": round(area_mm2, 1), "polygon_px": poly, "max_dev_mm": deviation, "holes_px": holes})
    return regions
