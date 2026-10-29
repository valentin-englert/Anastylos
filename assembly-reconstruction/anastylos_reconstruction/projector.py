"""Read-only Assembly State v1 projector. Standard library only.

Reads one assembly-memory JSONL log plus the change-detection
result.json files it cites (verbatim, file-contract coupling only --
this module never imports producer code), optionally joined with one
read-only assembly_graph.json. Emits a deterministic Assembly State v1
document. Never writes to any input; never invents identity, geometry,
or dependencies.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

from anastylos_reconstruction.schema import (
    LAYOUT,
    PROJECTOR_NAME,
    PROJECTOR_VERSION,
    SCHEMA_VERSION,
)

UNKNOWN_IDENTITY_NOTE = (
    "memory records no part names; identity stays unknown until an "
    "operator asserts it elsewhere"
)

CROP_ROLES = ("before_rectified", "after_rectified")


def _sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


_HEXDIGITS = frozenset("0123456789abcdefABCDEF")


def _check_cited_hash(cited, current: str) -> tuple[str, str]:
    """Verdict for a cited hash against current file bytes.

    Returns (verdict, note). "verified": the 64-hex cited hash matches
    the bytes on disk. "mismatch": a well-formed cited hash differs --
    the file was substituted or edited after import; reject. Anything
    else is "unverified": resolve the region for continuity with
    historical logs, but never mark it verified.
    """
    if (isinstance(cited, str) and len(cited) == 64
            and all(char in _HEXDIGITS for char in cited)):
        if cited.lower() == current:
            return "verified", \
                "cited result_sha256 matches the result file bytes"
        return "mismatch", \
            f"cited result_sha256 {cited} != current file hash {current}"
    if cited is None or cited == "":
        return "unverified", \
            "no result_sha256 cited; region geometry is unverified"
    return "unverified", \
        f"cited result_sha256 {cited!r} is malformed; " \
        "region geometry is unverified"


def _is_number(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _is_box(value, count: int) -> bool:
    return (
        isinstance(value, list)
        and len(value) == count
        and all(_is_number(item) for item in value)
    )


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# Operator-declared events (phase, declared_removal, storage_link, compatible_group) are valid
# memory but carry no geometry; the projection reads only observations and tray links.
MEMORY_EVENT_TYPES = ("observation", "tray_link", "declared_removal", "storage_link", "compatible_group", "phase")


def load_log(memory_path: Path) -> tuple[list[dict] | None, str | None]:
    """Parse the JSONL log. Malformed lines abort the whole read."""
    try:
        text = memory_path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"cannot read memory log: {exc}"
    events: list[dict] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            return None, f"line {lineno}: invalid JSON ({exc})"
        if (
            not isinstance(event, dict)
            or event.get("type") not in MEMORY_EVENT_TYPES
            or not event.get("id")
        ):
            return None, f"line {lineno}: not a known assembly-memory event"
        events.append(event)
    seen: set[str] = set()
    for event in events:
        if event["id"] in seen:
            return None, f"duplicate event id {event['id']!r}"
        seen.add(event["id"])
    return events, None


def _replay(events: list[dict]) -> tuple[list[dict], list[str], list[dict], str | None]:
    """Live observations in log order, superseded ids, live tray links."""
    by_id = {event["id"]: event for event in events}
    follow = {
        event["supersedes"]: event["id"]
        for event in events
        if event.get("supersedes")
    }
    for old, new in follow.items():
        if old not in by_id:
            return [], [], [], f"event {new!r} supersedes missing id {old!r}"
    live_obs = [
        event
        for event in events
        if event.get("type") == "observation" and event["id"] not in follow
    ]
    links = []
    for event in events:
        if event.get("type") != "tray_link" or event["id"] in follow:
            continue
        target = event.get("observation_id")
        while target in follow:
            target = follow[target]
        if target not in {item["id"] for item in live_obs}:
            return [], [], [], (
                f"link {event['id']!r} points at missing observation {target!r}"
            )
        links.append({**event, "observation_id": target})
    return live_obs, sorted(follow), links, None


def _resolve_result(
    cited: str, results_dir: str | None, memory_path: Path
) -> tuple[dict | None, Path | None]:
    """Locate a cited result.json. Same search order as the graph builder."""
    candidates = [Path(cited)]
    if results_dir:
        candidates.append(Path(results_dir) / Path(cited).name)
    candidates.append(memory_path.parent / cited)
    for candidate in candidates:
        result = _read_json(candidate)
        if isinstance(result, dict):
            return result, candidate
    return None, None


def _find_region(result: dict, label: str) -> dict | None:
    for region in result.get("regions") or []:
        if isinstance(region, dict) and region.get("label") == label:
            return region
    return None


def _resolve_crops(
    result: dict, result_path: Path
) -> tuple[list[dict], str | None]:
    """Rectified-image clips that exist on disk. Never interpreted."""
    output_files = result.get("output_files")
    if not isinstance(output_files, dict):
        return [], "result has no output_files map"
    rectified = result.get("rectified") or {}
    size_px = rectified.get("size_px")
    reasons = []
    crops = []
    for role in CROP_ROLES:
        name = output_files.get(role)
        if not name:
            reasons.append(f"{role} not listed")
            continue
        image_path = result_path.parent / name
        if not image_path.is_file():
            reasons.append(f"{role} file missing ({name})")
            continue
        crops.append({"role": role, "path": image_path, "size_px": size_px})
    if crops:
        return crops, None
    return [], "; ".join(reasons) if reasons else "no rectified images listed"


def _attach_region(
    observation: dict,
    results_dir: str | None,
    memory_path: Path,
    provenance_files: dict,
) -> tuple[dict | None, str | None, list[dict], str | None, dict]:
    """Resolve the cited region and its crops. Returns region, missing
    reason, crops, crop-unavailable reason, and provenance diagnostics
    (cited/current result hashes). A hash mismatch rejects the region:
    substituted bytes are never trusted."""
    evidence = observation.get("evidence") or {}
    cited = evidence.get("result_json") or ""
    label = evidence.get("region") or ""
    cited_hash = evidence.get("result_sha256")
    result, used = _resolve_result(cited, results_dir, memory_path)
    if result is None:
        return None, f"result file unreadable ({cited or 'no citation'})", [], \
            "region unresolved, no crop attempted", \
            {"cited_sha256": cited_hash, "current_sha256": None}
    key = str(used)
    current = _sha256_file(used)
    provenance_files.setdefault(key, current)
    if current is None:
        return None, f"result file unreadable for hashing ({key})", [], \
            "region unresolved, no crop attempted", \
            {"cited_sha256": cited_hash, "current_sha256": None}
    verdict, note = _check_cited_hash(cited_hash, current)
    if verdict == "mismatch":
        return None, f"result hash mismatch for {key}: {note}", [], \
            "region rejected: provenance mismatch; no crop attempted", \
            {"cited_sha256": cited_hash, "current_sha256": current}
    region = _find_region(result, label)
    if region is None:
        return None, f"region {label!r} not found in {key}", [], \
            "region unresolved, no crop attempted", \
            {"cited_sha256": cited_hash, "current_sha256": current}
    rectified = result.get("rectified") or {}
    resolved = {
        "bbox_mm": region.get("bbox_mm"),
        "centroid_mm": region.get("centroid_mm"),
        "bbox_px": region.get("bbox_px"),
        "view": rectified.get("view"),
        "result_json": key,
        "pair_id": evidence.get("pair_id"),
        "region": label,
        "result_sha256": cited_hash,
        "result_sha256_current": current,
        "provenance_verified": verdict == "verified",
        "provenance_note": note,
        "rectified": {
            "px_per_mm": rectified.get("px_per_mm"),
            "size_px": rectified.get("size_px"),
            "origin_frame_mm": rectified.get("origin_frame_mm"),
        },
    }
    proven = {"cited_sha256": cited_hash, "current_sha256": current}
    clip = region.get("bbox_px")
    if not _is_box(clip, 4):
        return resolved, None, [], \
            "region has no bbox_px; crop coordinates unknown", proven
    found, reason = _resolve_crops(result, used)
    crops = [
        {
            "role": item["role"],
            "image": str(item["path"]),
            "clip_px": list(clip),
            "image_size_px": list(item["size_px"])
            if _is_box(item["size_px"], 2) else item["size_px"],
            "note": "clip in rectified-image px; before/after are shown "
                    "side by side, never interpreted",
        }
        for item in found
    ]
    if not crops:
        return resolved, None, [], reason, proven
    return resolved, None, crops, None, proven


def _layout(components: list[dict]) -> None:
    """Fill each component's exploded coordinates in place. Deterministic:
    log order only, fixed constants, no randomness."""
    scale = LAYOUT["scale"]
    per_column = LAYOUT["column_rows"]
    columns: dict[int, list[dict]] = {}
    for index, component in enumerate(components):
        column = index // per_column
        columns.setdefault(column, []).append(component)
    for column, items in columns.items():
        x = LAYOUT["margin_x"] + column * (LAYOUT["column_width"] + LAYOUT["column_gap"])
        y = LAYOUT["margin_y"]
        for row, component in enumerate(items):
            box = component["physical"]["bbox_mm"]
            width = box[2] * scale if _is_box(box, 4) else 0.0
            height = box[3] * scale if _is_box(box, 4) else 0.0
            component["exploded"] = {
                "x": round(x, 2),
                "y": round(y, 2),
                "w": round(width, 2),
                "h": round(height, 2),
                "units": "layout_px",
                "column": column,
                "row": row,
            }
            y += height + LAYOUT["label_band"] + LAYOUT["row_gap"]


def _relationships(graph_path: str | None) -> dict:
    """Copy a graph file verbatim, or report an explicit unknown."""
    empty = {
        "source": None,
        "source_sha256": None,
        "status": "unknown",
        "reason": "no graph input given",
        "confirmed": [],
        "candidates": [],
        "contradictions": [],
        "cycles": [],
        "orderings": None,
        "reassembly": None,
        "missing_confirmations": [],
        "note": "confirmed edges are operator-confirmed by construction; "
                "candidates are unconfirmed proposals",
    }
    if not graph_path:
        return empty
    graph = _read_json(Path(graph_path))
    if not isinstance(graph, dict):
        empty["reason"] = f"graph file unreadable ({graph_path})"
        return empty
    return {
        "source": graph_path,
        "source_sha256": _sha256_file(Path(graph_path)),
        "status": graph.get("status", "unknown"),
        "reason": graph.get("reason"),
        "confirmed": copy.deepcopy(graph.get("confirmed", [])),
        "candidates": copy.deepcopy(graph.get("candidates", [])),
        "contradictions": copy.deepcopy(graph.get("contradictions", [])),
        "cycles": copy.deepcopy(graph.get("cycles", [])),
        "orderings": copy.deepcopy(graph.get("orderings")),
        "reassembly": copy.deepcopy(graph.get("reassembly")),
        "missing_confirmations": copy.deepcopy(
            graph.get("missing_confirmations", [])),
        "note": empty["note"],
    }


def build_state(
    memory_path: str,
    results_dir: str | None = None,
    graph_path: str | None = None,
) -> dict:
    """Project one memory log to Assembly State v1. Read-only."""
    memory = Path(memory_path)
    provenance_files: dict[str, str | None] = {}
    events, error = load_log(memory)
    if events is None:
        return {
            "schema_version": SCHEMA_VERSION,
            "projector": {"name": PROJECTOR_NAME, "version": PROJECTOR_VERSION},
            "status": "unavailable",
            "reason": error,
            "provenance": {
                "memory": memory_path,
                "memory_sha256": _sha256_file(memory),
                "results_dir": results_dir,
                "result_files": {},
            },
            "coordinate_systems": _coordinate_systems(),
            "components": [],
            "superseded": [],
            "relationships": _relationships(graph_path),
            "missing_evidence": [],
            "warnings": [],
        }
    live_obs, superseded, links, replay_error = _replay(events)
    if replay_error is not None:
        state = build_empty(memory_path, results_dir, graph_path)
        state["reason"] = replay_error
        return state
    links_by_obs: dict[str, list[dict]] = {}
    for link in links:
        links_by_obs.setdefault(link["observation_id"], []).append(link)
    components = []
    missing_evidence: list[dict] = []
    warnings: list[str] = []
    for index, observation in enumerate(live_obs):
        region, missing_reason, crops, crop_reason, proven = _attach_region(
            observation, results_dir, memory, provenance_files)
        if missing_reason is not None:
            missing_evidence.append({
                "observation_id": observation.get("id"),
                "cited": (observation.get("evidence") or {}).get("result_json"),
                "region": (observation.get("evidence") or {}).get("region"),
                "reason": missing_reason,
                "cited_sha256": proven["cited_sha256"],
                "current_sha256": proven["current_sha256"],
            })
        linked = links_by_obs.get(observation["id"], [])
        tray: dict | None = None
        trays: list[dict] | None = None
        if len(linked) == 1:
            tray = {
                "link_id": linked[0]["id"],
                "tray": copy.deepcopy(linked[0].get("tray")),
                "basis": copy.deepcopy(linked[0].get("basis")),
            }
        elif len(linked) > 1:
            trays = [
                {
                    "link_id": item["id"],
                    "tray": copy.deepcopy(item.get("tray")),
                    "basis": copy.deepcopy(item.get("basis")),
                }
                for item in linked
            ]
            warnings.append(
                f"{observation['id']}: {len(linked)} live tray links; "
                "ambiguous, no link preferred")
        entry = {
            "id": observation["id"],
            "timeline_index": index,
            "identity": {"status": "unknown", "label": None,
                         "note": UNKNOWN_IDENTITY_NOTE},
            "physical": {
                "source_view": observation.get("source_view"),
                "bbox_mm": copy.deepcopy(observation.get("bbox_mm")),
                "centroid_mm": copy.deepcopy(observation.get("centroid_mm")),
                "evidence": copy.deepcopy(observation.get("evidence")),
                "region_resolved": region is not None,
                "region": region,
                "missing_reason": missing_reason,
            },
            "crops": crops,
            "crop_unavailable": crop_reason,
            "tray": tray,
            "trays": trays,
            "exploded": {},
        }
        components.append(entry)
    _layout(components)
    relationships = _relationships(graph_path)
    for cycle in relationships.get("cycles", []):
        warnings.append(
            f"{cycle.get('kind', 'unknown')} cycle: "
            f"{' -> '.join(cycle.get('nodes', []))}")
    for contradiction in relationships.get("contradictions", []):
        warnings.append(
            f"candidate {contradiction.get('before')} -> "
            f"{contradiction.get('after')} contradicts a confirmed edge")
    if not components:
        status, reason = "unknown", "no live observations"
    elif missing_evidence or relationships.get("missing_confirmations"):
        status = "ambiguous"
        reason = "some evidence or confirmations did not resolve"
    elif relationships.get("cycles"):
        status = "ambiguous"
        reason = "dependency cycles present; order unresolved"
    else:
        status, reason = "ok", None
    return {
        "schema_version": SCHEMA_VERSION,
        "projector": {"name": PROJECTOR_NAME, "version": PROJECTOR_VERSION},
        "status": status,
        "reason": reason,
        "provenance": {
            "memory": memory_path,
            "memory_sha256": _sha256_file(memory),
            "results_dir": results_dir,
            "result_files": provenance_files,
        },
        "coordinate_systems": _coordinate_systems(),
        "components": components,
        "superseded": superseded,
        "relationships": relationships,
        "missing_evidence": missing_evidence,
        "warnings": warnings,
    }


def build_empty(
    memory_path: str,
    results_dir: str | None = None,
    graph_path: str | None = None,
) -> dict:
    """Unavailable-state shell for unreadable or unreplayable logs."""
    memory = Path(memory_path)
    return {
        "schema_version": SCHEMA_VERSION,
        "projector": {"name": PROJECTOR_NAME, "version": PROJECTOR_VERSION},
        "status": "unavailable",
        "reason": "memory log has no events",
        "provenance": {
            "memory": memory_path,
            "memory_sha256": _sha256_file(memory),
            "results_dir": results_dir,
            "result_files": {},
        },
        "coordinate_systems": _coordinate_systems(),
        "components": [],
        "superseded": [],
        "relationships": _relationships(graph_path),
        "missing_evidence": [],
        "warnings": [],
    }


def _coordinate_systems() -> dict:
    return {
        "physical": "frame millimetres per source_view, origin top-left, "
                    "+x right, +y down; stored verbatim, never transformed",
        "exploded": "layout-only numbers in layout_px; positions carry no "
                    "physical meaning",
    }
