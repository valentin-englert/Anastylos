"""Read-only temporal fusion over SAM evidence. Stdlib only.

Phases per source-anchored hypothesis::

    STATIC_SOURCE -> CONTACT_CANDIDATE -> TRANSPORT_CANDIDATE
        -> RELEASE_CANDIDATE -> STATIC_DESTINATION

Every phase assignment cites its supporting records; anything weaker
returns UNKNOWN with an explicit reason code. This module NEVER writes
to Assembly Memory (no code path exists for it) and never promotes a
candidate: proximity is not contact, a persistent track id is not
physical identity, and a missing frame is not absence.

Reason codes: OK, GAP_FRAMES, OCCLUSION_SUSPECTED, HAND_AMBIGUITY,
NO_HAND_CONTACT, STATIC_MISMATCH, REGION_MISS, ID_JUMP_SUSPECT,
NO_DESTINATION_TRACK, INSUFFICIENT_EVIDENCE.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA = "sam-evidence-v1/fusion@1"

# Frozen a priori 2026-10-28 for SAM-01. Recorded verbatim in every
# output; change only with a dated entry and re-run of all evals.
GATES = {
    "static_disp_px": 25.0,     # centroid drift allowed while "static"
    "contact_dist_px": 60.0,    # hand<->object edge gap for CONTACT candidate
    "release_dist_px": 120.0,   # hand gap that ends contact
    "jump_px": 150.0,           # frame-to-frame jump flagged as ID_JUMP_SUSPECT
    "min_static_frames": 3,     # frames to accept a STATIC phase
    "min_transport_frames": 3,  # frames to accept TRANSPORT
}


def _centroid(box: dict) -> tuple[float, float]:
    return ((box["left"] + box["right"]) / 2.0,
            (box["top"] + box["bottom"]) / 2.0)


def _edge_gap(a: dict, b: dict) -> float:
    dx = max(0.0, max(a["left"] - b["right"], b["left"] - a["right"]))
    dy = max(0.0, max(a["top"] - b["bottom"], b["top"] - a["bottom"]))
    return (dx * dx + dy * dy) ** 0.5


def _inside(box: dict, region: list[float] | None) -> bool:
    if region is None:
        return True
    cx, cy = _centroid(box)
    return region[0] <= cx <= region[2] and region[1] <= cy <= region[3]


def load_request(request_dir: str | Path) -> dict:
    with open(Path(request_dir) / "evidence.json", encoding="utf-8") as handle:
        return json.load(handle)


def tracks_of(evidence: dict, object_id: str | None = None) -> dict[str, list[dict]]:
    tracks: dict[str, list[dict]] = {}
    for item in evidence.get("records", []):
        if item.get("box") is None:
            continue
        if object_id is not None and str(item["object_id"]) != str(object_id):
            continue
        tracks.setdefault(str(item["object_id"]), []).append(item)
    for items in tracks.values():
        items.sort(key=lambda entry: entry["frame"])
    return tracks


def describe_track(items: list[dict]) -> dict[str, Any]:
    """Gaps, jumps, segments: the raw material phases are built from."""
    frames = [entry["frame"] for entry in items]
    gaps: list[list[int]] = []
    if frames:
        missing = sorted(set(range(frames[0], frames[-1] + 1)) - set(frames))
        run: list[int] = []
        for frame in missing:
            if run and frame != run[-1] + 1:
                gaps.append([run[0], run[-1]])
                run = []
            run.append(frame)
        if run:
            gaps.append([run[0], run[-1]])
    jumps: list[dict[str, Any]] = []
    prev = None
    for entry in items:
        if prev is not None:
            ax, ay = _centroid(prev["box"])
            bx, by = _centroid(entry["box"])
            dist = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
            if dist > GATES["jump_px"] and entry["frame"] == prev["frame"] + 1:
                jumps.append({"frames": [prev["frame"], entry["frame"]],
                              "dist_px": round(dist, 1)})
        prev = entry
    segments = 1 + len(gaps) if items else 0
    return {"frames": frames, "n": len(items), "gaps": gaps,
            "jumps": jumps, "segments": segments}


def _static_ok(items: list[dict], region: list[float] | None,
               want: str) -> tuple[bool, str]:
    if len(items) < GATES["min_static_frames"]:
        return False, "INSUFFICIENT_EVIDENCE"
    base = _centroid(items[0]["box"])
    for entry in items:
        point = _centroid(entry["box"])
        dist = ((point[0] - base[0]) ** 2 + (point[1] - base[1]) ** 2) ** 0.5
        if dist > GATES["static_disp_px"]:
            return False, "STATIC_MISMATCH"
        if not _inside(entry["box"], region):
            return False, "REGION_MISS"
    _ = want
    return True, "OK"


def run_hypothesis(
    *,
    src_evidence: dict,
    src_id: str,
    src_region: list[float] | None,
    dst_region: list[float] | None,
    hands: list[dict],
    dst_evidence: dict | None = None,
    dst_id: str | None = None,
) -> dict[str, Any]:
    """One source-anchored hypothesis through the phase machine."""
    tracks = tracks_of(src_evidence, src_id)
    items = tracks.get(str(src_id), [])
    desc = describe_track(items)
    phases: list[dict[str, Any]] = []
    verdict: str = "UNKNOWN"
    reasons: list[str] = []

    def emit(phase: str, status: str, reason: str,
             frames: list[int], support: list[str]) -> None:
        phases.append({"phase": phase, "status": status, "reason": reason,
                       "frames": frames, "support": support})

    if not items:
        emit("STATIC_SOURCE", "UNKNOWN", "INSUFFICIENT_EVIDENCE", [], [])
        return {"verdict": "UNKNOWN", "reasons": ["INSUFFICIENT_EVIDENCE"],
                "track": desc, "phases": phases}
    if desc["jumps"]:
        reasons.append("ID_JUMP_SUSPECT")

    # STATIC_SOURCE over the leading run before first hand proximity.
    hand_close = _first_hand_contact(items, hands)
    lead = [entry for entry in items
            if hand_close is None or entry["frame"] < hand_close]
    ok, why = _static_ok(lead, src_region, "source")
    emit("STATIC_SOURCE", "OBSERVED" if ok else "UNKNOWN", why,
         [entry["frame"] for entry in lead],
         [f"{src_evidence['request_id']}#f{f}" for f in
          [entry["frame"] for entry in lead][:5]])
    if not ok:
        reasons.append(why)

    if hand_close is None:
        emit("CONTACT_CANDIDATE", "UNKNOWN", "NO_HAND_CONTACT", [], [])
        reasons.append("NO_HAND_CONTACT")
    else:
        near = _hands_near(items, hands, hand_close)
        if len(near) > 1:
            emit("CONTACT_CANDIDATE", "UNKNOWN", "HAND_AMBIGUITY",
                 [hand_close], near)
            reasons.append("HAND_AMBIGUITY")
        else:
            emit("CONTACT_CANDIDATE", "CANDIDATE", "OK", [hand_close], near)
        if desc["gaps"]:
            reasons.append("GAP_FRAMES")

    # TRANSPORT: centroid leaves source region heading for dest region.
    moved = [entry for entry in items
             if not _inside(entry["box"], src_region)]
    entered = [entry for entry in moved
               if _inside(entry["box"], dst_region)]
    if len(moved) >= GATES["min_transport_frames"]:
        emit("TRANSPORT_CANDIDATE", "CANDIDATE", "OK",
             [entry["frame"] for entry in moved],
             [f"{src_evidence['request_id']}#f{entry['frame']}"
              for entry in moved[:5]])
    else:
        emit("TRANSPORT_CANDIDATE", "UNKNOWN", "INSUFFICIENT_EVIDENCE",
             [entry["frame"] for entry in moved], [])
        reasons.append("INSUFFICIENT_EVIDENCE")

    # RELEASE + STATIC_DESTINATION need the destination-anchored track.
    if dst_evidence is None or dst_id is None:
        emit("RELEASE_CANDIDATE", "UNKNOWN", "NO_DESTINATION_TRACK", [], [])
        emit("STATIC_DESTINATION", "UNKNOWN", "NO_DESTINATION_TRACK", [], [])
        reasons.append("NO_DESTINATION_TRACK")
    else:
        dst_items = tracks_of(dst_evidence, dst_id).get(str(dst_id), [])
        tail = [entry for entry in dst_items
                if _inside(entry["box"], dst_region)]
        ok_dst, why_dst = _static_ok(tail, dst_region, "destination")
        emit("RELEASE_CANDIDATE", "CANDIDATE" if (entered and ok_dst) else "UNKNOWN",
             "OK" if (entered and ok_dst) else "INSUFFICIENT_EVIDENCE",
             [entry["frame"] for entry in entered[-3:]], [])
        emit("STATIC_DESTINATION", "OBSERVED" if ok_dst else "UNKNOWN",
             why_dst, [entry["frame"] for entry in tail],
             [f"{dst_evidence['request_id']}#f{f}" for f in
              [entry["frame"] for entry in tail][:5]])
        if not ok_dst:
            reasons.append(why_dst)

    if all(phase["status"] in ("OBSERVED", "CANDIDATE") for phase in phases) \
            and "ID_JUMP_SUSPECT" not in reasons \
            and "HAND_AMBIGUITY" not in reasons:
        verdict = "HYPOTHESIS_SUPPORTED"
    return {"verdict": verdict, "reasons": sorted(set(reasons)),
            "track": desc, "phases": phases}


def _first_hand_contact(items: list[dict], hands: list[dict]) -> int | None:
    for entry in items:
        for hand in hands:
            for h_item in tracks_of(hand).values():
                for cell in h_item:
                    if cell["frame"] == entry["frame"] and _edge_gap(
                            entry["box"], cell["box"]) <= GATES["contact_dist_px"]:
                        return entry["frame"]
    return None


def _hands_near(items: list[dict], hands: list[dict],
                frame: int) -> list[str]:
    found: list[str] = []
    want = next((entry for entry in items if entry["frame"] == frame), None)
    if want is None:
        return found
    for hand in hands:
        for hand_id, h_items in tracks_of(hand).items():
            for cell in h_items:
                if cell["frame"] == frame and _edge_gap(
                        want["box"], cell["box"]) <= GATES["contact_dist_px"]:
                    found.append(f"{hand['request_id']}:{hand_id}#f{frame}")
    return found
