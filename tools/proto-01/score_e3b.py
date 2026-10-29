"""Score one PROTO-01-E3B segment against the owner's sealed actual file (Technical Lead tool).

Frozen with docs/PROTO-01-E3B.md before any E3-B footage exists. Run only after
the session-run outputs of the segment are hashed and committed. Reads:

- ``--run``: the session-runner output folder of the segment (parts.json,
  registry.json, pass2/events.json),
- ``--memory``: the assembly-memory log the run wrote to,
- ``--actual``: the owner's sealed ``e3b-actual@1`` file for this segment,

and writes ``e3b-score@1``. Rules (docs/PROTO-01-E3B.md §6):

- A link is **correct** when a true step put a part into the linked cell, the
  step's removal time lies inside the linked removal window ± 2 s, and the
  step's source point lies inside a linked source box grown by 3 mm.
  Otherwise it is **incorrect**.
- A link is **unsupported** when its removal window contains an accepted device
  realignment while a hand covered one of its sites, or when a hand covered one
  of its sites at the window's start or end sample (the FULL-01 P1 artefact).
- Controls: no link may cite the shifted part, start inside the arm-rest
  interval, or start after the pose push.
- Every automatic memory tray link must carry ``inference``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

TIME_TOLERANCE_S = 2.0
SPACE_TOLERANCE_MM = 3.0
EDGE_SAMPLE_S = 0.25
SCHEMA = "e3b-score@1"


def _load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _inside(point: list[float], box: list[float], grow: float) -> bool:
    x, y, w, h = box
    return x - grow <= point[0] <= x + w + grow and y - grow <= point[1] <= y + h + grow


def _covers(spans: list[list[float]], t0: float, t1: float) -> bool:
    return any(s[0] <= t1 and s[1] >= t0 for s in spans)


def judge_link(part: dict, steps: list[dict], boxes: dict[str, list[float]]) -> dict:
    window = part["removal_window_s"]
    linked_boxes = [boxes[s["site"]] for s in part.get("sources", []) if s["site"] in boxes]
    into_cell = [s for s in steps if s["destination"] == part["cell"]]
    for step in into_cell:
        in_time = window[0] - TIME_TOLERANCE_S <= step["removal_t_s"] <= window[1] + TIME_TOLERANCE_S
        in_space = any(_inside(step["source_point_mm"], box, SPACE_TOLERANCE_MM) for box in linked_boxes)
        if in_time and in_space:
            return {"verdict": "correct", "true_part": step["part"]}
    why = ("no true part went into this cell" if not into_cell
           else "the true part's removal time or source point does not match the linked removal")
    return {"verdict": "incorrect", "why": why, "true_parts_into_cell": [s["part"] for s in into_cell]}


def artefact_audit(part: dict, events: dict) -> list[str]:
    visits = events.get("hand_visits") or {}
    sites = [s["site"] for s in part.get("sources", [])]
    t0, t1 = part["removal_window_s"]
    flags = []
    for site in sites:
        spans = visits.get(site, [])
        if _covers(spans, t0 - EDGE_SAMPLE_S, t0) or _covers(spans, t1, t1 + EDGE_SAMPLE_S):
            flags.append(f"hand over {site} at the evidence sample of the removal window")
    devices = [events.get("device")] + list(events.get("devices") or [])
    for device in filter(None, devices):
        for realignment in device.get("realignments") or []:
            if not isinstance(realignment, dict) or not realignment.get("accepted"):
                continue
            t = realignment["t_s"]
            if t0 <= t <= t1 and any(_covers(visits.get(site, []), t, t) for site in sites):
                flags.append(f"device realignment accepted at {t} s while a hand covered a linked site")
    return flags


def score(run: Path, memory: Path, actual_path: Path) -> dict:
    actual = _load(actual_path)
    if actual.get("schema") != "e3b-actual@1":
        raise SystemExit(f"{actual_path}: not an e3b-actual@1 file")
    parts = _load(run / "parts.json")
    registry = _load(run / "registry.json")
    events = _load(run / "pass2" / "events.json")
    boxes = {s["id"]: s["geometry"]["bbox_mm"] for s in registry["sources"]}
    steps = actual["steps"]
    controls = actual.get("controls") or {}

    links = []
    for part in parts["parts"]:
        if not part.get("removal_event"):
            continue
        result = {"part": part["part"], "cell": part["cell"], "status": part["status"], "removal_window_s": part["removal_window_s"],
                  **judge_link(part, steps, boxes)}
        result["artefact_flags"] = artefact_audit(part, events)
        links.append(result)

    violations = []
    shift = controls.get("shift")
    for link, part in zip(links, [p for p in parts["parts"] if p.get("removal_event")]):
        t0, t1 = part["removal_window_s"]
        linked_boxes = [boxes[s["site"]] for s in part.get("sources", []) if s["site"] in boxes]
        if shift and t0 - TIME_TOLERANCE_S <= shift["t_s"] <= t1 + TIME_TOLERANCE_S and any(
                _inside(shift["point_mm"], b, SPACE_TOLERANCE_MM) for b in linked_boxes):
            violations.append(f"{link['part']} cites the shifted, not removed part")
        rest = controls.get("arm_rest")
        if rest and rest["from_s"] <= t0 and t1 <= rest["to_s"]:
            violations.append(f"{link['part']} has a removal window inside the arm rest")
        push = controls.get("pose_push")
        if push and t0 >= push["t_s"]:
            violations.append(f"{link['part']} cites a source change after the pose push")

    memory_events = [json.loads(line) for line in Path(memory).read_text(encoding="utf-8").splitlines() if line.strip()]
    no_inference = [e["id"] for e in memory_events if e["type"] == "tray_link"
                    and (e.get("basis") or {}).get("kind") == "evidence" and not e.get("inference")]

    true_cells = [s for s in steps if str(s["destination"]).startswith("cell-")]
    correct = sum(l["verdict"] == "correct" for l in links)
    incorrect = sum(l["verdict"] == "incorrect" for l in links)
    unsupported = sum(bool(l["artefact_flags"]) for l in links)
    recall_hits = sum(any(_inside(s["source_point_mm"], box, SPACE_TOLERANCE_MM) for box in boxes.values()) for s in steps)
    share = correct / len(true_cells) if true_cells else 0.0
    recall = recall_hits / len(steps) if steps else 0.0

    clean = incorrect == 0 and unsupported == 0 and not violations and not no_inference
    if not clean:
        outcome = "FAIL"
    elif actual["segment"] == "A":
        outcome = ("STRONG PASS" if share >= 0.8 and recall >= 0.8 else "PASS" if share >= 0.5 else "NOT PASS")
    else:
        outcome = "PASS"
    return {
        "schema": SCHEMA,
        "segment": actual["segment"],
        "outcome": outcome,
        "counts": {"true_steps": len(steps), "true_deposits_in_cells": len(true_cells), "automatic_links": len(links),
                   "correct": correct, "incorrect": incorrect, "unsupported": unsupported,
                   "correct_share_of_cell_deposits": round(share, 3), "source_recall": round(recall, 3),
                   "questions": parts["counts"].get("questions")},
        "links": links,
        "control_violations": violations,
        "tray_links_without_inference": no_inference,
        "inputs": {"parts": _sha(run / "parts.json"), "registry": _sha(run / "registry.json"),
                   "events": _sha(run / "pass2" / "events.json"), "memory": _sha(memory), "actual": _sha(actual_path)},
        "tolerances": {"time_s": TIME_TOLERANCE_S, "space_mm": SPACE_TOLERANCE_MM, "edge_sample_s": EDGE_SAMPLE_S},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--memory", required=True, type=Path)
    parser.add_argument("--actual", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    result = score(args.run, args.memory, args.actual)
    args.out.write_bytes((json.dumps(result, indent=2, ensure_ascii=False) + chr(10)).encode("utf-8"))
    print(f"{result['segment']}: {result['outcome']} {json.dumps(result['counts'])}")


if __name__ == "__main__":
    main()
