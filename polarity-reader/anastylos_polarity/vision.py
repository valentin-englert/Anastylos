"""Deterministic polarity-marker pipeline: localize, detect, rank.

OpenCV and NumPy only, no learned models. A component body is
localized in the crop, geometric marker detectors observe candidate
markers (bands, stripes, dots, notches, keys, printed top marks),
and the pure rule interpreter ranks family-dependent hypotheses.
`detector` and `ranker` are adapter seams: pass a callable to
substitute a future segmentation/detection or ranking provider
without touching the pipeline.

Coordinate convention (also reported in every result): source-frame
pixels, origin at the top-left, +x right, +y down. Angles are
degrees clockwise from +x, in [0, 360).
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from anastylos_polarity.geometry import convert_geometry
from anastylos_polarity.interpret import FAMILIES, interpret

MIN_CROP_SIDE = 24
MIN_BODY_FRACTION = 0.02
MAX_BODY_FRACTION = 0.95


def angle_of(dx: float, dy: float) -> float:
    """Clockwise-from-+x angle in [0, 360) for a y-down frame."""
    return round(math.degrees(math.atan2(dy, dx)) % 360.0, 1)


def _largest_contour(mask: np.ndarray):
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    return max(contours, key=cv2.contourArea)


def _region_candidates(blurred: np.ndarray) -> list:
    """Threshold sweep, both polarities, gaps bridged by closing.

    Closing bridges internal marker splits (bands spanning the body)
    while preserving the outer outline, so one contour covers the
    whole body even when a marker touches its edges.
    """
    height, width = blurred.shape
    total = height * width
    level, _ = cv2.threshold(blurred, 0, 255,
                             cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mean, sd = float(blurred.mean()), float(blurred.std())
    median = float(np.median(blurred))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31))
    found = []
    levels = sorted({level, mean - sd, mean + sd, median - 25, median + 25})
    for thresh in levels:
        _, binary = cv2.threshold(blurred, thresh, 255, cv2.THRESH_BINARY)
        for mask in (binary, 255 - binary):
            closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
            contour = _largest_contour(closed)
            if contour is None:
                continue
            area = cv2.contourArea(contour)
            if not total * MIN_BODY_FRACTION < area < total * MAX_BODY_FRACTION:
                continue
            if cv2.pointPolygonTest(contour, (width / 2, height / 2),
                                    False) < 0:
                continue
            found.append((area, contour))
    return found


def _touches_border(contour, width: int, height: int) -> bool:
    x, y, w, h = cv2.boundingRect(contour)
    return x <= 1 or y <= 1 or x + w >= width - 1 or y + h >= height - 1


def _pick(candidates: list, width: int, height: int):
    if not candidates:
        return None
    inner = [item for item in candidates
             if not _touches_border(item[1], width, height)]
    pool = inner or candidates
    return max(pool, key=lambda item: item[0])[1]


def localize_body(gray: np.ndarray) -> tuple[dict | None, np.ndarray | None]:
    """Find the component body; pixel units, no dimension claims."""
    height, width = gray.shape
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    contour = _pick(_region_candidates(blurred), width, height)
    if contour is None:
        return None, None
    # Geometry comes from the fitted rectangle: internal marker edges
    # that touch the outline corrupt contour fills but not the fit.
    rect = cv2.minAreaRect(contour)
    (ccx, ccy), (bw, bh) = rect[0], rect[1]
    filled = np.zeros_like(gray)
    cv2.drawContours(filled, [cv2.boxPoints(rect).astype(np.int32)],
                     0, 255, -1)
    area = float(cv2.contourArea(cv2.boxPoints(rect).astype(np.int32)))
    corners = cv2.boxPoints(rect)
    edges = [corners[(i + 1) % 4] - corners[i] for i in range(4)]
    lengths = [float(np.linalg.norm(edge)) for edge in edges]
    long_edge = edges[int(np.argmax(lengths))]
    norm = float(np.linalg.norm(long_edge)) or 1.0
    long_unit = [round(float(long_edge[0] / norm), 4),
                 round(float(long_edge[1] / norm), 4)]
    long_len = max(float(bw), float(bh))
    short_len = max(min(float(bw), float(bh)), 1.0)
    x, y, w, h = cv2.boundingRect(corners.astype(np.int32))
    body = {"center_xy": [round(float(ccx), 1), round(float(ccy), 1)],
            "bbox_xywh": [int(x), int(y), int(w), int(h)],
            "size_px": [round(long_len, 1), round(short_len, 1)],
            "long_axis_xy": long_unit,
            "area_px": int(area),
            "median_tone": float(np.median(gray[filled > 0]))}
    return body, filled


def _long_direction(box: np.ndarray) -> tuple[np.ndarray, float, float]:
    corners = cv2.boxPoints(box) if isinstance(box, tuple) else box
    edges = [(corners[(i + 1) % 4] - corners[i]) for i in range(4)]
    lengths = [float(np.linalg.norm(edge)) for edge in edges]
    best = edges[int(np.argmax(lengths))]
    norm = float(np.linalg.norm(best)) or 1.0
    short = min(lengths) or 1.0
    return best / norm, float(np.linalg.norm(best)), short


def _tone_masks(gray: np.ndarray, body_mask: np.ndarray,
                body: dict) -> tuple[np.ndarray, np.ndarray, float, float]:
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    inner = cv2.erode(body_mask, kernel, iterations=1)
    vals = gray[inner > 0].astype(float)
    med = float(np.median(vals))
    sd = float(np.std(vals))
    span = max(28.0, 2.2 * sd)
    bright = np.zeros_like(gray)
    dark = np.zeros_like(gray)
    bright[(inner > 0) & (gray > med + span)] = 255
    dark[(inner > 0) & (gray < med - span)] = 255
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    bright = cv2.morphologyEx(
        cv2.morphologyEx(bright, cv2.MORPH_OPEN, kernel),
        cv2.MORPH_CLOSE, kernel)
    dark = cv2.morphologyEx(
        cv2.morphologyEx(dark, cv2.MORPH_OPEN, kernel),
        cv2.MORPH_CLOSE, kernel)
    return bright, dark, med, sd


def _contours(mask: np.ndarray):
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    return contours


def _rotated_fill(mask: np.ndarray, rect) -> float:
    """Mask support inside the rotated rect; rotation-invariant."""
    box = cv2.boxPoints(rect).astype(np.int32)
    canvas = np.zeros_like(mask)
    cv2.drawContours(canvas, [box], 0, 255, -1)
    inside = canvas > 0
    if not inside.any():
        return 0.0
    return round(float((mask[inside] > 0).sum() / inside.sum()), 3)


def _circularity(contour) -> float:
    area = cv2.contourArea(contour)
    peri = cv2.arcLength(contour, True)
    if peri <= 0:
        return 0.0
    return round(4 * math.pi * area / (peri * peri), 3)


def _project(rel, unit) -> float:
    return float(rel[0] * unit[0] + rel[1] * unit[1])


def _vetoed(bbox: list[int], vetoes: list[dict]) -> bool:
    return any(_iou(bbox, item["bbox_xywh"]) > 0.3 for item in vetoes)


def _detect_bars(gray, body, body_mask, masks, vetoes) -> list[dict]:
    found = []
    bcx, bcy = body["center_xy"]
    long_unit = np.array(body["long_axis_xy"], dtype=float)
    short_unit = np.array([-long_unit[1], long_unit[0]])
    long_len, short_len = body["size_px"]
    body_area = body["area_px"]
    for polarity, mask in masks:
        for contour in _contours(mask):
            area = cv2.contourArea(contour)
            frac = area / max(body_area, 1)
            if not 0.004 <= frac <= 0.30:
                continue
            rect = cv2.minAreaRect(contour)
            (ccx, ccy), _, _ = rect
            direction, length, width = _long_direction(
                cv2.boxPoints(rect))
            aspect = length / max(width, 1e-6)
            if aspect < 2.2:
                continue
            rel = np.array([ccx - bcx, ccy - bcy])
            axial = _project(rel, long_unit) / (long_len / 2)
            lateral = _project(rel, short_unit) / (short_len / 2)
            align = abs(float(np.dot(direction, long_unit)))
            contrast = abs(float(np.median(
                gray[cv2.drawContours(
                    np.zeros_like(gray), [contour], -1, 255,
                    -1) > 0].astype(float))) - body["median_tone"])
            fill = _rotated_fill(mask, rect)
            base = (min(1.0, contrast / 110.0)
                    * min(1.0, frac / 0.06) * min(1.0, fill / 0.8))
            common = {"centroid_xy": [round(float(ccx), 1),
                                      round(float(ccy), 1)],
                      "strength": 0.0,
                      "evidence": {"polarity": polarity,
                                   "contrast": round(contrast, 1),
                                   "area_fraction": round(frac, 4),
                                   "aspect": round(aspect, 2),
                                   "fill": fill,
                                   "axial": round(axial, 2),
                                   "lateral": round(lateral, 2)}}
            x, y, w, h = cv2.boundingRect(contour)
            common["bbox_xywh"] = [int(x), int(y), int(w), int(h)]
            if _vetoed(common["bbox_xywh"], vetoes):
                continue
            if (align < 0.55 and 0.40 <= abs(axial) <= 1.10
                    and abs(lateral) <= 0.45):
                span = min(1.0, (length / max(short_len, 1.0)) / 0.80)
                common["strength"] = round(float(base * span), 3)
                common["evidence"]["span"] = round(span, 3)
                found.append({**common, "marker_type": "band"})
            elif (align > 0.65 and 0.30 <= abs(lateral) <= 1.05
                    and length >= 0.40 * long_len):
                span = min(1.0, (length / max(long_len, 1.0)) / 0.65)
                common["strength"] = round(float(base * span), 3)
                common["evidence"]["span"] = round(span, 3)
                found.append({**common, "marker_type": "stripe"})
    return found


def _detect_dots(gray, body, body_mask, masks, vetoes) -> list[dict]:
    found = []
    bcx, bcy = body["center_xy"]
    long_len, short_len = body["size_px"]
    half_diag = math.hypot(long_len, short_len) / 2 or 1.0
    body_area = body["area_px"]
    for polarity, mask in masks:
        for contour in _contours(mask):
            area = cv2.contourArea(contour)
            frac = area / max(body_area, 1)
            if not 0.0008 <= frac <= 0.02:
                continue
            circ = _circularity(contour)
            if circ < 0.55:
                continue
            moments = cv2.moments(contour)
            if moments["m00"] <= 0:
                continue
            ccx = moments["m10"] / moments["m00"]
            ccy = moments["m01"] / moments["m00"]
            dist = math.hypot(ccx - bcx, ccy - bcy) / half_diag
            if dist < 0.20:
                continue
            contrast = abs(float(np.median(
                gray[cv2.drawContours(
                    np.zeros_like(gray), [contour], -1, 255,
                    -1) > 0].astype(float))) - body["median_tone"])
            strength = (min(1.0, contrast / 110.0)
                        * min(1.0, frac / 0.008) * circ)
            x, y, w, h = cv2.boundingRect(contour)
            if _vetoed([int(x), int(y), int(w), int(h)], vetoes):
                continue
            found.append(
                {"marker_type": "dot",
                 "bbox_xywh": [int(x), int(y), int(w), int(h)],
                 "centroid_xy": [round(float(ccx), 1),
                                 round(float(ccy), 1)],
                 "strength": round(float(strength), 3),
                 "evidence": {"polarity": polarity,
                              "contrast": round(contrast, 1),
                              "area_fraction": round(frac, 4),
                              "circularity": circ,
                              "radial": round(dist, 2)}})
    return found


def _background_tone(gray: np.ndarray, body_mask: np.ndarray) -> float:
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    grown = cv2.dilate(body_mask, kernel, iterations=3)
    outside = gray[grown == 0]
    return float(np.median(outside)) if outside.size else 128.0


def _detect_intrusions(gray, body, body_mask,
                       bg_med: float) -> tuple[list[dict], list[dict]]:
    """Background-tone bites connected to the body exterior.

    Mid-edge bites are package notches; corner cuts are keying.
    Enclosed blobs (dots, text) never connect outside and are ignored.
    """
    notches, keys = [], []
    _, short_len = body["size_px"]
    x, y, w, h = body["bbox_xywh"]
    corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    grown = cv2.dilate(body_mask, kernel, iterations=2)
    ring_out = (grown > 0) & (body_mask == 0)
    bg = (np.abs(gray.astype(float) - bg_med) < 25) & (grown > 0)
    count, labels = cv2.connectedComponents(bg.astype(np.uint8))
    inside = body_mask > 0
    depth_map = cv2.distanceTransform(inside.astype(np.uint8),
                                      cv2.DIST_L2, 3)
    for label in range(1, count):
        cells = labels == label
        if not (cells & ring_out).any():
            continue
        part = cells & inside
        # Deep pixels only: edge-blur rings share the component but
        # sit at depth 1-2 and would drag the centroid to the body.
        deep = part & (depth_map >= 3.0)
        if not deep.any():
            continue
        frac = deep.sum() / max(body["area_px"], 1)
        if not 0.003 <= frac <= 0.06:
            continue
        part = deep
        depth = float(depth_map[part].max())
        depth_frac = depth / max(short_len, 1.0)
        if not 0.04 <= depth_frac <= 0.30:
            continue
        ys, xs = np.nonzero(part)
        ccx, ccy = float(xs.mean()), float(ys.mean())
        corner_dist = min(math.hypot(ccx - cx, ccy - cy)
                          for cx, cy in corners)
        strength = min(1.0, frac / 0.012) * min(1.0, depth_frac / 0.08)
        marker = {"bbox_xywh": [int(xs.min()), int(ys.min()),
                                int(xs.max() - xs.min() + 1),
                                int(ys.max() - ys.min() + 1)],
                  "centroid_xy": [round(ccx, 1), round(ccy, 1)],
                  "strength": round(float(strength), 3),
                  "evidence": {"depth_px": round(depth, 1),
                               "depth_fraction": round(depth_frac, 3),
                               "area_fraction": round(float(frac), 4),
                               "corner_distance_px": round(corner_dist, 1)}}
        if corner_dist < 0.25 * min(w, h):
            keys.append({**marker, "marker_type": "key"})
        else:
            notches.append({**marker, "marker_type": "notch"})
    return notches, keys


def _detect_latches(gray, body, body_mask, masks, dots,
                    vetoes) -> list[dict]:
    found = []
    bcx, bcy = body["center_xy"]
    long_unit = np.array(body["long_axis_xy"], dtype=float)
    short_unit = np.array([-long_unit[1], long_unit[0]])
    long_len, short_len = body["size_px"]
    body_area = body["area_px"]
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    inner = cv2.erode(body_mask, kernel, iterations=2)
    for polarity, mask in masks:
        for contour in _contours(mask):
            area = cv2.contourArea(contour)
            frac = area / max(body_area, 1)
            if not 0.005 <= frac <= 0.10:
                continue
            canvas = np.zeros_like(body_mask)
            cv2.drawContours(canvas, [contour], -1, 255, -1)
            if (canvas > 0).sum() == 0 or ((canvas > 0) & (inner == 0)).any():
                continue
            rect = cv2.minAreaRect(contour)
            (ccx, ccy), _, _ = rect
            _, length, width = _long_direction(cv2.boxPoints(rect))
            aspect = length / max(width, 1e-6)
            if not 1.15 <= aspect < 3.0:
                continue
            probe = list(cv2.boundingRect(contour))
            if any(_iou(probe, dot["bbox_xywh"]) > 0.15 for dot in dots):
                continue
            if _vetoed(probe, vetoes):
                continue
            rel = np.array([ccx - bcx, ccy - bcy])
            axial = _project(rel, long_unit) / (long_len / 2)
            lateral = _project(rel, short_unit) / (short_len / 2)
            if max(abs(axial), abs(lateral)) < 0.45:
                continue
            contrast = abs(float(np.median(
                gray[cv2.drawContours(
                    np.zeros_like(gray), [contour], -1, 255,
                    -1) > 0].astype(float))) - body["median_tone"])
            strength = (min(1.0, contrast / 110.0)
                        * min(1.0, frac / 0.02))
            x, y, w, h = cv2.boundingRect(contour)
            found.append(
                {"marker_type": "key",
                 "bbox_xywh": [int(x), int(y), int(w), int(h)],
                 "centroid_xy": [round(float(ccx), 1),
                                 round(float(ccy), 1)],
                 "strength": round(float(strength), 3),
                 "evidence": {"polarity": polarity,
                              "contrast": round(contrast, 1),
                              "area_fraction": round(frac, 4),
                              "edge": round(max(abs(axial),
                                                abs(lateral)), 2)}})
    return found


def _detect_topmark(body, masks) -> list[dict]:
    small: list = []
    total = 0
    for _, mask in masks:
        for contour in _contours(mask):
            area = cv2.contourArea(contour)
            frac = area / max(body["area_px"], 1)
            if 0.0002 <= frac <= 0.010:
                small.append(contour)
                total += frac
    if len(small) < 6 or total > 0.08:
        return []
    union = np.concatenate(small)
    x, y, w, h = cv2.boundingRect(union)
    bx, by, bw, bh = body["bbox_xywh"]
    if w < 0.30 * bw and h < 0.30 * bh:
        return []
    rect = cv2.minAreaRect(union)
    (ccx, ccy), _, _ = rect
    direction, _, _ = _long_direction(cv2.boxPoints(rect))
    strength = min(1.0, len(small) / 10.0) * min(1.0, total / 0.02)
    return [{"marker_type": "topmark",
             "bbox_xywh": [int(x), int(y), int(w), int(h)],
             "centroid_xy": [round(float(ccx), 1), round(float(ccy), 1)],
             "strength": round(max(float(strength), 0.35), 3),
             "axis_xy": [round(float(direction[0]), 4),
                         round(float(direction[1]), 4)],
             "evidence": {"glyphs": len(small),
                          "area_fraction": round(total, 4),
                          "axis_only": True,
                          "direction_ambiguous": True}}]


def _iou(left: list[int], right: list[int]) -> float:
    ax, ay, aw, ah = left
    bx, by, bw, bh = right
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def _dedup(markers: list[dict]) -> list[dict]:
    kept: list[dict] = []
    for marker in sorted(markers, key=lambda item: -item["strength"]):
        if any(item["marker_type"] == marker["marker_type"]
               and _iou(item["bbox_xywh"], marker["bbox_xywh"]) > 0.4
               for item in kept):
            continue
        kept.append(marker)
    return kept


def _orient(markers: list[dict], body: dict) -> list[dict]:
    bcx, bcy = body["center_xy"]
    for index, marker in enumerate(markers, 1):
        marker["marker_id"] = f"m{index}"
        if marker["marker_type"] == "topmark":
            dx, dy = marker.pop("axis_xy")
            marker["orientation_vector_xy"] = [dx, dy]
            marker["orientation_angle_deg"] = angle_of(dx, dy)
            continue
        ccx, ccy = marker["centroid_xy"]
        dx, dy = ccx - bcx, ccy - bcy
        norm = math.hypot(dx, dy) or 1.0
        marker["orientation_vector_xy"] = [round(dx / norm, 4),
                                           round(dy / norm, 4)]
        marker["orientation_angle_deg"] = angle_of(dx, dy)
    return markers


def default_detector(gray: np.ndarray, body: dict,
                     body_mask: np.ndarray) -> list[dict]:
    """Observe geometric markers. Family-independent, pixel units."""
    bright, dark, _, _ = _tone_masks(gray, body_mask, body)
    masks = [("bright", bright), ("dark", dark)]
    bg_med = _background_tone(gray, body_mask)
    notches, chamfers = _detect_intrusions(gray, body, body_mask, bg_med)
    vetoes = notches + chamfers
    markers = _detect_bars(gray, body, body_mask, masks, vetoes)
    dots = _detect_dots(gray, body, body_mask, masks, vetoes)
    markers += dots
    markers += notches + chamfers
    markers += _detect_latches(gray, body, body_mask, masks, dots, vetoes)
    markers += _detect_topmark(body, masks)
    return _orient(_dedup(markers), body)


def _quality(gray: np.ndarray, body: dict, body_mask: np.ndarray,
             markers: list[dict]) -> dict:
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    grown = cv2.dilate(body_mask, kernel, iterations=4)
    outside = gray[grown == 0]
    bg_med = float(np.median(outside)) if outside.size else 128.0
    contrast = round(abs(body["median_tone"] - bg_med) / 255.0, 3)
    total = gray.size
    glare = round(float((gray > 245).sum() / total), 3)
    shadow = round(float(((gray < 25) & (body_mask == 0)).sum() / total), 3)
    blur = cv2.Laplacian(gray, cv2.CV_64F)
    support = round(max([item["strength"] for item in markers]
                        or [0.0]), 3)
    return {"contrast": contrast, "glare_fraction": glare,
            "shadow_fraction": shadow,
            "blur_variance": round(float(blur.var()), 1),
            "marker_support": support}


def _check_family(family: str | None) -> str | None:
    if family is None:
        return None
    normalized = str(family).strip().lower()
    if normalized == "unknown":
        return None
    if normalized not in FAMILIES:
        raise ValueError(f"unknown component family {family!r}; "
                         f"expected one of {sorted(FAMILIES)} or 'unknown'")
    return normalized


def _check_reference(reference: dict | None) -> dict | None:
    if reference is None:
        return None
    if not isinstance(reference, dict):
        raise ValueError("reference must be an object with kind/angle_deg")
    kind = reference.get("kind")
    angle = reference.get("angle_deg")
    if not isinstance(kind, str) or not kind.strip():
        raise ValueError("reference.kind must be a non-empty string")
    if isinstance(angle, bool) or not isinstance(angle, (int, float)) \
            or not math.isfinite(angle):
        raise ValueError("reference.angle_deg must be a finite number")
    return {"kind": kind.strip(), "angle_deg": float(angle) % 360.0}


def analyze(image: np.ndarray, roi: tuple[int, int, int, int] | None = None,
            family: str | None = None, reference: dict | None = None,
            frame_metadata: dict | None = None, detector=None,
            ranker=None) -> dict:
    """Detect polarity markers in one crop. JSON-serializable."""
    height, width = image.shape[:2]
    x, y, w, h = roi or (0, 0, width, height)
    if not (0 <= x < width and 0 <= y < height and w > 0 and h > 0
            and x + w <= width and y + h <= height):
        raise ValueError(f"ROI {(x, y, w, h)} is outside {width}x{height}")
    hint = _check_family(family)
    ref = _check_reference(reference)
    crop = image[y:y + h, x:x + w]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    convention = ("source-frame pixels; origin top-left; +x right; +y "
                  "down; angle_deg clockwise from +x in [0, 360)")
    if min(h, w) < MIN_CROP_SIDE:
        return {"status": "unavailable",
                "reason": f"crop {w}x{h} below {MIN_CROP_SIDE}px minimum",
                "coordinate_convention": convention,
                "body": None, "markers": [], "candidates": [],
                "installation": {"claimed": False,
                                 "explanation": "no observation to anchor"},
                "quality": None, "geometry_mm": None,
                "calibration_error": None, "explanation": "crop too small"}
    body, mask = localize_body(gray)
    if body is None or mask is None:
        return {"status": "unknown", "reason": "no component body localized",
                "coordinate_convention": convention,
                "body": None, "markers": [], "candidates": [],
                "installation": {"claimed": False,
                                 "explanation": "no marker observed"},
                "quality": {"contrast": 0.0}, "geometry_mm": None,
                "calibration_error": None,
                "explanation": "no component body found in crop"}
    detect = detector or default_detector
    observed = detect(gray, body, mask)
    if observed is None:
        return {"status": "unavailable",
                "reason": "marker provider returned no observation",
                "coordinate_convention": convention,
                "body": None, "markers": [], "candidates": [],
                "installation": {"claimed": False,
                                 "explanation": "no observation to anchor"},
                "quality": None, "geometry_mm": None,
                "calibration_error": None,
                "explanation": "detection provider unavailable"}
    quality = _quality(gray, body, mask, observed)
    for marker in observed:
        marker["bbox_xywh"][0] += x
        marker["bbox_xywh"][1] += y
        marker["centroid_xy"] = [round(marker["centroid_xy"][0] + x, 1),
                                 round(marker["centroid_xy"][1] + y, 1)]
    body["bbox_xywh"][0] += x
    body["bbox_xywh"][1] += y
    body["center_xy"] = [round(body["center_xy"][0] + x, 1),
                         round(body["center_xy"][1] + y, 1)]
    rank = ranker or interpret
    verdict = rank(observed, body, quality, hint, ref)
    geometry_mm, calibration_error = convert_geometry(
        observed, body, frame_metadata)
    return {**verdict, "coordinate_convention": convention,
            "body": body, "markers": observed, "quality": quality,
            "geometry_mm": geometry_mm,
            "calibration_error": calibration_error}
