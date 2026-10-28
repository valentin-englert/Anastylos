"""Validation for anastylos-observation-v1 and anastylos-sync-v1.

Standard library only. Raises ObservationError on any contract
violation; fail closed, never guess.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

OBS_SCHEMA = "anastylos-observation-v1"
SYNC_SCHEMA = "anastylos-sync-v1"
MAX_OBS_BYTES = 16 * 1024 * 1024
MAX_EVENTS = 50_000
MAX_STRING = 512

KINDS = (
    "tool_interaction",
    "source_state",
    "tray_state",
    "visibility_gap",
    "pose_change",
)
STATUSES = ("observed", "inferred")
ACTIONS = (
    "contact",
    "torque",
    "grasp",
    "release",
    "pickup_candidate",
    "handoff",
    "carry",
    "unknown",
)
PRESENCE = ("present", "absent", "loosened", "unknown", "occluded")
CHANGES = ("addition", "removal", "unchanged", "unknown")
SCOPES = ("source", "tray", "camera", "global")
GAP_REASONS = ("occlusion", "missing_frames", "out_of_frame", "blur", "unknown")

GRASP_ACTIONS = ("grasp", "pickup_candidate")


class ObservationError(ValueError):
    """An observation file or sync manifest violates the contract."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _finite_num(value: Any) -> bool:
    return _is_num(value) and value == value and value not in (float("inf"), float("-inf"))


def _req_str(obj: dict[str, Any], key: str, label: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value or len(value) > MAX_STRING:
        raise ObservationError(f"{label} must be a non-empty string <= {MAX_STRING} chars")
    return value


def _opt_str(obj: dict[str, Any], key: str, label: str) -> str | None:
    value = obj.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > MAX_STRING:
        raise ObservationError(f"{label} must be null or a non-empty string <= {MAX_STRING} chars")
    return value


def _req_sha256_or_null(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) != 64:
        raise ObservationError(f"{label} must be null or a SHA-256 hex digest")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ObservationError(f"{label} must be null or a SHA-256 hex digest") from exc
    return value.lower()


def _check_time(time: Any, event_id: str) -> dict[str, Any]:
    if not isinstance(time, dict):
        raise ObservationError(f"event {event_id}: time must be an object")
    clock_id = time.get("clock_id")
    if not isinstance(clock_id, str) or not clock_id or len(clock_id) > MAX_STRING:
        raise ObservationError(f"event {event_id}: time.clock_id must be a non-empty string")
    start = time.get("start_ms")
    end = time.get("end_ms")
    if not _finite_num(start) or start < 0 or not _finite_num(end) or end < 0:
        raise ObservationError(f"event {event_id}: time bounds must be numbers >= 0")
    if end < start:
        raise ObservationError(f"event {event_id}: time.end_ms must be >= time.start_ms")
    return {"clock_id": clock_id, "start_ms": start, "end_ms": end}


def _check_location(location: Any, event_id: str) -> dict[str, Any] | None:
    if location is None:
        return None
    if not isinstance(location, dict):
        raise ObservationError(f"event {event_id}: location must be null or an object")
    camera_id = location.get("camera_id")
    if not isinstance(camera_id, str) or not camera_id or len(camera_id) > MAX_STRING:
        raise ObservationError(f"event {event_id}: location.camera_id must be a non-empty string")
    xy_px = location.get("xy_px")
    if xy_px is not None:
        if (
            not isinstance(xy_px, list)
            or len(xy_px) != 2
            or not all(_finite_num(v) for v in xy_px)
        ):
            raise ObservationError(f"event {event_id}: location.xy_px must be null or [x, y] numbers")
        xy_px = [xy_px[0], xy_px[1]]
    uncertainty = location.get("uncertainty_px")
    if uncertainty is not None and (not _finite_num(uncertainty) or uncertainty < 0):
        raise ObservationError(f"event {event_id}: location.uncertainty_px must be null or a number >= 0")
    epoch = location.get("pose_epoch")
    if epoch is not None and (not isinstance(epoch, str) or not epoch or len(epoch) > MAX_STRING):
        raise ObservationError(f"event {event_id}: location.pose_epoch must be null or a non-empty string")
    frame = location.get("frame")
    if frame is not None and frame != "display":
        raise ObservationError(f"event {event_id}: location.frame must be null or 'display'")
    return {
        "camera_id": camera_id,
        "xy_px": xy_px,
        "uncertainty_px": uncertainty,
        "pose_epoch": epoch,
        "frame": frame,
    }


def _check_evidence(evidence: Any, event_id: str, time: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        raise ObservationError(f"event {event_id}: evidence must be an object")
    camera_id = evidence.get("camera_id")
    if camera_id is not None and (
        not isinstance(camera_id, str) or not camera_id or len(camera_id) > MAX_STRING
    ):
        raise ObservationError(f"event {event_id}: evidence.camera_id must be null or a non-empty string")
    frame_index = evidence.get("frame_index")
    if frame_index is not None and (
        isinstance(frame_index, bool) or not isinstance(frame_index, int) or frame_index < 0
    ):
        raise ObservationError(f"event {event_id}: evidence.frame_index must be null or an integer >= 0")
    pts = evidence.get("pts_ms")
    if pts is not None:
        if not _finite_num(pts) or pts < 0:
            raise ObservationError(f"event {event_id}: evidence.pts_ms must be null or a number >= 0")
        if pts < time["start_ms"] or pts > time["end_ms"]:
            raise ObservationError(f"event {event_id}: evidence.pts_ms must lie inside the event time interval")
    source_ref = evidence.get("source_ref")
    if not isinstance(source_ref, str) or not source_ref or len(source_ref) > MAX_STRING:
        raise ObservationError(f"event {event_id}: evidence.source_ref must be a non-empty string")
    sha = _req_sha256_or_null(evidence.get("source_sha256"), f"event {event_id}: evidence.source_sha256")
    return {
        "camera_id": camera_id,
        "frame_index": frame_index,
        "pts_ms": pts,
        "source_ref": source_ref,
        "source_sha256": sha,
    }


def _check_appearance(value: Any, event_id: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ObservationError(f"event {event_id}: appearance must be null or an object")
    out: dict[str, Any] = {}
    for key in ("component_class", "head_drive", "marks"):
        field = value.get(key)
        if field is not None and (not isinstance(field, str) or not field or len(field) > MAX_STRING):
            raise ObservationError(f"event {event_id}: appearance.{key} must be null or a non-empty string")
        out[key] = field
    for key in ("length_mm", "diameter_mm"):
        field = value.get(key)
        if field is not None and (not _finite_num(field) or field <= 0):
            raise ObservationError(f"event {event_id}: appearance.{key} must be null or a number > 0")
        out[key] = field
    return out


def _check_state(kind: str, state: Any, event_id: str) -> dict[str, Any] | None:
    if state is None:
        return None
    if not isinstance(state, dict):
        raise ObservationError(f"event {event_id}: state must be null or an object")
    if kind == "tool_interaction":
        actor = state.get("actor")
        if not isinstance(actor, str) or not actor or len(actor) > MAX_STRING:
            raise ObservationError(f"event {event_id}: state.actor must be a non-empty string")
        action = state.get("action")
        if action not in ACTIONS:
            raise ObservationError(f"event {event_id}: state.action must be one of {list(ACTIONS)}")
        target = state.get("target_ref")
        if target is not None and (not isinstance(target, str) or not target or len(target) > MAX_STRING):
            raise ObservationError(f"event {event_id}: state.target_ref must be null or a non-empty string")
        contact = state.get("contact_verified")
        if not isinstance(contact, bool):
            raise ObservationError(f"event {event_id}: state.contact_verified must be a boolean")
        handoff_from = state.get("handoff_from")
        handoff_to = state.get("handoff_to")
        if action == "handoff":
            for key, val in (("handoff_from", handoff_from), ("handoff_to", handoff_to)):
                if not isinstance(val, str) or not val or len(val) > MAX_STRING:
                    raise ObservationError(f"event {event_id}: state.{key} must be a non-empty string for handoff")
        elif handoff_from is not None or handoff_to is not None:
            raise ObservationError(f"event {event_id}: handoff endpoints must be null unless action is handoff")
        return {
            "actor": actor,
            "action": action,
            "target_ref": target,
            "contact_verified": contact,
            "handoff_from": handoff_from,
            "handoff_to": handoff_to,
        }
    if kind == "source_state":
        site_ref = state.get("site_ref")
        if not isinstance(site_ref, str) or not site_ref or len(site_ref) > MAX_STRING:
            raise ObservationError(f"event {event_id}: state.site_ref must be a non-empty string")
        presence = state.get("presence")
        if presence not in PRESENCE:
            raise ObservationError(f"event {event_id}: state.presence must be one of {list(PRESENCE)}")
        verified = state.get("verified")
        if not isinstance(verified, bool):
            raise ObservationError(f"event {event_id}: state.verified must be a boolean")
        return {
            "site_ref": site_ref,
            "presence": presence,
            "verified": verified,
            "appearance": _check_appearance(state.get("appearance"), event_id),
        }
    if kind == "tray_state":
        tray_ref = state.get("tray_ref")
        if not isinstance(tray_ref, str) or not tray_ref or len(tray_ref) > MAX_STRING:
            raise ObservationError(f"event {event_id}: state.tray_ref must be a non-empty string")
        cell_ref = state.get("cell_ref")
        if cell_ref is not None and (not isinstance(cell_ref, str) or not cell_ref or len(cell_ref) > MAX_STRING):
            raise ObservationError(f"event {event_id}: state.cell_ref must be null or a non-empty string")
        change = state.get("change")
        if change not in CHANGES:
            raise ObservationError(f"event {event_id}: state.change must be one of {list(CHANGES)}")
        supported = state.get("supported")
        if not isinstance(supported, bool):
            raise ObservationError(f"event {event_id}: state.supported must be a boolean")
        return {
            "tray_ref": tray_ref,
            "cell_ref": cell_ref,
            "change": change,
            "supported": supported,
            "appearance": _check_appearance(state.get("appearance"), event_id),
        }
    if kind == "visibility_gap":
        scope = state.get("scope")
        if scope not in SCOPES:
            raise ObservationError(f"event {event_id}: state.scope must be one of {list(SCOPES)}")
        scope_ref = state.get("scope_ref")
        if scope == "global":
            if scope_ref is not None:
                raise ObservationError(f"event {event_id}: global gaps carry no scope_ref")
        elif scope_ref is not None and (
            not isinstance(scope_ref, str) or not scope_ref or len(scope_ref) > MAX_STRING
        ):
            raise ObservationError(f"event {event_id}: state.scope_ref must be null or a non-empty string")
        reason = state.get("reason")
        if reason not in GAP_REASONS:
            raise ObservationError(f"event {event_id}: state.reason must be one of {list(GAP_REASONS)}")
        return {"scope": scope, "scope_ref": scope_ref, "reason": reason}
    if kind == "pose_change":
        device_ref = state.get("device_ref")
        if not isinstance(device_ref, str) or not device_ref or len(device_ref) > MAX_STRING:
            raise ObservationError(f"event {event_id}: state.device_ref must be a non-empty string")
        from_epoch = state.get("from_epoch")
        to_epoch = state.get("to_epoch")
        for key, val in (("from_epoch", from_epoch), ("to_epoch", to_epoch)):
            if not isinstance(val, str) or not val or len(val) > MAX_STRING:
                raise ObservationError(f"event {event_id}: state.{key} must be a non-empty string")
        links = state.get("site_links")
        if not isinstance(links, list):
            raise ObservationError(f"event {event_id}: state.site_links must be an array")
        checked = []
        for link in links:
            if not isinstance(link, dict):
                raise ObservationError(f"event {event_id}: site_links entries must be objects")
            site_ref = link.get("site_ref")
            link_from = link.get("from_epoch")
            link_to = link.get("to_epoch")
            for key, val in (
                ("site_ref", site_ref),
                ("from_epoch", link_from),
                ("to_epoch", link_to),
            ):
                if not isinstance(val, str) or not val or len(val) > MAX_STRING:
                    raise ObservationError(f"event {event_id}: site_links.{key} must be a non-empty string")
            checked.append({"site_ref": site_ref, "from_epoch": link_from, "to_epoch": link_to})
        return {
            "device_ref": device_ref,
            "from_epoch": from_epoch,
            "to_epoch": to_epoch,
            "site_links": checked,
        }
    raise ObservationError(f"event {event_id}: unknown kind {kind!r}")


def check_event(raw: Any) -> dict[str, Any]:
    """Validate one event object; returns a normalized copy."""
    if not isinstance(raw, dict):
        raise ObservationError("every event must be an object")
    if raw.get("schema_version") != OBS_SCHEMA:
        raise ObservationError("every event must carry schema_version anastylos-observation-v1")
    event_id = raw.get("event_id")
    if not isinstance(event_id, str) or not event_id or len(event_id) > MAX_STRING:
        raise ObservationError("event_id must be a non-empty string")
    kind = raw.get("kind")
    if kind not in KINDS:
        raise ObservationError(f"event {event_id}: kind must be one of {list(KINDS)}")
    status = raw.get("status")
    if status not in STATUSES:
        raise ObservationError(f"event {event_id}: status must be observed or inferred")
    if "limitations" not in raw or not isinstance(raw["limitations"], list):
        raise ObservationError(f"event {event_id}: limitations must be an array")
    for entry in raw["limitations"]:
        if not isinstance(entry, str) or len(entry) > MAX_STRING:
            raise ObservationError(f"event {event_id}: limitations entries must be strings")
    time = _check_time(raw.get("time"), event_id)
    return {
        "schema_version": OBS_SCHEMA,
        "event_id": event_id,
        "kind": kind,
        "status": status,
        "time": time,
        "location": _check_location(raw.get("location"), event_id),
        "state": _check_state(kind, raw.get("state"), event_id),
        "evidence": _check_evidence(raw.get("evidence"), event_id, time),
        "limitations": list(raw["limitations"]),
    }


def load_observation_file(path: Path) -> tuple[list[dict[str, Any]], str]:
    """Load and validate one observation file. Returns (events, sha256)."""
    size = path.stat().st_size
    if size > MAX_OBS_BYTES:
        raise ObservationError(f"{path.name} exceeds the 16 MiB size limit")
    digest = sha256_file(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ObservationError(f"{path.name} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != OBS_SCHEMA:
        raise ObservationError(f"{path.name}: schema_version must be {OBS_SCHEMA}")
    session_ref = data.get("session_ref")
    if not isinstance(session_ref, str) or len(session_ref) > MAX_STRING:
        raise ObservationError(f"{path.name}: session_ref must be a string")
    raw_events = data.get("events")
    if not isinstance(raw_events, list) or not raw_events or len(raw_events) > MAX_EVENTS:
        raise ObservationError(f"{path.name}: events must be a non-empty array <= {MAX_EVENTS}")
    return [check_event(raw) for raw in raw_events], digest


def load_sync_manifest(path: Path | None) -> tuple[list[dict[str, Any]], str | None]:
    """Load and validate the sync manifest. Returns (offsets, sha256)."""
    if path is None:
        return [], None
    if path.stat().st_size > MAX_OBS_BYTES:
        raise ObservationError(f"{path.name} exceeds the 16 MiB size limit")
    digest = sha256_file(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ObservationError(f"{path.name} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != SYNC_SCHEMA:
        raise ObservationError(f"{path.name}: schema_version must be {SYNC_SCHEMA}")
    raw_offsets = data.get("offsets")
    if not isinstance(raw_offsets, list):
        raise ObservationError(f"{path.name}: offsets must be an array")
    offsets = []
    for entry in raw_offsets:
        if not isinstance(entry, dict):
            raise ObservationError(f"{path.name}: offsets entries must be objects")
        from_clock = entry.get("from_clock")
        to_clock = entry.get("to_clock")
        for key, val in (("from_clock", from_clock), ("to_clock", to_clock)):
            if not isinstance(val, str) or not val or len(val) > MAX_STRING:
                raise ObservationError(f"{path.name}: offsets.{key} must be a non-empty string")
        offset_min = entry.get("offset_min_ms")
        offset_max = entry.get("offset_max_ms")
        if (
            not _finite_num(offset_min)
            or not _finite_num(offset_max)
            or offset_max < offset_min
        ):
            raise ObservationError(f"{path.name}: offset bounds must be numbers with max >= min")
        validated = entry.get("validated")
        if not isinstance(validated, bool):
            raise ObservationError(f"{path.name}: offsets.validated must be a boolean")
        basis_ref = entry.get("basis_ref")
        if not isinstance(basis_ref, str) or not basis_ref or len(basis_ref) > MAX_STRING:
            raise ObservationError(f"{path.name}: offsets.basis_ref must be a non-empty string")
        offsets.append(
            {
                "from_clock": from_clock,
                "to_clock": to_clock,
                "offset_min_ms": offset_min,
                "offset_max_ms": offset_max,
                "validated": validated,
                "basis_ref": basis_ref,
            }
        )
    return offsets, digest


def main(argv: list[str] | None = None) -> int:
    """CLI: validate observation files (and an optional sync manifest)."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--obs", required=True, action="append", type=Path,
                        help="anastylos-observation-v1 file (repeatable)")
    parser.add_argument("--sync", type=Path, default=None,
                        help="optional anastylos-sync-v1 manifest")
    args = parser.parse_args(argv)
    try:
        total = 0
        seen: set[str] = set()
        files = []
        for path in args.obs:
            if not path.is_file():
                raise ObservationError(f"input is not a file: {path}")
            file_events, digest = load_observation_file(path)
            for event in file_events:
                if event["event_id"] in seen:
                    raise ObservationError(f"duplicate event_id: {event['event_id']}")
                seen.add(event["event_id"])
            total += len(file_events)
            files.append({"path": str(path), "sha256": digest, "events": len(file_events)})
        sync_info = None
        if args.sync is not None:
            if not args.sync.is_file():
                raise ObservationError(f"input is not a file: {args.sync}")
            offsets, sync_digest = load_sync_manifest(args.sync)
            sync_info = {
                "path": str(args.sync),
                "sha256": sync_digest,
                "offsets": len(offsets),
                "validated": sum(1 for entry in offsets if entry["validated"]),
            }
    except (ObservationError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "status": "VALID",
        "observation_schema": OBS_SCHEMA,
        "files": files,
        "total_events": total,
        "sync": sync_info,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
