"""Register a before/after pair and list visual changes.

Marker positions come from the calibration config. This module does not
hard-code them.
"""

from __future__ import annotations

import csv
import math
import re
import time
from pathlib import Path

import cv2
import numpy as np

STATUSES = (
    "no_change",
    "single_change",
    "multiple_changes",
    "registration_invalid",
    "view_mismatch",
    "input_error",
)
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}
EXPECT_STATUS = {
    "none": "no_change",
    "single": "single_change",
    "multiple": "multiple_changes",
    "invalid": "registration_invalid",
    "registration_invalid": "registration_invalid",
    "view_mismatch": "view_mismatch",
}
_VIEW_COLOR = {
    "fixture_front": (40, 140, 40),
    "fixture_back": (160, 40, 160),
    "surface_a3": (40, 110, 200),
    "surface_a4": (180, 120, 30),
}


class Kit:
    def __init__(self, kit: dict, dictionary: str):
        self.kit = kit
        self.dictionary = dictionary
        self.views = {}
        self.id_to_view = {}
        for sheet in kit["sheets"]:
            markers = [marker for marker in sheet["markers"] if marker["dictionary"] == dictionary]
            if not markers:
                continue
            frame = sheet["frame"]
            origin = kit["frames"][frame]["origin_on_page_mm"]
            width, height = sheet["page"]["width_mm"], sheet["page"]["height_mm"]
            outline = next((zone["rect_frame_mm"] for zone in sheet.get("zones", []) if zone["type"] == "fixture_board_outline"), None)
            self.views[frame] = {
                "sheet": sheet,
                "markers": {marker["id"]: marker for marker in markers},
                "zones": sheet.get("zones", []),
                "page_rect_mm": [-origin[0], -origin[1], width, height],
                "board_mm": outline,
                "origin_mm": origin,
            }
            for marker in markers:
                if marker["id"] in self.id_to_view:
                    raise SystemExit(f"Marker {marker['id']} is used more than once in {dictionary}")
                self.id_to_view[marker["id"]] = frame


def make_detector(harness: dict):
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = getattr(cv2.aruco, harness["registration"]["corner_refinement"])
    dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, harness["registration"]["dictionary"]))
    return cv2.aruco.ArucoDetector(dictionary, params)


def detect_markers(gray: np.ndarray, detector, kit: Kit):
    corners, ids, _rejected = detector.detectMarkers(gray)
    by_view = {view: {} for view in kit.views}
    unknown = []
    if ids is None:
        return by_view, unknown
    for quad, marker_id in zip(corners, ids.ravel().tolist()):
        view = kit.id_to_view.get(int(marker_id))
        if view is None:
            unknown.append(int(marker_id))
            continue
        if int(marker_id) in by_view[view]:
            by_view[view].setdefault("_duplicates", []).append(int(marker_id))
            continue
        by_view[view][int(marker_id)] = quad.reshape(4, 2).astype(np.float64)
    return by_view, unknown


def _counts(by_view) -> dict:
    return {view: len([key for key in found if key != "_duplicates"]) for view, found in by_view.items()}


def determine_view(by_view, harness: dict):
    counts = _counts(by_view)
    minimum = harness["registration"]["min_markers_view_presence"]
    for group in harness["registration"]["view_groups"]:
        present = [view for view in group if counts.get(view, 0) >= minimum]
        if len(present) == 1:
            return present[0], "auto", counts
        if len(present) > 1:
            return None, "ambiguous: " + ", ".join(present), counts
    return None, "no_view", counts


def _group_index(view: str, harness: dict):
    for index, group in enumerate(harness["registration"]["view_groups"]):
        if view in group:
            return index
    return None


def register_view(by_view, view: str, kit: Kit, harness: dict):
    rules = harness["registration"]
    found = {key: value for key, value in by_view.get(view, {}).items() if key != "_duplicates"}
    result = {"view": view, "valid": False, "reasons": [], "marker_ids_used": sorted(found), "n_markers": len(found), "n_corners": 4 * len(found)}
    duplicates = by_view.get(view, {}).get("_duplicates")
    if duplicates:
        result["reasons"].append(f"duplicate ids: {duplicates}")
    if len(found) < rules["min_markers"]:
        result["reasons"].append(f"{len(found)} markers < min_markers {rules['min_markers']}")
        return result, None
    order = sorted(found)
    image_pts = np.concatenate([found[marker_id] for marker_id in order]).astype(np.float64)
    object_pts = np.concatenate([np.array(kit.views[view]["markers"][marker_id]["corners_frame_mm"], dtype=np.float64) for marker_id in order])
    homography, mask = cv2.findHomography(image_pts, object_pts, cv2.RANSAC, rules["ransac_reproj_threshold_mm"])
    if homography is None or not np.isfinite(homography).all() or abs(np.linalg.det(homography)) < 1e-12:
        result["reasons"].append("homography failed")
        return result, None
    inliers = mask.ravel().astype(bool)
    result["ransac_inliers"] = int(inliers.sum())
    result["ransac_outliers"] = int((~inliers).sum())
    projected_mm = cv2.perspectiveTransform(image_pts.reshape(-1, 1, 2), homography).reshape(-1, 2)
    error_mm = np.linalg.norm(projected_mm - object_pts, axis=1)
    inverse = np.linalg.inv(homography)
    projected_px = cv2.perspectiveTransform(object_pts.reshape(-1, 1, 2), inverse).reshape(-1, 2)
    error_px = np.linalg.norm(projected_px - image_pts, axis=1)
    result.update({
        "reprojection_rms_mm": round(float(np.sqrt(np.mean(error_mm ** 2))), 4),
        "reprojection_max_mm": round(float(error_mm.max()), 4),
        "reprojection_rms_px": round(float(np.sqrt(np.mean(error_px ** 2))), 4),
        "reprojection_max_px": round(float(error_px.max()), 4),
        "H_image_to_frame_mm": [[round(float(value), 8) for value in row] for row in homography],
    })
    if result["ransac_outliers"]:
        result["reasons"].append(f"{result['ransac_outliers']} RANSAC outliers")
    if result["reprojection_rms_mm"] > rules["max_reprojection_rms_mm"]:
        result["reasons"].append(f"rms {result['reprojection_rms_mm']} mm > {rules['max_reprojection_rms_mm']} mm")
        return result, None
    if result["reprojection_max_mm"] > rules["max_reprojection_error_mm"]:
        result["reasons"].append(f"max {result['reprojection_max_mm']} mm > {rules['max_reprojection_error_mm']} mm")
        return result, None
    result["valid"] = True
    return result, homography


class Frame:
    """Rectified millimetre frame for one view."""

    def __init__(self, view: str, kit: Kit, harness: dict):
        spec = harness["rectification"]
        info = kit.views[view]
        extent = info["board_mm"] if spec["extent"] == "fixture_board_outline_or_page" and info["board_mm"] else info["page_rect_mm"]
        margin = float(spec["margin_mm"])
        self.view = view
        self.info = info
        self.harness = harness
        self.ppm = float(spec["px_per_mm"])
        self.x0 = extent[0] - margin
        self.y0 = extent[1] - margin
        self.w_mm = extent[2] + 2 * margin
        self.h_mm = extent[3] + 2 * margin
        self.size = (int(round(self.w_mm * self.ppm)), int(round(self.h_mm * self.ppm)))
        self.extent_mm = extent
        self.to_px = np.array([[self.ppm, 0, -self.x0 * self.ppm], [0, self.ppm, -self.y0 * self.ppm], [0, 0, 1.0]], dtype=np.float64)

    def mm_to_px(self, x: float, y: float) -> tuple[float, float]:
        return (x - self.x0) * self.ppm, (y - self.y0) * self.ppm

    def rect_mask(self, rect_mm, grow_mm: float = 0.0) -> np.ndarray:
        x, y, w, h = rect_mm
        x0, y0 = self.mm_to_px(x - grow_mm, y - grow_mm)
        x1, y1 = self.mm_to_px(x + w + grow_mm, y + h + grow_mm)
        mask = np.zeros((self.size[1], self.size[0]), np.uint8)
        if x1 > x0 and y1 > y0:
            cv2.rectangle(mask, (int(round(x0)), int(round(y0))), (int(round(x1)) - 1, int(round(y1)) - 1), 255, -1)
        return mask

    def marker_mask(self) -> np.ndarray:
        extra = float(self.harness["roi"]["marker_mask_extra_mm"])
        mask = np.zeros((self.size[1], self.size[0]), np.uint8)
        for marker in self.info["markers"].values():
            corners = np.array(marker["corners_frame_mm"], dtype=float)
            x, y = corners[:, 0].min(), corners[:, 1].min()
            pad = float(marker.get("quiet_zone_mm", 0)) + extra
            mask = cv2.bitwise_or(mask, self.rect_mask([x, y, marker["size_mm"], marker["size_mm"]], pad))
        return mask

    def roi_mask(self) -> np.ndarray:
        kinds = self.harness["roi"]["zones_by_view"].get(self.view, [])
        shrink = float(self.harness["roi"]["shrink_mm"])
        mask = np.zeros((self.size[1], self.size[0]), np.uint8)
        for zone in self.info["zones"]:
            if zone["type"] in kinds:
                mask = cv2.bitwise_or(mask, self.rect_mask(zone["rect_frame_mm"], -shrink))
        return cv2.bitwise_and(mask, cv2.bitwise_not(self.marker_mask()))

    def stable_mask(self) -> np.ndarray:
        guard = int(round(float(self.harness["ecc"]["roi_guard_mm"]) * self.ppm))
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2 * guard + 1, 2 * guard + 1))
        grown = cv2.dilate(self.roi_mask(), kernel)
        mask = cv2.bitwise_and(self.rect_mask(self.extent_mm), cv2.bitwise_not(grown))
        if not self.harness["ecc"]["include_markers"]:
            mask = cv2.bitwise_and(mask, cv2.bitwise_not(self.marker_mask()))
        return mask


def _channel(image: np.ndarray, name: str) -> np.ndarray:
    if image.ndim == 2:
        return image
    if name == "lab_l":
        return cv2.cvtColor(image, cv2.COLOR_BGR2LAB)[:, :, 0]
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def normalize(values: np.ndarray, roi: np.ndarray, frame: Frame, spec: dict) -> np.ndarray:
    pixels = values.astype(np.float32)
    method = spec["method"]
    if method == "none":
        out = pixels
    elif method == "meanstd":
        sample = pixels[roi > 0]
        mean = float(sample.mean())
        std = float(sample.std()) + 1e-6
        out = (pixels - mean) / std * float(spec["meanstd_target_std"]) + 128.0
    elif method == "clahe":
        clahe = cv2.createCLAHE(clipLimit=float(spec["clahe_clip_limit"]), tileGridSize=(int(spec["clahe_tile_grid"]),) * 2)
        out = clahe.apply(values).astype(np.float32)
    elif method == "local_mean_division":
        sigma = float(spec["local_mean_sigma_mm"]) * frame.ppm
        local = cv2.GaussianBlur(pixels, (0, 0), sigma)
        out = pixels / (local + 1.0) * 128.0
    else:
        raise SystemExit(f"Unknown normalization {method}")
    return np.clip(out, 0, 255).astype(np.float32)


def read_image(path: Path):
    data = np.fromfile(str(path), dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def write_image(path: Path, image: np.ndarray, quality: int = 92) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    params = [cv2.IMWRITE_JPEG_QUALITY, quality] if suffix in {".jpg", ".jpeg"} else []
    ok, encoded = cv2.imencode(suffix if suffix else ".png", image, params)
    if not ok:
        raise RuntimeError(f"Could not encode {path}")
    encoded.tofile(str(path))


def _analyse(image, detector, kit: Kit, harness: dict, forced_view: str | None):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    by_view, unknown = detect_markers(gray, detector, kit)
    view, reason, counts = determine_view(by_view, harness)
    report = {
        "detected_counts_by_view": counts,
        "unknown_ids": unknown,
        "view_auto": view,
        "view_reason": reason,
        "detected_ids_by_view": {name: sorted(key for key in found if key != "_duplicates") for name, found in by_view.items() if found},
    }
    if forced_view is not None and view != forced_view:
        report["view"] = None
        report["view_confirmed_expected"] = forced_view
        report["view_reason"] = f"view_not_confirmed: expected {forced_view}, saw {view} ({reason})"
        return report, by_view
    report["view"] = view
    return report, by_view


def _residual(before: np.ndarray, after: np.ndarray, stable: np.ndarray, frame: Frame) -> dict:
    ys, xs = np.where(stable > 0)
    if len(xs) < 100:
        return {"shift_mm": None, "note": "stable area too small"}
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    window = (stable[y0:y1, x0:x1] > 0).astype(np.float32)
    left = (before[y0:y1, x0:x1] - 128.0) * window
    right = (after[y0:y1, x0:x1] - 128.0) * window
    hann = cv2.createHanningWindow((left.shape[1], left.shape[0]), cv2.CV_32F)
    (dx, dy), response = cv2.phaseCorrelate(left.astype(np.float32), right.astype(np.float32), hann)
    return {
        "dx_mm": round(dx / frame.ppm, 4),
        "dy_mm": round(dy / frame.ppm, 4),
        "shift_mm": round(math.hypot(dx, dy) / frame.ppm, 4),
        "phase_corr_response": round(float(response), 4),
    }


def _ecc(before_norm, after_norm, after_channel, after_color, stable, frame: Frame, harness: dict):
    spec = harness["ecc"]
    motion = {"translation": cv2.MOTION_TRANSLATION, "euclidean": cv2.MOTION_EUCLIDEAN, "affine": cv2.MOTION_AFFINE}[spec["motion"]]
    warp = np.eye(2, 3, dtype=np.float32)
    info = {"motion": spec["motion"], "applied": False}
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, int(spec["max_iterations"]), float(spec["epsilon"]))
    try:
        correlation, warp = cv2.findTransformECC(before_norm, after_norm, warp, motion, criteria, stable, int(spec["gauss_filt_size"]))
    except cv2.error as exc:
        info["status"] = f"ECC failed: {str(exc).splitlines()[-1][:140]}"
        return after_norm, after_channel, after_color, info
    shift_mm = math.hypot(float(warp[0, 2]), float(warp[1, 2])) / frame.ppm
    info.update({"correlation": round(float(correlation), 5), "shift_mm": round(shift_mm, 4)})
    if shift_mm > float(spec["max_shift_mm"]):
        info["status"] = f"rejected shift {shift_mm:.3f} mm"
        return after_norm, after_channel, after_color, info
    flags = cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP
    after_norm = cv2.warpAffine(after_norm, warp, frame.size, flags=flags)
    after_channel = cv2.warpAffine(after_channel, warp, frame.size, flags=flags)
    after_color = cv2.warpAffine(after_color, warp, frame.size, flags=flags)
    info["applied"] = True
    info["status"] = "ok"
    return after_norm, after_channel, after_color, info


def process_pair(pair_id: str, before_path: Path, after_path: Path, harness: dict, kit: Kit, detector, forced_view: str | None = None):
    started = time.perf_counter()
    result = {"pair_id": pair_id, "before": str(before_path), "after": str(after_path), "status": None, "regions": [], "warnings": []}
    before = read_image(before_path)
    after = read_image(after_path)
    if before is None or after is None:
        result["status"] = "input_error"
        result["warnings"].append("image unreadable")
        result["processing_s"] = round(time.perf_counter() - started, 3)
        return result, None
    result["image_size_before"] = [before.shape[1], before.shape[0]]
    result["image_size_after"] = [after.shape[1], after.shape[0]]
    view_before, markers_before = _analyse(before, detector, kit, harness, forced_view)
    view_after, markers_after = _analyse(after, detector, kit, harness, forced_view)
    result["view_before"] = view_before
    result["view_after"] = view_after
    visual = {"before": before, "after": after, "markers_before": markers_before, "markers_after": markers_after}
    if view_before["view"] is None or view_after["view"] is None:
        result["status"] = "registration_invalid"
        result["warnings"].append("view not confirmed: " + "; ".join(item["view_reason"] for item in (view_before, view_after) if item["view"] is None))
        result["processing_s"] = round(time.perf_counter() - started, 3)
        return result, visual
    if _group_index(view_before["view"], harness) != _group_index(view_after["view"], harness):
        result["status"] = "registration_invalid"
        result["warnings"].append(f"views in different groups ({view_before['view']} -> {view_after['view']})")
        result["processing_s"] = round(time.perf_counter() - started, 3)
        return result, visual
    if view_before["view"] != view_after["view"]:
        result["status"] = "view_mismatch"
        result["warnings"].append(f"{view_before['view']} -> {view_after['view']}")
        result["processing_s"] = round(time.perf_counter() - started, 3)
        return result, visual

    view = view_before["view"]
    result["view"] = view
    gray_before = cv2.cvtColor(before, cv2.COLOR_BGR2GRAY)
    gray_after = cv2.cvtColor(after, cv2.COLOR_BGR2GRAY)
    reg_before, homography_before = register_view(markers_before, view, kit, harness)
    reg_after, homography_after = register_view(markers_after, view, kit, harness)
    result["registration_before"] = reg_before
    result["registration_after"] = reg_after
    if homography_before is None or homography_after is None:
        result["status"] = "registration_invalid"
        bits = []
        for name, reg in (("before", reg_before), ("after", reg_after)):
            if not reg["valid"]:
                bits.append(f"{name}: {', '.join(reg['reasons'])}")
        result["warnings"].append("registration invalid: " + "; ".join(bits))
        result["processing_s"] = round(time.perf_counter() - started, 3)
        return result, visual

    frame = Frame(view, kit, harness)
    warp_before = frame.to_px @ homography_before
    warp_after = frame.to_px @ homography_after
    color_before = cv2.warpPerspective(before, warp_before, frame.size, flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))
    color_after = cv2.warpPerspective(after, warp_after, frame.size, flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))
    valid_before = cv2.warpPerspective(np.full(gray_before.shape, 255, np.uint8), warp_before, frame.size, flags=cv2.INTER_NEAREST)
    valid_after = cv2.warpPerspective(np.full(gray_after.shape, 255, np.uint8), warp_after, frame.size, flags=cv2.INTER_NEAREST)
    roi = cv2.bitwise_and(frame.roi_mask(), cv2.bitwise_and(valid_before, valid_after))
    if int(roi.sum()) == 0:
        result["status"] = "registration_invalid"
        result["warnings"].append("ROI is outside both images")
        result["processing_s"] = round(time.perf_counter() - started, 3)
        return result, visual
    roi_full = frame.roi_mask()
    coverage = float((roi > 0).sum()) / max(1, int((roi_full > 0).sum()))
    if coverage < 0.98:
        result["warnings"].append(f"ROI coverage {coverage * 100:.1f}%")

    spec = harness["normalization"]
    channel_before = _channel(color_before, spec["channel"])
    channel_after = _channel(color_after, spec["channel"])
    norm_before = normalize(channel_before, roi, frame, spec)
    norm_after = normalize(channel_after, roi, frame, spec)
    stable = cv2.bitwise_and(frame.stable_mask(), cv2.bitwise_and(valid_before, valid_after))
    result["residual_alignment"] = _residual(norm_before, norm_after, stable, frame)
    shift = result["residual_alignment"].get("shift_mm")
    if shift is not None and shift > harness["diagnostics"]["residual_warn_mm"]:
        result["warnings"].append(f"residual shift {shift} mm")
    result["ecc"] = {"enabled": bool(harness["ecc"]["enabled"])}
    if harness["ecc"]["enabled"]:
        norm_after, channel_after, color_after, ecc_info = _ecc(norm_before, norm_after, channel_after, color_after, stable, frame, harness)
        result["ecc"].update(ecc_info)
        if ecc_info.get("applied"):
            result["warnings"].append("ECC refinement applied")

    change = harness["change"]
    sigma = float(change["pre_blur_sigma_mm"]) * frame.ppm
    blurred_before = cv2.GaussianBlur(norm_before, (0, 0), sigma) if sigma > 0 else norm_before
    blurred_after = cv2.GaussianBlur(norm_after, (0, 0), sigma) if sigma > 0 else norm_after
    diff = cv2.absdiff(blurred_before, blurred_after)
    diff[roi == 0] = 0
    mask = ((diff > float(change["threshold"])) & (roi > 0)).astype(np.uint8) * 255
    close = max(1, int(round(float(change["close_kernel_mm"]) * frame.ppm)))
    opening = max(1, int(round(float(change["open_kernel_mm"]) * frame.ppm)))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close, close)))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (opening, opening)))
    mask = cv2.bitwise_and(mask, roi)
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    minimum_px = float(change["min_area_mm2"]) * frame.ppm ** 2
    regions = []
    small = 0
    for index in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[index])
        if area < minimum_px:
            small += 1
            mask[labels == index] = 0
            continue
        component = labels == index
        regions.append({
            "bbox_px": [x, y, w, h],
            "bbox_mm": [round(x / frame.ppm + frame.x0, 2), round(y / frame.ppm + frame.y0, 2), round(w / frame.ppm, 2), round(h / frame.ppm, 2)],
            "centroid_mm": [round(float(centroids[index][0]) / frame.ppm + frame.x0, 2), round(float(centroids[index][1]) / frame.ppm + frame.y0, 2)],
            "area_px": area,
            "area_mm2": round(area / frame.ppm ** 2, 2),
            "mean_diff": round(float(diff[component].mean()), 2),
            "max_diff": round(float(diff[component].max()), 2),
            "sign": "darker_after" if float((blurred_before - blurred_after)[component].mean()) > 0 else "brighter_after",
        })
    regions.sort(key=lambda region: -region["area_px"])
    for index, region in enumerate(regions, 1):
        region["label"] = f"R{index}"
    result["regions"] = regions
    result["n_regions"] = len(regions)
    result["discarded_small_components"] = small
    result["roi_area_mm2"] = round(float((roi > 0).sum()) / frame.ppm ** 2, 1)
    result["changed_fraction_roi"] = round(float((mask > 0).sum()) / max(1, int((roi > 0).sum())), 5)
    roi_values = diff[roi > 0]
    result["diff_stats_roi"] = {
        "p50": round(float(np.percentile(roi_values, 50)), 2),
        "p99": round(float(np.percentile(roi_values, 99)), 2),
        "max": round(float(roi_values.max()), 2),
    }
    if result["changed_fraction_roi"] > harness["diagnostics"]["max_changed_fraction_warn"]:
        result["warnings"].append("changed area is very large")
    if not regions:
        result["status"] = "no_change"
    elif len(regions) == 1:
        result["status"] = "single_change"
    else:
        result["status"] = "multiple_changes"
    result["rectified"] = {"view": view, "px_per_mm": frame.ppm, "size_px": list(frame.size), "origin_frame_mm": [frame.x0, frame.y0]}
    result["processing_s"] = round(time.perf_counter() - started, 3)
    visual.update({"frame": frame, "color_before": color_before, "color_after": color_after, "diff": diff, "mask": mask, "roi": roi})
    return result, visual


def find_pairs(folder: Path) -> dict:
    pairs = {}
    for path in sorted(folder.iterdir()):
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        match = re.match(r"^(.+)_(before|after)$", path.stem, re.I)
        if match:
            pairs.setdefault(match.group(1), {})[match.group(2).lower()] = path
    return pairs


def load_manifest(path: Path | None, folder: Path):
    candidates = [path] if path else [folder / "manifest.csv", folder / "manifest.json"]
    for candidate in candidates:
        if candidate is None or not Path(candidate).exists():
            continue
        candidate = Path(candidate)
        if candidate.suffix.lower() == ".json":
            rows = json_load(candidate)
            rows = rows["pairs"] if isinstance(rows, dict) else rows
        else:
            with candidate.open(newline="", encoding="utf-8-sig") as handle:
                rows = list(csv.DictReader(handle))
        parsed = {}
        for row in rows:
            boxes = []
            raw = row.get("expected_bbox_mm") or ""
            if isinstance(raw, list):
                boxes = raw
            else:
                for part in str(raw).split(";"):
                    numbers = [float(item) for item in part.replace(" ", "").split(",") if item != ""]
                    if len(numbers) == 4:
                        boxes.append(numbers)
            parsed[str(row["pair_id"])] = {
                "expected": (row.get("expected") or "").strip().lower() or None,
                "expected_bboxes_mm": boxes,
                "view": (row.get("view") or "").strip() or None,
                "assert": str(row.get("assert", "1")).strip().lower() not in {"0", "false", "no"},
                "notes": row.get("notes", ""),
            }
        return parsed, str(candidate)
    return {}, None


def json_load(path: Path):
    import json
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate(result: dict, expected: dict, tolerance_mm: float) -> dict:
    report = {"expected": None, "outcome_ok": None, "false_positive": False, "missed_change": False, "note": ""}
    if not expected or not expected.get("expected"):
        return report
    name = expected["expected"]
    want = EXPECT_STATUS.get(name, name)
    report["expected"] = name
    status = result["status"]
    report["outcome_ok"] = status == want
    if want == "no_change" and status in {"single_change", "multiple_changes"}:
        report["false_positive"] = True
        report["note"] = f"false alarm: {result.get('n_regions', 0)} region(s)"
    if want in {"single_change", "multiple_changes"}:
        if status == "no_change":
            report["missed_change"] = True
            report["note"] = "change missed"
        elif status in {"registration_invalid", "view_mismatch", "input_error"}:
            report["note"] = "not comparable"
        elif want == "single_change" and status == "multiple_changes":
            report["note"] = "more regions than expected"
        elif want == "multiple_changes" and status == "single_change":
            report["note"] = "fewer regions than expected"
    boxes = expected.get("expected_bboxes_mm") or []
    if boxes and status in {"no_change", "single_change", "multiple_changes"}:
        grown = [[box[0] - tolerance_mm, box[1] - tolerance_mm, box[2] + 2 * tolerance_mm, box[3] + 2 * tolerance_mm] for box in boxes]

        def hits(a, b):
            return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]

        regions = result.get("regions") or []
        matched = [any(hits(box, region["bbox_mm"]) for region in regions) for box in grown]
        unmatched = [region["label"] for region in regions if not any(hits(box, region["bbox_mm"]) for box in grown)]
        report["expected_bboxes"] = len(boxes)
        report["expected_bboxes_hit"] = int(sum(matched))
        report["unmatched_regions"] = unmatched
        if not all(matched):
            report["missed_change"] = True
            extra = f"{len(boxes) - sum(matched)} expected box(es) missed"
            report["note"] = f"{report['note']}; {extra}" if report["note"] else extra
        if unmatched:
            report["false_positive"] = True
            extra = "regions outside expected boxes: " + " ".join(unmatched)
            report["note"] = f"{report['note']}; {extra}" if report["note"] else extra
        errors = []
        for box in boxes:
            cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
            distances = [math.hypot(region["centroid_mm"][0] - cx, region["centroid_mm"][1] - cy) for region in regions]
            if distances:
                errors.append(round(min(distances), 2))
        report["centroid_error_mm"] = errors
    return report


def _banner(image: np.ndarray, lines: list[str], color) -> np.ndarray:
    height = 22 * len(lines) + 8
    canvas = cv2.copyMakeBorder(image, height, 0, 0, 0, cv2.BORDER_CONSTANT, value=(255, 255, 255))
    for index, line in enumerate(lines):
        cv2.putText(canvas, line[:180], (8, 18 + 22 * index), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color if index == 0 else (30, 30, 30), 1, cv2.LINE_AA)
    return canvas


def _draw_markers(image: np.ndarray, by_view, max_width: int) -> np.ndarray:
    canvas = image.copy()
    for view, found in by_view.items():
        color = _VIEW_COLOR.get(view, (0, 0, 255))
        for marker_id, quad in found.items():
            if marker_id == "_duplicates":
                continue
            cv2.polylines(canvas, [quad.astype(np.int32).reshape(-1, 1, 2)], True, color, 2)
            cv2.putText(canvas, str(marker_id), tuple(int(v) for v in quad[0]), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA)
    scale = min(1.0, max_width / canvas.shape[1])
    if scale < 1:
        canvas = cv2.resize(canvas, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return canvas


def save_visuals(result: dict, visual: dict | None, folder: Path, harness: dict) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    files = {}
    if visual is None:
        return files
    max_width = int(harness["diagnostics"]["overlay_max_width_px"])
    lines = [f"{result['pair_id']}: {result['status']}  regions={result.get('n_regions', '-')}"]
    for warning in result["warnings"][:2]:
        lines.append(warning[:160])
    if "frame" not in visual:
        panels = [_draw_markers(visual["before"], visual["markers_before"], max_width // 2), _draw_markers(visual["after"], visual["markers_after"], max_width // 2)]
        height = max(panel.shape[0] for panel in panels)
        panels = [cv2.copyMakeBorder(panel, 0, height - panel.shape[0], 0, 4, cv2.BORDER_CONSTANT, value=(255, 255, 255)) for panel in panels]
        write_image(folder / "overlay.jpg", _banner(np.hstack(panels), lines, (40, 40, 160)))
        files["overlay"] = "overlay.jpg"
        return files
    frame = visual["frame"]
    overlay = visual["color_after"].copy()
    contours, _ = cv2.findContours(visual["roi"], cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(overlay, contours, -1, (255, 160, 0), 1)
    color = np.zeros_like(overlay)
    color[visual["mask"] > 0] = (0, 0, 220)
    overlay = cv2.addWeighted(overlay, 1.0, color, 0.45, 0)
    for region in result["regions"]:
        x, y, w, h = region["bbox_px"]
        cv2.rectangle(overlay, (x, y), (x + w, y + h), (0, 0, 255), 2)
        cv2.putText(overlay, f"{region['label']} {region['area_mm2']}mm2", (x, max(14, y - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1, cv2.LINE_AA)
    write_image(folder / "overlay.jpg", _banner(overlay, lines, (20, 20, 20)))
    files["overlay"] = "overlay.jpg"
    scaled = np.clip(visual["diff"] * (255.0 / max(1.0, 2 * harness["change"]["threshold"])), 0, 255).astype(np.uint8)
    heat = cv2.applyColorMap(scaled, cv2.COLORMAP_INFERNO)
    heat[visual["roi"] == 0] = (heat[visual["roi"] == 0] * 0.25).astype(np.uint8)
    contours, _ = cv2.findContours(visual["mask"], cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(heat, contours, -1, (255, 255, 0), 1)
    write_image(folder / "diff.png", heat)
    write_image(folder / "mask.png", visual["mask"])
    write_image(folder / "before_rectified.jpg", visual["color_before"])
    write_image(folder / "after_rectified.jpg", visual["color_after"])
    panels = [
        _draw_markers(visual["before"], visual["markers_before"], max_width // 2),
        _draw_markers(visual["after"], visual["markers_after"], max_width // 2),
    ]
    height = max(panel.shape[0] for panel in panels)
    panels = [cv2.copyMakeBorder(panel, 0, height - panel.shape[0], 0, 0, cv2.BORDER_CONSTANT, value=(255, 255, 255)) for panel in panels]
    write_image(folder / "registration.jpg", np.hstack(panels))
    files.update({"diff": "diff.png", "mask": "mask.png", "before_rectified": "before_rectified.jpg", "after_rectified": "after_rectified.jpg", "registration": "registration.jpg"})
    return files


def summarize(rows: list[dict]) -> dict:
    counts = {status: 0 for status in STATUSES}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    expected = [row for row in rows if row.get("expected")]
    return {
        "pairs": len(rows),
        "status_counts": counts,
        "pairs_with_expectation": len(expected),
        "outcome_ok": sum(1 for row in expected if row.get("outcome_ok") == 1),
        "outcome_mismatch": sum(1 for row in expected if row.get("outcome_ok") == 0),
        "false_positive_pairs": sum(int(row.get("false_positive") or 0) for row in rows),
        "missed_change_pairs": sum(int(row.get("missed_change") or 0) for row in rows),
    }


def run_folder(folder: Path, out: Path, harness: dict, kit: Kit, meta: dict, detector, forced_view: str | None, manifest_path: str | None, quiet: bool = False):
    import json

    out.mkdir(parents=True, exist_ok=True)
    pairs = find_pairs(folder)
    manifest, found_manifest = load_manifest(Path(manifest_path) if manifest_path else None, folder)
    config_hash = meta["harness_config_canonical_sha256"]
    tolerance = harness["diagnostics"]["bbox_match_tolerance_mm"]
    rows = []
    results = []
    for pair_id in sorted(pairs):
        paths = pairs[pair_id]
        spec = manifest.get(pair_id, {})
        view = spec.get("view") or forced_view
        if "before" not in paths or "after" not in paths:
            result, visual = {"pair_id": pair_id, "status": "input_error", "regions": [], "warnings": ["before or after missing"]}, None
        else:
            result, visual = process_pair(pair_id, paths["before"], paths["after"], harness, kit, detector, view)
        result["evaluation"] = evaluate(result, spec, tolerance)
        result["evaluation"]["assert"] = spec.get("assert", True) if spec else None
        result["harness_config_sha256"] = config_hash
        result["kit_config_sha256"] = meta["kit_config_sha256"]
        result["output_files"] = save_visuals(result, visual, out / pair_id, harness)
        (out / pair_id).mkdir(parents=True, exist_ok=True)
        (out / pair_id / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        results.append(result)
        evaluation = result["evaluation"]
        reg_b = result.get("registration_before") or {}
        reg_a = result.get("registration_after") or {}
        rows.append({
            "pair_id": pair_id,
            "status": result["status"],
            "expected": evaluation.get("expected") or "",
            "outcome_ok": "" if evaluation.get("outcome_ok") is None else int(bool(evaluation["outcome_ok"])),
            "false_positive": int(bool(evaluation.get("false_positive"))),
            "missed_change": int(bool(evaluation.get("missed_change"))),
            "n_regions": result.get("n_regions", ""),
            "expected_bboxes": evaluation.get("expected_bboxes", ""),
            "expected_bboxes_hit": evaluation.get("expected_bboxes_hit", ""),
            "unmatched_regions": " ".join(evaluation.get("unmatched_regions") or []),
            "centroid_error_mm": " ".join(str(value) for value in evaluation.get("centroid_error_mm") or []),
            "region_bboxes_mm": " | ".join(",".join(str(value) for value in region["bbox_mm"]) for region in result.get("regions") or []),
            "view_before": (result.get("view_before") or {}).get("view") or "",
            "view_after": (result.get("view_after") or {}).get("view") or "",
            "markers_before": reg_b.get("n_markers", ""),
            "markers_after": reg_a.get("n_markers", ""),
            "reproj_rms_mm_before": reg_b.get("reprojection_rms_mm", ""),
            "reproj_rms_mm_after": reg_a.get("reprojection_rms_mm", ""),
            "residual_shift_mm": (result.get("residual_alignment") or {}).get("shift_mm", ""),
            "changed_fraction_roi": result.get("changed_fraction_roi", ""),
            "note": evaluation.get("note", ""),
            "assert": "" if evaluation.get("assert") is None else int(bool(evaluation["assert"])),
            "warnings": " | ".join(result.get("warnings") or []),
        })
        if not quiet:
            flag = "" if evaluation.get("outcome_ok") in (None, True) else "  MISMATCH"
            print(f"{pair_id:24} {result['status']:22} regions={result.get('n_regions', '-')} expected={evaluation.get('expected') or '-'}{flag}")
    fieldnames = list(rows[0]) if rows else ["pair_id"]
    with (out / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    totals = summarize(rows)
    (out / "summary.json").write_text(json.dumps({"totals": totals, "harness_config_sha256": config_hash, "pairs": rows}, indent=2) + "\n", encoding="utf-8")
    run_info = {
        "tool": "ANASTYLOS change detection",
        "config_meta": meta,
        "harness_config_snapshot": harness,
        "manifest": found_manifest,
        "views_from_kit": {view: sorted(info["markers"]) for view, info in kit.views.items()},
        "synthetic": False,
    }
    (out / "run_info.json").write_text(json.dumps(run_info, indent=2) + "\n", encoding="utf-8")
    if not quiet:
        print(f"\n{totals['pairs']} pairs | false alarms {totals['false_positive_pairs']} | missed {totals['missed_change_pairs']} | mismatches {totals['outcome_mismatch']}")
        print(f"harness config sha256: {config_hash}")
    return results, rows, totals
