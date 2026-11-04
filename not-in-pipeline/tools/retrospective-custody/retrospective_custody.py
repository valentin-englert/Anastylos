"""Offline inspection of cached screwdriver geometry against its source clip."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

import cv2
import numpy as np


SCHEMA = "retrospective-custody-origin-trace-v1"
MAX_EVIDENCE_BYTES = 64 * 1024 * 1024
MAX_TRANSCRIPT_BYTES = 64 * 1024 * 1024
MAX_MASK_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 100_000
MAX_CROPS = 12
MAX_FRAME_PIXELS = 40_000_000
MIN_MASK_PIXELS = 20
MIN_AXIS_RATIO = 1.5
TERMINAL_BAND_FRACTION = 0.05
MIN_TERMINAL_DEPTH_PX = 4.0
MAX_NARROW_END_WIDTH_RATIO = 0.80
MAX_SECONDARY_COMPONENT_AREA_RATIO = 0.10
CROP_RADIUS_PX = 96


class EvidenceError(ValueError):
    """Evidence failed a structural or integrity check."""


def sha256_file(path: Path, *, max_bytes: int | None = None) -> str:
    size = path.stat().st_size
    if max_bytes is not None and size > max_bytes:
        raise EvidenceError(f"input exceeds size limit: {path.name}")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_file_hash(path: Path, expected: str, label: str, *, max_bytes: int | None = None) -> str:
    expected = _require_sha256(expected, f"{label} sha256")
    actual = sha256_file(path, max_bytes=max_bytes)
    if actual != expected:
        raise EvidenceError(f"{label} SHA-256 mismatch")
    return actual


def _require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise EvidenceError(f"{label} must be a SHA-256 hex digest")
    try:
        int(value, 16)
    except ValueError as exc:
        raise EvidenceError(f"{label} must be a SHA-256 hex digest") from exc
    return value.lower()


def _load_manifest(path: Path) -> tuple[dict[str, Any], str]:
    if path.stat().st_size > MAX_EVIDENCE_BYTES:
        raise EvidenceError("evidence.json exceeds 64 MiB")
    evidence_hash = sha256_file(path, max_bytes=MAX_EVIDENCE_BYTES)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"invalid evidence JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != "sam-evidence-v1/request@1":
        raise EvidenceError("unsupported evidence schema")
    prompt = data.get("prompt")
    if not isinstance(prompt, dict) or prompt.get("concept") != "screwdriver":
        raise EvidenceError("evidence must be a screwdriver concept request")
    if not isinstance(data.get("records"), list) or len(data["records"]) > MAX_RECORDS:
        raise EvidenceError("evidence records are missing or exceed the record limit")
    return data, evidence_hash


def _pts_is_valid(pts_ms: float, frame_index: int, previous_pts_ms: float) -> bool:
    if not math.isfinite(pts_ms) or pts_ms < 0:
        return False
    return frame_index == 0 or pts_ms > previous_pts_ms


def _safe_mask_path(evidence_dir: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative:
        raise EvidenceError("mask path is missing")
    candidate = (evidence_dir / relative).resolve()
    if not candidate.is_relative_to(evidence_dir.resolve()):
        raise EvidenceError("mask path escapes its evidence directory")
    if not candidate.is_file():
        raise EvidenceError(f"mask file is missing: {relative}")
    if candidate.stat().st_size > MAX_MASK_BYTES:
        raise EvidenceError(f"mask exceeds size limit: {Path(relative).name}")
    return candidate


def _mask_sha256(data: dict[str, Any], mask_ref: dict[str, Any], relative: str) -> str:
    expected = _require_sha256(mask_ref.get("sha256"), "record mask sha256")
    files = data.get("files")
    masks = files.get("masks") if isinstance(files, dict) else None
    file_entry = masks.get(relative) if isinstance(masks, dict) else None
    if file_entry is None and isinstance(masks, dict):
        file_entry = masks.get(Path(relative).name)
    if not isinstance(file_entry, dict):
        raise EvidenceError(f"mask is not declared in files.masks: {relative}")
    declared = _require_sha256(file_entry.get("sha256"), "files.masks sha256")
    if expected != declared:
        raise EvidenceError(f"mask hash declarations disagree: {relative}")
    return expected


def _record_frame(record: dict[str, Any]) -> int:
    frame = record.get("frame")
    if isinstance(frame, bool) or not isinstance(frame, int) or frame < 0:
        raise EvidenceError("every video mask record needs a nonnegative frame index")
    return frame


def _record_box(record: dict[str, Any], width: int, height: int) -> tuple[int, int, int, int]:
    box = record.get("box")
    if not isinstance(box, dict):
        raise EvidenceError("mask record has no box")
    try:
        left, top, right, bottom = (int(box[k]) for k in ("left", "top", "right", "bottom"))
    except (KeyError, TypeError, ValueError) as exc:
        raise EvidenceError("mask record box is malformed") from exc
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        raise EvidenceError("mask record box falls outside decoded display dimensions")
    return left, top, right, bottom


def _geometry(mask: np.ndarray, box: tuple[int, int, int, int], display: tuple[int, int]) -> dict[str, Any]:
    left, top, right, bottom = box
    display_width, display_height = display
    if left == 0 or top == 0 or right == display_width or bottom == display_height:
        return {"status": "ambiguous", "reason": "MASK_TOUCHES_DISPLAY_BORDER"}
    points_yx = np.argwhere(mask > 0)
    if len(points_yx) < MIN_MASK_PIXELS:
        return {"status": "ambiguous", "reason": "MASK_TOO_SMALL"}

    components, _, stats, _ = cv2.connectedComponentsWithStats(
        (mask > 0).astype(np.uint8), connectivity=8
    )
    component_areas = sorted((int(stats[i, cv2.CC_STAT_AREA]) for i in range(1, components)), reverse=True)
    if not component_areas:
        return {"status": "ambiguous", "reason": "EMPTY_MASK"}
    if len(component_areas) > 1 and component_areas[1] >= component_areas[0] * MAX_SECONDARY_COMPONENT_AREA_RATIO:
        return {"status": "ambiguous", "reason": "MULTIPLE_COMPONENTS"}

    y, x = points_yx.astype(np.float64).T
    points = np.column_stack((x, y))
    centered = points - points.mean(axis=0)
    covariance = np.cov(centered, rowvar=False)
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    short_axis = max(float(eigenvalues[0]), 1e-9)
    long_axis = max(float(eigenvalues[1]), 1e-9)
    aspect_ratio = math.sqrt(long_axis / short_axis)
    if aspect_ratio < MIN_AXIS_RATIO:
        return {"status": "ambiguous", "reason": "MASK_NOT_ELONGATED", "aspect_ratio": aspect_ratio}

    axis = eigenvectors[:, 1]
    along = centered @ axis
    across = centered @ eigenvectors[:, 0]
    span = float(along.max() - along.min())
    terminal_depth = max(MIN_TERMINAL_DEPTH_PX, span * TERMINAL_BAND_FRACTION)
    end_masks = (along <= along.min() + terminal_depth, along >= along.max() - terminal_depth)
    widths = [float(np.ptp(across[selection])) if np.count_nonzero(selection) > 1 else 0.0 for selection in end_masks]
    smallest = min(widths)
    largest = max(widths)
    if largest <= 0 or smallest / largest > MAX_NARROW_END_WIDTH_RATIO:
        return {
            "status": "ambiguous",
            "reason": "ENDPOINT_AMBIGUOUS",
            "aspect_ratio": aspect_ratio,
            "terminal_widths_px": widths,
        }

    selected = end_masks[0] if widths[0] < widths[1] else end_masks[1]
    terminal_points = points[selected]
    tip_x, tip_y = terminal_points.mean(axis=0)
    full_x = left + float(tip_x)
    full_y = top + float(tip_y)
    geometry = {
        "aspect_ratio": round(aspect_ratio, 4),
        "terminal_widths_px": [round(value, 2) for value in widths],
        "narrow_end_xy_px": [round(full_x, 2), round(full_y, 2)],
    }
    return {"status": "narrow_end_candidate", "reason": "NARROW_TERMINAL_GEOMETRY_ONLY", **geometry}


def _contains(roi: tuple[int, int, int, int] | None, point: list[float] | None) -> bool:
    if roi is None or point is None:
        return False
    x, y = point
    left, top, right, bottom = roi
    return left <= x < right and top <= y < bottom


def _validate_roi(value: str | None, width: int, height: int) -> tuple[int, int, int, int] | None:
    if value is None:
        return None
    try:
        roi = tuple(int(part.strip()) for part in value.split(","))
    except ValueError as exc:
        raise EvidenceError("ROI must be left,top,right,bottom integers") from exc
    if len(roi) != 4:
        raise EvidenceError("ROI must be left,top,right,bottom integers")
    left, top, right, bottom = roi
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        raise EvidenceError("static source ROI falls outside decoded display dimensions")
    return roi


def _validate_output_dir(path: Path) -> None:
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise EvidenceError("output directory must be absent or empty")


def _select_crop_frames(rows: list[dict[str, Any]], max_crops: int) -> list[int]:
    candidates = sorted({
        row["frame_index"] for row in rows
        if row.get("narrow_end_in_source_area") and row.get("geometry", {}).get("narrow_end_xy_px")
    })
    if len(candidates) <= max_crops:
        return candidates
    positions = np.linspace(0, len(candidates) - 1, max_crops, dtype=int)
    return [candidates[int(position)] for position in positions]


def inspect_track(
    clip_path: Path,
    evidence_path: Path,
    output_dir: Path,
    source_roi_value: str | None,
    max_crops: int = MAX_CROPS,
) -> dict[str, Any]:
    started = perf_counter()
    if not 1 <= max_crops <= MAX_CROPS:
        raise EvidenceError(f"max-crops must be between 1 and {MAX_CROPS}")
    if not clip_path.is_file() or not evidence_path.is_file():
        raise EvidenceError("clip and evidence.json must both be existing files")
    if clip_path.stat().st_size > 4 * 1024 * 1024 * 1024:
        raise EvidenceError("clip exceeds 4 GiB limit")
    _validate_output_dir(output_dir)

    data, evidence_sha = _load_manifest(evidence_path)
    files = data.get("files")
    transcript = files.get("transcript") if isinstance(files, dict) else None
    if not isinstance(transcript, dict) or not isinstance(transcript.get("path"), str):
        raise EvidenceError("cached transcript reference is missing")
    transcript_path = (evidence_path.parent / transcript["path"]).resolve()
    if not transcript_path.is_relative_to(evidence_path.parent.resolve()):
        raise EvidenceError("transcript path escapes its evidence directory")
    if not transcript_path.is_file():
        raise EvidenceError("cached transcript is missing")
    transcript_sha = verify_file_hash(
        transcript_path, transcript.get("sha256"), "transcript", max_bytes=MAX_TRANSCRIPT_BYTES
    )
    media = data.get("media")
    media_clip = media.get("clip") if isinstance(media, dict) else None
    if not isinstance(media, dict) or media.get("kind") != "video":
        raise EvidenceError("evidence media kind is not video")
    clip_sha = verify_file_hash(clip_path, media.get("sha256"), "clip")
    if isinstance(media_clip, dict):
        sidecar_sha = _require_sha256(media_clip.get("clip_sha256"), "clip sidecar sha256")
        if sidecar_sha != clip_sha:
            raise EvidenceError("clip SHA-256 does not match its provenance sidecar")

    capture = cv2.VideoCapture(str(clip_path))
    if not capture.isOpened():
        raise EvidenceError("OpenCV could not open the clip")
    orientation_meta = capture.get(cv2.CAP_PROP_ORIENTATION_META)
    orientation_auto = capture.get(cv2.CAP_PROP_ORIENTATION_AUTO)
    ok, first_frame = capture.read()
    if not ok:
        capture.release()
        raise EvidenceError("clip decoded zero frames")
    display_height, display_width = first_frame.shape[:2]
    if display_width * display_height > MAX_FRAME_PIXELS:
        capture.release()
        raise EvidenceError("decoded frame exceeds pixel limit")
    source_roi = _validate_roi(source_roi_value, display_width, display_height)

    rows: list[dict[str, Any]] = []
    records_by_frame: dict[int, list[tuple[int, dict[str, Any]]]] = {}
    all_mask_hashes: set[str] = set()
    for order, record in enumerate(data["records"]):
        if not isinstance(record, dict):
            capture.release()
            raise EvidenceError("evidence contains a malformed record")
        frame_index = _record_frame(record)
        records_by_frame.setdefault(frame_index, []).append((order, record))

    for frame_index in sorted(records_by_frame):
        duplicate_frame = len(records_by_frame[frame_index]) > 1
        for order, record in records_by_frame[frame_index]:
            if duplicate_frame:
                rows.append({
                    "input_order": order,
                    "frame_index": frame_index,
                    "geometry_status": "ambiguous",
                    "contact_status": "ambiguous",
                    "reasons": ["DUPLICATE_FRAME_RECORDS"],
                })
                continue
            mask_ref = record.get("mask")
            if not isinstance(mask_ref, dict):
                capture.release()
                raise EvidenceError("video record has no mask reference")
            relative_mask = mask_ref.get("png")
            mask_path = _safe_mask_path(evidence_path.parent, relative_mask)
            expected_mask_sha = _mask_sha256(data, mask_ref, relative_mask)
            actual_mask_sha = verify_file_hash(mask_path, expected_mask_sha, "mask", max_bytes=MAX_MASK_BYTES)
            all_mask_hashes.add(actual_mask_sha)
            mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
            if mask is None:
                capture.release()
                raise EvidenceError(f"could not decode mask: {Path(relative_mask).name}")
            box = _record_box(record, display_width, display_height)
            left, top, right, bottom = box
            native_mask_height, native_mask_width = mask.shape
            box_width, box_height = right - left, bottom - top
            mask_resized_to_box = (native_mask_width, native_mask_height) != (box_width, box_height)
            if mask_resized_to_box:
                mask = cv2.resize(mask, (box_width, box_height), interpolation=cv2.INTER_NEAREST)
            geometry = _geometry(mask, box, (display_width, display_height))
            point = geometry.get("narrow_end_xy_px")
            inside_roi = _contains(source_roi, point)
            narrow_end_in_source_area = geometry["status"] == "narrow_end_candidate" and inside_roi
            if narrow_end_in_source_area:
                contact_status = "unknown"
                reasons = [geometry["reason"], "MASK_ENDPOINT_IS_NOT_VERIFIED_TOOL_TIP", "PHYSICAL_CONTACT_NOT_VERIFIED"]
            elif geometry["status"] == "ambiguous":
                contact_status = "ambiguous"
                reasons = [geometry["reason"]]
            elif source_roi is None:
                contact_status = "unknown"
                reasons = [geometry["reason"], "NO_STATIC_SOURCE_SETUP"]
            else:
                contact_status = "no_candidate"
                reasons = [geometry["reason"], "TIP_OUTSIDE_STATIC_SOURCE_AREA"]
            rows.append({
                "input_order": order,
                "frame_index": frame_index,
                "timebase": "decoded_frame_index_and_pts_in_clip",
                "object_id": str(record.get("object_id", "")),
                "track_scope": str(record.get("scope", "")),
                "mask_sha256": actual_mask_sha,
                "mask_native_dimensions_wh": [native_mask_width, native_mask_height],
                "mask_resized_to_record_box": mask_resized_to_box,
                "sam_confidence": record.get("confidence"),
                "geometry": geometry,
                "geometry_status": geometry["status"],
                "source_area_id": "operator_static_roi" if source_roi else None,
                "narrow_end_in_source_area": narrow_end_in_source_area,
                "contact_status": contact_status,
                "source_state": "unknown",
                "source_change": "SOURCE_CHANGE_UNVERIFIED",
                "reasons": reasons,
            })

    candidate_frames = _select_crop_frames(rows, max_crops)
    rows_by_frame = {row["frame_index"]: row for row in rows if "geometry" in row}
    selected_frames = set(candidate_frames)
    frame_crops: dict[int, np.ndarray] = {}
    pts_by_frame: dict[int, float] = {}
    frame_index = 0
    decoded_count = 0
    previous_pts = -math.inf
    timebase_status = "valid"
    frame = first_frame
    while True:
        height, width = frame.shape[:2]
        if (width, height) != (display_width, display_height):
            timebase_status = "invalid_frame_dimensions"
        pts_ms = float(capture.get(cv2.CAP_PROP_POS_MSEC))
        if not _pts_is_valid(pts_ms, frame_index, previous_pts):
            timebase_status = "non_monotonic_pts"
        previous_pts = pts_ms
        if frame_index in records_by_frame:
            pts_by_frame[frame_index] = pts_ms
        if frame_index in selected_frames:
            row = rows_by_frame[frame_index]
            point = row["geometry"]["narrow_end_xy_px"]
            x, y = (int(round(value)) for value in point)
            radius = CROP_RADIUS_PX
            left, top = max(0, x - radius), max(0, y - radius)
            right, bottom = min(display_width, x + radius + 1), min(display_height, y + radius + 1)
            frame_crops[frame_index] = frame[top:bottom, left:right].copy()
            row["source_crop_bbox_px"] = [left, top, right, bottom]
        decoded_count += 1
        next_ok, next_frame = capture.read()
        if not next_ok:
            break
        frame_index += 1
        frame = next_frame
    capture.release()
    if timebase_status != "valid":
        raise EvidenceError("decoded frame dimensions or PTS are not a valid monotonic timebase")

    for row in rows:
        index = row["frame_index"]
        pts_ms = pts_by_frame.get(index)
        if pts_ms is None:
            row["pts_ms"] = None
            row["geometry_status"] = "unknown"
            row["contact_status"] = "unknown"
            row["narrow_end_in_source_area"] = False
            if isinstance(row.get("geometry"), dict):
                row["geometry"]["status"] = "unknown"
                row["geometry"]["reason"] = "FRAME_INDEX_OUT_OF_RANGE"
            row["reasons"] = ["FRAME_INDEX_OUT_OF_RANGE"]
        else:
            row["pts_ms"] = round(pts_ms, 3)

    output_dir.mkdir(parents=True, exist_ok=True)
    crop_dir = output_dir / "crops"
    crop_dir.mkdir()
    crop_refs: list[dict[str, Any]] = []
    for index in candidate_frames:
        crop = frame_crops.get(index)
        if crop is None:
            continue
        crop_name = f"source-mask-narrow-end-frame-{index:06d}.png"
        crop_path = crop_dir / crop_name
        if not cv2.imwrite(str(crop_path), crop):
            raise EvidenceError(f"could not write source crop {crop_name}")
        row = rows_by_frame[index]
        reference = {
            "path": f"crops/{crop_name}",
            "sha256": sha256_file(crop_path),
            "bbox_px": row["source_crop_bbox_px"],
            "frame_index": index,
            "pts_ms": row["pts_ms"],
        }
        row["source_narrow_end_crop"] = reference
        crop_refs.append(reference)

    sidecar = media_clip if isinstance(media_clip, dict) else {}
    source_video_sha = sidecar.get("src_sha256")
    if source_video_sha is not None:
        source_video_sha = _require_sha256(source_video_sha, "source video sidecar sha256")
    counts = {
        "sam_records": len(data["records"]),
        "decoded_frames": decoded_count,
        "narrow_end_geometry_candidates_in_source_roi": sum(
            row.get("narrow_end_in_source_area", False) for row in rows
        ),
        "narrow_end_geometry_frame_indexes": [
            row["frame_index"] for row in rows if row.get("narrow_end_in_source_area", False)
        ],
        "physical_contact_candidates_supported": None,
        "ambiguous_geometry": sum(row.get("geometry_status") == "ambiguous" for row in rows),
        "source_changes_verified": None,
        "deposit_triggers_observed": None,
        "correspondence_candidates_supported": None,
    }
    summary = {
        "schema": SCHEMA,
        "status": "PENDING_EVIDENCE",
        "run_scope": "origin_localization_gate_only",
        "inputs": {
            "clip_sha256": clip_sha,
            "clip_path": str(clip_path.resolve()),
            "evidence_sha256": evidence_sha,
            "evidence_path": str(evidence_path.resolve()),
            "transcript_sha256": transcript_sha,
            "source_video_sha256_from_clip_sidecar": source_video_sha,
            "source_video_sha256_directly_verified": False,
        },
        "coordinate_frame": {
            "width_px": display_width,
            "height_px": display_height,
            "origin": "top_left",
            "axes": "x_right_y_down",
            "orientation_meta_degrees": orientation_meta,
            "orientation_auto": bool(orientation_auto),
            "media_metadata_width_height": [media.get("width"), media.get("height")],
            "source_timeline_mapping": "unverified; frame refs stay clip-local",
        },
        "timebase": {
            "basis": "OpenCV decoded frame index and CAP_PROP_POS_MSEC after each read",
            "scope": "clip-local",
            "decoded_frame_count": decoded_count,
            "sam_reported_nb_frames": media.get("nb_frames"),
            "pts_monotonic": timebase_status == "valid",
        },
        "setup": {
            "source_roi_px": list(source_roi) if source_roi else None,
            "source_roi_is_static_operator_setup": bool(source_roi),
            "source_roi_scope": "fixed work area only; not a screw-site label",
            "source_roi_provenance": "static operator setup; no event times or labels",
            "event_endpoints_or_labels_supplied": False,
        },
        "run_config": {
            "runner": "retrospective_custody.py",
            "max_crops": max_crops,
            "crop_radius_px": CROP_RADIUS_PX,
            "min_mask_pixels": MIN_MASK_PIXELS,
            "min_axis_ratio": MIN_AXIS_RATIO,
            "terminal_band_fraction": TERMINAL_BAND_FRACTION,
            "minimum_terminal_depth_px": MIN_TERMINAL_DEPTH_PX,
            "max_narrow_end_width_ratio": MAX_NARROW_END_WIDTH_RATIO,
            "max_secondary_component_area_ratio": MAX_SECONDARY_COMPONENT_AREA_RATIO,
            "mask_mapping": "box-local nearest-neighbor resize to recorded box",
            "border_policy": "ambiguous",
        },
        "counts": counts,
        "capabilities": {
            "tool_tip_and_physical_contact": "NOT_IMPLEMENTED",
            "source_object_presence_absence": "NOT_IMPLEMENTED",
            "deposit_detection": "NOT_IMPLEMENTED",
            "retrospective_correspondence": "NOT_IMPLEMENTED",
        },
        "limitations": [
            "SAM prompt and track id are observations, not physical identity.",
            "The mask may cover only a tool handle; its narrow endpoint is not verified as the screwdriver tip.",
            "No explicit source-object present-to-absent observation is available in this run.",
            "No destination deposit observation is joined in this run.",
            "A clip-local frame/PTS is not mapped to the original video timeline.",
            "No Assembly Memory writes or physical confirmation occur.",
        ],
        "records": rows,
        "destination_crop_refs": [],
        "selected_source_crop_refs": crop_refs,
        "evidence": {
            "mask_sha256_count": len(all_mask_hashes),
            "artifacts": [],
        },
        "elapsed_seconds": round(perf_counter() - started, 3),
    }

    trace_path = output_dir / "trace.jsonl"
    with trace_path.open("w", encoding="utf-8", newline="\n") as trace:
        for row in rows:
            trace.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    artifacts = [{"path": "trace.jsonl", "sha256": sha256_file(trace_path)}]
    artifacts.extend({"path": ref["path"], "sha256": ref["sha256"]} for ref in crop_refs)
    summary["evidence"]["artifacts"] = artifacts
    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clip", required=True, type=Path, help="clip represented by cached SAM evidence")
    parser.add_argument("--evidence", required=True, type=Path, help="hash-declared sam-evidence-v1 evidence.json")
    parser.add_argument("--out", required=True, type=Path, help="new or empty directory for bounded run evidence")
    parser.add_argument("--source-roi", help="optional static operator ROI: left,top,right,bottom display pixels")
    parser.add_argument("--max-crops", type=int, default=MAX_CROPS, help=f"maximum selected mask-terminal crops (1-{MAX_CROPS})")
    args = parser.parse_args(argv)
    try:
        summary = inspect_track(args.clip, args.evidence, args.out, args.source_roi, args.max_crops)
    except (EvidenceError, OSError, cv2.error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "status": summary["status"],
        "report": str((args.out / "report.json").resolve()),
        "trace": str((args.out / "trace.jsonl").resolve()),
        "counts": summary["counts"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
