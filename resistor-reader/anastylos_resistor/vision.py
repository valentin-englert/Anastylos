"""Minimal OpenCV pipeline: crop -> deskew -> band profile -> colors -> decode.

Works from a resistor crop or ROI. Bands are found as color deviations from
the body along the body axis; each band is classified against HSV prototypes
with scored alternatives. Weak evidence yields multiple candidates or an
explicit unknown; nothing here snaps to standard resistor values.
"""

from __future__ import annotations

import math
from itertools import product

import cv2
import numpy as np

from anastylos_resistor.decode import decode

# HSV prototypes in OpenCV ranges (H 0-179, S/V 0-255), with per-axis scales.
PROTOTYPES = {
    "black": ((0, 0, 25), (90, 90, 28)),
    "brown": ((9, 175, 130), (7, 70, 60)),
    "red": ((0, 220, 205), (6, 70, 60)),
    "orange": ((13, 235, 250), (6, 60, 50)),
    "yellow": ((29, 235, 235), (6, 60, 60)),
    "green": ((60, 200, 140), (12, 70, 60)),
    "blue": ((112, 215, 205), (10, 70, 60)),
    "violet": ((141, 160, 165), (12, 70, 60)),
    "gray": ((0, 10, 150), (90, 22, 45)),
    "white": ((0, 6, 246), (90, 22, 30)),
    "gold": ((19, 215, 222), (7, 60, 55)),
    "silver": ((0, 5, 198), (90, 16, 26)),
}

MIN_SCORE = 0.50
TIE_MARGIN = 0.10
MAX_CANDIDATES = 5
MAX_COMBOS = 96


def _largest_contour(mask: np.ndarray):
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    return max(contours, key=cv2.contourArea)


def _mask_from_channel(channel: np.ndarray) -> np.ndarray | None:
    blurred = cv2.GaussianBlur(channel, (5, 5), 0)
    _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (45, 15))
    total = channel.shape[0] * channel.shape[1]
    for mask in (binary, 255 - binary):
        closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        filled = _fill_holes(closed)
        contour = _largest_contour(filled)
        if contour is not None and total * 0.08 < cv2.contourArea(contour) < total * 0.92:
            return cv2.drawContours(np.zeros_like(filled), [contour], -1, 255, -1)
    return None


def _fill_holes(mask: np.ndarray) -> np.ndarray:
    flood = mask.copy()
    flood_mask = np.zeros((mask.shape[0] + 2, mask.shape[1] + 2), np.uint8)
    cv2.floodFill(flood, flood_mask, (0, 0), 128)
    holes = (flood != 128) & (mask == 0)
    filled = mask.copy()
    filled[holes] = 255
    return filled


def _body_mask(bgr: np.ndarray) -> np.ndarray:
    """Colorful body against a neutral background, else a gray fallback."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    mask = _mask_from_channel(hsv[:, :, 1])
    if mask is not None:
        return mask
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    mask = _mask_from_channel(gray)
    if mask is not None:
        return mask
    return np.full(gray.shape, 255, np.uint8)


def _deskew(crop: np.ndarray, mask: np.ndarray):
    contour = _largest_contour(mask)
    height0, width0 = crop.shape[:2]
    identity = cv2.getRotationMatrix2D((width0 / 2, height0 / 2), 0.0, 1.0)
    if contour is None:
        return crop, np.full(crop.shape[:2], 255, np.uint8), 0.0, identity
    (_, _), (width, height), angle = cv2.minAreaRect(contour)
    if width < height:
        angle += 90.0
    rotation = angle - 180.0 if angle > 90.0 else angle
    if abs(rotation) < 1.5:
        return crop, mask, 0.0, identity
    matrix = cv2.getRotationMatrix2D((width0 / 2, height0 / 2), rotation, 1.0)
    straight = cv2.warpAffine(crop, matrix, (width0, height0), borderMode=cv2.BORDER_REPLICATE)
    return straight, _body_mask(straight), round(rotation, 2), matrix


def _map_box(matrix: np.ndarray, box: tuple[int, int, int, int]) -> list[int]:
    inverse = cv2.invertAffineTransform(matrix)
    x, y, w, h = box
    corners = np.array([[[x, y], [x + w, y], [x + w, y + h], [x, y + h]]], dtype=np.float32)
    mapped = cv2.transform(corners, inverse)[0]
    x0, y0 = mapped[:, 0].min(), mapped[:, 1].min()
    x1, y1 = mapped[:, 0].max(), mapped[:, 1].max()
    return [int(round(x0)), int(round(y0)), int(round(x1 - x0)), int(round(y1 - y0))]


def _profile(straight: np.ndarray, mask: np.ndarray):
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        height, width = mask.shape
        rows = slice(height // 4, 3 * height // 4)
        cols = slice(width // 20, width - width // 20)
    else:
        x, y, width, height = cv2.boundingRect(max(contours, key=cv2.contourArea))
        rows = slice(y + height // 4, y + 3 * height // 4)
        cols = slice(x + width // 25, x + width - width // 25)
    strip = straight[rows, cols]
    hsv = cv2.cvtColor(strip, cv2.COLOR_BGR2HSV)
    profile = np.median(strip, axis=0).astype(np.uint8)
    profile_hsv = cv2.cvtColor(profile[None, :, :], cv2.COLOR_BGR2HSV)[0]
    glare = ((hsv[:, :, 1].astype(int) < 25) & (hsv[:, :, 2].astype(int) > 245))
    glare_fraction = glare.mean(axis=0)
    return profile_hsv, glare_fraction, (cols.start, rows.start, cols.stop - cols.start, rows.stop - rows.start)


def _rolling_median(values: np.ndarray, span: int) -> np.ndarray:
    pad = span // 2
    padded = np.pad(values, pad, mode="edge")
    return np.median(np.lib.stride_tricks.sliding_window_view(padded, span), axis=1)


def _band_runs(profile_hsv: np.ndarray) -> tuple[list[tuple[int, int]], dict]:
    width = profile_hsv.shape[0]
    smooth = cv2.GaussianBlur(profile_hsv[None, :, :].astype(np.float32), (21, 1), 0)[0]
    span = max(31, (width // 5) | 1)
    body = np.stack([_rolling_median(smooth[:, channel], span) for channel in range(3)], axis=1)
    hue_diff = np.abs(smooth[:, 0] - body[:, 0])
    hue_diff = np.minimum(hue_diff, 180 - hue_diff) / 90.0
    sat_diff = np.abs(smooth[:, 1] - body[:, 1]) / 255.0
    val_diff = np.abs(smooth[:, 2] - body[:, 2]) / 255.0
    bandness = np.sqrt((hue_diff * 2.2) ** 2 + (sat_diff * 1.2) ** 2 + (val_diff * 0.8) ** 2)
    median, mad = float(np.median(bandness)), float(np.median(np.abs(bandness - np.median(bandness))))
    threshold = median + max(3.0 * mad, 0.12)
    active = bandness > threshold
    min_width, max_width = max(3, width // 220), max(8, width // 5)
    runs: list[tuple[int, int]] = []
    start = None
    for index, flag in enumerate(active):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            runs.append((start, index))
            start = None
    if start is not None:
        runs.append((start, width))
    merged: list[list[int]] = []
    for run_start, run_end in runs:
        if merged and run_start - merged[-1][1] < min_width:
            merged[-1][1] = run_end
        else:
            merged.append([run_start, run_end])
    kept = [(run_start, run_end) for run_start, run_end in merged
            if min_width <= run_end - run_start <= max_width]
    return kept, {"threshold": round(threshold, 3), "median_bandness": round(median, 3)}


def classify(hsv: np.ndarray) -> list[tuple[str, float]]:
    """Score all IEC colors for one HSV sample; best first, sums to 1."""
    hsv = np.asarray(hsv, dtype=float)
    raw: dict[str, float] = {}
    for color, ((ph, ps, pv), (sh, ss, sv)) in PROTOTYPES.items():
        hue_diff = abs(hsv[0] - ph)
        hue_diff = min(hue_diff, 180 - hue_diff) / sh
        distance = math.sqrt(hue_diff ** 2 + ((hsv[1] - ps) / ss) ** 2 + ((hsv[2] - pv) / sv) ** 2)
        raw[color] = math.exp(-(distance / 0.9) ** 2)
    total = sum(raw.values())
    return sorted(((color, round(score / total, 3)) for color, score in raw.items()),
                  key=lambda item: -item[1])


def _band_options(profile_hsv: np.ndarray, glare_fraction: np.ndarray,
                  runs: list[tuple[int, int]]) -> list[list[tuple[str, float]]]:
    options = []
    for run_start, run_end in runs:
        segment = profile_hsv[run_start:run_end]
        clear = glare_fraction[run_start:run_end] < 0.6
        sample = np.median(segment[clear], axis=0) if clear.any() else np.median(segment, axis=0)
        ranked = classify(sample)
        glare_hit = float(glare_fraction[run_start:run_end].mean())
        ranked = [(color, round(score * (1.0 - 0.7 * glare_hit), 3)) for color, score in ranked]
        total = sum(score for _, score in ranked) or 1.0
        options.append([(color, round(score / total, 3)) for color, score in ranked])
    return options


def _shortlist(options: list[list[tuple[str, float]]]) -> list[list[tuple[str, float]]]:
    short = [[item for item in band if item[1] >= 0.12][:3] or band[:1] for band in options]
    size = 1
    for band in short:
        size *= len(band)
    depth = 3
    while size > MAX_COMBOS and depth > 1:
        depth -= 1
        short = [band[:depth] for band in short]
        size = 1
        for band in short:
            size *= len(band)
    return short


def _combine(short: list[list[tuple[str, float]]]) -> list[dict]:
    found: dict[tuple[tuple[str, ...], str], dict] = {}
    for combo in product(*short):
        colors = [color for color, _ in combo]
        for candidate in decode(colors, len(colors)):
            scores = [dict(combo).get(color, 0.0) for color in colors]
            mean = math.prod(scores) ** (1.0 / len(scores)) if all(s > 0 for s in scores) else 0.0
            key = (tuple(candidate["sequence"]), candidate["orientation"])
            if key not in found or mean > found[key]["score"]:
                found[key] = {**candidate, "score": round(mean, 3)}
    return sorted(found.values(), key=lambda item: -item["score"])


def analyze(image: np.ndarray, roi: tuple[int, int, int, int] | None = None) -> dict:
    """Run the full pipeline on a BGR image; returns a JSON-serializable result."""
    height, width = image.shape[:2]
    x, y, w, h = roi or (0, 0, width, height)
    if not (0 <= x < width and 0 <= y < height and w > 0 and h > 0 and x + w <= width and y + h <= height):
        raise ValueError(f"ROI {(x, y, w, h)} is outside {width}x{height}")
    crop = image[y:y + h, x:x + w]
    straight, mask, angle, matrix = _deskew(crop, _body_mask(crop))
    profile_hsv, glare_fraction, (px, py, pw, ph) = _profile(straight, mask)
    runs, signal = _band_runs(profile_hsv)
    options = _band_options(profile_hsv, glare_fraction, runs)
    bands = []
    for index, ((run_start, run_end), ranked) in enumerate(zip(runs, options)):
        inner = _map_box(matrix, (px + run_start, py, run_end - run_start, ph))
        inner[0] += x
        inner[1] += y
        bands.append({
            "index": index,
            "bbox_xywh": inner,
            "color_candidates": [{"color": color, "score": score} for color, score in ranked[:3]],
        })
    diagnostics = {
        "deskew_angle_deg": angle,
        "glare_fraction": round(float(glare_fraction.mean()), 3),
        **signal,
    }
    base = {"bands_detected": len(bands), "band_boxes": bands, "diagnostics": diagnostics,
            "experimental": True}
    if len(bands) not in (4, 5, 6):
        return {**base, "status": "unknown", "candidates": [],
                "reason": f"expected 4-6 bands, detected {len(bands)}"}
    candidates = _combine(_shortlist(options))
    if not candidates:
        return {**base, "status": "unknown", "candidates": [],
                "reason": "no color sequence decodes in either direction"}
    best = candidates[0]["score"]
    if best < MIN_SCORE:
        return {**base, "status": "unknown", "candidates": candidates[:MAX_CANDIDATES],
                "reason": f"best score {best} below {MIN_SCORE}"}
    rivals = [item for item in candidates[:MAX_CANDIDATES]
              if item["score"] >= best - TIE_MARGIN
              and item["resistance_ohms"] != candidates[0]["resistance_ohms"]]
    if rivals:
        return {**base, "status": "ambiguous", "candidates": [candidates[0], *rivals]}
    return {**base, "status": "ok", "candidates": [candidates[0]]}


def annotate(image: np.ndarray, result: dict) -> np.ndarray:
    """Draw band boxes, top colors, and the verdict onto a copy of the image."""
    view = image.copy()
    for band in result["band_boxes"]:
        bx, by, bw, bh = band["bbox_xywh"]
        cv2.rectangle(view, (bx, by), (bx + bw, by + bh), (0, 255, 0), 2)
        label = band["color_candidates"][0]["color"] if band["color_candidates"] else "?"
        cv2.putText(view, f"{band['index']}:{label}", (bx, max(15, by - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
    if result["candidates"]:
        top = result["candidates"][0]
        verdict = f"{result['status']}: {top['resistance']} +/-{top['tolerance_percent']}%"
    else:
        verdict = f"{result['status']}: {result.get('reason', '')}"
    cv2.putText(view, verdict, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 255), 2)
    return view
