"""Deterministic calibrated screw measurement, top and side views.

OpenCV and NumPy only, no learned models. Top view measures the visible
head disc (diameter, outline, center); side view measures the visible
silhouette (overall length, shaft length/diameter, head boundary) where
edges segment reliably. Pixels become millimetres only through a valid
calibration; otherwise millimetre fields stay null with reasons.

`segmenter` is an adapter seam: a callable ``(gray, context) -> mask``
returning screw pixels, or None to keep the built-in segmentation. A
future provider plugs in without touching the pipeline.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from anastylos_screw_measure.calibration import blank, to_millimetres

MODES = ("top", "side")

NOT_ESTIMATED = {
    "nominal_specification": "visible size is not a nominal size; standards lookup is out of scope",
    "thread_pitch": "threads are not resolved by this pipeline",
    "thread_type": "threads are not resolved by this pipeline",
    "drive_size": "drive metrology is not implemented",
}

MIN_RIM_SUPPORT = 0.30
WEAK_RIM_SUPPORT = 0.50
ROUND_AXES_RATIO = 0.92
SEVERE_AXES_RATIO = 0.75
MIN_CONTRAST = 0.08
STRONG_GLARE = 0.25
MIN_EDGE_SUPPORT = 0.35
WEAK_EDGE_SUPPORT = 0.60
HEAD_STEP_RATIO = 1.25
SIDE_MIN_ASPECT = 1.3
PX_FLOOR = 0.5


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


def _crop_roi(image: np.ndarray, roi) -> tuple[np.ndarray, dict]:
    height, width = image.shape[:2]
    x, y, w, h = roi if roi is not None else (0, 0, width, height)
    if not (0 <= x < width and 0 <= y < height and w > 0 and h > 0
            and x + w <= width and y + h <= height):
        raise ValueError(f"ROI {(x, y, w, h)} is outside {width}x{height}")
    return image[y:y + h, x:x + w], {"x": x, "y": y, "w": w, "h": h}


def _normalize(gray: np.ndarray) -> np.ndarray:
    """Flatten broad illumination by local-mean division."""
    sigma = max(10.0, min(gray.shape) / 10.0)
    local = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), sigma)
    return np.clip(gray.astype(np.float32) / (local + 1.0) * 128.0, 0, 255).astype(np.uint8)


def _edges(gray: np.ndarray) -> np.ndarray:
    return cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 50, 150)


def _rim_support(edges: np.ndarray, cx: float, cy: float, radius: float) -> float:
    angles = np.deg2rad(np.arange(0, 360, 2))
    xs = np.clip((cx + radius * np.cos(angles)).astype(int), 0, edges.shape[1] - 1)
    ys = np.clip((cy + radius * np.sin(angles)).astype(int), 0, edges.shape[0] - 1)
    hit = sum(1 for x, y in zip(xs, ys)
              if edges[max(0, y - 2):y + 3, max(0, x - 2):x + 3].any())
    return round(hit / len(xs), 3)


def _kasa_fit(xs: np.ndarray, ys: np.ndarray) -> tuple[float, float, float]:
    design = np.stack([xs, ys, np.ones_like(xs)], axis=1)
    target = -(xs ** 2 + ys ** 2)
    coef, *_ = np.linalg.lstsq(design, target, rcond=None)
    cx, cy = -coef[0] / 2.0, -coef[1] / 2.0
    radius = math.sqrt(max((coef[0] ** 2 + coef[1] ** 2) / 4.0 - coef[2], 1.0))
    return float(cx), float(cy), float(radius)


def _blur_variance(gray: np.ndarray, mask: np.ndarray) -> float:
    lap = cv2.Laplacian(gray, cv2.CV_64F)
    return round(float(lap[mask > 0].var()), 1) if (mask > 0).any() else 0.0


def _measurement(name: str, value_px: float | None, px_std: float,
                 calibration: dict, status: str, reasons: list[str],
                 evidence: dict, mm_suppressed: str | None = None,
                 extra: dict | None = None) -> dict:
    value_mm, interval, cal_reasons = (None, None, [])
    if value_px is not None and status != "unsupported":
        if mm_suppressed is not None:
            cal_reasons = [mm_suppressed]
        else:
            value_mm, interval, cal_reasons = to_millimetres(value_px, px_std, calibration)
    out = {"name": name,
           "value_px": None if value_px is None else round(float(value_px), 2),
           "px_std": round(float(px_std), 3),
           "value_mm": value_mm, "mm_interval": interval, "unit": "mm",
           "status": status, "reasons": [str(r) for r in reasons + cal_reasons],
           "evidence": _py(evidence)}
    if extra:
        out.update(_py(extra))
    return out


def _worst(*statuses: str) -> str:
    rank = {"ok": 0, "ambiguous": 1, "unknown": 2}
    return max(statuses, key=lambda s: rank.get(s, 2))


# ---------------------------------------------------------------- top view

def _top_candidates(gray: np.ndarray, norm: np.ndarray, provider_mask=None) -> list:
    height, width = gray.shape
    small, total = min(height, width), height * width
    blurred = cv2.GaussianBlur(gray, (9, 9), 0)
    found: list[tuple[float, float, float]] = []
    circles = cv2.HoughCircles(blurred, cv2.HOUGH_GRADIENT, 1, max(small // 3, 10),
                               param1=120, param2=25,
                               minRadius=int(small * 0.08), maxRadius=int(small * 0.62))
    if circles is not None:
        for cx, cy, radius in circles[0]:
            if math.hypot(cx - width / 2, cy - height / 2) < small * 0.30:
                found.append((float(cx), float(cy), float(radius)))
    _, binary = cv2.threshold(cv2.GaussianBlur(norm, (5, 5), 0), 0, 255,
                              cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    masks = [binary, 255 - binary]
    if provider_mask is not None:
        masks.append((provider_mask > 0).astype(np.uint8) * 255)
    for mask in masks:
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:3]:
            area = cv2.contourArea(contour)
            if not total * 0.03 < area < total * 0.92:
                continue
            if cv2.pointPolygonTest(contour, (width / 2, height / 2), False) < 0:
                continue
            (cx, cy), radius = cv2.minEnclosingCircle(contour)
            if small * 0.08 < radius < small * 0.62:
                found.append((float(cx), float(cy), float(radius)))
    return found


def _refine_circle(edges: np.ndarray, cx: float, cy: float, radius: float) -> dict:
    points = np.column_stack(np.nonzero(edges))
    dist = np.sqrt((points[:, 1] - cx) ** 2 + (points[:, 0] - cy) ** 2)
    near = points[np.abs(dist - radius) <= max(3.0, 0.03 * radius)]
    if len(near) < 12:
        return {"cx": cx, "cy": cy, "radius": radius, "radial_std": 1.5,
                "support_points": int(len(near)), "axes": None, "axes_ratio": 1.0}
    fx, fy, fr = _kasa_fit(near[:, 1].astype(float), near[:, 0].astype(float))
    radial = np.sqrt((near[:, 1] - fx) ** 2 + (near[:, 0] - fy) ** 2)
    axes, ratio = None, 1.0
    if len(near) >= 20:
        try:
            _, (d1, d2), _ = cv2.fitEllipse(near[:, ::-1].reshape(-1, 1, 2).astype(np.int32))
            axes = sorted([float(d1), float(d2)])
            ratio = round(axes[0] / axes[1], 4) if axes[1] > 0 else 1.0
        except cv2.error:
            axes, ratio = None, 1.0
    return {"cx": fx, "cy": fy, "radius": fr,
            "radial_std": round(float(radial.std()), 3),
            "support_points": int(len(near)), "axes": axes, "axes_ratio": ratio}


def _outline_metrics(gray: np.ndarray, norm: np.ndarray, fit: dict) -> dict:
    _, binary = cv2.threshold(cv2.GaussianBlur(norm, (5, 5), 0), 0, 255,
                              cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    target = math.pi * fit["radius"] ** 2
    best = None
    for mask in (binary, 255 - binary):
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            area = cv2.contourArea(contour)
            if not 0.25 * target < area < 1.6 * target:
                continue
            moments = cv2.moments(contour)
            if moments["m00"] <= 0:
                continue
            mx, my = moments["m10"] / moments["m00"], moments["m01"] / moments["m00"]
            if math.hypot(mx - fit["cx"], my - fit["cy"]) > fit["radius"] * 0.35:
                continue
            if best is None or abs(area - target) < abs(best[0] - target):
                best = (area, contour)
    if best is None:
        return {"contour": None, "area_px": 0, "perimeter_px": 0.0,
                "circularity": 0.0, "vertices": 0, "solidity": 0.0}
    area, contour = best
    perimeter = cv2.arcLength(contour, True)
    hull = cv2.convexHull(contour)
    approx = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
    return {"contour": contour, "area_px": int(area), "perimeter_px": round(float(perimeter), 1),
            "circularity": round(4 * math.pi * area / max(perimeter ** 2, 1.0), 3),
            "vertices": int(len(approx)),
            "solidity": round(area / max(cv2.contourArea(hull), 1.0), 3)}


def _contour_support(edges: np.ndarray, contour: np.ndarray) -> float:
    """Fraction of contour points coinciding with edges (polygonal heads)."""
    points = contour.reshape(-1, 2)
    height, width = edges.shape
    inside = points[(points[:, 0] >= 0) & (points[:, 0] < width)
                    & (points[:, 1] >= 0) & (points[:, 1] < height)]
    if len(inside) == 0:
        return 0.0
    dilated = cv2.dilate(edges, np.ones((5, 5), np.uint8))
    return round(float(dilated[inside[:, 1], inside[:, 0]].mean() / 255.0), 3)


def _hex_widths(vertices: np.ndarray) -> tuple[float, float, float] | None:
    """Across-flats mean/std plus across-corners for 6 ordered vertices."""
    pts = vertices.reshape(-1, 2).astype(float)
    if len(pts) != 6:
        return None
    corners = max(float(np.linalg.norm(pts[i] - pts[j]))
                  for i in range(6) for j in range(i + 1, 6))
    edges = [(pts[i], pts[(i + 1) % 6]) for i in range(6)]
    angles = [math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180.0 for a, b in edges]
    used, widths = [False] * 6, []
    for i in range(6):
        if used[i]:
            continue
        best, best_gap = None, 1e9
        for j in range(6):
            if j == i or used[j]:
                continue
            gap = abs((angles[i] - angles[j] + 90) % 180 - 90)
            if gap < best_gap:
                best, best_gap = j, gap
        if best is None or best_gap > 12.0:
            return None
        used[i] = used[best] = True
        (a1, b1), (a2, _b2) = edges[i], edges[best]
        direction = b1 - a1
        norm = float(np.linalg.norm(direction))
        if norm <= 0:
            return None
        widths.append(abs(float(np.cross(direction / norm, a2 - a1))))
    if len(widths) != 3:
        return None
    return float(np.mean(widths)), float(np.std(widths)), corners


def _head_shape(outline: dict, axes_ratio: float) -> str:
    if outline["contour"] is None:
        return "unknown"
    if 5 <= outline["vertices"] <= 7 and outline["solidity"] >= 0.93:
        return "hex"
    if outline["circularity"] >= 0.82 and axes_ratio >= 0.90:
        return "round"
    if outline["circularity"] >= 0.70:
        return "irregular"
    return "unknown"


def _top_quality(gray: np.ndarray, fit: dict, edges: np.ndarray, support: float,
                 support_kind: str = "rim") -> tuple[dict, list[str]]:
    height, width = gray.shape
    yy, xx = np.mgrid[0:height, 0:width].astype(float)
    dist = np.sqrt((xx - fit["cx"]) ** 2 + (yy - fit["cy"]) ** 2)
    disc = dist <= fit["radius"]
    ring = (dist > fit["radius"] * 1.08) & (dist <= fit["radius"] * 1.35)
    contrast = 0.0
    if disc.any() and ring.any():
        contrast = abs(float(np.median(gray[disc].astype(float)))
                       - float(np.median(gray[ring].astype(float)))) / 255.0
    touches = (fit["cx"] - fit["radius"] < 3 or fit["cy"] - fit["radius"] < 3
               or fit["cx"] + fit["radius"] > width - 3 or fit["cy"] + fit["radius"] > height - 3)
    disc_mask = disc.astype(np.uint8) * 255
    quality = {"contrast": round(contrast, 3),
               "glare_fraction": round(float(((gray > 245) & disc).sum() / max(disc.sum(), 1)), 3),
               "shadow_fraction": round(float(((gray < 25) & disc).sum() / max(disc.sum(), 1)), 3),
               "blur_variance": _blur_variance(gray, disc_mask),
               "rim_support": support,
               "axes_ratio": fit["axes_ratio"],
               "touches_border": bool(touches),
               "support_points": fit["support_points"]}
    flags = []
    if touches:
        flags.append("head touches the crop border; boundary is incomplete")
    if support < MIN_RIM_SUPPORT:
        flags.append(f"{support_kind} support {support} below {MIN_RIM_SUPPORT}")
    elif support < WEAK_RIM_SUPPORT:
        flags.append(f"{support_kind} support {support} is partial; "
                     "occlusion or weak edges suspected")
    if fit["axes_ratio"] < SEVERE_AXES_RATIO:
        flags.append(f"axes ratio {fit['axes_ratio']}: perspective invalidates metric diameter")
    elif fit["axes_ratio"] < ROUND_AXES_RATIO:
        flags.append(f"axes ratio {fit['axes_ratio']}: tilt suspected, diameter spans both axes")
    if contrast < MIN_CONTRAST:
        flags.append(f"contrast {round(contrast, 3)} below {MIN_CONTRAST}")
    if quality["glare_fraction"] > STRONG_GLARE:
        flags.append(f"glare covers {quality['glare_fraction']} of the head")
    return quality, flags


def analyze_top(crop: np.ndarray, gray: np.ndarray, calibration: dict,
                segmenter=None, context: dict | None = None) -> dict:
    """Measure the visible head disc. No shaft inference happens here."""
    norm, edges = _normalize(gray), _edges(gray)
    provider_mask = None
    if segmenter is not None:
        provider_mask = segmenter(gray, {**(context or {}), "mode": "top"})
        if provider_mask is not None and provider_mask.shape[:2] != gray.shape:
            raise ValueError("segmenter mask must match the crop size")
    scored = []
    for cx, cy, radius in _top_candidates(gray, norm, provider_mask):
        support = _rim_support(edges, cx, cy, radius)
        scored.append((support, cx, cy, radius))
    plausible = [c for c in scored if c[0] >= MIN_RIM_SUPPORT]
    if plausible:
        _rim, cx, cy, radius = max(plausible, key=lambda c: c[0])
    else:
        rescued = None
        for _rim, cx0, cy0, radius0 in sorted(scored, reverse=True)[:3]:
            outline0 = _outline_metrics(gray, norm, {"cx": cx0, "cy": cy0, "radius": radius0})
            if outline0["contour"] is None:
                continue
            outline_support0 = _contour_support(edges, outline0["contour"])
            if outline_support0 >= WEAK_RIM_SUPPORT \
                    and (rescued is None or outline_support0 > rescued[0]):
                rescued = (outline_support0, cx0, cy0, radius0)
        if rescued is None:
            quality = {"contrast": 0.0,
                       "rim_support": round(max([s[0] for s in scored] + [0.0]), 3)}
            measurement = _measurement("visible_head_diameter", None, 0.0, calibration,
                                       "unknown", ["no circular head localized"],
                                       {"candidates": len(scored)})
            return {"geometry": None, "measurements": [measurement], "quality": quality,
                    "verdict": "unknown", "reasons": ["no circular head localized"],
                    "evidence_refs": [f"head_candidates:{len(scored)}"]}
        _support, cx, cy, radius = rescued
    fit = _refine_circle(edges, cx, cy, radius)
    outline = _outline_metrics(gray, norm, fit)
    shape = _head_shape(outline, fit["axes_ratio"])
    rim = _rim_support(edges, fit["cx"], fit["cy"], fit["radius"])
    outline_support = _contour_support(edges, outline["contour"]) \
        if outline["contour"] is not None else 0.0
    use_outline = shape == "hex" and outline["contour"] is not None
    support = outline_support if use_outline else rim
    kind = "outline" if use_outline else "rim"
    quality, flags = _top_quality(gray, fit, edges, support, kind)
    quality["rim_support"] = rim
    quality["outline_support"] = outline_support
    px_std = max(PX_FLOOR, 2.0 * fit["radial_std"])
    diameter_px = 2.0 * fit["radius"]
    status, mm_suppressed, extra = "ok", None, {}
    if fit["axes_ratio"] < SEVERE_AXES_RATIO:
        status = "unknown"
        mm_suppressed = "perspective invalidates the metric diameter; use a level top view"
    elif fit["axes_ratio"] < ROUND_AXES_RATIO and fit["axes"] is not None:
        minor, major = fit["axes"]
        diameter_px = (minor + major) / 2.0
        px_std = max(px_std, (major - minor) / 2.0)
        status = "ambiguous"
        extra = {"minor_axis_px": round(minor, 2), "major_axis_px": round(major, 2)}
    hex_dims = None
    if status == "ok" and shape == "hex" and outline["contour"] is not None:
        approx = cv2.approxPolyDP(outline["contour"],
                                  0.02 * cv2.arcLength(outline["contour"], True), True)
        hex_dims = _hex_widths(approx)
    if hex_dims is not None:
        flats_mean, flats_std, corners = hex_dims
        diameter_px, px_std = flats_mean, max(PX_FLOOR, flats_std)
        extra = {"across_flats_px": round(flats_mean, 2),
                 "across_corners_px": round(corners, 2)}
    if status == "ok" and (quality["touches_border"] or support < WEAK_RIM_SUPPORT
                            or quality["contrast"] < MIN_CONTRAST
                            or quality["glare_fraction"] > STRONG_GLARE):
        status = "ambiguous"
    if support < MIN_RIM_SUPPORT:
        status = "unknown"
        mm_suppressed = mm_suppressed or (f"{kind} support {support} is too weak "
                                          "for a metric diameter")
    evidence = {"boundary_kind": kind, "rim_support": rim,
                "outline_support": outline_support,
                "support_points": fit["support_points"],
                "radial_std_px": fit["radial_std"], "axes_ratio": fit["axes_ratio"],
                "outline": {k: outline[k] for k in
                            ("area_px", "perimeter_px", "circularity", "vertices", "solidity")}}
    measurements = [
        _measurement("visible_head_diameter", diameter_px, px_std, calibration,
                     status, flags or ([] if status == "ok" else ["boundary evidence is partial"]),
                     evidence, mm_suppressed, extra),
        _measurement("visible_shaft_length", None, 0.0, calibration, "unsupported",
                     ["shaft length cannot be measured from a top-down photograph"], {}),
    ]
    geometry = {"center_xy": [round(fit["cx"], 1), round(fit["cy"], 1)],
                "radius_px": round(fit["radius"], 2),
                "ellipse_axes_px": None if fit["axes"] is None else [round(a, 2) for a in fit["axes"]],
                "axes_ratio": fit["axes_ratio"], "head_shape": shape,
                "outline": {k: outline[k] for k in
                            ("area_px", "perimeter_px", "circularity", "vertices", "solidity")},
                "rim_support": rim, "outline_support": outline_support}
    verdict = status
    reasons = flags if status != "ok" else []
    refs = [f"circle_fit:{fit['support_points']}_edge_points",
            f"{kind}_support:{support}", f"head_shape:{shape}"]
    return {"geometry": geometry, "measurements": measurements, "quality": quality,
            "verdict": verdict, "reasons": reasons, "evidence_refs": refs}


# --------------------------------------------------------------- side view

def _side_silhouette(norm: np.ndarray, gray: np.ndarray, provider_mask=None):
    height, width = gray.shape
    total = height * width
    stages = [("otsu", cv2.GaussianBlur(norm, (5, 5), 0)),
              ("otsu", cv2.GaussianBlur(gray, (5, 5), 0))]
    if provider_mask is not None:
        stages.append(("mask", (provider_mask > 0).astype(np.uint8) * 255))
    for kind, stage in stages:
        if kind == "mask":
            masks = [stage]
        else:
            _, binary = cv2.threshold(stage, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            masks = [binary, 255 - binary]
        best = None
        for mask in masks:
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:3]:
                area = cv2.contourArea(contour)
                if not total * 0.02 < area < total * 0.90:
                    continue
                if cv2.pointPolygonTest(contour, (width / 2, height / 2), False) < 0:
                    continue
                rect = cv2.minAreaRect(contour)
                side_a, side_b = rect[1]
                if min(side_a, side_b) <= 0:
                    continue
                if max(side_a, side_b) / min(side_a, side_b) < SIDE_MIN_ASPECT:
                    continue
                if best is None or area > best[0]:
                    best = (area, contour)
        if best is not None:
            return best[1]
    return None


def _axis_profile(mask: np.ndarray) -> dict:
    """Project silhouette pixels onto the PCA long axis; warp-free profile."""
    points = np.column_stack(np.nonzero(mask)).astype(float)[:, ::-1]
    mean = points.mean(axis=0)
    _, vecs = np.linalg.eigh(np.cov((points - mean).T))
    axis = vecs[:, 1]
    if axis[0] < 0 or (axis[0] == 0 and axis[1] < 0):
        axis = -axis
    rel = points - mean
    along = rel @ axis
    t_min, t_max = float(along.min()), float(along.max())
    index = np.floor(along - t_min).astype(int)
    width = np.bincount(index, minlength=int(math.ceil(t_max - t_min)) + 1).astype(float)
    by_bin = [points[index == i].mean(axis=0) if (index == i).any() else mean
              for i in range(len(width))]
    return {"mean": mean, "axis": axis, "t_min": t_min, "width": width,
            "centers": np.array(by_bin), "points": points, "along": along, "index": index}


def _smooth(values: np.ndarray) -> np.ndarray:
    window = int(min(max(len(values) // 30, 5), 21)) | 1
    kernel = np.ones(window) / window
    padded = np.pad(values, window // 2, mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def _profile_sections(width: np.ndarray) -> dict:
    smooth = _smooth(width)
    floor = max(3.0, 0.12 * smooth.max())
    active = np.nonzero(smooth > floor)[0]
    if len(active) < 20:
        return {"lo": None, "smooth": smooth}
    lo, hi = int(active[0]), int(active[-1])
    span = hi - lo
    margin = max(int(span * 0.08), 5)
    window = max(10, span // 20)
    candidates = []
    for split in range(lo + margin, hi - margin + 1):
        left = smooth[max(lo, split - window):split]
        right = smooth[split:min(hi + 1, split + window)]
        med_l, med_r = float(np.median(left)), float(np.median(right))
        if min(med_l, med_r) <= 0:
            continue
        ratio = max(med_l, med_r) / min(med_l, med_r)
        if ratio >= HEAD_STEP_RATIO:
            candidates.append((ratio, split))
    left_end = float(np.median(smooth[lo:lo + window]))
    right_end = float(np.median(smooth[hi - window + 1:hi + 1]))
    ends_ratio = (max(left_end, right_end) / min(left_end, right_end)
                  if min(left_end, right_end) > 0 else 1.0)
    best = None
    if ends_ratio >= 1.15:
        for ratio, split in sorted(candidates, reverse=True):
            med_l = float(np.median(smooth[max(lo, split - window):split]))
            med_r = float(np.median(smooth[split:min(hi + 1, split + window)]))
            if med_l >= med_r:
                wide_ok = left_end >= 0.75 * med_l
                narrow_ok = right_end <= 1.33 * med_r and right_end < 0.9 * left_end
            else:
                wide_ok = right_end >= 0.75 * med_r
                narrow_ok = left_end <= 1.33 * med_l and left_end < 0.9 * right_end
            if wide_ok and narrow_ok:
                best = (ratio, split)
                break
    section = {"lo": lo, "hi": hi, "smooth": smooth, "split": None}
    if best is None:
        return section
    ratio, split = best
    med_l = float(np.median(smooth[max(lo, split - window):split]))
    med_r = float(np.median(smooth[split:min(hi + 1, split + window)]))
    head_left = med_l > med_r
    shaft_lo, shaft_hi = (split, hi) if head_left else (lo, split)
    core = smooth[shaft_lo:shaft_hi + 1]
    trim = len(core) // 5
    core = core[trim:len(core) - trim] if len(core) - 2 * trim > 5 else core
    mode = float(np.argmax(np.bincount(np.round(core).astype(int))))
    cluster = core[np.abs(core - mode) <= 4.0]
    diameter = float(np.median(cluster)) if len(cluster) >= 5 else float(np.median(core))
    section.update({"split": int(split), "step_ratio": round(ratio, 3),
                    "head_left": bool(head_left),
                    "shaft_lo": int(shaft_lo), "shaft_hi": int(shaft_hi),
                    "shaft_diameter_px": round(diameter, 2),
                    "shaft_width_std": round(float(core.std()), 3),
                    "core_min": round(float(core.min()), 2),
                    "core_max": round(float(core.max()), 2)})
    return section


def _edge_support(edges: np.ndarray, profile: dict, lo: int, hi: int) -> float:
    index = np.floor((np.column_stack(np.nonzero(edges)).astype(float)[:, ::-1]
                      - profile["mean"]) @ profile["axis"] - profile["t_min"]).astype(int)
    counts = np.bincount(index[index >= 0], minlength=len(profile["width"]))
    span = counts[max(lo, 0):hi + 1]
    if len(span) == 0:
        return 0.0
    return round(float((span >= 2).sum() / len(span)), 3)


def _convergence(section: dict) -> float:
    if section.get("split") is None:
        return 1.0
    smooth = section["smooth"]
    shaft = smooth[section["shaft_lo"]:section["shaft_hi"] + 1]
    window = max(len(shaft) * 15 // 100, 5)
    ends = [float(np.median(shaft[:window])), float(np.median(shaft[-window:]))]
    if max(ends) <= 0:
        return 1.0
    return round(min(ends) / max(ends), 3)


def analyze_side(crop: np.ndarray, gray: np.ndarray, calibration: dict,
                 segmenter=None, context: dict | None = None) -> dict:
    """Measure the visible silhouette: length, shaft, head boundary."""
    norm, edges = _normalize(gray), _edges(gray)
    provider_mask = None
    if segmenter is not None:
        provider_mask = segmenter(gray, {**(context or {}), "mode": "side"})
        if provider_mask is not None and provider_mask.shape[:2] != gray.shape:
            raise ValueError("segmenter mask must match the crop size")
    contour = _side_silhouette(norm, gray, provider_mask)
    if contour is None:
        measurement = _measurement("visible_overall_length", None, 0.0, calibration,
                                   "unknown", ["no elongated silhouette localized"], {})
        return {"geometry": None, "measurements": [measurement],
                "quality": {"contrast": 0.0, "edge_support": 0.0},
                "verdict": "unknown", "reasons": ["no elongated silhouette localized"],
                "evidence_refs": ["silhouette:none"]}
    mask = cv2.drawContours(np.zeros(gray.shape, np.uint8), [contour], -1, 255, -1)
    profile = _axis_profile(mask)
    section = _profile_sections(profile["width"])
    x, y, w, h = cv2.boundingRect(contour)
    bbox = np.zeros(gray.shape, np.uint8)
    bbox[y:y + h, x:x + w] = 255
    outside = (bbox > 0) & (mask == 0)
    contrast = 0.0
    if outside.any():
        contrast = abs(float(np.median(gray[mask > 0].astype(float)))
                       - float(np.median(gray[outside].astype(float)))) / 255.0
    touches = bool((mask[0, :] > 0).any() or (mask[-1, :] > 0).any()
                    or (mask[:, 0] > 0).any() or (mask[:, -1] > 0).any())
    if section.get("lo") is None:
        measurement = _measurement("visible_overall_length", None, 0.0, calibration,
                                   "unknown", ["silhouette too small for a length"], {})
        return {"geometry": None, "measurements": [measurement],
                "quality": {"contrast": round(contrast, 3)}, "verdict": "unknown",
                "reasons": ["silhouette too small for a length"],
                "evidence_refs": ["profile:empty"]}
    lo, hi = section["lo"], section["hi"]
    length_px = float(hi - lo)
    support = _edge_support(edges, profile, lo, hi)
    convergence = _convergence(section)
    quality = {"contrast": round(contrast, 3),
               "glare_fraction": round(float(((gray > 245) & (bbox > 0)).sum()
                                         / max((bbox > 0).sum(), 1)), 3),
               "shadow_fraction": round(float(((gray < 25) & (bbox > 0) & (mask == 0)).sum()
                                          / max((bbox > 0).sum(), 1)), 3),
               "blur_variance": _blur_variance(gray, bbox),
               "edge_support": support,
               "convergence_ratio": convergence,
               "touches_border": touches}
    reasons: list[str] = []
    if touches:
        reasons.append("silhouette touches the crop border; length is the visible portion only")
    if convergence < 0.80:
        reasons.append(f"shaft edges converge ({convergence}); perspective invalidates diameters")
    elif convergence < 0.92:
        reasons.append(f"shaft edges converge slightly ({convergence}); diameters span the taper")
    length_status = "ok"
    if touches or support < WEAK_EDGE_SUPPORT or convergence < 0.92:
        length_status = "ambiguous"
    if support < MIN_EDGE_SUPPORT:
        length_status = "unknown"
    width_std = section.get("shaft_width_std", 1.0) or 1.0
    measurements = [_measurement(
        "visible_overall_length", length_px, max(1.0, 2.0 * width_std), calibration,
        "unknown" if length_status == "unknown" else length_status,
        [r for r in reasons if "converge" not in r or length_status != "ok"]
        or ([] if length_status == "ok" else ["edge evidence is partial"]),
        {"edge_support": support, "extent_bins": [lo, hi]},
        extra={"partial": touches})]
    if section.get("split") is None:
        measurements.append(_measurement(
            "visible_shaft_length", None, 0.0, calibration, "unknown",
            ["no head/shaft width step observed; boundary not measurable"], {}))
        measurements.append(_measurement(
            "visible_shaft_diameter", None, 0.0, calibration, "unknown",
            ["no head/shaft width step observed; shaft extent not measurable"], {}))
    else:
        split = section["split"]
        head_len = float(split - lo if section["head_left"] else hi - split)
        shaft_len = float(hi - split if section["head_left"] else split - lo)
        shaft_support = _edge_support(edges, profile, section["shaft_lo"], section["shaft_hi"])
        shaft_status = "ok"
        shaft_reasons: list[str] = []
        if shaft_support < MIN_EDGE_SUPPORT:
            shaft_status, shaft_reasons = "unknown", [f"shaft edge support {shaft_support} too weak"]
        elif shaft_support < WEAK_EDGE_SUPPORT:
            shaft_status, shaft_reasons = "ambiguous", [f"shaft edge support {shaft_support} is partial"]
        measurements.append(_measurement(
            "visible_head_length", head_len, max(1.0, 2.0 * width_std), calibration,
            shaft_status if shaft_status != "ok" else ("ambiguous" if reasons else "ok"),
            shaft_reasons + reasons,
            {"step_ratio": section["step_ratio"], "split_bin": split}))
        measurements.append(_measurement(
            "visible_shaft_length", shaft_len, max(1.0, 2.0 * width_std), calibration,
            shaft_status if shaft_status != "ok" else ("ambiguous" if reasons else "ok"),
            shaft_reasons + reasons,
            {"edge_support": shaft_support, "shaft_bins": [section["shaft_lo"], section["shaft_hi"]]}))
        dia_status, mm_suppressed = shaft_status, None
        if convergence < 0.80:
            dia_status, mm_suppressed = "unknown", ("shaft edges converge; perspective "
                                                   "invalidates the metric diameter")
        elif convergence < 0.92:
            dia_status = "ambiguous"
        taper_half = (section["core_max"] - section["core_min"]) / 2.0
        measurements.append(_measurement(
            "visible_shaft_diameter", section["shaft_diameter_px"],
            max(PX_FLOOR, section["shaft_width_std"], taper_half if dia_status != "ok" else 0.0),
            calibration, dia_status,
            shaft_reasons + ([r for r in reasons if "converge" in r]),
            {"edge_support": shaft_support, "width_std_px": section["shaft_width_std"],
             "convergence_ratio": convergence}, mm_suppressed))
    angle = float(np.degrees(np.arctan2(profile["axis"][1], profile["axis"][0])))
    raw = profile["width"]

    def _nearest(fallback: int) -> int:
        if raw[fallback] > 0:
            return fallback
        for delta in range(1, len(raw)):
            for cand in (fallback - delta, fallback + delta):
                if 0 <= cand < len(raw) and raw[cand] > 0:
                    return cand
        return fallback

    end_lo, end_hi = _nearest(lo), _nearest(hi)
    split_bin = None if section.get("split") is None else _nearest(section["split"])
    geometry = {"axis_angle_deg": round(angle, 1),
                "center_xy": [round(float(profile["mean"][0]), 1),
                              round(float(profile["mean"][1]), 1)],
                "endpoints_xy": [[round(float(v), 1) for v in profile["centers"][end_lo]],
                                 [round(float(v), 1) for v in profile["centers"][end_hi]]],
                "extent_bins": [lo, hi], "split_bin": section.get("split"),
                "step_ratio": section.get("step_ratio"),
                "silhouette_area_px": int((mask > 0).sum()),
                "head_left": section.get("head_left"),
                "split_xy": None if split_bin is None else
                [round(float(v), 1) for v in profile["centers"][split_bin]]}
    core = [m["status"] for m in measurements if m["name"] == "visible_overall_length"]
    verdict = _worst(*core)
    refs = [f"silhouette:{int((mask > 0).sum())}px", f"edge_support:{support}",
            f"extent:{lo}-{hi}"]
    if section.get("split") is not None:
        refs.append(f"head_shaft_split:{section['split']}_ratio{section['step_ratio']}")
    return {"geometry": _py(geometry), "measurements": measurements, "quality": quality,
            "verdict": verdict, "reasons": reasons, "evidence_refs": refs}


# ------------------------------------------------------------------ entry

def analyze(image: np.ndarray, mode: str = "top", roi=None,
            calibration: dict | None = None, segmenter=None) -> dict:
    """Measure one screw crop. Returns a JSON-serializable result."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    calibration = calibration or blank("no calibration supplied; pixel-only output")
    crop, crop_box = _crop_roi(image, roi)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop.copy()
    context = {"crop_xywh": [crop_box["x"], crop_box["y"], crop_box["w"], crop_box["h"]]}
    worker = analyze_top if mode == "top" else analyze_side
    result = worker(crop, gray, calibration, segmenter, context)
    geometry = result.get("geometry")
    if geometry:
        ox, oy = crop_box["x"], crop_box["y"]
        for key in ("center_xy", "split_xy"):
            if geometry.get(key):
                geometry[key] = [round(geometry[key][0] + ox, 1),
                                 round(geometry[key][1] + oy, 1)]
        if geometry.get("endpoints_xy"):
            geometry["endpoints_xy"] = [[round(p[0] + ox, 1), round(p[1] + oy, 1)]
                                        for p in geometry["endpoints_xy"]]
    frame_name = calibration.get("frame") or "image_pixels"
    return {"mode": mode, "crop": crop_box,
            "frame": {"name": frame_name, "axes": "x right, y down",
                      "origin": "top-left of the source image",
                      "note": ("millimetre values share the calibration plane; "
                               "off-plane parts are not corrected")},
            "calibration": calibration, **result,
            "not_estimated": NOT_ESTIMATED}
