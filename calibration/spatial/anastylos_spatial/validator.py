"""Semantic validator for ``anastylos-spatial-evidence@1`` sidecars.

JSON Schema (checked at G0 with ajv) is necessary, not sufficient: this
module enforces the frozen semantic rules that a schema cannot express --
finite numbers, rigid-transform validity (orthonormal, determinant +1,
above-board negative-z), exposure/pose association, quality-gate
consistency, epoch joins, and the canonical basis vocabulary. Standard
library only; the frozen contract is consumed read-only.

Verdicts: ``accept`` (truthful record), ``needs_review`` (truthful but
incomplete -- never metric-ready), ``reject`` (contradictory or invalid).
``metric_ready`` reports the phone-side *necessary* conditions only
(frozen resolution 1: promotion additionally requires a Core validator
report plus a ``qualified`` Receipt, both Core-owned).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

from .geometry import (
    bottom_row_ok,
    camera_center_board,
    det3,
    is_valid_rotation,
    rotation_block,
)

SCHEMA_ID = "anastylos-spatial-evidence@1"

TOP_LEVEL_KEYS = frozenset({
    "schema", "evidence_id", "session_id", "source_id", "request_id",
    "request_revision", "attempt_id", "created_at", "supersedes", "asset",
    "camera", "calibration", "image_geometry", "capture_time", "pose",
    "target", "quality", "observer_relation", "interpretations",
    "provenance", "limitations",
})

BOARD_FRAMES = ("surface_a3", "surface_a4", "fixture_front", "fixture_back")
METRIC_VALIDITY = ("validated", "nominal_only", "invalid", "unknown")
POSE_VALIDITY = ("valid", "degraded", "ambiguous", "lost", "bad_calibration", "unregistered")
SOLVE_SOURCE = ("saved_image", "timestamp_interpolated", "unknown")
QUALITY_OVERALL = ("accepted", "rejected", "unknown")
GATE_STATUS = ("pass", "fail", "unknown")
GATE_NAMES = ("pose", "motion", "focus", "exposure", "visibility")
ASSOCIATION = ("verified", "operator_declared", "ambiguous", "unknown")
BASIS_VOCABULARY = ("geometric_measurement", "visual_observation", "model_inference",
                    "operator_testimony", "model_assumption")
INTERPRETATION_STATE = ("observed", "derived", "suggested", "operator_declared", "unknown")
ROI_BASIS = ("board_plane", "geometric_measurement", "operator_testimony", "model_assumption")
MIME_TYPES = ("image/jpeg", "image/png", "image/webp", "image/heic", "image/heif")

# Frozen profile guidance-hypothesis-1 (END-TO-END-ACCEPTANCE + tracking section 5).
RMS_GATE_PX = 1.2
MAX_RESIDUAL_GATE_PX = 3.0
REF_WIDTH_PX = 1280
INLIER_RATIO_GATE = 0.9
MIN_MARKERS = 3
POSE_AGE_GATE_MS = 150
BRACKET_GAP_MAX_NS = 50_000_000

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_NS = re.compile(r"^[0-9]{1,20}$")


class NonFiniteError(ValueError):
    pass


def _reject_constant(value: str):
    raise NonFiniteError(f"non-finite JSON constant: {value}")


def load_json_strict(path: str | Path) -> tuple[dict, str]:
    """Parse JSON, rejecting NaN/Infinity; returns (document, bytes sha256)."""
    raw = Path(path).read_bytes()
    return (json.loads(raw.decode("utf-8"), parse_constant=_reject_constant),
            hashlib.sha256(raw).hexdigest())


def _is_uuid(value) -> bool:
    return isinstance(value, str) and _UUID.match(value) is not None


def _is_hex64(value) -> bool:
    return isinstance(value, str) and _HEX64.match(value) is not None


def _is_utc_z(value) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _utc_seconds(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _finite_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _finite_list(values, size: int) -> bool:
    return (isinstance(values, list) and len(values) == size
            and all(_finite_number(v) for v in values))


def _text(value, max_len: int) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= max_len


class _Checker:
    def __init__(self, doc: dict):
        self.doc = doc
        self.rejects: list[str] = []
        self.reviews: list[str] = []
        self.notes: list[str] = []

    def reject(self, code: str) -> None:
        if code not in self.rejects:
            self.rejects.append(code)

    def review(self, code: str) -> None:
        if code not in self.reviews:
            self.reviews.append(code)

    def note(self, code: str) -> None:
        if code not in self.notes:
            self.notes.append(code)

    def verdict(self) -> dict:
        if self.rejects:
            verdict = "reject"
        elif self.reviews:
            verdict = "needs_review"
        else:
            verdict = "accept"
        return {"verdict": verdict, "reasons": self.rejects,
                "reviews": self.reviews, "notes": self.notes}


def _check_identity(check: _Checker, doc: dict) -> None:
    if doc.get("schema") != SCHEMA_ID:
        check.reject("SCHEMA_ID_MISMATCH")
    for key in ("evidence_id", "session_id", "source_id", "request_id", "attempt_id"):
        if not _is_uuid(doc.get(key)):
            check.reject(f"BAD_{key.upper()}")
    revision = doc.get("request_revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        check.reject("BAD_REQUEST_REVISION")
    if not _is_utc_z(doc.get("created_at")):
        check.reject("BAD_CREATED_AT")
    supersedes = doc.get("supersedes")
    if supersedes is not None and not _is_uuid(supersedes):
        check.reject("BAD_SUPERSEDES")
    if supersedes is not None and supersedes == doc.get("evidence_id"):
        check.reject("SUPERSEDES_SELF")
    extra = set(doc) - TOP_LEVEL_KEYS
    if extra:
        check.reject("UNKNOWN_TOP_LEVEL_FIELD")


def _check_asset(check: _Checker, doc: dict) -> None:
    asset = doc.get("asset")
    if not isinstance(asset, dict):
        check.reject("BAD_ASSET")
        return
    for key in ("package_id", "observation_id", "image_id", "asset_id"):
        if not _is_uuid(asset.get(key)):
            check.reject(f"BAD_ASSET_{key.upper()}")
    if not _is_hex64(asset.get("sha256")):
        check.reject("BAD_ASSET_SHA256")
    length = asset.get("byte_length")
    if not isinstance(length, int) or isinstance(length, bool) or not 1 <= length <= 26214400:
        check.reject("BAD_BYTE_LENGTH")
    if asset.get("mime") not in MIME_TYPES:
        check.reject("BAD_MIME")


def _check_camera(check: _Checker, doc: dict) -> dict:
    camera = doc.get("camera")
    if not isinstance(camera, dict):
        check.reject("BAD_CAMERA")
        return {}
    if not _text(camera.get("camera_id"), 256):
        check.reject("BAD_CAMERA_ID")
    lens = camera.get("physical_lens_id")
    if lens is not None and not _text(lens, 256):
        check.reject("BAD_PHYSICAL_LENS_ID")
    if camera.get("lens_facing") not in ("back", "front", "external"):
        check.reject("BAD_LENS_FACING")
    if not _text(camera.get("sensor_mode"), 256):
        check.reject("BAD_SENSOR_MODE")
    zoom = camera.get("zoom")
    if not _finite_number(zoom) or not 0.1 <= zoom <= 100:
        check.reject("BAD_ZOOM")
    focus = camera.get("focus_distance_diopters")
    if focus is not None and (not _finite_number(focus) or focus < 0):
        check.reject("BAD_FOCUS_DISTANCE")
    if camera.get("focus_status") not in ("locked", "converged", "searching", "unknown"):
        check.reject("BAD_FOCUS_STATUS")
    return camera


def _check_calibration_group(check: _Checker, doc: dict) -> dict:
    group = doc.get("calibration")
    if not isinstance(group, dict):
        check.reject("BAD_CALIBRATION_GROUP")
        return {}
    if not _is_uuid(group.get("profile_id")):
        check.reject("BAD_PROFILE_ID")
    if not _is_hex64(group.get("profile_sha256")):
        check.reject("BAD_PROFILE_SHA256")
    if not _is_hex64(group.get("layout_sha256")):
        check.reject("BAD_LAYOUT_SHA256")
    if not _is_uuid(group.get("board_instance_id")):
        check.reject("BAD_BOARD_INSTANCE_ID")
    if not _is_hex64(group.get("board_instance_sha256")):
        check.reject("BAD_BOARD_INSTANCE_SHA256")
    if group.get("board_frame_id") not in BOARD_FRAMES:
        check.reject("BAD_BOARD_FRAME_ID")
    if group.get("metric_validity") not in METRIC_VALIDITY:
        check.reject("BAD_METRIC_VALIDITY")
    reasons = group.get("reasons")
    if not isinstance(reasons, list) or len(reasons) > 32 or not all(_text(r, 1024) for r in reasons):
        check.reject("BAD_CALIBRATION_REASONS")
    return group


def _check_image_geometry(check: _Checker, doc: dict) -> dict:
    geo = doc.get("image_geometry")
    if not isinstance(geo, dict):
        check.reject("BAD_IMAGE_GEOMETRY")
        return {}
    for key in ("width_px", "height_px"):
        value = geo.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 32768:
            check.reject(f"BAD_{key.upper()}")
    exif = geo.get("exif_orientation")
    if not isinstance(exif, int) or isinstance(exif, bool) or not 1 <= exif <= 8:
        check.reject("BAD_EXIF_ORIENTATION")
    if geo.get("pixel_convention") != "top_left_pixel_center_0_0_x_right_y_down":
        check.reject("BAD_PIXEL_CONVENTION")
    if not _text(geo.get("calibration_pixel_frame_id"), 256):
        check.reject("BAD_PIXEL_FRAME_ID")
    transform = geo.get("reference_to_image")
    if not _finite_list(transform, 9):
        check.reject("BAD_REFERENCE_TO_IMAGE")
    else:
        det = det3([float(v) for v in transform])
        if abs(det) <= 1e-12:
            check.reject("SINGULAR_IMAGE_TRANSFORM")
        elif det < 0:
            check.reject("MIRRORED_IMAGE_TRANSFORM")
    if geo.get("distortion_before_image_transform") is not True:
        check.reject("BAD_DISTORTION_ORDER")
    if not _is_uuid(geo.get("transform_epoch")):
        check.reject("BAD_TRANSFORM_EPOCH")
    return geo


def _check_capture_time(check: _Checker, doc: dict) -> dict:
    time = doc.get("capture_time")
    if not isinstance(time, dict):
        check.reject("BAD_CAPTURE_TIME")
        return {}
    start, end = time.get("sensor_start_ns"), time.get("sensor_end_ns")
    for key, value in (("sensor_start_ns", start), ("sensor_end_ns", end)):
        if value is not None and (not isinstance(value, str) or _NS.match(value) is None):
            check.reject(f"BAD_{key.upper()}")
    if (isinstance(start, str) and isinstance(end, str)
            and _NS.match(start) and _NS.match(end) and int(start) > int(end)):
        check.reject("TIME_ORDER_VIOLATION")
    if time.get("sensor_timestamp_source") not in ("realtime", "unknown"):
        check.reject("BAD_TIMESTAMP_SOURCE")
    if not _is_uuid(time.get("boot_id")):
        check.reject("BAD_BOOT_ID")
    if not _is_utc_z(time.get("callback_utc")):
        check.reject("BAD_CALLBACK_UTC")
    monotonic = time.get("callback_monotonic_ns")
    if not isinstance(monotonic, str) or _NS.match(monotonic) is None:
        check.reject("BAD_CALLBACK_MONOTONIC_NS")
    estimate = time.get("exposure_utc_estimate")
    if estimate is not None and not _is_utc_z(estimate):
        check.reject("BAD_EXPOSURE_UTC_ESTIMATE")
    uncertainty = time.get("uncertainty_ms")
    if uncertainty is not None and (not _finite_number(uncertainty) or uncertainty < 0):
        check.reject("BAD_UNCERTAINTY_MS")
    sync = time.get("sync_exchange_ref")
    if sync is not None and (not isinstance(sync, dict) or not _is_uuid(sync.get("asset_id"))
                             or not _is_hex64(sync.get("sha256"))):
        check.reject("BAD_SYNC_EXCHANGE_REF")
    return time


def _transform_ids_ok(check: _Checker, transform: dict, camera: dict, group: dict) -> None:
    want_from = f"camera:{camera.get('camera_id')}" if isinstance(camera, dict) else None
    if not isinstance(transform.get("from"), str) or transform.get("from") != want_from:
        check.reject("TRANSFORM_FROM_MISMATCH")
    want_to = f"board:{group.get('board_instance_id')}:{group.get('board_frame_id')}"
    if not isinstance(transform.get("to"), str) or transform.get("to") != want_to:
        check.reject("TRANSFORM_TO_MISMATCH")
    if transform.get("units") != "mm":
        check.reject("TRANSFORM_UNITS_NOT_MM")


def _check_transform_matrix(check: _Checker, transform: dict) -> list[float] | None:
    matrix = transform.get("matrix")
    if not _finite_list(matrix, 16):
        check.reject("NONFINITE_TRANSFORM")
        return None
    values = [float(v) for v in matrix]
    if not bottom_row_ok(values):
        check.reject("BAD_TRANSFORM_BOTTOM_ROW")
        return None
    ok, code = is_valid_rotation(rotation_block(values))
    if not ok:
        check.reject("REFLECTION_DET_MINUS_ONE" if code == "REFLECTION" else "INVALID_ROTATION")
        return None
    if camera_center_board(values)[2] >= 0:
        check.reject("INVERTED_BOARD_Z")
        return None
    return values


def _check_pose(check: _Checker, doc: dict, camera: dict, group: dict, geo: dict) -> tuple[dict, list[float] | None]:
    pose = doc.get("pose")
    if not isinstance(pose, dict):
        check.reject("BAD_POSE")
        return {}, None
    validity = pose.get("validity")
    if validity not in POSE_VALIDITY:
        check.reject("BAD_POSE_VALIDITY")
    transform = pose.get("T_board_camera")
    matrix: list[float] | None = None
    if transform is not None:
        if not isinstance(transform, dict):
            check.reject("BAD_TRANSFORM_OBJECT")
        else:
            _transform_ids_ok(check, transform, camera, group)
            matrix = _check_transform_matrix(check, transform)
    elif validity == "valid":
        check.reject("VALIDITY_WITHOUT_TRANSFORM")
    if not _text(pose.get("method"), 256):
        check.reject("BAD_POSE_METHOD")
    if not _text(pose.get("version"), 256):
        check.reject("BAD_POSE_VERSION")
    if pose.get("solve_source") not in SOLVE_SOURCE:
        check.reject("BAD_SOLVE_SOURCE")
    if not _is_hex64(pose.get("image_sha256")):
        check.reject("BAD_POSE_IMAGE_SHA256")
    sample_ns = pose.get("sample_sensor_ns")
    if sample_ns is not None and (not isinstance(sample_ns, str) or _NS.match(sample_ns) is None):
        check.reject("BAD_SAMPLE_SENSOR_NS")
    age = pose.get("sample_age_ms")
    if age is not None and (not _finite_number(age) or age < 0):
        check.reject("BAD_SAMPLE_AGE_MS")
    bracketing = pose.get("bracketing_samples")
    if not isinstance(bracketing, list) or len(bracketing) > 2:
        check.reject("BAD_BRACKETING_SAMPLES")
        bracketing = []
    for sample in bracketing:
        if (not isinstance(sample, dict) or not _is_uuid(sample.get("sample_id"))
                or not isinstance(sample.get("sensor_ns"), str) or _NS.match(sample["sensor_ns"]) is None
                or not isinstance(sample.get("pose_asset"), dict)
                or not _is_uuid(sample["pose_asset"].get("asset_id"))
                or not _is_hex64(sample["pose_asset"].get("sha256"))):
            check.reject("BAD_BRACKETING_SAMPLE")
    _check_pose_metrics(check, pose, geo)
    reasons = pose.get("reasons")
    if not isinstance(reasons, list) or len(reasons) > 32 or not all(_text(r, 1024) for r in reasons):
        check.reject("BAD_POSE_REASONS")
    return pose, matrix


def _check_pose_metrics(check: _Checker, pose: dict, geo: dict) -> None:
    metrics = pose.get("metrics")
    if not isinstance(metrics, dict):
        check.reject("BAD_POSE_METRICS")
        return
    detected = metrics.get("detected_marker_ids")
    inliers = metrics.get("inlier_marker_ids")
    for key, value in (("detected_marker_ids", detected), ("inlier_marker_ids", inliers)):
        if (not isinstance(value, list) or len(value) > 100
                or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in value)):
            check.reject(f"BAD_{key.upper()}")
            return
    if set(inliers) - set(detected):
        check.reject("INLIER_NOT_DETECTED")
    for key in ("rms_px", "max_residual_px"):
        value = metrics.get(key)
        if value is not None and (not _finite_number(value) or value < 0):
            check.reject(f"BAD_{key.upper()}")
    ref_width = metrics.get("residual_reference_width_px")
    if not isinstance(ref_width, int) or isinstance(ref_width, bool) or not 1 <= ref_width <= 32768:
        check.reject("BAD_RESIDUAL_REFERENCE_WIDTH")
    ratio = metrics.get("inlier_ratio")
    if ratio is not None and (not _finite_number(ratio) or not 0 <= ratio <= 1):
        check.reject("BAD_INLIER_RATIO")
    for key in ("alternate_solution_separation_mm", "alternate_solution_separation_deg"):
        value = metrics.get(key)
        if value is not None and (not _finite_number(value) or value < 0):
            check.reject(f"BAD_{key.upper()}")
    if not _text(metrics.get("uncertainty_method"), 256):
        check.reject("BAD_UNCERTAINTY_METHOD")
    position = metrics.get("position_uncertainty_mm")
    if position is not None and not _finite_list(position, 3):
        check.reject("BAD_POSITION_UNCERTAINTY")
    elif isinstance(position, list) and any(v < 0 for v in position):
        check.reject("BAD_POSITION_UNCERTAINTY")
    angle = metrics.get("angle_uncertainty_deg")
    if angle is not None and (not _finite_number(angle) or angle < 0):
        check.reject("BAD_ANGLE_UNCERTAINTY")
    if str(metrics.get("uncertainty_method")).lower() == "none" and (position is not None or angle is not None):
        check.reject("UNCERTAINTY_METHOD_CONTRADICTION")
    corner = metrics.get("corner_evidence")
    if corner is not None and (not isinstance(corner, dict) or not _is_uuid(corner.get("asset_id"))
                               or not _is_hex64(corner.get("sha256"))):
        check.reject("BAD_CORNER_EVIDENCE")


def _check_target(check: _Checker, doc: dict, group: dict) -> None:
    target = doc.get("target")
    if not isinstance(target, dict):
        check.reject("BAD_TARGET")
        return
    if not _is_hex64(target.get("goal_sha256")):
        check.reject("BAD_GOAL_SHA256")
    roi = target.get("requested_roi")
    if not isinstance(roi, dict):
        check.reject("BAD_REQUESTED_ROI")
    else:
        want_frame = f"board:{group.get('board_instance_id')}:{group.get('board_frame_id')}"
        if roi.get("frame") != want_frame:
            check.reject("TARGET_FRAME_MISMATCH")
        vertices = roi.get("vertices_mm")
        if (not isinstance(vertices, list) or not 1 <= len(vertices) <= 64
                or not all(_finite_list(v, 3) for v in vertices)):
            check.reject("NONFINITE_ROI")
        elif roi.get("basis") == "board_plane" and any(abs(v[2]) > 1e-9 for v in vertices):
            check.reject("OFF_PLANE_BOARD_BASIS")
        if roi.get("basis") not in ROI_BASIS:
            check.reject("BAD_ROI_BASIS")
        refs = roi.get("evidence_refs")
        if (not isinstance(refs, list) or len(refs) > 64 or not all(
                isinstance(r, dict) and _is_uuid(r.get("asset_id")) and _is_hex64(r.get("sha256"))
                for r in refs)):
            check.reject("BAD_ROI_EVIDENCE_REFS")
    for key in ("subject_ref", "object_pose_epoch_ref"):
        value = target.get(key)
        if value is not None and not _text(value, 256):
            check.reject(f"BAD_{key.upper()}")


def _check_quality(check: _Checker, doc: dict, pose: dict, group: dict) -> dict:
    quality = doc.get("quality")
    if not isinstance(quality, dict):
        check.reject("BAD_QUALITY")
        return {}
    overall = quality.get("overall")
    if overall not in QUALITY_OVERALL:
        check.reject("BAD_QUALITY_OVERALL")
    gates = {}
    for name in GATE_NAMES:
        gate = quality.get(name)
        if not isinstance(gate, dict):
            check.reject(f"BAD_QUALITY_{name.upper()}")
            continue
        if gate.get("status") not in GATE_STATUS:
            check.reject(f"BAD_QUALITY_{name.upper()}")
        value, threshold = gate.get("value"), gate.get("threshold")
        if gate.get("status") == "pass" and (value is None or threshold is None):
            check.review("NULL_GATE_VALUE")
        for key in ("value", "threshold"):
            item = gate.get(key)
            if item is not None and not _finite_number(item):
                check.reject(f"BAD_QUALITY_{name.upper()}_{key.upper()}")
        unit = gate.get("unit")
        if unit is not None and not _text(unit, 32):
            check.reject(f"BAD_QUALITY_{name.upper()}_UNIT")
        if not _text(gate.get("algorithm"), 256):
            check.reject(f"BAD_QUALITY_{name.upper()}_ALGORITHM")
        reason = gate.get("reason")
        if reason is not None and not _text(reason, 1024):
            check.reject(f"BAD_QUALITY_{name.upper()}_REASON")
        gates[name] = gate
    if overall == "accepted":
        if pose.get("validity") != "valid":
            check.reject("CONTRADICTORY_QUALITY_POSE")
        if group.get("metric_validity") != "validated":
            check.reject("CONTRADICTORY_QUALITY_CALIBRATION")
        for name in GATE_NAMES:
            if gates.get(name, {}).get("status") != "pass":
                check.reject("CONTRADICTORY_QUALITY_GATE")
                break
    if quality.get("visibility_assessment") not in ("visible", "partially_occluded", "occluded", "unknown"):
        check.reject("BAD_VISIBILITY_ASSESSMENT")
    in_frame = quality.get("in_frame")
    if in_frame is not None and not isinstance(in_frame, bool):
        check.reject("BAD_IN_FRAME")
    if not _is_hex64(quality.get("threshold_config_sha256")):
        check.reject("BAD_THRESHOLD_CONFIG_SHA256")
    return quality


def _check_observer_relation(check: _Checker, doc: dict, time: dict, ctx: dict) -> dict:
    relation = doc.get("observer_relation")
    if not isinstance(relation, dict):
        check.reject("BAD_OBSERVER_RELATION")
        return {}
    source = relation.get("observer_source_id")
    if source is not None and not _is_uuid(source):
        check.reject("BAD_OBSERVER_SOURCE_ID")
    for key in ("run_ref", "time_mapping_ref"):
        ref = relation.get(key)
        if ref is not None and (not isinstance(ref, dict) or not _is_uuid(ref.get("asset_id"))
                                or not _is_hex64(ref.get("sha256"))):
            check.reject(f"BAD_{key.upper()}")
    epoch = relation.get("epoch_id")
    if epoch is not None and not _text(epoch, 256):
        check.reject("BAD_EPOCH_ID")
    interval = relation.get("capture_interval_video_s")
    if interval is not None:
        if (not _finite_list(interval, 2) or interval[0] > interval[1]):
            check.reject("BAD_CAPTURE_INTERVAL")
    association = relation.get("association")
    if association not in ASSOCIATION:
        check.reject("BAD_ASSOCIATION")
    reason = relation.get("reason")
    if reason is not None and not _text(reason, 1024):
        check.reject("BAD_ASSOCIATION_REASON")
    if association == "verified":
        if relation.get("run_ref") is None or epoch is None or relation.get("time_mapping_ref") is None:
            check.reject("UNVERIFIED_REFS")
        if time.get("exposure_utc_estimate") is None or time.get("uncertainty_ms") is None:
            check.reject("UNJOINABLE_VERIFIED")
    if association == "verified" or association == "operator_declared":
        _check_epoch_overlap(check, relation, time, ctx)
    else:
        _note_epoch_overlap(check, time, ctx)
    return relation


def _capture_interval_utc(time: dict) -> tuple[float, float] | None:
    estimate, uncertainty = time.get("exposure_utc_estimate"), time.get("uncertainty_ms")
    if estimate is None or uncertainty is None or not _is_utc_z(estimate):
        return None
    center = _utc_seconds(estimate)
    half = float(uncertainty) / 1000.0
    return (center - half, center + half)


def _interval_within_one_epoch(interval: tuple[float, float], epochs: list[dict]) -> bool:
    for epoch in epochs:
        try:
            start = _utc_seconds(epoch["start_utc"])
            end = _utc_seconds(epoch["end_utc"])
        except (KeyError, ValueError, TypeError):
            continue
        if start <= interval[0] and interval[1] <= end:
            return True
    return False


def _check_epoch_overlap(check: _Checker, relation: dict, time: dict, ctx: dict) -> None:
    """A verified/declared join must sit fully inside one epoch: an overlap
    with a break forbids geometry/identity carryover (schema section 5)."""
    epochs = ctx.get("epochs")
    if not epochs:
        check.note("EPOCH_CHECK_SKIPPED")
        return
    interval = _capture_interval_utc(time)
    if interval is None:
        check.note("EPOCH_CHECK_CLOCK_UNKNOWN")
        return
    if not _interval_within_one_epoch(interval, epochs):
        check.reject("EPOCH_OVERLAP_ACROSS_BREAK")


def _note_epoch_overlap(check: _Checker, time: dict, ctx: dict) -> None:
    epochs = ctx.get("epochs")
    if not epochs:
        check.note("EPOCH_CHECK_SKIPPED")
        return
    interval = _capture_interval_utc(time)
    if interval is None:
        check.note("EPOCH_CHECK_CLOCK_UNKNOWN")
        return
    if not _interval_within_one_epoch(interval, epochs):
        check.note("EPOCH_BREAK_OVERLAP_AMBIGUOUS")


def _check_interpretations(check: _Checker, doc: dict) -> None:
    interpretations = doc.get("interpretations")
    if not isinstance(interpretations, list) or len(interpretations) > 128:
        check.reject("BAD_INTERPRETATIONS")
        return
    for item in interpretations:
        if not isinstance(item, dict):
            check.reject("BAD_INTERPRETATION")
            continue
        if not _is_uuid(item.get("id")):
            check.reject("BAD_INTERPRETATION_ID")
        if not _text(item.get("kind"), 256):
            check.reject("BAD_INTERPRETATION_KIND")
        basis = item.get("basis")
        if basis not in BASIS_VOCABULARY:
            check.reject("UNKNOWN_BASIS")
        state = item.get("state")
        if state not in INTERPRETATION_STATE:
            check.reject("BAD_INTERPRETATION_STATE")
        if state == "suggested" and basis == "geometric_measurement":
            check.reject("PROMOTED_SUGGESTION")
        if state == "unknown" and basis == "geometric_measurement":
            check.review("UNCONFIRMED_MEASUREMENT")
        if basis == "operator_testimony" and state not in ("operator_declared", "unknown"):
            check.review("TESTIMONY_STATE")
        if not _text(item.get("claim"), 2048):
            check.reject("BAD_INTERPRETATION_CLAIM")
        refs = item.get("evidence_refs")
        if (not isinstance(refs, list) or not 1 <= len(refs) <= 64 or not all(
                isinstance(r, dict) and _is_uuid(r.get("asset_id")) and _is_hex64(r.get("sha256"))
                for r in refs)):
            check.reject("BAD_INTERPRETATION_EVIDENCE_REFS")
        if not _text(item.get("method"), 256):
            check.reject("BAD_INTERPRETATION_METHOD")
        region = item.get("region_ref")
        if region is not None and not _text(region, 256):
            check.reject("BAD_INTERPRETATION_REGION_REF")
        confirm = item.get("operator_confirmation_ref")
        if confirm is not None and (not isinstance(confirm, dict)
                                    or not _is_uuid(confirm.get("asset_id"))
                                    or not _is_hex64(confirm.get("sha256"))):
            check.reject("BAD_OPERATOR_CONFIRMATION_REF")


def _check_provenance(check: _Checker, doc: dict) -> dict:
    provenance = doc.get("provenance")
    if not isinstance(provenance, dict):
        check.reject("BAD_PROVENANCE")
        return {}
    if not _text(provenance.get("producer"), 256):
        check.reject("BAD_PRODUCER")
    if not _text(provenance.get("app_version"), 256):
        check.reject("BAD_APP_VERSION")
    if not _text(provenance.get("build_sha"), 64):
        check.reject("BAD_BUILD_SHA")
    for key, minimum, maximum in (("algorithm_config_refs", 1, 64), ("input_refs", 1, 256),
                                 ("operator_action_refs", 0, 64)):
        refs = provenance.get(key)
        if (not isinstance(refs, list) or not minimum <= len(refs) <= maximum or not all(
                isinstance(r, dict) and _is_uuid(r.get("asset_id")) and _is_hex64(r.get("sha256"))
                for r in refs)):
            check.reject(f"BAD_{key.upper()}")
    basis = provenance.get("basis")
    if not isinstance(basis, list) or not 1 <= len(basis) <= 5:
        check.reject("EMPTY_BASIS")
    elif any(b not in BASIS_VOCABULARY for b in basis):
        check.reject("UNKNOWN_BASIS")
    limitations = doc.get("limitations")
    if (not isinstance(limitations, list) or len(limitations) > 64
            or not all(_text(item, 1024) for item in limitations)):
        check.reject("BAD_LIMITATIONS")
    return provenance


def _check_solve_association(check: _Checker, doc: dict, pose: dict, time: dict) -> None:
    """Exposure-time association: the pose must belong to this image's exposure."""
    solve = pose.get("solve_source")
    asset = doc.get("asset") if isinstance(doc.get("asset"), dict) else {}
    if solve == "saved_image":
        if pose.get("image_sha256") != asset.get("sha256"):
            check.reject("WRONG_SOURCE_IMAGE")
        age = pose.get("sample_age_ms")
        if isinstance(age, (int, float)) and not isinstance(age, bool) and age > POSE_AGE_GATE_MS:
            check.review("STALE_SAMPLE_NOTED")
        return
    if solve == "timestamp_interpolated":
        bracketing = pose.get("bracketing_samples")
        if not isinstance(bracketing, list) or len(bracketing) != 2:
            check.reject("BRACKETING_COUNT")
            return
        if time.get("sensor_timestamp_source") != "realtime":
            check.reject("CROSS_TIMEBASE_SUBTRACTION")
            return
        start, end = time.get("sensor_start_ns"), time.get("sensor_end_ns")
        if not (isinstance(start, str) and isinstance(end, str)):
            check.reject("CANNOT_BRACKET_WITHOUT_EXPOSURE")
            return
        samples = sorted(int(s["sensor_ns"]) for s in bracketing
                         if isinstance(s, dict) and isinstance(s.get("sensor_ns"), str)
                         and _NS.match(s["sensor_ns"]))
        if len(samples) != 2:
            check.reject("BAD_BRACKETING_SAMPLE")
            return
        if samples[1] - samples[0] > BRACKET_GAP_MAX_NS:
            check.reject("BRACKET_GAP_TOO_LARGE")
        if not (samples[0] <= int(start) and int(end) <= samples[1]):
            check.reject("EXPOSURE_NOT_BRACKETED")
        return
    check.review("UNKNOWN_SOLVE_SOURCE")


def _check_sample_age(check: _Checker, pose: dict) -> None:
    solve = pose.get("solve_source")
    age = pose.get("sample_age_ms")
    if solve == "saved_image":
        return
    if age is None:
        check.review("UNKNOWN_SAMPLE_AGE")
    elif _finite_number(age) and age > POSE_AGE_GATE_MS:
        check.reject("STALE_SAMPLE")


def _check_metric_gates(check: _Checker, pose: dict, geo: dict) -> bool:
    """Frozen valid-gate thresholds. True when every gate confirms."""
    if pose.get("validity") != "valid":
        check.note("POSE_NOT_VALID")
        return False
    metrics = pose.get("metrics")
    if not isinstance(metrics, dict):
        check.reject("BAD_POSE_METRICS")
        return False
    ok = True
    width = geo.get("width_px") if isinstance(geo, dict) else None
    ref_width = metrics.get("residual_reference_width_px")
    scale = (width / ref_width) if isinstance(width, int) and isinstance(ref_width, int) and ref_width else 1.0
    rms = metrics.get("rms_px")
    if rms is None:
        check.review("NULL_RMS")
        ok = False
    elif rms > RMS_GATE_PX * scale:
        check.reject("RMS_OVER_GATE")
        ok = False
    maximum = metrics.get("max_residual_px")
    if maximum is None:
        check.review("NULL_MAX_RESIDUAL")
        ok = False
    elif maximum > MAX_RESIDUAL_GATE_PX * scale:
        check.reject("MAX_RESIDUAL_OVER_GATE")
        ok = False
    ratio = metrics.get("inlier_ratio")
    if ratio is None:
        check.review("NULL_INLIER_RATIO")
        ok = False
    elif ratio < INLIER_RATIO_GATE:
        check.reject("INLIER_RATIO_LOW")
        ok = False
    detected = metrics.get("detected_marker_ids")
    if not isinstance(detected, list) or len(set(detected)) < MIN_MARKERS:
        check.reject("INSUFFICIENT_MARKERS")
        ok = False
    if metrics.get("position_uncertainty_mm") is None or metrics.get("angle_uncertainty_deg") is None:
        check.review("NULL_UNCERTAINTY")
        ok = False
    return ok


def _check_layout_binding(check: _Checker, doc: dict, pose: dict, ctx: dict) -> None:
    layout = ctx.get("layout")
    if layout is None:
        check.note("LAYOUT_CHECK_SKIPPED")
        return
    frames = layout.get("frames") if isinstance(layout, dict) else None
    frame_id = doc["calibration"].get("board_frame_id") if isinstance(doc.get("calibration"), dict) else None
    if not isinstance(frames, dict) or frame_id not in frames:
        check.reject("UNKNOWN_FRAME")
        return
    used: set[int] = set()
    for entry in layout.get("id_ranges", []):
        if isinstance(entry, dict) and entry.get("role") == frame_id:
            used.update(entry.get("used", []))
    metrics = pose.get("metrics") if isinstance(pose, dict) else None
    detected = metrics.get("detected_marker_ids") if isinstance(metrics, dict) else None
    inliers = metrics.get("inlier_marker_ids") if isinstance(metrics, dict) else None
    if isinstance(detected, list) and isinstance(inliers, list):
        unknown = (set(detected) | set(inliers)) - used
        if unknown:
            check.reject("UNKNOWN_MARKER_ID")
    expected = ctx.get("layout_sha256")
    actual = doc["calibration"].get("layout_sha256")
    if expected is None:
        check.review("LAYOUT_HASH_UNVERIFIED")
    elif actual != expected:
        check.reject("LAYOUT_HASH_MISMATCH")


def _check_asset_resolution(check: _Checker, doc: dict, ctx: dict) -> None:
    store = ctx.get("image_shas")
    asset = doc.get("asset") if isinstance(doc.get("asset"), dict) else {}
    if store is None:
        check.note("NO_ASSET_STORE")
        return
    sha = asset.get("sha256")
    if sha not in store:
        check.review("IMAGE_UNRESOLVED")
        return
    if store[sha] != asset.get("byte_length"):
        check.reject("BYTE_LENGTH_MISMATCH")
    else:
        check.note("IMAGE_RESOLVED")


def validate_sidecar(doc: dict, ctx: dict | None = None) -> dict:
    """Validate one spatial sidecar. ``ctx`` optionally binds layout, profile,
    image bytes and epoch intervals; without it only the self-contained
    record is judged and chain checks are reported skipped."""
    ctx = ctx or {}
    check = _Checker(doc if isinstance(doc, dict) else {})
    if not isinstance(doc, dict):
        check.reject("NOT_AN_OBJECT")
        result = check.verdict()
        result["metric_ready"] = False
        return result
    _check_identity(check, doc)
    camera = _check_camera(check, doc)
    group = _check_calibration_group(check, doc)
    geo = _check_image_geometry(check, doc)
    time = _check_capture_time(check, doc)
    pose, _matrix = _check_pose(check, doc, camera, group, geo)
    _check_target(check, doc, group)
    quality = _check_quality(check, doc, pose, group)
    _check_observer_relation(check, doc, time, ctx)
    _check_interpretations(check, doc)
    provenance = _check_provenance(check, doc)
    _check_solve_association(check, doc, pose, time)
    _check_sample_age(check, pose)
    metric_gates_ok = _check_metric_gates(check, pose, geo)
    if quality.get("overall") != "accepted":
        check.note("QUALITY_NOT_ACCEPTED")
    if group.get("metric_validity") != "validated":
        check.note("CALIBRATION_NOT_VALIDATED")
    basis = provenance.get("basis") if isinstance(provenance, dict) else None
    if not isinstance(basis, list) or "geometric_measurement" not in basis:
        check.note("NO_GEOMETRIC_BASIS")
    _check_layout_binding(check, doc, pose, ctx)
    _check_asset_resolution(check, doc, ctx)

    profile_ctx = ctx.get("profile")
    if profile_ctx is not None:
        from .profiles import check_profile_binding
        check_profile_binding(check, doc, profile_ctx, ctx.get("profile_sha256"))

    result = check.verdict()
    result["metric_ready"] = (
        result["verdict"] == "accept"
        and metric_gates_ok
        and quality.get("overall") == "accepted"
        and group.get("metric_validity") == "validated"
        and isinstance(basis, list) and "geometric_measurement" in basis
        and pose.get("solve_source") in ("saved_image", "timestamp_interpolated")
    )
    return result


