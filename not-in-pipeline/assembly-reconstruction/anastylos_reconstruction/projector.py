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

from anastylos_reconstruction.contour import ContourError, extract_mask_contour
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

DECLARED_IDENTITY_NOTE = (
    "operator_label is a declaration, not a verified part identity; "
    "identity stays unknown until asserted through confirmation channels"
)

CROP_ROLES = ("before_rectified", "after_rectified")
COMPONENT_EVENT_TYPES = ("observation", "declared_removal")


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


# Operator-declared events are valid memory. Observations keep their evidence
# path; declared removals project as components with null metric geometry.
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


def _follow_map(events: list[dict]) -> tuple[dict[str, str], str | None]:
    by_id = {event["id"]: event for event in events}
    follow = {
        event["supersedes"]: event["id"]
        for event in events
        if event.get("supersedes")
    }
    for old, new in follow.items():
        if old not in by_id:
            return {}, f"event {new!r} supersedes missing id {old!r}"
    return follow, None


def _resolve_follow(target, follow: dict[str, str]):
    while target in follow:
        target = follow[target]
    return target


def _replay(events: list[dict]) -> tuple[
    list[dict], list[str], list[dict], list[dict], list[dict], list[dict], str | None
]:
    """Live component events, superseded ids, tray/storage links, groups, phases."""
    follow, error = _follow_map(events)
    if error is not None:
        return [], [], [], [], [], [], error
    live_components = [
        event
        for event in events
        if event.get("type") in COMPONENT_EVENT_TYPES and event["id"] not in follow
    ]
    live_ids = {item["id"] for item in live_components}
    live_obs_ids = {
        item["id"] for item in live_components if item.get("type") == "observation"
    }
    tray_links = []
    for event in events:
        if event.get("type") != "tray_link" or event["id"] in follow:
            continue
        target = _resolve_follow(event.get("observation_id"), follow)
        if target not in live_obs_ids:
            return [], [], [], [], [], [], (
                f"link {event['id']!r} points at missing observation {target!r}"
            )
        tray_links.append({**event, "observation_id": target})
    storage_links = []
    for event in events:
        if event.get("type") != "storage_link" or event["id"] in follow:
            continue
        target = _resolve_follow(event.get("target_id"), follow)
        if target not in live_ids:
            return [], [], [], [], [], [], (
                f"storage_link {event['id']!r} points at missing target {target!r}"
            )
        storage_links.append({**event, "target_id": target})
    groups = [
        event for event in events
        if event.get("type") == "compatible_group" and event["id"] not in follow
    ]
    phases = [
        event for event in events
        if event.get("type") == "phase" and event["id"] not in follow
    ]
    return (
        live_components, sorted(follow), tray_links, storage_links, groups, phases, None
    )


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
            if _is_box(box, 4):
                width = box[2] * scale
                height = box[3] * scale
            elif component.get("kind") == "declared_removal":
                width = LAYOUT["declared_w"]
                height = LAYOUT["declared_h"]
            else:
                width = 0.0
                height = 0.0
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


def _pack_storage(linked: list[dict]) -> tuple[dict | None, list[dict] | None, str | None]:
    if len(linked) == 1:
        return {
            "link_id": linked[0]["id"],
            "location": linked[0].get("location"),
            "basis": copy.deepcopy(linked[0].get("basis")),
        }, None, None
    if len(linked) > 1:
        storages = [
            {
                "link_id": item["id"],
                "location": item.get("location"),
                "basis": copy.deepcopy(item.get("basis")),
            }
            for item in linked
        ]
        return None, storages, (
            f"{len(linked)} live storage links; ambiguous, no link preferred"
        )
    return None, None, None


def _pack_tray(linked: list[dict]) -> tuple[dict | None, list[dict] | None, str | None]:
    if len(linked) == 1:
        return {
            "link_id": linked[0]["id"],
            "tray": copy.deepcopy(linked[0].get("tray")),
            "basis": copy.deepcopy(linked[0].get("basis")),
        }, None, None
    if len(linked) > 1:
        trays = [
            {
                "link_id": item["id"],
                "tray": copy.deepcopy(item.get("tray")),
                "basis": copy.deepcopy(item.get("basis")),
            }
            for item in linked
        ]
        return None, trays, (
            f"{len(linked)} live tray links; ambiguous, no link preferred"
        )
    return None, None, None


def _groups_for(component_id: str, groups: list[dict]) -> list[dict]:
    matched = []
    for group in groups:
        members = group.get("members") or []
        if component_id in members:
            matched.append({
                "id": group["id"],
                "label": group.get("label"),
                "members": copy.deepcopy(members),
                "basis": copy.deepcopy(group.get("basis")),
            })
    return matched


def _mask_path_for_region(region: dict | None) -> Path | None:
    if not isinstance(region, dict):
        return None
    result_json = region.get("result_json")
    if not result_json:
        return None
    result_path = Path(result_json)
    result = _read_json(result_path)
    if not isinstance(result, dict):
        return None
    output_files = result.get("output_files") or {}
    name = output_files.get("mask")
    if not name:
        return None
    candidate = result_path.parent / name
    return candidate if candidate.is_file() else None


def _build_representation(
    *,
    kind: str,
    region: dict | None,
    crops: list[dict],
    declaration: dict | None,
) -> dict:
    """Honest drawing basis: mask contour, evidence crop, or declared card."""
    if kind == "observation" and region is not None:
        clip = region.get("bbox_px")
        mask_path = _mask_path_for_region(region)
        if mask_path is not None and _is_box(clip, 4):
            try:
                return extract_mask_contour(mask_path, clip)
            except (ContourError, OSError) as exc:
                # Fall through to evidence crop / card with the reason noted.
                contour_error = str(exc)
        else:
            contour_error = (
                "no usable change-detection mask for contour extraction"
                if mask_path is None else "region has no bbox_px"
            )
        if crops:
            crop = crops[0]
            return {
                "kind": "evidence_crop",
                "method": "rectified-evidence-crop-v1",
                "parameters": {"preferred_role": crop.get("role")},
                "source_image": crop.get("image"),
                "source_sha256": _sha256_file(Path(crop["image"]))
                if crop.get("image") else None,
                "roi_px": copy.deepcopy(crop.get("clip_px")),
                "coordinate_space": "rectified_image_px",
                "image_size_px": copy.deepcopy(crop.get("image_size_px")),
                "points_px": None,
                "note": "SUBSTITUTE — evidence crop clip, not a recognized "
                        f"silhouette ({contour_error})",
                "contour_error": contour_error,
            }
        return {
            "kind": "declared_card",
            "method": "unavailable-silhouette-v1",
            "parameters": {},
            "source_image": None,
            "source_sha256": None,
            "roi_px": None,
            "coordinate_space": None,
            "points_px": None,
            "note": "SUBSTITUTE — no mask contour and no evidence crop "
                    f"({contour_error})",
            "contour_error": contour_error,
        }
    label = (declaration or {}).get("operator_label")
    return {
        "kind": "declared_card",
        "method": "operator-declaration-card-v1",
        "parameters": {},
        "source_image": None,
        "source_sha256": None,
        "roi_px": None,
        "coordinate_space": None,
        "points_px": None,
        "note": "SUBSTITUTE — operator declaration; no measured silhouette"
                + (f" ({label})" if label else ""),
    }


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
            "phases": [],
            "components": [],
            "superseded": [],
            "relationships": _relationships(graph_path),
            "missing_evidence": [],
            "warnings": [],
        }
    (live_components, superseded, tray_links, storage_links, groups, phases,
     replay_error) = _replay(events)
    if replay_error is not None:
        state = build_empty(memory_path, results_dir, graph_path)
        state["reason"] = replay_error
        return state
    links_by_obs: dict[str, list[dict]] = {}
    for link in tray_links:
        links_by_obs.setdefault(link["observation_id"], []).append(link)
    storage_by_target: dict[str, list[dict]] = {}
    for link in storage_links:
        storage_by_target.setdefault(link["target_id"], []).append(link)
    # Conflicting storage: same location claimed by targets that are not
    # co-members of one live compatible_group (shared cells inside a group
    # are expected for interchangeable parts).
    by_location: dict[str, list[str]] = {}
    for link in storage_links:
        location = link.get("location")
        if isinstance(location, str) and location.strip():
            by_location.setdefault(location, []).append(link["target_id"])
    group_sets = [
        frozenset(group.get("members") or [])
        for group in groups
        if isinstance(group.get("members"), list)
    ]
    components = []
    missing_evidence: list[dict] = []
    warnings: list[str] = []
    for location, targets in sorted(by_location.items()):
        unique = sorted(set(targets))
        if len(unique) <= 1:
            continue
        covered = any(set(unique) <= members for members in group_sets)
        if covered:
            continue
        warnings.append(
            f"storage location {location!r} claimed by multiple targets: "
            + ", ".join(unique)
            + " (contradiction left visible; no winner chosen)")
    for index, event in enumerate(live_components):
        kind = event["type"]
        if kind == "observation":
            region, missing_reason, crops, crop_reason, proven = _attach_region(
                event, results_dir, memory, provenance_files)
            if missing_reason is not None:
                missing_evidence.append({
                    "observation_id": event.get("id"),
                    "component_id": event.get("id"),
                    "cited": (event.get("evidence") or {}).get("result_json"),
                    "region": (event.get("evidence") or {}).get("region"),
                    "reason": missing_reason,
                    "cited_sha256": proven["cited_sha256"],
                    "current_sha256": proven["current_sha256"],
                })
            tray, trays, tray_warn = _pack_tray(links_by_obs.get(event["id"], []))
            if tray_warn:
                warnings.append(f"{event['id']}: {tray_warn}")
            declaration = None
            identity_note = UNKNOWN_IDENTITY_NOTE
            physical = {
                "source_view": event.get("source_view"),
                "bbox_mm": copy.deepcopy(event.get("bbox_mm")),
                "centroid_mm": copy.deepcopy(event.get("centroid_mm")),
                "evidence": copy.deepcopy(event.get("evidence")),
                "region_resolved": region is not None,
                "region": region,
                "missing_reason": missing_reason,
            }
        else:
            region = None
            crops = []
            crop_reason = "declared_removal has no change-detection crops"
            tray = None
            trays = None
            declaration = {
                "operator_label": event.get("operator_label"),
                "source": event.get("source"),
                "phase": event.get("phase"),
                "declared_by": event.get("declared_by"),
                "evidence_note": event.get("evidence_note"),
                "t_s": event.get("t_s"),
                "observation_id": event.get("observation_id"),
            }
            identity_note = DECLARED_IDENTITY_NOTE
            physical = {
                "source_view": None,
                "bbox_mm": None,
                "centroid_mm": None,
                "evidence": None,
                "region_resolved": False,
                "region": None,
                "missing_reason": "declared_removal carries no metric geometry",
            }
        storage, storages, storage_warn = _pack_storage(
            storage_by_target.get(event["id"], []))
        if storage_warn:
            warnings.append(f"{event['id']}: {storage_warn}")
        representation = _build_representation(
            kind=kind, region=region, crops=crops, declaration=declaration)
        entry = {
            "id": event["id"],
            "timeline_index": index,
            "kind": kind,
            "identity": {"status": "unknown", "label": None, "note": identity_note},
            "declaration": declaration,
            "physical": physical,
            "crops": crops,
            "crop_unavailable": crop_reason,
            "tray": tray,
            "trays": trays,
            "storage": storage,
            "storages": storages,
            "compatible_groups": _groups_for(event["id"], groups),
            "representation": representation,
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
    phase_index = [
        {
            "id": phase["id"],
            "label": phase.get("label"),
            "starts_after": phase.get("starts_after"),
        }
        for phase in phases
    ]
    if not components:
        status, reason = "unknown", "no live components"
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
        "phases": phase_index,
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
        "phases": [],
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
