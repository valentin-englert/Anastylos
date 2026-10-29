"""Derived immutable spatial dataset manifests (``anastylos-spatial-capture-set@1``).

A manifest cites the Core sealed delivery manifest (never invents it),
partitions evaluated sidecars into accepted/rejected/missing/skipped,
and separates transfer completion (``result``) from reconstruction
suitability (``geometry_ready``). Redundant and degenerate views are
flagged; counting *useful* views stays with the CAD evaluator (frozen
preregistration). Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from .geometry import axis_angle_deg, center_separation_mm, optical_axis_board

DATASET_SCHEMA_ID = "anastylos-spatial-capture-set@1"
RESULTS = ("complete", "partial", "cancelled", "failed")

# Provisional Wave-1 heuristics (evidence_class=synthetic, not frozen thresholds).
REDUNDANT_SEPARATION_MM = 1.0
MIN_BASELINE_MM = 5.0
PURE_ROTATION_AXIS_DEG = 5.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _entry_key(entry: dict) -> tuple:
    return (entry.get("request_id"), entry.get("request_revision"))


def partition_entries(evaluated: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split evaluated sidecars into accepted / not-accepted manifest entries.

    Each evaluated item carries ``sidecar`` (doc), ``sidecar_sha256``,
    ``verdict`` and ``metric_ready``. Only ``accept`` verdicts land in the
    accepted list; anything else is listed as rejected with its codes as
    the reason -- never silently dropped.
    """
    accepted, rejected = [], []
    for item in evaluated:
        sidecar = item["sidecar"]
        ref = {"request_id": sidecar["request_id"], "request_revision": sidecar["request_revision"],
               "attempt_id": sidecar["attempt_id"], "evidence_id": sidecar["evidence_id"],
               "evidence_sha256": item["sidecar_sha256"],
               "image_sha256": sidecar["asset"]["sha256"]}
        if item.get("verdict") == "accept":
            ref["metric_ready"] = bool(item.get("metric_ready"))
            accepted.append(ref)
        else:
            ref["reason"] = list(item.get("reasons") or []) + list(item.get("reviews") or [])
            ref["verdict"] = item.get("verdict")
            rejected.append(ref)
    return accepted, rejected


def check_entry_uniqueness(accepted: list[dict], multi_image_targets: set[tuple] | None = None) -> list[str]:
    """Each accepted entry is unique on (request_id, revision) unless the
    target explicitly allows several images."""
    multi_image_targets = multi_image_targets or set()
    seen: set[tuple] = set()
    issues = []
    for entry in accepted:
        key = _entry_key(entry)
        if key in seen and key not in multi_image_targets:
            issues.append(f"DUPLICATE_REQUEST_REVISION:{key[0]}:{key[1]}")
        seen.add(key)
    return issues


def flag_view_pairs(accepted_with_poses: list[dict]) -> tuple[list[list[str]], list[list[str]]]:
    """Flag redundant (same viewpoint) and degenerate (pure rotation) pairs.

    Items carry ``evidence_id`` and ``T_board_camera`` (matrix or None).
    Returns (redundant_pairs, degenerate_pairs) of evidence-id pairs.
    """
    posed = [e for e in accepted_with_poses if e.get("T_board_camera")]
    redundant, degenerate = [], []
    for i in range(len(posed)):
        for j in range(i + 1, len(posed)):
            a, b = posed[i]["T_board_camera"], posed[j]["T_board_camera"]
            separation = center_separation_mm(a, b)
            if separation < REDUNDANT_SEPARATION_MM:
                axis = axis_angle_deg(optical_axis_board(a), optical_axis_board(b))
                pair = [posed[i]["evidence_id"], posed[j]["evidence_id"]]
                if axis >= PURE_ROTATION_AXIS_DEG:
                    degenerate.append(pair)
                else:
                    redundant.append(pair)
    return redundant, degenerate


def max_baseline_mm(posed: list[dict]) -> float:
    best = 0.0
    matrices = [e["T_board_camera"] for e in posed if e.get("T_board_camera")]
    for i in range(len(matrices)):
        for j in range(i + 1, len(matrices)):
            best = max(best, center_separation_mm(matrices[i], matrices[j]))
    return best


def build_dataset(evaluated: list[dict], delivery: dict | None, required: list[dict],
                  skipped: list[dict] | None = None, multi_image_targets: set[tuple] | None = None,
                  capture_set_id: str | None = None, created_at: str | None = None,
                  session_id: str | None = None, target_plan_sha256: str | None = None) -> tuple[dict, list[str]]:
    """Assemble a dataset manifest. Returns (manifest, issues).

    ``issues`` holds blocking codes (duplicates, mixed sessions, invented
    delivery); an empty list means the manifest is internally consistent.
    ``result=complete`` additionally requires every required current
    target revision to hold an accepted entry.
    """
    skipped = skipped or []
    issues: list[str] = []
    accepted, rejected = partition_entries(evaluated)

    sessions = {item["sidecar"]["session_id"] for item in evaluated}
    if len(sessions) > 1:
        issues.append("MIXED_SESSIONS")
    resolved_session = session_id or (next(iter(sessions)) if len(sessions) == 1 else None)
    if resolved_session is None:
        issues.append("NO_SESSION")

    issues.extend(check_entry_uniqueness(accepted, multi_image_targets))

    accepted_keys = {_entry_key(e) for e in accepted}
    missing = [r for r in required if (r.get("request_id"), r.get("request_revision")) not in accepted_keys]

    if delivery is not None:
        for key in ("delivery_capture_set_id", "delivery_capture_set_revision", "delivery_manifest_sha256"):
            if not delivery.get(key):
                issues.append("INCOMPLETE_DELIVERY_REF")

    if delivery is not None and not missing and not issues and accepted:
        result = "complete"
    elif evaluated and not accepted:
        result = "failed"
    else:
        result = "partial"

    posed = [{"evidence_id": item["sidecar"]["evidence_id"],
              "T_board_camera": ((item["sidecar"]["pose"] or {}).get("T_board_camera") or {}).get("matrix")}
             for item in evaluated if item.get("verdict") == "accept"]
    redundant, degenerate = flag_view_pairs(posed)
    baseline = max_baseline_mm(posed)
    frames = {item["sidecar"]["calibration"]["board_frame_id"] for item in evaluated
              if item.get("verdict") == "accept"}
    epochs = {item["sidecar"]["target"].get("object_pose_epoch_ref") for item in evaluated
              if item.get("verdict") == "accept"}

    geometry_notes = []
    if len(posed) < 2:
        geometry_notes.append("FEWER_THAN_TWO_ACCEPTED_POSES")
    if baseline < MIN_BASELINE_MM:
        geometry_notes.append("INSUFFICIENT_BASELINE")
    if len(frames) > 1:
        geometry_notes.append("MIXED_BOARD_FRAMES")
    if len(epochs - {None}) > 1:
        geometry_notes.append("MIXED_OBJECT_EPOCHS")
    if redundant:
        geometry_notes.append("REDUNDANT_VIEWS_PRESENT")
    if degenerate:
        geometry_notes.append("DEGENERATE_VIEWS_PRESENT")
    geometry_ready = (len(posed) >= 2 and baseline >= MIN_BASELINE_MM
                      and len(frames) <= 1 and len(epochs - {None}) <= 1)

    manifest = {
        "schema": DATASET_SCHEMA_ID,
        "capture_set_id": capture_set_id or str(uuid.uuid4()),
        "session_id": resolved_session,
        "revision": 1,
        "created_at": created_at or _utc_now(),
        "delivery_capture_set_id": (delivery or {}).get("delivery_capture_set_id"),
        "delivery_capture_set_revision": (delivery or {}).get("delivery_capture_set_revision"),
        "delivery_manifest_sha256": (delivery or {}).get("delivery_manifest_sha256"),
        "target_plan_sha256": target_plan_sha256,
        "accepted": accepted,
        "rejected": rejected,
        "missing": missing,
        "skipped": skipped,
        "redundant_view_pairs": redundant,
        "degenerate_view_pairs": degenerate,
        "max_baseline_mm": baseline,
        "result": result,
        "geometry_ready": geometry_ready,
        "geometry_notes": geometry_notes,
    }
    return manifest, issues


def manifest_bytes(manifest: dict) -> bytes:
    return (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def manifest_sha256(manifest: dict) -> str:
    return hashlib.sha256(manifest_bytes(manifest)).hexdigest()


def validate_dataset_manifest(doc: dict) -> list[str]:
    """Structural check of a dataset manifest. Returns blocking codes."""
    issues = []
    if not isinstance(doc, dict):
        return ["NOT_AN_OBJECT"]
    if doc.get("schema") != DATASET_SCHEMA_ID:
        issues.append("DATASET_SCHEMA_ID_MISMATCH")
    for key in ("capture_set_id", "session_id", "created_at"):
        if not doc.get(key):
            issues.append(f"MISSING_{key.upper()}")
    if doc.get("result") not in RESULTS:
        issues.append("BAD_RESULT")
    if not isinstance(doc.get("geometry_ready"), bool):
        issues.append("BAD_GEOMETRY_READY")
    if doc.get("result") == "complete":
        if not doc.get("delivery_capture_set_id") or not doc.get("delivery_manifest_sha256"):
            issues.append("COMPLETE_WITHOUT_DELIVERY")
        if doc.get("missing"):
            issues.append("COMPLETE_WITH_MISSING")
        if not doc.get("accepted"):
            issues.append("COMPLETE_WITHOUT_ACCEPTED")
    for key in ("accepted", "rejected", "missing", "skipped"):
        if not isinstance(doc.get(key), list):
            issues.append(f"BAD_{key.upper()}")
    return issues
