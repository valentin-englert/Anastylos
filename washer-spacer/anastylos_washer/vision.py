"""Washer/spacer pipeline: segment, measure per view, rank, convert.

OpenCV and NumPy only, no learned models. `segmenter` and `ranker`
are adapter seams: pass callables to substitute a future
segmentation or classification provider without touching the
pipeline.

Coordinate convention (also reported in every result): source-frame
pixels, origin at the top-left, +x right, +y down. Angles are
degrees clockwise from +x in [0, 360). Millimetres appear only
with valid frame metadata (see geometry).
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from anastylos_washer.classify import classify
from anastylos_washer.geometry import convert_observations

MIN_CROP_SIDE = 24
MIN_SEGMENT_FRACTION = 0.01
MAX_SEGMENT_FRACTION = 0.80
VIEWS = ("top", "side")


def _bbox_iou(left, right) -> float:
    ax, ay, aw, ah = left
    bx, by, bw, bh = right
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def default_segmenter(gray: np.ndarray) -> list:
    """Candidate part contours via threshold sweep, both polarities."""
    height, width = gray.shape
    total = height * width
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    level, _ = cv2.threshold(blurred, 0, 255,
                             cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mean, sd = float(blurred.mean()), float(blurred.std())
    found: list = []
    for thresh in sorted({level, mean - sd, mean + sd}):
        _, binary = cv2.threshold(blurred, thresh, 255, cv2.THRESH_BINARY)
        for mask in (binary, 255 - binary):
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                           cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                area = cv2.contourArea(contour)
                if not total * MIN_SEGMENT_FRACTION < area < total * MAX_SEGMENT_FRACTION:
                    continue
                found.append(contour)
    kept: list = []
    for contour in sorted(found, key=cv2.contourArea, reverse=True):
        box = list(cv2.boundingRect(contour))
        if any(_bbox_iou(box, list(cv2.boundingRect(item))) > 0.7
               for item in kept):
            continue
        kept.append(contour)
    kept.sort(key=lambda item: (cv2.boundingRect(item)[1],
                                cv2.boundingRect(item)[0]))
    return kept


def _ellipse_residual(contour, center, axes, angle_deg) -> float:
    pts = contour[::4, 0, :].astype(float)
    dx, dy = pts[:, 0] - center[0], pts[:, 1] - center[1]
    rad = math.radians(angle_deg)
    ca, sa = math.cos(rad), math.sin(rad)
    xp, yp = dx * ca + dy * sa, -dx * sa + dy * ca
    a, b = max(axes[0] / 2, 1e-6), max(axes[1] / 2, 1e-6)
    return round(float(np.mean(np.abs(1 - (xp / a) ** 2 - (yp / b) ** 2))), 4)


def _fit_outer(contour) -> dict | None:
    if len(contour) < 5:
        return None
    (ccx, ccy), (w, h), angle = cv2.fitEllipse(contour)
    long_d, short_d = max(w, h), min(w, h)
    ecc = math.sqrt(max(0.0, 1 - (short_d / max(long_d, 1e-6)) ** 2))
    return {"center_xy": [round(float(ccx), 1), round(float(ccy), 1)],
            "long_d": round(float(long_d), 1),
            "short_d": round(float(short_d), 1),
            "mean_d": round(float((long_d + short_d) / 2), 1),
            "eccentricity": round(float(ecc), 3),
            "angle_deg": round(float(angle), 1),
            "rms": _ellipse_residual(contour, (ccx, ccy), (w, h), angle)}


def _isoperimetric(area: float, peri: float) -> float:
    if area <= 0:
        return 0.0
    return round(peri * peri / (4 * math.pi * area), 3)


def _deep_defects(contour, area: float) -> list:
    if len(contour) < 5:
        return []
    hull = cv2.convexHull(contour, returnPoints=False)
    defects = cv2.convexityDefects(contour, hull)
    if defects is None:
        return []
    floor = max(6.0, 0.05 * math.sqrt(area))
    found = []
    for row in defects[:, 0]:
        depth = float(row[3]) / 256.0
        if depth >= floor:
            far = contour[int(row[2])][0]
            found.append({"depth": round(depth, 1),
                          "point_xy": [round(float(far[0]), 1),
                                       round(float(far[1]), 1)]})
    found.sort(key=lambda item: -item["depth"])
    return found


def _defect_separation(defects: list, center) -> float | None:
    if len(defects) < 2:
        return None
    first = np.array(defects[0]["point_xy"]) - np.array(center)
    second = np.array(defects[1]["point_xy"]) - np.array(center)
    cosine = float(np.dot(first, second)
                   / max(np.linalg.norm(first) * np.linalg.norm(second),
                          1e-9))
    return round(math.degrees(math.acos(max(-1.0, min(1.0, cosine)))), 1)


def _part_masks(gray: np.ndarray, bbox, bg_med: float):
    x, y, w, h = bbox
    window = gray[y:y + h, x:x + w]
    _, binary = cv2.threshold(window, 0, 255,
                              cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    dark = window[binary == 0].astype(float)
    bright = window[binary == 255].astype(float)
    mid = (float(dark.mean()) + float(bright.mean())) / 2 if dark.size \
        and bright.size else float(np.median(window))
    if bg_med >= mid:
        part = (window.astype(float) < mid).astype(np.uint8) * 255
    else:
        part = (window.astype(float) > mid).astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    return cv2.morphologyEx(part, cv2.MORPH_OPEN, kernel), mid


def _find_holes(gray: np.ndarray, bbox, part: np.ndarray,
                bg_med: float, part_area: float) -> tuple[dict | None, int]:
    x, y, w, h = bbox
    window = gray[y:y + h, x:x + w].astype(float)
    void = (part == 0).astype(np.uint8) * 255
    count, labels = cv2.connectedComponents(void)
    border = np.zeros_like(part)
    border[0, :] = border[-1, :] = border[:, 0] = border[:, -1] = 255
    holes = []
    for label in range(1, count):
        cells = labels == label
        if (cells & (border > 0)).any():
            continue
        area = int(cells.sum())
        frac = area / max(part_area, 1)
        if not 0.005 <= frac <= 0.65:
            continue
        tone = float(np.median(window[cells]))
        if abs(tone - bg_med) > 35:
            continue
        ys, xs = np.nonzero(cells)
        contour, _ = cv2.findContours(cells.astype(np.uint8),
                                      cv2.RETR_EXTERNAL,
                                      cv2.CHAIN_APPROX_SIMPLE)
        if not contour:
            continue
        biggest = max(contour, key=cv2.contourArea)
        peri = cv2.arcLength(biggest, True)
        circ = 4 * math.pi * area / max(peri * peri, 1e-9)
        if circ < 0.5:
            continue
        hole = {"centroid_xy": [round(float(xs.mean()) + x, 1),
                                round(float(ys.mean()) + y, 1)],
                "area_px": area, "circularity": round(float(circ), 3)}
        if len(biggest) >= 5:
            (_, _), (wd, ht), _ = cv2.fitEllipse(biggest)
            hole["diameter_px"] = round(float((wd + ht) / 2), 1)
        else:
            _, radius = cv2.minEnclosingCircle(biggest)
            hole["diameter_px"] = round(float(2 * radius), 1)
        holes.append(hole)
    holes.sort(key=lambda item: -item["area_px"])
    primary = holes[0] if holes else None
    return primary, max(len(holes) - 1, 0)


def _ring_profile(part: np.ndarray, center, hole_r: float,
                  outer_r: float) -> tuple[float, float, float | None]:
    height, width = part.shape
    yy, xx = np.mgrid[0:height, 0:width].astype(float)
    dx, dy = xx - center[0], yy - center[1]
    dist = np.sqrt(dx * dx + dy * dy)
    phi = (np.degrees(np.arctan2(dy, dx)) + 360) % 360
    ring = (part > 0) & (dist > hole_r * 0.6) & (dist < outer_r * 1.05)
    bins = 72
    occupied = []
    for index in range(bins):
        lo, hi = index * 5.0, (index + 1) * 5.0
        sector = ring & (phi >= lo) & (phi < hi)
        band = ((dist > hole_r * 0.6) & (dist < outer_r * 1.05)
                & (phi >= lo) & (phi < hi))
        occupied.append(bool(sector.sum() > 0.3 * max(band.sum(), 1)))
    coverage = round(sum(occupied) / bins, 3)
    best, run, start, best_start = 0, 0, 0, None
    for index in range(2 * bins):
        if not occupied[index % bins]:
            if run == 0:
                start = index
            run += 1
            if run > best:
                best, best_start = run, start
        else:
            run = 0
    gap_deg = round(min(best, bins) * 5.0, 1)
    gap_at = round(((best_start or 0) % bins) * 5.0, 1) if best else None
    return coverage, gap_deg, gap_at


def _hex_calipers(vertices: np.ndarray) -> tuple[float, float]:
    """Across-corners and across-flats distances of a hex polygon."""
    corners = 0.0
    for i in range(len(vertices)):
        for j in range(i + 1, len(vertices)):
            corners = max(corners, float(np.linalg.norm(vertices[i]
                                                        - vertices[j])))
    centroid = vertices.mean(axis=0)
    gaps = []
    for i in range(len(vertices)):
        edge = vertices[(i + 1) % len(vertices)] - vertices[i]
        arm = centroid - vertices[i]
        cross = abs(float(edge[0] * arm[1] - edge[1] * arm[0]))
        gaps.append(cross / max(float(np.linalg.norm(edge)), 1e-9))
    apothem = min(gaps)
    return round(corners, 1), round(2 * apothem, 1)


def measure_top(gray: np.ndarray, contour, bbox, bg_med: float) -> dict:
    """Top-view geometry: outer ellipse, hole, shape, split evidence."""
    area = float(cv2.contourArea(contour))
    peri = float(cv2.arcLength(contour, True))
    outer = _fit_outer(contour)
    approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
    hull = cv2.convexHull(contour)
    solidity = area / max(float(cv2.contourArea(hull)), 1e-9)
    defects = _deep_defects(contour, area)
    part, _ = _part_masks(gray, bbox, bg_med)
    hole, extras = _find_holes(gray, bbox, part, bg_med, area)
    center = outer["center_xy"] if outer else [0.0, 0.0]
    if outer:
        local = [center[0] - bbox[0], center[1] - bbox[1]]
        hole_r = (hole["diameter_px"] / 2) if hole else outer["mean_d"] * 0.2
        coverage, gap_deg, gap_at = _ring_profile(
            part, local, hole_r, outer["mean_d"] / 2)
    else:
        coverage, gap_deg, gap_at = 0.0, 0.0, None
    concentricity = None
    if hole and outer:
        offset = math.hypot(hole["centroid_xy"][0] - center[0],
                            hole["centroid_xy"][1] - center[1])
        concentricity = round(offset / max(outer["mean_d"] / 2, 1e-9), 3)
    quotient = _isoperimetric(area, peri)
    hexagon = 5 <= len(approx) <= 7 and 1.04 <= quotient <= 1.26
    calipers: dict = {}
    if hexagon:
        across_corners, across_flats = _hex_calipers(approx.reshape(-1, 2))
        calipers = {"across_corners_px": across_corners,
                    "across_flats_px": across_flats}
    if outer is not None and gap_deg >= 12.0 and hole is None:
        # Ellipse fits shrink on C-shapes; the enclosing circle holds.
        _, radius = cv2.minEnclosingCircle(contour)
        outer["mean_d"] = round(float(2 * radius), 1)
        outer["long_d"] = outer["short_d"] = outer["mean_d"]
    return {"outer": outer, "hole": hole, "holes_extra": extras,
            "hex_calipers": calipers,
            "concentricity": concentricity,
            "isoperimetric_q": quotient,
            "vertices": int(len(approx)),
            "hexagon_likely": bool(hexagon),
            "solidity": round(float(solidity), 3),
            "coverage": coverage, "gap_deg": gap_deg, "gap_at_deg": gap_at,
            "defects": defects[:4],
            "defect_separation_deg": _defect_separation(defects, center),
            "area_px": int(area)}


def _order_box(box: np.ndarray) -> np.ndarray:
    pts = box.reshape(4, 2).astype(float)
    total = pts.sum(axis=1)
    diff = np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(total)], pts[np.argmin(diff)],
                     pts[np.argmax(total)], pts[np.argmax(diff)]])


def _straighten(gray: np.ndarray, box: np.ndarray):
    ordered = _order_box(box)
    top = float(np.linalg.norm(ordered[1] - ordered[0]))
    side = float(np.linalg.norm(ordered[3] - ordered[0]))
    width, height = max(8, int(round(top))), max(8, int(round(side)))
    dst = np.float32([[0, 0], [width - 1, 0], [width - 1, height - 1],
                      [0, height - 1]])
    warp = cv2.getPerspectiveTransform(ordered.astype(np.float32), dst)
    flat = cv2.warpPerspective(gray, warp, (width, height),
                               flags=cv2.INTER_LINEAR, borderValue=0)
    if width < height:
        flat = cv2.rotate(flat, cv2.ROTATE_90_CLOCKWISE)
        width, height = height, width
    return flat, width, height


def _band_interfaces(column_fill: np.ndarray) -> int:
    solid = column_fill > 0.8
    gap = column_fill < 0.5
    interfaces = 0
    inside = False
    for index in range(len(column_fill)):
        if gap[index] and not inside:
            left = solid[max(0, index - 6):index].any()
            right = solid[index + 1:index + 7].any()
            if left and right:
                interfaces += 1
                inside = True
        elif solid[index]:
            inside = False
    return interfaces


def measure_side(gray: np.ndarray, contour, bbox, bg_med: float) -> dict:
    """Side-view geometry: length, thickness, bands, end openings."""
    area = float(cv2.contourArea(contour))
    peri = float(cv2.arcLength(contour, True))
    rect = cv2.minAreaRect(contour)
    (_, _), (w, h), _ = rect
    length, thick = max(float(w), float(h)), min(float(w), float(h))
    aspect = length / max(thick, 1e-9)
    box = cv2.boxPoints(rect)
    approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
    edge_ratio: float | None = None
    if len(approx) == 4:
        corners = approx.reshape(4, 2).astype(float)
        sides = [float(np.linalg.norm(corners[(i + 1) % 4] - corners[i]))
                 for i in range(4)]
        sides.sort()
        # Short-pair ratio exposes trapezoid taper in both orientations.
        edge_ratio = round(sides[0] / max(sides[1], 1e-9), 3)
    flat, width, height = _straighten(gray, box)
    if bg_med >= 128:
        part = (flat < bg_med - 25).astype(float)
    else:
        part = (flat > bg_med + 25).astype(float)
    column_fill = part.mean(axis=0)
    row_fill = part.mean(axis=1)
    interfaces_long = _band_interfaces(column_fill)
    interfaces_transverse = _band_interfaces(row_fill)
    interfaces = interfaces_long + interfaces_transverse
    fill = round(float(part.mean()), 3)
    part_med = float(np.median(flat[part > 0])) if (part > 0).any() else bg_med
    openings: int | None = None
    if thick >= 40:
        openings = 0
        margin_w = max(4, int(width * 0.12))
        margin_h = max(4, int(height * 0.20))
        limit = min(part_med - 25, 60)
        for zone in (flat[margin_h:height - margin_h, 0:margin_w],
                     flat[margin_h:height - margin_h, width - margin_w:width]):
            dark = (zone.astype(float) < limit).mean()
            if dark > 0.03:
                openings += 1
    flats = False
    if thick >= 40:
        thirds = [float(np.median(strip[strip > 0]))
                  if (strip > 0).any() else part_med
                  for strip in np.array_split(flat * (part > 0), 3, axis=0)]
        flats = max(thirds) - min(thirds) > 15
    return {"length_px": round(length, 1), "thickness_px": round(thick, 1),
            "aspect": round(float(aspect), 2),
            "rect_fill": fill, "vertices": int(len(approx)),
            "edge_ratio": edge_ratio, "band_interfaces": interfaces,
            "interfaces_long": interfaces_long,
            "interfaces_transverse": interfaces_transverse,
            "openings": openings, "flats_likely": bool(flats),
            "area_px": int(area)}


def _segment_quality(gray: np.ndarray, contour, bbox) -> dict:
    canvas = np.zeros_like(gray)
    cv2.drawContours(canvas, [contour], -1, 255, -1)
    inside = gray[canvas > 0].astype(float)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    grown = cv2.dilate(canvas, kernel, iterations=4)
    outside = gray[grown == 0].astype(float)
    contrast = 0.0
    if inside.size and outside.size:
        contrast = round(abs(float(np.median(inside))
                             - float(np.median(outside))) / 255.0, 3)
    x, y, w, h = bbox
    window = gray[y:y + h, x:x + w]
    blur = cv2.Laplacian(window, cv2.CV_64F)
    return {"contrast": contrast,
            "glare_fraction": round(float((window > 245).mean()), 3),
            "shadow_fraction": round(float((window < 25).mean()), 3),
            "blur_variance": round(float(blur.var()), 1)}


def _check_view(view: str | None) -> str:
    if view not in VIEWS:
        raise ValueError(f"view must be one of {list(VIEWS)}, got {view!r}")
    return view


def _check_family(family: str | None) -> str | None:
    from anastylos_washer.classify import FAMILIES
    if family is None:
        return None
    normalized = str(family).strip().lower()
    if normalized == "unknown":
        return None
    if normalized not in FAMILIES:
        raise ValueError(f"unknown component family {family!r}; expected "
                         f"one of {sorted(FAMILIES)} or 'unknown'")
    return normalized


def _merge_collinear_side(gray: np.ndarray, contours: list) -> list:
    """Rejoin thin side bars split by a full-height slit.

    Only horizontal-collinear thin pairs merge, so vertical stacks
    of separable washers are never fused.
    """
    boxes = [list(cv2.boundingRect(item)) for item in contours]
    order = sorted(range(len(contours)), key=lambda i: boxes[i][0])
    used = [False] * len(contours)
    merged: list = []
    bg_med = float(np.median(gray))
    for pos, index in enumerate(order):
        if used[index]:
            continue
        group = [index]
        x, y, w, h = boxes[index]
        if w <= 2.5 * h:
            merged.append(contours[index])
            used[index] = True
            continue
        for other in order[pos + 1:]:
            if used[other]:
                continue
            ox, oy, ow, oh = boxes[other]
            if ow <= 2.5 * oh:
                continue
            overlap = min(y + h, oy + oh) - max(y, oy)
            if overlap < 0.6 * min(h, oh):
                continue
            gap = ox - (x + w)
            if not 1 <= gap <= 0.25 * (w + ow):
                continue
            group.append(other)
            used[other] = True
            w = ox + ow - x
            y, h = min(y, oy), max(y + h, oy + oh) - min(y, oy)
        used[index] = True
        if len(group) == 1:
            merged.append(contours[index])
            continue
        pad = 4
        rx, ry = max(x - pad, 0), max(y - pad, 0)
        roi = gray[ry:y + h + pad, rx:x + w + pad]
        level, _ = cv2.threshold(roi, 0, 255,
                                 cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        mask = (roi < level) if bg_med >= 128 else (roi > level)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21))
        closed = cv2.morphologyEx(mask.astype(np.uint8) * 255,
                                  cv2.MORPH_CLOSE, kernel)
        found, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL,
                                    cv2.CHAIN_APPROX_SIMPLE)
        if not found:
            merged.extend(contours[i] for i in group)
            continue
        best = max(found, key=cv2.contourArea)
        best[:, 0, 0] += rx
        best[:, 0, 1] += ry
        merged.append(best)
    merged.sort(key=lambda item: (cv2.boundingRect(item)[1],
                                  cv2.boundingRect(item)[0]))
    return merged


def _mark_stacked(observations: list[dict]) -> None:
    """Cross-reference vertically adjacent thin side profiles."""
    for left in observations:
        if left["geometry"].get("limits", {}).get("shape") != "thin bar":
            continue
        lx, ly, lw, lh = left["bbox_xywh"]
        for right in observations:
            if right is left:
                continue
            if right["geometry"].get("limits", {}).get("shape") != "thin bar":
                continue
            rx, ry, rw, rh = right["bbox_xywh"]
            overlap = min(lx + lw, rx + rw) - max(lx, rx)
            if overlap < 0.7 * min(lw, rw):
                continue
            gap = max(0, max(ly, ry) - min(ly + lh, ry + rh))
            if gap < 1.2 * min(lh, rh):
                limits = left["geometry"]["limits"]
                limits.setdefault("stacked_with", []).append(
                    right["observation_id"])
                left["explanation"] += ("; vertically adjacent parts "
                                        "suggest stacking")


def analyze(image: np.ndarray, view: str | None,
            roi: tuple[int, int, int, int] | None = None,
            family: str | None = None, frame_metadata: dict | None = None,
            segmenter=None, ranker=None) -> dict:
    """Segment parts, measure per view, rank families. JSON-serializable."""
    height, width = image.shape[:2]
    x, y, w, h = roi or (0, 0, width, height)
    if not (0 <= x < width and 0 <= y < height and w > 0 and h > 0
            and x + w <= width and y + h <= height):
        raise ValueError(f"ROI {(x, y, w, h)} is outside {width}x{height}")
    checked_view = _check_view(view)
    hint = _check_family(family)
    crop = image[y:y + h, x:x + w]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    convention = ("source-frame pixels; origin top-left; +x right; +y "
                  "down; angle_deg clockwise from +x in [0, 360)")
    if min(h, w) < MIN_CROP_SIDE:
        return {"status": "unavailable",
                "reason": f"crop {w}x{h} below {MIN_CROP_SIDE}px minimum",
                "coordinate_convention": convention, "view": checked_view,
                "observations": [], "quality": None, "geometry_mm": None,
                "calibration_error": None,
                "explanation": "crop too small to assess"}
    segment = segmenter or default_segmenter
    contours = segment(gray)
    if contours is not None and checked_view == "side":
        contours = _merge_collinear_side(gray, contours)
    if contours is None:
        return {"status": "unavailable",
                "reason": "segmentation provider returned no observation",
                "coordinate_convention": convention, "view": checked_view,
                "observations": [], "quality": None, "geometry_mm": None,
                "calibration_error": None,
                "explanation": "segmentation provider unavailable"}
    bg_med = float(np.median(gray))
    segments = []
    for contour in contours:
        bx, by, bw, bh = (int(v) for v in cv2.boundingRect(contour))
        bbox = [bx, by, bw, bh]
        borders = (bx <= 1) + (by <= 1) + (bx + bw >= w - 1) \
            + (by + bh >= h - 1)
        truncated = borders > 0
        if truncated:
            canvas = np.zeros_like(gray)
            cv2.drawContours(canvas, [contour], -1, 255, -1)
            seg_med = float(np.median(gray[canvas > 0]))
            tone_gap = abs(seg_med - bg_med)
            if tone_gap < 30 or (borders >= 2 and tone_gap < 60):
                continue
        if checked_view == "top":
            geometry = measure_top(gray, contour, bbox, bg_med)
        else:
            geometry = measure_side(gray, contour, bbox, bg_med)
        segments.append({"contour": contour, "bbox_xywh": bbox,
                         "geometry": geometry,
                         "truncated": truncated,
                         "quality": _segment_quality(gray, contour, bbox)})
    rank = ranker or classify
    observations = rank(segments, checked_view, hint)
    if checked_view == "side":
        _mark_stacked(observations)
    for obs in observations:
        obs["bbox_xywh"][0] += x
        obs["bbox_xywh"][1] += y
        for key in ("center_xy", "hole_center_xy"):
            if obs["geometry"].get(key):
                obs["geometry"][key] = [
                    round(obs["geometry"][key][0] + x, 1),
                    round(obs["geometry"][key][1] + y, 1)]
    geometry_mm, calibration_error = convert_observations(
        observations, frame_metadata)
    statuses = [obs["status"] for obs in observations]
    if not observations:
        status, reason = "unknown", "no part-like segments found"
    elif "ambiguous" in statuses:
        status, reason = "ambiguous", "at least one observation unresolved"
    elif "ok" in statuses:
        status, reason = "ok", None
    else:
        status, reason = "unknown", "no observation classified"
    return {"status": status, "reason": reason,
            "coordinate_convention": convention, "view": checked_view,
            "observations": observations,
            "quality": {"segments": len(segments)},
            "geometry_mm": geometry_mm,
            "calibration_error": calibration_error,
            "explanation": f"{len(observations)} observation(s) in "
                           f"{checked_view} view"}
