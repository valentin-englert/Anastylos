"""Read-only queries over the event log. Standard library only.

"Where does this go?" returns the part's ORIGINAL source location with its
evidence. The tray only locates the stored part and resolves the removal
observation behind it. Ambiguous or missing links are reported as such;
an identity is never invented.
"""

from __future__ import annotations

import json
from pathlib import Path

from anastylos_memory.store import CorruptLogError, ValidationError, sha256_file, successors


def load_kit(path: str | Path) -> tuple[dict, str]:
    path = Path(path)
    try:
        return json.loads(path.read_text(encoding="utf-8")), sha256_file(path) or ""
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot read kit config {path}: {exc}") from exc


def resolve_tray(kit: dict, sheet_id: str, number: int) -> dict:
    """Snapshot a tray cell from the kit config. The kit is the authority."""
    for sheet in kit.get("sheets", []):
        if sheet_id not in (sheet.get("sheet_id"), sheet.get("frame")):
            continue
        for zone in sheet.get("zones", []):
            if zone.get("type") == "tray_cell" and zone.get("number") == number:
                return {
                    "sheet_id": sheet["sheet_id"],
                    "frame": sheet.get("frame", sheet["sheet_id"]),
                    "number": number,
                    "rect_frame_mm": list(zone["rect_frame_mm"]),
                }
    raise ValidationError(f"no tray {number} on sheet {sheet_id!r} in the kit config")


def replay(events: list[dict]) -> tuple[dict[str, dict], dict[str, str], list[dict]]:
    """Current observations, successor map, and current tray links."""
    by_id = {event["id"]: event for event in events}
    follow = successors(events)
    for old, new in follow.items():
        if old not in by_id:
            raise CorruptLogError(f"event {new!r} supersedes missing id {old!r}")
    current_obs = {
        event["id"]: event
        for event in events
        if event["type"] == "observation" and event["id"] not in follow
    }
    links = []
    for event in events:
        if event["type"] != "tray_link" or event["id"] in follow:
            continue
        target = event.get("observation_id")
        while target in follow:
            target = follow[target]
        if target not in current_obs:
            raise CorruptLogError(f"link {event['id']!r} points at missing observation {target!r}")
        links.append({**event, "observation_id": target})
    return current_obs, follow, links


def _observation_card(event: dict) -> dict:
    return {
        "id": event["id"],
        "source_view": event["source_view"],
        "bbox_mm": list(event["bbox_mm"]),
        "centroid_mm": list(event["centroid_mm"]),
        "evidence": dict(event["evidence"]),
    }


def where_observation(events: list[dict], observation_id: str) -> dict:
    current_obs, follow, links = replay(events)
    target = observation_id
    while target in follow:
        target = follow[target]
    if observation_id not in current_obs and observation_id not in follow:
        return {"status": "unknown", "id": observation_id}
    answer: dict = {"status": "unassigned", "observation": _observation_card(current_obs[target])}
    if target != observation_id:
        answer["superseded_by"] = target
    attached = [link for link in links if link["observation_id"] == target]
    if attached:
        answer["status"] = "assigned"
        answer["trays"] = [
            {"tray": link["tray"], "basis": link["basis"], "link_id": link["id"]} for link in attached
        ]
    return answer


def where_tray(events: list[dict], kit: dict, sheet_id: str, number: int) -> dict:
    tray = resolve_tray(kit, sheet_id, number)
    current_obs, _follow, links = replay(events)
    hits = [
        link for link in links
        if link["tray"].get("sheet_id") == tray["sheet_id"] and link["tray"].get("number") == number
    ]
    seen: dict[str, dict] = {}
    for link in hits:
        observation = current_obs[link["observation_id"]]
        seen.setdefault(observation["id"], {
            **_observation_card(observation),
            "link_id": link["id"],
            "link_basis": link["basis"],
        })
    candidates = list(seen.values())
    if not candidates:
        return {"status": "unknown", "tray": tray, "observations": []}
    if len(candidates) > 1:
        return {"status": "ambiguous", "tray": tray, "observations": candidates}
    return {"status": "located", "tray": tray, "observations": candidates}
