"""Connector observation: deterministic mating-face feature extraction.

Builds one versioned observation record from an image crop: housing
geometry, visible contacts (pins vs cavities), contact layout and
pitch, keying and latch structures, orientation, color, and quality
indicators. The connector role (plug/socket) is reported only when
the pixels support it; anything else is unknown role.

Millimetre pitch is reported only with valid calibration metadata on
a frontal mating face. Pixel measurements are preserved as pixels
and never compared across images as sizes.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import cv2
import numpy as np

OBSERVATION_VERSION = 1

MIN_SIDE_PX = 24
MIN_GRAY_STD = 2.0
MIN_BLUR_VARIANCE = 10.0
MAX_GLARE_FRACTION = 0.6
MIN_HOUSING_FRACTION = 0.02
MAX_HOUSING_FRACTION = 0.995
MIN_FACE_SIDE_PX = 40
BLOB_CONTRAST = 40
MIN_BLOB_FRACTION = 0.0004
MAX_BLOB_FRACTION = 0.05
MIN_CIRCULARITY = 0.45


def validate_roi(image_shape, roi):
    """Split a crop or raise; same contract as the sibling readers."""
    height, width = image_shape[:2]
    x, y, w, h = roi or (0, 0, width, height)
    if not (0 <= x < width and 0 <= y < height and w > 0 and h > 0
            and x + w <= width and y + h <= height):
        raise ValueError(f"ROI {(x, y, w, h)} is outside {width}x{height}")
    return x, y, w, h


def validate_frame_meta(meta):
    """Accept pcb-ocr style frame metadata; only px_per_mm enables size."""
    if meta is None:
        return None
    if not isinstance(meta, dict):
        raise ValueError("frame metadata must be an object")
    ppm = meta.get("px_per_mm")
    if not isinstance(ppm, (int, float)) or ppm <= 0:
        raise ValueError("frame metadata needs a positive 'px_per_mm'")
    return {"px_per_mm": float(ppm), "view": meta.get("view"),
            "origin_frame_mm": meta.get("origin_frame_mm")}


def _touches_border(contour, shape):
    height, width = shape[:2]
    x, y, w, h = cv2.boundingRect(contour)
    return x <= 0 or y <= 0 or x + w >= width or y + h >= height


def _housing(gray):
    """Largest contour under either Otsu polarity, preferring interior."""
    if float(gray.std()) < MIN_GRAY_STD:
        return None
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    total = gray.shape[0] * gray.shape[1]
    candidates = []
    for mask in (binary, 255 - binary):
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            fraction = cv2.contourArea(contour) / total
            if MIN_HOUSING_FRACTION <= fraction <= MAX_HOUSING_FRACTION:
                candidates.append((mask, contour, fraction))
    if not candidates:
        return None
    interior = [item for item in candidates if not _touches_border(item[1], gray.shape)]
    pool = interior or candidates
    return max(pool, key=lambda item: cv2.contourArea(item[1]))


def _housing_shape(contour, fraction):
    area = cv2.contourArea(contour)
    hull = cv2.convexHull(contour)
    hull_area = max(cv2.contourArea(hull), 1e-9)
    approx = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
    rect = cv2.minAreaRect(contour)
    short, long = sorted(rect[1])
    x, y, w, h = cv2.boundingRect(contour)
    return {
        "bbox_xywh": [int(x), int(y), int(w), int(h)],
        "area_fraction": round(fraction, 6),
        "solidity": round(area / hull_area, 6),
        "vertices": int(len(approx)),
        "extent": round(area / max(w * h, 1), 6),
        "rect_extent": round(area / max(long * short, 1e-9), 6),
        "aspect": round(float(long / max(short, 1e-9)), 4),
        "angle_deg": round(float(rect[2]), 2),
        "long_px": round(float(long), 3),
        "short_px": round(float(short), 3),
    }


def _face_region(gray, mask, contour):
    filled = np.zeros_like(mask)
    cv2.drawContours(filled, [contour], -1, 255, -1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31))
    shrunk = cv2.erode(filled, kernel)
    return shrunk > 0


def _blobs(gray, face_pixels, face_median, bright):
    if bright:
        blobs = (gray > face_median + BLOB_CONTRAST) & face_pixels
    else:
        blobs = (gray < face_median - BLOB_CONTRAST) & face_pixels
    total = gray.shape[0] * gray.shape[1]
    found = []
    # Connected components, not external contours: blobs nested inside an
    # enclosing ring are children of its hole and RETR_EXTERNAL drops them.
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(
        blobs.astype(np.uint8), 8)
    for index in range(1, count):
        area = float(stats[index, cv2.CC_STAT_AREA])
        if not MIN_BLOB_FRACTION <= area / total <= MAX_BLOB_FRACTION:
            continue
        single = (labels == index).astype(np.uint8)
        contours, _ = cv2.findContours(single, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        perimeter = cv2.arcLength(contours[0], True)
        circularity = 4 * np.pi * area / max(perimeter * perimeter, 1e-9)
        if circularity < MIN_CIRCULARITY:
            continue
        x, y, w, h = (int(stats[index, kind]) for kind in
                      (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP,
                       cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT))
        found.append({"xy": [round(float(centroids[index][0]), 1),
                             round(float(centroids[index][1]), 1)],
                      "bbox_xywh": [x, y, w, h],
                      "area_px": round(area, 1),
                      "circularity": round(float(circularity), 3)})
    found.sort(key=lambda item: (item["xy"][1], item["xy"][0]))
    return found


def _canonical_frame(contour):
    """Housing-fixed coordinate frame: center, long/short axes and sizes.

    Long axis first, so the frame (up to dihedral flips, resolved at
    match time) does not depend on the viewing rotation.
    """
    (cx, cy), (w, h), angle = cv2.minAreaRect(contour)
    if w < h:
        w, h = h, w
        angle += 90.0
    theta = np.deg2rad(angle)
    axis_u = (float(np.cos(theta)), float(np.sin(theta)))
    axis_v = (-axis_u[1], axis_u[0])
    return (float(cx), float(cy)), axis_u, axis_v, float(w), float(h)


def _canon(point, frame):
    (cx, cy), axis_u, axis_v, w, h = frame
    dx, dy = point[0] - cx, point[1] - cy
    cu = (dx * axis_u[0] + dy * axis_u[1]) / max(w, 1e-9) + 0.5
    cv = (dx * axis_v[0] + dy * axis_v[1]) / max(h, 1e-9) + 0.5
    return [round(cu, 4), round(cv, 4)]


def _rows_and_pitch(blobs):
    if not blobs:
        return {"rows": 0, "per_row": [], "pitch_px": None, "grid": []}
    rows: list[list[dict]] = []
    for blob in sorted(blobs, key=lambda item: item["cuv"][1]):
        for row in rows:
            if abs(blob["cuv"][1] - row[0]["cuv"][1]) <= 0.12:
                row.append(blob)
                break
        else:
            rows.append([blob])
    for row in rows:
        row.sort(key=lambda item: item["cuv"][0])
    nearest = []
    for index, blob in enumerate(blobs):
        others = [np.hypot(blob["xy"][0] - other["xy"][0],
                            blob["xy"][1] - other["xy"][1])
                  for j, other in enumerate(blobs) if j != index]
        if others:
            nearest.append(min(others))
    pitch = round(float(np.median(nearest)), 2) if nearest else None
    grid = [[blob["cuv"] for blob in row] for row in rows]
    return {"rows": len(rows), "per_row": [len(row) for row in rows],
            "pitch_px": pitch, "grid": grid}


def _keying(contour, housing, frame, latch_points):
    defects = cv2.convexityDefects(contour, cv2.convexHull(contour, returnPoints=False))
    notches = []
    if defects is not None:
        limit = max(4.0, cv2.arcLength(contour, True) * 0.012)
        for row in defects[:, 0]:
            depth = row[3] / 256.0
            if depth < limit:
                continue
            point = [float(contour[row[2]][0][0]), float(contour[row[2]][0][1])]
            cuv = _canon(point, frame)
            if any(np.hypot(cuv[0] - lu, cuv[1] - lv) < 0.25 for lu, lv in latch_points):
                continue
            notches.append({"depth_px": round(float(depth), 1),
                            "at_xy": [int(point[0]), int(point[1])],
                            "cuv": cuv})
    notches.sort(key=lambda item: (item["cuv"][1], item["cuv"][0]))
    merged: list[dict] = []
    for notch in notches:
        if merged and abs(notch["cuv"][0] - merged[-1]["cuv"][0]) < 0.12 \
                and abs(notch["cuv"][1] - merged[-1]["cuv"][1]) < 0.12:
            merged[-1]["depth_px"] = max(merged[-1]["depth_px"], notch["depth_px"])
        else:
            merged.append(notch)
    return merged


def _latches(gray, mask, contour, housing):
    """Protruding tabs as convex bumps between adjacent defect pairs.

    A latch tab leaves two concave base corners with a convex bump
    between them along the contour; a keying wedge leaves concave
    corners with an inward dent instead, so wedges never qualify.
    Contour-intrinsic, hence rotation-tolerant and color-blind.
    """
    defects = cv2.convexityDefects(contour, cv2.convexHull(contour, returnPoints=False))
    tabs = []
    if defects is None or len(defects) < 1:
        return tabs
    moments = cv2.moments(contour)
    if moments["m00"] <= 0:
        return tabs
    centroid = np.array([moments["m10"] / moments["m00"],
                         moments["m01"] / moments["m00"]])
    points = contour.reshape(-1, 2).astype(float)
    total = len(points)
    floor = max(4.0, cv2.arcLength(contour, True) * 0.012)
    rows = sorted((row for row in defects[:, 0] if row[3] / 256.0 >= floor),
                  key=lambda row: int(row[2]))
    if len(rows) < 2:
        return tabs
    pairs = list(zip(rows, rows[1:])) + [(rows[-1], rows[0])]
    frame = _canonical_frame(contour)
    perimeter = cv2.arcLength(contour, True)
    for first, second in pairs:
        start, stop = int(first[2]), int(second[2])
        index = start
        segment = []
        while True:
            segment.append(index % total)
            if index % total == stop % total:
                break
            index += 1
            if len(segment) > total:
                break
        if len(segment) < 3:
            continue
        run = sum(float(np.hypot(*(points[segment[i + 1]] - points[segment[i]])))
                  for i in range(len(segment) - 1))
        if run > 0.35 * perimeter:
            continue
        chord_a, chord_b = points[start % total], points[stop % total]
        chord = chord_b - chord_a
        length = float(np.hypot(chord[0], chord[1]))
        if not 15.0 <= length <= 100.0:
            continue
        unit = chord / length
        outside = centroid - (chord_a + chord_b) / 2
        side = -1.0 if (outside[0] * -unit[1] + outside[1] * unit[0]) > 0 else 1.0
        best = 0.0
        scores = []
        for idx in segment[1:-1]:
            rel = points[idx] - chord_a
            deviation = side * (rel[0] * -unit[1] + rel[1] * unit[0])
            scores.append((deviation, points[idx]))
            best = max(best, deviation)
        if best > 8.0:
            reach = [float(np.hypot(point[0] - centroid[0], point[1] - centroid[1]))
                     for _, point in scores]
            peak = max(reach)
            tip = np.mean([point for (_, point), dist in zip(scores, reach)
                           if dist >= peak - 2.0], axis=0)
            tabs.append({"cuv": _canon([float(tip[0]), float(tip[1])], frame)})
    tabs.sort(key=lambda item: (item["cuv"][1], item["cuv"][0]))
    return tabs[:4]


def _colors(bgr, mask, contour, housing):
    filled = np.zeros_like(mask)
    cv2.drawContours(filled, [contour], -1, 255, -1)
    inside = filled > 0
    mean = bgr[inside].mean(axis=0) if inside.any() else np.zeros(3)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    hist, _ = np.histogram(hsv[:, :, 0][inside], bins=12, range=(0, 180))
    hist = hist.astype(float)
    total = hist.sum()
    return {"mean_bgr": [round(float(v), 1) for v in mean],
            "hue_hist": [round(float(v / total), 4) for v in hist] if total else [0.0] * 12,
            "mean_saturation": round(float(hsv[:, :, 1][inside].mean()), 1) if inside.any() else 0.0}


def _quality(gray, found, housing_shape):
    blur = cv2.Laplacian(gray, cv2.CV_64F)
    total = gray.shape[0] * gray.shape[1]
    quality = {
        "gray_std": round(float(gray.std()), 3),
        "blur_variance": round(float(blur.var()), 1),
        "glare_fraction": round(float((gray > 245).sum() / total), 4),
        "shadow_fraction": round(float((gray < 25).sum() / total), 4),
        "housing_fraction": round(found[2], 6) if found else 0.0,
        "usable": True,
        "reason": None,
    }
    if min(gray.shape[:2]) < MIN_SIDE_PX:
        quality.update(usable=False, reason=f"crop side below {MIN_SIDE_PX}px")
    elif quality["gray_std"] < MIN_GRAY_STD:
        quality.update(usable=False, reason="crop has no contrast")
    elif quality["blur_variance"] < MIN_BLUR_VARIANCE:
        quality.update(usable=False, reason="crop is too blurry")
    elif quality["glare_fraction"] > MAX_GLARE_FRACTION:
        quality.update(usable=False, reason="crop is mostly glare")
    elif found is None:
        quality.update(usable=False, reason="no connector housing found")
    return quality


def observe(image, roi=None, frame_meta=None, marking=None,
            source=None, record_id=None, now=None, assistants=()):
    """Build one versioned connector observation from an image crop."""
    if image is None or not isinstance(image, np.ndarray) or image.size == 0:
        raise ValueError("cannot observe an empty image")
    x, y, w, h = validate_roi(image.shape, roi)
    meta = validate_frame_meta(frame_meta)
    crop = image[y:y + h, x:x + w]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    bgr = crop if crop.ndim == 3 else cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
    found = _housing(gray)
    shape = _housing_shape(found[1], found[2]) if found else None
    quality = _quality(gray, found, shape)
    pins = cavities = []
    face_visible = False
    frontal = False
    face_side = 0
    frame = None
    if found:
        mask, contour, _ = found
        face_pixels = _face_region(gray, mask, contour)
        face_side = int(np.sqrt(face_pixels.sum()))
        face_median = float(np.median(gray[face_pixels])) if face_pixels.any() else 128.0
        pins = _blobs(gray, face_pixels, face_median, bright=True)
        cavities = _blobs(gray, face_pixels, face_median, bright=False)
        frame = _canonical_frame(contour)
        for blob in pins + cavities:
            blob["cuv"] = _canon(blob["xy"], frame)
        frontal = shape["solidity"] > 0.90 and shape["rect_extent"] > 0.80
        face_visible = bool(pins or cavities) and face_side >= MIN_FACE_SIDE_PX
    contacts = pins + cavities
    layout = _rows_and_pitch(contacts) if face_visible else _rows_and_pitch([])
    pitch_mm = None
    pitch_note = "pixel pitch only; not comparable across images"
    if layout["pitch_px"] is not None and meta is not None and frontal and face_visible:
        pitch_mm = round(layout["pitch_px"] / meta["px_per_mm"], 3)
        pitch_note = "frontal mating face with valid calibration"
    elif layout["pitch_px"] is not None and meta is not None and not (frontal and face_visible):
        pitch_note = "calibrated but measurement plane unsuitable (face not frontal/visible)"
    if pins and not cavities:
        role, role_evidence = "plug", \
            f"{len(pins)} protruding {'pin' if len(pins) == 1 else 'pins'}, no cavities"
    elif cavities and not pins:
        role, role_evidence = "socket", \
            f"{len(cavities)} {'cavity' if len(cavities) == 1 else 'cavities'}, no pins"
    elif pins and cavities:
        role, role_evidence = "unknown", "mixed pin and cavity evidence"
    else:
        role, role_evidence = "unknown", "no contact blobs visible"
    if not face_visible:
        role, role_evidence = "unknown", "mating face not sufficiently visible"
    text = None
    if marking is not None:
        normalized = str(marking).strip().upper()
        text = {"raw": str(marking), "normalized": normalized} if normalized else None
    latches = _latches(gray, found[0], found[1], shape) if found else []
    notches = _keying(found[1], shape, frame,
                      [tab["cuv"] for tab in latches]) if found else []
    stamp = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = {
        "id": record_id,
        "observation_version": OBSERVATION_VERSION,
        "created_utc": stamp,
        "source": dict(source or {}, roi_xywh=[x, y, w, h],
                       crop_sha256=hashlib.sha256(np.ascontiguousarray(crop).tobytes()).hexdigest(),
                       crop_size_xy=[w, h]),
        "frame_meta": meta,
        "quality": quality,
        "housing": shape,
        "mating_face_visible": face_visible,
        "frontal": frontal if found else False,
        "role": role,
        "role_evidence": role_evidence,
        "pins": pins if face_visible else [],
        "cavities": cavities if face_visible else [],
        "contact_count": len(contacts) if face_visible else 0,
        "layout": layout,
        "pitch_mm": pitch_mm,
        "pitch_note": pitch_note,
        "keying_notches": notches,
        "latches": latches,
        "color": _colors(bgr, found[0], found[1], shape) if found else None,
        "text": text,
    }
    notes = {}
    for assistant in assistants:
        try:
            advice = assistant.advise(record)
        except Exception as exc:  # noqa: BLE001 - advisors must not break observation
            advice = {"error": f"assistant failed: {exc}"}
        if advice is not None:
            notes[assistant.name] = {"version": assistant.version(), "notes": advice}
    record["assistant_notes"] = notes or None
    return record


def housing_mask(image, roi=None):
    """Recompute the housing mask for diagnostics; None when absent."""
    x, y, w, h = validate_roi(image.shape, roi)
    crop = image[y:y + h, x:x + w]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    found = _housing(gray)
    if found is None:
        return None
    canvas = np.zeros(image.shape[:2], np.uint8)
    canvas[y:y + h, x:x + w] = found[0]
    return canvas
