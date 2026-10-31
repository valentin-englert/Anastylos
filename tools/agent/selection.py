"""Read-only, hash-bound drawing selection against an existing guide package."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import guide
from contracts import CorruptLogError, load_events
from state import SessionError

SCHEMA = "anastylos-drawing-selection@1"


class SelectionError(SessionError):
    """Selection cannot be resolved safely; no part answer is returned."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SelectionError(message)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _binding(value: dict) -> None:
    _require(isinstance(value, dict) and value.get("schema") == SCHEMA,
             "Unsupported selection schema; a versioned single-part binding is required.")
    _require(set(value) == {"schema", "drawing_element_id", "source", "memory_sha256", "guide_sha256", "event_id"},
             "Selection must bind exactly one event; composite selections are unsupported.")
    for key in ("drawing_element_id", "event_id"):
        _require(isinstance(value[key], str) and bool(value[key].strip()), f"Selection needs a non-empty {key}.")
    source = value["source"]
    _require(isinstance(source, dict) and set(source) == {"session", "memory_path"},
             "Selection needs an explicit source session and memory_path.")
    for key in ("session", "memory_path"):
        _require(isinstance(source[key], str) and bool(source[key].strip()), f"Selection source needs {key}.")
    _require(Path(source["memory_path"]).is_absolute(), "Selection source memory_path must be absolute.")
    for key in ("memory_sha256", "guide_sha256"):
        _require(isinstance(value[key], str) and re.fullmatch(r"[0-9a-f]{64}", value[key]) is not None,
                 f"Selection needs an expected lowercase SHA-256 for {key}.")


def resolve(memory_path: Path, guide_path: Path, binding: dict) -> dict:
    """Resolve only the explicitly bound declared part, without opening referenced evidence or writing logs."""
    try:
        return _resolve(Path(memory_path), Path(guide_path), binding)
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SelectionError(f"Selection inputs cannot be read or parsed ({type(exc).__name__}); no part was resolved.") from exc


def _resolve(memory_path: Path, guide_path: Path, binding: dict) -> dict:
    _binding(binding)
    memory_bytes = memory_path.read_bytes()
    _require(_sha(memory_bytes) == binding["memory_sha256"],
             "Memory SHA-256 differs from the selection binding; use the explicitly matching data, not another session.")
    data = guide_path.read_bytes()
    _require(_sha(data) == binding["guide_sha256"], "Guide SHA-256 differs from the selection binding.")
    package, problem = guide.claims.load_package(data, binding["guide_sha256"])
    _require(problem is None, f"Guide package cannot be checked: {problem}")
    raw = json.loads(data)
    reference = raw.get("inputs", {}).get("memory", {})
    _require(reference.get("sha256") == binding["memory_sha256"] and
             Path(reference.get("path", "")).resolve() == Path(binding["source"]["memory_path"]).resolve(),
             "Guide input does not bind the selected memory source and SHA-256.")
    try:
        events = load_events(memory_path)
    except CorruptLogError as exc:
        raise SelectionError("Memory has malformed or duplicate records; no part was resolved.") from exc
    _require(_sha(memory_path.read_bytes()) == binding["memory_sha256"], "Memory changed while resolving selection.")
    _require(all(e.get("session") == binding["source"]["session"] for e in events),
             "Memory session differs from the selection source.")
    event_id = binding["event_id"]
    records = {e["id"]: e for e in events}
    event = records.get(event_id)
    _require(event is not None and event.get("type") == "declared_removal",
             "Selected ID is missing or is not a supported single declared-removal part; composites are unsupported.")
    superseded = {e["supersedes"] for e in events if e.get("supersedes")}
    _require(event_id not in superseded, "Selected event is superseded; select an explicit current event instead.")
    _require(len({p["id"] for p in raw["parts"]}) == len(raw["parts"]), "Guide has ambiguous duplicate part IDs.")
    _require(event_id in package.parts, "Selected event is not an exact part ID in the guide.")
    part = package.parts[event_id]
    storage = [e for e in events if e.get("target_id") == event_id and e["type"] == "storage_link"
               and e["id"] not in superseded]
    _require(len(storage) <= 1, "Selected part has ambiguous active storage records; no location was preferred.")
    stored = storage[0] if storage else None
    _require(part.basis == "operator_declared" and part.label == event.get("operator_label") and
             part.source.get("described_as") == event.get("source"), "Guide part contradicts the memory declaration.")
    _require((part.record, part.location) == ((stored["id"], stored["location"]) if stored else (None, None)),
             "Guide storage contradicts the memory or cites a superseded storage record.")
    missing = []
    if event.get("source") is None:
        missing.append("original_location")
    if stored is None:
        missing.append("stored_location")
    evidence = event.get("evidence")
    observation_id = event.get("observation_id")
    observation = records.get(observation_id) if observation_id else None
    if observation_id:
        _require(observation is not None and observation.get("type") == "observation" and
                 observation_id not in superseded, "Declaration references a missing or superseded observation.")
        evidence = observation.get("evidence")
    if not evidence:
        missing.append("direct_image_evidence")
    return {"schema": "anastylos-drawing-resolution@1", "drawing_element_id": binding["drawing_element_id"],
            "event_id": event_id, "source": binding["source"], "memory_sha256": binding["memory_sha256"],
            "guide_sha256": package.sha256, "operator_label": part.label,
            "original_location": {"value": event.get("source"), "basis": "operator_declared",
                                  "declared_by": event.get("declared_by"), "record_id": event_id,
                                  "evidence_note": event.get("evidence_note"), "evidence": evidence,
                                  "observation_id": observation_id, "supersedes": event.get("supersedes")},
            "stored_location": {"value": part.location, "record_id": part.record,
                                "basis": stored.get("basis") if stored else None},
            "phase": event.get("phase"), "t_s": event.get("t_s"), "missing": missing,
            "ambiguities": [], "frame_record_confirmation": "not established by this selection",
            "limits": ["Operator statements, not automatic identity or mechanical approval.",
                       "Source prose is preserved verbatim; no precise installation point is inferred.",
                       "Evidence references are reported as stored, not opened or independently verified."]}
