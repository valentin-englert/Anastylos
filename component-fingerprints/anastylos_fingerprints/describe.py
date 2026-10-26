"""Fingerprint creation: deterministic visual descriptors + quality gates.

One function builds one record from an image crop. Descriptors cover
silhouette geometry, holes and notches, edges, aspect, calibrated size
(when valid frame metadata is supplied), color, normalized appearance,
rotation-tolerant ORB features, and explicitly supplied marking text.

Records carry a descriptor version and full provenance. Poor-quality
crops are recorded with usable=false and a reason; enrollment refuses
them and matching reports them as rejected.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import cv2
import numpy as np

DESCRIPTOR_VERSION = 1

MIN_SIDE_PX = 24
MIN_GRAY_STD = 2.0
MIN_BLUR_VARIANCE = 10.0
MAX_GLARE_FRACTION = 0.6
MIN_SILHOUETTE_FRACTION = 0.02
MAX_SILHOUETTE_FRACTION = 0.995
ORB_FEATURES = 64
ORB_KEPT = 48
THUMB_SIZE = 16
HIST_BINS = (16, 8, 8)


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


def _silhouette(gray):
    """Largest contour under either Otsu polarity.

    Contours touching the crop border lose to interior ones, so the
    frame edge never passes as a silhouette; when everything touches
    (a tight crop of a large part), the largest contour still wins.
    """
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
            if MIN_SILHOUETTE_FRACTION <= fraction <= MAX_SILHOUETTE_FRACTION:
                candidates.append((mask, contour, fraction))
    if not candidates:
        return None
    interior = [item for item in candidates if not _touches_border(item[1], gray.shape)]
    pool = interior or candidates
    return max(pool, key=lambda item: cv2.contourArea(item[1]))


def _hu_moments(contour):
    moments = cv2.moments(contour)
    hu = cv2.HuMoments(moments).flatten()
    scaled = []
    for value in hu:
        sign = -1.0 if value < 0 else 1.0
        magnitude = abs(float(value))
        scaled.append(float(round(-sign * np.log10(magnitude) if magnitude > 0 else 0.0, 6)))
    return scaled


def _shape(contour, fraction):
    area = cv2.contourArea(contour)
    hull = cv2.convexHull(contour)
    hull_area = max(cv2.contourArea(hull), 1e-9)
    defects = cv2.convexityDefects(contour, cv2.convexHull(contour, returnPoints=False))
    depth_limit = max(2.0, np.sqrt(area) * 0.05)
    strong = 0 if defects is None else sum(1 for row in defects[:, 0]
                                           if row[3] / 256.0 > depth_limit)
    approx = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
    rect = cv2.minAreaRect(contour)
    short, long = sorted(rect[1])
    ellipse_ratio = 1.0
    if len(contour) >= 5:
        try:
            _, (axis_a, axis_b), _ = cv2.fitEllipse(contour)
            if min(axis_a, axis_b) > 0:
                ellipse_ratio = max(axis_a, axis_b) / min(axis_a, axis_b)
        except cv2.error:
            pass
    _, _, box_w, box_h = cv2.boundingRect(contour)
    return {
        "area_fraction": round(fraction, 6),
        "solidity": round(area / hull_area, 6),
        "vertices": int(len(approx)),
        "defects": int(strong),
        "ellipse_ratio": round(float(ellipse_ratio), 4),
        "extent": round(area / max(box_w * box_h, 1), 6),
        "aspect": round(float(long / max(short, 1e-9)), 4),
        "long_px": round(float(long), 3),
        "short_px": round(float(short), 3),
    }


def _holes(mask, contour):
    filled = np.zeros_like(mask)
    cv2.drawContours(filled, [contour], -1, 255, -1)
    interior = cv2.bitwise_and(filled, cv2.bitwise_not(mask))
    contours, _ = cv2.findContours(interior, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    total = mask.shape[0] * mask.shape[1]
    areas = sorted((cv2.contourArea(c) / total for c in contours
                    if cv2.contourArea(c) / total > 0.0005), reverse=True)
    return {"count": len(areas),
            "area_fractions": [round(a, 6) for a in areas[:8]]}


def _edges(gray, mask):
    edges = cv2.Canny(gray, 60, 150)
    pixels = mask > 0
    density = float((edges[pixels] > 0).mean()) if pixels.any() else 0.0
    gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    magnitude = np.sqrt(gx * gx + gy * gy)
    mean_gradient = float(magnitude[pixels].mean()) if pixels.any() else 0.0
    return {"density": round(density, 6), "mean_gradient": round(mean_gradient, 3)}


def _color_hist(bgr, mask):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    pixels = mask > 0
    if not pixels.any():
        pixels = np.ones(mask.shape, bool)
    hists = []
    for channel, bins, limit in ((0, HIST_BINS[0], 180), (1, HIST_BINS[1], 256),
                                 (2, HIST_BINS[2], 256)):
        hist, _ = np.histogram(hsv[:, :, channel][pixels], bins=bins, range=(0, limit))
        hist = hist.astype(float)
        total = hist.sum()
        hists.append([round(float(v / total), 5) for v in hist] if total else [0.0] * bins)
    return {"h": hists[0], "s": hists[1], "v": hists[2],
            "mean_saturation": round(float(hsv[:, :, 1][pixels].mean()), 1)}


def _thumbnail(gray, mask, contour):
    """Silhouette-region thumbnail with background imputed to the mean.

    Rotation tolerance comes from the silhouette, geometry, color, and
    local components plus the rotation search at match time; the thumb
    itself stays a plain bbox resample so damaged silhouettes degrade
    it gracefully instead of misaligning it.
    """
    if contour is not None:
        x, y, w, h = cv2.boundingRect(contour)
        region = gray[y:y + h, x:x + w].astype(float)
        inside = mask[y:y + h, x:x + w] > 0
        fill = region[inside].mean() if inside.any() else region.mean()
        region = np.where(inside, region, fill)
    else:
        region = gray.astype(float)
    small = cv2.resize(region, (THUMB_SIZE, THUMB_SIZE), interpolation=cv2.INTER_AREA)
    return {"pixels": [int(v) for v in small.flatten()],
            "mean": round(float(small.mean()), 3), "std": round(float(small.std()), 3)}


def _orb(gray, mask):
    orb = cv2.ORB_create(nfeatures=ORB_FEATURES, edgeThreshold=5, patchSize=21,
                         fastThreshold=12)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    dilated = cv2.dilate(mask, kernel) if (mask > 0).any() else None
    keypoints, descriptors = orb.detectAndCompute(gray, dilated)
    if not keypoints or descriptors is None:
        return {"count": 0, "points": [], "descriptors": []}
    order = sorted(range(len(keypoints)),
                   key=lambda i: (-keypoints[i].response, keypoints[i].pt[0], keypoints[i].pt[1]))
    kept = order[:ORB_KEPT]
    return {
        "count": len(kept),
        "points": [[round(float(keypoints[i].pt[0]), 1),
                    round(float(keypoints[i].pt[1]), 1),
                    round(float(keypoints[i].angle), 1)] for i in kept],
        "descriptors": [descriptors[i].tobytes().hex() for i in kept],
    }


def _quality(gray, found):
    blur = cv2.Laplacian(gray, cv2.CV_64F)
    total = gray.shape[0] * gray.shape[1]
    quality = {
        "gray_std": round(float(gray.std()), 3),
        "blur_variance": round(float(blur.var()), 1),
        "glare_fraction": round(float((gray > 245).sum() / total), 4),
        "shadow_fraction": round(float((gray < 25).sum() / total), 4),
        "silhouette_fraction": round(found[2], 6) if found else 0.0,
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
        quality.update(usable=False, reason="no component silhouette found")
    return quality


def fingerprint(image, roi=None, frame_meta=None, marking=None,
                providers=(), source=None, record_id=None, now=None):
    """Build one versioned fingerprint record from an image crop."""
    if image is None or not isinstance(image, np.ndarray) or image.size == 0:
        raise ValueError("cannot fingerprint an empty image")
    x, y, w, h = validate_roi(image.shape, roi)
    meta = validate_frame_meta(frame_meta)
    crop = image[y:y + h, x:x + w]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    found = _silhouette(gray)
    quality = _quality(gray, found)
    mask = found[0] if found else np.zeros_like(gray)
    contour = found[1] if found else None
    shape = _shape(contour, found[2]) if found else None
    bgr = crop if crop.ndim == 3 else cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
    size_mm = None
    if meta is not None and shape is not None:
        ppm = meta["px_per_mm"]
        size_mm = [round(shape["long_px"] / ppm, 3), round(shape["short_px"] / ppm, 3)]
    embeddings = {}
    for provider in providers:
        vector = provider.embed(crop)
        if vector is not None:
            embeddings[provider.name] = {"version": provider.version(),
                                         "vector": [round(float(v), 6) for v in vector]}
    text = None
    if marking is not None:
        normalized = str(marking).strip().upper()
        text = {"raw": str(marking), "normalized": normalized} if normalized else None
    stamp = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "id": record_id,
        "descriptor_version": DESCRIPTOR_VERSION,
        "created_utc": stamp,
        "source": dict(source or {}, roi_xywh=[x, y, w, h],
                       crop_sha256=hashlib.sha256(np.ascontiguousarray(crop).tobytes()).hexdigest(),
                       crop_size_xy=[w, h]),
        "frame_meta": meta,
        "size_source": "calibrated" if size_mm else "uncalibrated",
        "size_mm": size_mm,
        "quality": quality,
        "hu": _hu_moments(contour) if found else None,
        "shape": shape,
        "holes": _holes(mask, contour) if found else None,
        "edges": _edges(gray, mask),
        "color": _color_hist(bgr, mask),
        "thumb": _thumbnail(gray, mask, contour),
        "orb": _orb(gray, mask),
        "text": text,
        "embeddings": embeddings,
    }


def silhouette_mask(image, roi=None):
    """Recompute the silhouette mask for diagnostics; None when absent."""
    x, y, w, h = validate_roi(image.shape, roi)
    crop = image[y:y + h, x:x + w]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    found = _silhouette(gray)
    if found is None:
        return None
    full = np.zeros(gray.shape, np.uint8)
    full[found[0] > 0] = 255
    canvas = np.zeros(image.shape[:2], np.uint8)
    canvas[y:y + h, x:x + w] = full
    return canvas
