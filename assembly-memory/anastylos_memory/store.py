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

EVENT_TYPES = ("observation", "tray_link")


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
            raise CorruptLogError(f"{path} line {lineno}: not an observation or tray_link event")
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
