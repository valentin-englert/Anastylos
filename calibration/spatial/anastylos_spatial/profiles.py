"""Validation for ``anastylos-camera-calibration@1`` profiles and
``anastylos-board-instance@1`` print records (schema section 4).

A profile is an immutable offline fit result: brown_conrady_5 model,
reference size, K, distortion, pixel convention, focus/zoom/sensor/crop
binding, and a validation group. Only ``validated`` profiles are
measurement-ready, and only with 25-40 calibration views and >=8
held-out poses (frozen resolution, IMPLEMENTATION-DEPENDENCIES). A board
instance carries the *measured* print bars; nominal layout millimetres
alone are never measured print scale. Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import math
import re

PROFILE_SCHEMA_ID = "anastylos-camera-calibration@1"
BOARD_SCHEMA_ID = "anastylos-board-instance@1"
CAMERA_MODEL = "brown_conrady_5"
PIXEL_CONVENTION = "top_left_pixel_center_0_0_x_right_y_down"

MIN_CALIBRATION_VIEWS = 25
MAX_CALIBRATION_VIEWS = 40
MIN_HELD_OUT_VIEWS = 8
HELD_OUT_RMS_GATE_PX = 0.8
MAX_VIEW_RMS_GATE_PX = 1.5
BAR_ERROR_LIMIT = 0.005  # 0.5 % per axis; above this reject, never silently rescale.

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def canonical_sha256(doc: dict) -> str:
    """Logical hash: sorted keys, compact separators, UTF-8 (same convention
    as the change-detection harness canonical hash)."""
    data = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _text(value, max_len: int) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= max_len


def _is_uuid(value) -> bool:
    return isinstance(value, str) and _UUID.match(value) is not None


def _is_utc_z(value) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        from datetime import datetime
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def validate_profile(doc: dict) -> dict:
    """Returns {"verdict", "reasons", "reviews", "notes"} for a profile doc."""
    rejects: list[str] = []
    reviews: list[str] = []
    notes: list[str] = []

    def reject(code: str) -> None:
        if code not in rejects:
            rejects.append(code)

    def review(code: str) -> None:
        if code not in reviews:
            reviews.append(code)

    if not isinstance(doc, dict):
        return {"verdict": "reject", "reasons": ["NOT_AN_OBJECT"], "reviews": [], "notes": []}
    if doc.get("schema") != PROFILE_SCHEMA_ID:
        reject("PROFILE_SCHEMA_ID_MISMATCH")
    if not _is_uuid(doc.get("profile_id")):
        reject("BAD_PROFILE_ID")
    if not _is_uuid(doc.get("source_id")):
        reject("BAD_PROFILE_SOURCE_ID")
    if not _text(doc.get("camera_id"), 256):
        reject("BAD_PROFILE_CAMERA_ID")
    lens = doc.get("physical_lens_id")
    if lens is not None and not _text(lens, 256):
        reject("BAD_PROFILE_LENS_ID")
    if not _text(doc.get("sensor_mode"), 256):
        reject("BAD_PROFILE_SENSOR_MODE")
    if not _is_utc_z(doc.get("created_at")):
        reject("BAD_PROFILE_CREATED_AT")
    for key in ("algorithm", "algorithm_version", "build_sha"):
        if not _text(doc.get(key), 256 if key != "build_sha" else 64):
            reject(f"BAD_PROFILE_{key.upper()}")
    if doc.get("camera_model") != CAMERA_MODEL:
        reject("BAD_CAMERA_MODEL")
    size = doc.get("reference_image_size_px")
    if (not isinstance(size, list) or len(size) != 2
            or not all(isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 32768 for v in size)):
        reject("BAD_REFERENCE_SIZE")
    k = doc.get("K")
    if (not isinstance(k, list) or len(k) != 9 or not all(_finite(v) for v in k)):
        reject("NONFINITE_K")
    elif not (k[0] > 0 and k[4] > 0):
        reject("NONPOSITIVE_FOCAL_LENGTH")
    dist = doc.get("distortion")
    if not isinstance(dist, list) or len(dist) != 5 or not all(_finite(v) for v in dist):
        reject("BAD_DISTORTION")
    if doc.get("pixel_convention") != PIXEL_CONVENTION:
        reject("BAD_PROFILE_PIXEL_CONVENTION")
    if not _text(doc.get("reference_pixel_frame_id"), 256):
        reject("BAD_REFERENCE_PIXEL_FRAME_ID")
    focus = doc.get("focus_distance_diopters")
    if focus is not None and (not _finite(focus) or focus < 0):
        reject("BAD_PROFILE_FOCUS")
    zoom = doc.get("zoom")
    if not _finite(zoom) or not 0.1 <= zoom <= 100:
        reject("BAD_PROFILE_ZOOM")
    crop = doc.get("sensor_crop")
    if crop is not None and (not isinstance(crop, list) or len(crop) != 4
                             or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in crop)):
        reject("BAD_SENSOR_CROP")
    assets = doc.get("calibration_image_assets")
    if (not isinstance(assets, list) or not assets
            or not all(isinstance(a, dict) and _is_uuid(a.get("asset_id")) and _is_hex(a.get("sha256"))
                       for a in assets)):
        reject("BAD_CALIBRATION_IMAGE_ASSETS")
    _check_validation_group(doc.get("validation"), reject, review)
    if doc.get("metric_validity") not in ("validated", "nominal_only", "invalid", "unknown"):
        reject("BAD_PROFILE_METRIC_VALIDITY")
    if doc.get("metric_validity") == "validated" and reviews:
        reject("VALIDATED_PROFILE_WITH_REVIEWS")
    limitations = doc.get("limitations")
    if (not isinstance(limitations, list)
            or not all(isinstance(item, str) and 1 <= len(item) <= 1024 for item in limitations)):
        reject("BAD_PROFILE_LIMITATIONS")

    if rejects:
        verdict = "reject"
    elif reviews:
        verdict = "needs_review"
    else:
        verdict = "accept"
    return {"verdict": verdict, "reasons": rejects, "reviews": reviews, "notes": notes}


def _is_hex(value) -> bool:
    return isinstance(value, str) and _HEX64.match(value) is not None


def _check_validation_group(group, reject, review) -> None:
    if not isinstance(group, dict):
        reject("BAD_VALIDATION_GROUP")
        return
    views = group.get("calibration_view_ids")
    if not isinstance(views, list) or not all(_is_uuid(v) for v in views):
        reject("BAD_CALIBRATION_VIEW_IDS")
        return
    held_out = group.get("held_out_view_ids")
    if not isinstance(held_out, list) or not all(_is_uuid(v) for v in held_out):
        reject("BAD_HELD_OUT_VIEW_IDS")
        return
    if set(held_out) - set(views):
        reject("HELD_OUT_NOT_IN_VIEWS")
    if not MIN_CALIBRATION_VIEWS <= len(views) <= MAX_CALIBRATION_VIEWS:
        review("CALIBRATION_VIEW_COUNT")
    if len(held_out) < MIN_HELD_OUT_VIEWS:
        review("HELD_OUT_COUNT")
    per_view = group.get("per_view_rms_px")
    if (not isinstance(per_view, dict) or set(per_view) != set(views)
            or not all(_finite(v) and v >= 0 for v in per_view.values())):
        reject("BAD_PER_VIEW_RMS")
    else:
        if max(per_view.values()) > MAX_VIEW_RMS_GATE_PX:
            review("MAX_VIEW_RMS_OVER_GATE")
    held_rms = group.get("held_out_rms_px")
    if held_rms is None or not _finite(held_rms) or held_rms < 0:
        reject("BAD_HELD_OUT_RMS")
    elif held_rms > HELD_OUT_RMS_GATE_PX:
        review("HELD_OUT_RMS_OVER_GATE")
    coverage = group.get("edge_coverage")
    if coverage is not None and (not _finite(coverage) or not 0 <= coverage <= 1):
        reject("BAD_EDGE_COVERAGE")
    residual = group.get("residual_map_ref")
    if residual is not None and (not isinstance(residual, dict) or not _is_uuid(residual.get("asset_id"))
                                 or not _is_hex(residual.get("sha256"))):
        reject("BAD_RESIDUAL_MAP_REF")
    scale = group.get("independent_scale_check_mm")
    if scale is None:
        review("NO_INDEPENDENT_SCALE_CHECK")
    elif (not isinstance(scale, dict) or not _finite(scale.get("nominal_mm"))
          or not _finite(scale.get("measured_mm")) or not _finite(scale.get("uncertainty_mm"))):
        reject("BAD_SCALE_CHECK")
    config = group.get("threshold_config_sha256")
    if config is not None and not _is_hex(config):
        reject("BAD_VALIDATION_CONFIG_SHA")


def validate_board_instance(doc: dict) -> dict:
    """Returns {"verdict", "reasons", "reviews", "notes"} for a board record."""
    rejects: list[str] = []
    reviews: list[str] = []
    notes: list[str] = []

    def reject(code: str) -> None:
        if code not in rejects:
            rejects.append(code)

    def review(code: str) -> None:
        if code not in reviews:
            reviews.append(code)

    if not isinstance(doc, dict):
        return {"verdict": "reject", "reasons": ["NOT_AN_OBJECT"], "reviews": [], "notes": []}
    if doc.get("schema") != BOARD_SCHEMA_ID:
        reject("BOARD_SCHEMA_ID_MISMATCH")
    if not _is_uuid(doc.get("board_instance_id")):
        reject("BAD_BOARD_INSTANCE_ID")
    if not _is_hex(doc.get("layout_sha256")):
        reject("BAD_BOARD_LAYOUT_SHA256")
    if doc.get("board_frame_id") not in ("surface_a3", "surface_a4", "fixture_front", "fixture_back"):
        reject("BAD_BOARD_FRAME_ID")
    bars = doc.get("measured_bars_mm")
    if bars is None:
        review("NO_MEASURED_BARS")
    elif not isinstance(bars, dict):
        reject("BAD_MEASURED_BARS")
    else:
        for axis in ("horizontal_mm", "vertical_mm"):
            value = bars.get(axis)
            if not _finite(value) or value <= 0:
                reject("BAD_MEASURED_BARS")
        for key, limit in (("instrument", 256), ("measured_by", 256)):
            if not _text(bars.get(key), limit):
                reject(f"BAD_BARS_{key.upper()}")
        uncertainty = bars.get("uncertainty_mm")
        if not _finite(uncertainty) or uncertainty < 0:
            reject("BAD_BARS_UNCERTAINTY")
        if not _is_utc_z(bars.get("measured_at")):
            reject("BAD_BARS_MEASURED_AT")
        nominal = bars.get("nominal_mm", 100.0)
        if _finite(nominal) and nominal > 0 and _finite(bars.get("horizontal_mm")) and _finite(bars.get("vertical_mm")):
            errors = [abs(bars["horizontal_mm"] - nominal) / nominal,
                      abs(bars["vertical_mm"] - nominal) / nominal]
            if max(errors) > BAR_ERROR_LIMIT:
                reject("PRINT_BAR_ERROR")
    flatness = doc.get("flatness")
    if flatness is not None and flatness not in ("flat", "warped", "unknown"):
        reject("BAD_FLATNESS")
    if flatness == "warped":
        reject("WARPED_BOARD")
    photos = doc.get("source_photo_refs")
    if photos is not None and (not isinstance(photos, list) or not all(
            isinstance(p, dict) and _is_uuid(p.get("asset_id")) and _is_hex(p.get("sha256")) for p in photos)):
        reject("BAD_BOARD_PHOTO_REFS")

    if rejects:
        verdict = "reject"
    elif reviews:
        verdict = "needs_review"
    else:
        verdict = "accept"
    return {"verdict": verdict, "reasons": rejects, "reviews": reviews, "notes": notes}


def check_profile_binding(check, doc: dict, profile: dict, profile_sha256: str | None) -> None:
    """Bind a sidecar's calibration claim to a validated profile document."""
    group = doc.get("calibration") if isinstance(doc.get("calibration"), dict) else {}
    camera = doc.get("camera") if isinstance(doc.get("camera"), dict) else {}
    geo = doc.get("image_geometry") if isinstance(doc.get("image_geometry"), dict) else {}
    result = validate_profile(profile)
    for reason in result["reasons"]:
        check.reject(f"PROFILE_{reason}")
    for item in result["reviews"]:
        check.review(f"PROFILE_{item}")
    if not isinstance(profile, dict):
        return
    if profile.get("profile_id") != group.get("profile_id"):
        check.reject("PROFILE_ID_MISMATCH")
    if profile_sha256 is None:
        check.review("PROFILE_HASH_UNVERIFIED")
    elif group.get("profile_sha256") != profile_sha256:
        check.reject("PROFILE_HASH_MISMATCH")
    if profile.get("camera_id") != camera.get("camera_id"):
        check.reject("PROFILE_CAMERA_MISMATCH")
    if profile.get("sensor_mode") != camera.get("sensor_mode"):
        check.reject("PROFILE_SENSOR_MODE_MISMATCH")
    if profile.get("zoom") != camera.get("zoom"):
        check.reject("PROFILE_ZOOM_MISMATCH")
    if profile.get("reference_pixel_frame_id") != geo.get("calibration_pixel_frame_id"):
        check.reject("PIXEL_FRAME_MISMATCH")
