"""Append-only disassembly event log. Standard library only.

The store is one JSON object per line. Events are never edited or deleted:
a correction is a new event whose ``supersedes`` names the earlier event id.
Coordinates are stored verbatim and never transformed here.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path


class ValidationError(ValueError):
    """An event was rejected before anything was written."""


class CorruptLogError(ValueError):
    """The log on disk cannot be trusted; the reader guesses nothing."""


# Observation events describe what was seen, never what a part is called.
IDENTITY_KEYS = ("part", "part_name", "part_id", "name")

EVENT_TYPES = ("observation", "tray_link", "declared_removal", "storage_link", "compatible_group", "phase")
# Operator-declared content (complete disassemblies): parts without image evidence, storage outside
# tray cells, interchangeable groups and pose phases. Each names who declared it; none of it is
# automatic evidence.
DECLARABLE_TARGETS = ("observation", "declared_removal")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_file(path: str | Path) -> str | None:
    """Hash of a file's bytes, or None when the file is not readable."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def evidence_ref(result_json: str, pair_id: str, region: str) -> dict:
    return {
        "result_json": result_json,
        "pair_id": pair_id,
        "region": region,
        "result_sha256": sha256_file(result_json),
    }


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _require_text(event: dict, key: str, what: str) -> str:
    value = event.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{what} needs a non-empty string {key!r}")
    return value


def _require_numbers(event: dict, key: str, count: int, what: str) -> list:
    value = event.get(key)
    if not isinstance(value, list) or len(value) != count or not all(_is_number(v) for v in value):
        raise ValidationError(f"{what} needs {key!r} as {count} finite numbers")
    return value


def _require_evidence(value, what: str) -> dict:
    if not isinstance(value, dict):
        raise ValidationError(f"{what} needs an evidence reference")
    for key in ("result_json", "pair_id", "region"):
        item = value.get(key)
        if not isinstance(item, str) or not item.strip():
            raise ValidationError(f"{what} needs a non-empty string evidence {key!r}")
    if value.get("result_sha256") is not None and not isinstance(value["result_sha256"], str):
        raise ValidationError(f"{what} needs evidence 'result_sha256' as a hex string or null")
    return value


def validate_observation(event: dict, known_ids: set[str]) -> None:
    for key in IDENTITY_KEYS:
        if event.get(key) is not None:
            raise ValidationError(f"observation must not invent part identity ({key!r} is forbidden)")
    _require_text(event, "session", "observation")
    _require_text(event, "source_view", "observation")
    _require_numbers(event, "bbox_mm", 4, "observation")
    _require_numbers(event, "centroid_mm", 2, "observation")
    _require_evidence(event.get("evidence"), "observation")
    if event.get("t_s") is not None and not _is_number(event["t_s"]):
        raise ValidationError("observation needs 't_s' as a finite number or null")
    supersedes = event.get("supersedes")
    if supersedes is not None and supersedes not in known_ids:
        raise ValidationError(f"observation supersedes unknown id {supersedes!r}")


def validate_tray_link(event: dict, known_ids: set[str], successors: dict[str, str]) -> None:
    _require_text(event, "session", "tray_link")
    target = _require_text(event, "observation_id", "tray_link")
    if target not in known_ids:
        raise ValidationError(f"tray_link points at unknown observation {target!r}")
    if target in successors:
        raise ValidationError(
            f"tray_link points at superseded observation {target!r}; link {successors[target]!r} instead"
        )
    tray = event.get("tray")
    if not isinstance(tray, dict) or not tray.get("sheet_id") or tray.get("number") is None:
        raise ValidationError("tray_link needs a tray with 'sheet_id' and 'number'")
    if not isinstance(tray["number"], int) or isinstance(tray["number"], bool):
        raise ValidationError("tray_link needs tray 'number' as an integer")
    basis = event.get("basis")
    if not isinstance(basis, dict):
        raise ValidationError("tray_link needs a 'basis': evidence or human confirmation")
    kind = basis.get("kind")
    if kind == "evidence":
        _require_evidence(basis, "tray_link basis")
    elif kind == "human":
        confirmed = basis.get("confirmed_by")
        if not isinstance(confirmed, str) or not confirmed.strip():
            raise ValidationError("tray_link human basis needs 'confirmed_by'")
    else:
        raise ValidationError("tray_link basis kind must be 'evidence' or 'human'")
    supersedes = event.get("supersedes")
    if supersedes is not None and supersedes not in known_ids:
        raise ValidationError(f"tray_link supersedes unknown id {supersedes!r}")


def _require_known(target: str, events_by_id: dict[str, dict], types: tuple, what: str) -> dict:
    event = events_by_id.get(target)
    if event is None or event.get("type") not in types:
        raise ValidationError(f"{what} points at unknown {' or '.join(types)} {target!r}")
    return event


def _require_supersedes(event: dict, events_by_id: dict[str, dict], what: str) -> None:
    supersedes = event.get("supersedes")
    if supersedes is not None and supersedes not in events_by_id:
        raise ValidationError(f"{what} supersedes unknown id {supersedes!r}")


def validate_phase(event: dict, events_by_id: dict[str, dict]) -> None:
    _require_text(event, "session", "phase")
    _require_text(event, "label", "phase")
    after = event.get("starts_after")
    if after is not None:
        _require_known(after, events_by_id, ("phase",), "phase starts_after")
    _require_supersedes(event, events_by_id, "phase")


def validate_declared_removal(event: dict, events_by_id: dict[str, dict]) -> None:
    _require_text(event, "session", "declared_removal")
    _require_text(event, "operator_label", "declared_removal")
    _require_text(event, "source", "declared_removal")
    _require_text(event, "declared_by", "declared_removal")
    _require_known(_require_text(event, "phase", "declared_removal"), events_by_id, ("phase",), "declared_removal")
    if event.get("t_s") is not None and not _is_number(event["t_s"]):
        raise ValidationError("declared_removal needs 't_s' as a finite number or null")
    related = event.get("observation_id")
    if related is not None:
        _require_known(related, events_by_id, ("observation",), "declared_removal observation_id")
    _require_supersedes(event, events_by_id, "declared_removal")


def validate_storage_link(event: dict, events_by_id: dict[str, dict]) -> None:
    _require_text(event, "session", "storage_link")
    _require_known(_require_text(event, "target_id", "storage_link"), events_by_id, DECLARABLE_TARGETS, "storage_link")
    _require_text(event, "location", "storage_link")
    basis = event.get("basis")
    if not isinstance(basis, dict) or basis.get("kind") != "human" or not str(basis.get("confirmed_by", "")).strip():
        raise ValidationError("storage_link needs a human basis with 'confirmed_by'")
    _require_supersedes(event, events_by_id, "storage_link")


def validate_compatible_group(event: dict, events_by_id: dict[str, dict]) -> None:
    _require_text(event, "session", "compatible_group")
    members = event.get("members")
    if not isinstance(members, list) or len(members) < 2 or len(set(members)) != len(members):
        raise ValidationError("compatible_group needs at least two distinct 'members'")
    for member in members:
        _require_known(member, events_by_id, DECLARABLE_TARGETS, "compatible_group member")
    basis = event.get("basis")
    kind = basis.get("kind") if isinstance(basis, dict) else None
    if kind == "operator_statement":
        if not str(basis.get("declared_by", "")).strip():
            raise ValidationError("compatible_group operator_statement needs 'declared_by'")
    elif kind == "measured":
        if not isinstance(basis.get("measurement"), dict) or not basis["measurement"]:
            raise ValidationError("compatible_group measured basis needs a 'measurement'")
    else:
        raise ValidationError("compatible_group basis kind must be 'operator_statement' or 'measured'")
    _require_supersedes(event, events_by_id, "compatible_group")


def load_events(path: str | Path) -> list[dict]:
    """Read every event. Any malformed line aborts the whole read."""
    path = Path(path)
    if not path.exists():
        return []
    events = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CorruptLogError(f"{path} line {lineno}: invalid JSON ({exc})") from exc
        if not isinstance(event, dict) or event.get("type") not in EVENT_TYPES:
            raise CorruptLogError(f"{path} line {lineno}: not a known event type {EVENT_TYPES}")
        if not event.get("id"):
            raise CorruptLogError(f"{path} line {lineno}: event without an id")
        events.append(event)
    seen: set[str] = set()
    for event in events:
        if event["id"] in seen:
            raise CorruptLogError(f"{path}: duplicate event id {event['id']!r}")
        seen.add(event["id"])
    return events


def next_id(events: list[dict], prefix: str) -> str:
    best = 0
    for event in events:
        ident = event.get("id", "")
        if ident.startswith(prefix + "-"):
            try:
                best = max(best, int(ident.rsplit("-", 1)[1]))
            except ValueError:
                continue
    return f"{prefix}-{best + 1:04d}"


def append_event(path: str | Path, event: dict) -> dict:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
    return event


def successors(events: list[dict]) -> dict[str, str]:
    """Map each superseded id to its replacement (later writers win)."""
    result: dict[str, str] = {}
    for event in events:
        if event.get("supersedes"):
            result[event["supersedes"]] = event["id"]
    return result
