"""Gate 2A blind case selection (machine outputs only).

Reads FROZEN Gate 1/1B machine outputs and emits correspondence test
cases by fixed deterministic rules. NEVER reads human labels,
E1-E8 descriptions, verdicts, or review notes. Rule outcomes depend
only on machine-recorded boxes/scores.

Rules (group "blind"):
  A1 plausible:      event with the most persistent tray-band
                     appearance boxes (centroid y > 3100 display px);
                     source = its largest persistent non-occluded
                     disappearance box, dest = its largest persistent
                     tray-band appearance box.
  A2 ambiguous:      highest-scoring ranked pair across all events
                     (nearest-miss abstention); source/dest = that pair.
  A3 negative-time:  A1's source + largest persistent appearance box
                     from the earliest event (temporal mismatch).
  A4 negative-worst: A1's event, its lowest-scoring ranked pair.

Source crops are cut from the BEFORE frame at the disappearance box
(same convention Gate 1 used); dest crops from the AFTER frame.
Boxes stay in display-full pixels (2160x3840, origin top-left).

Usage:
    python gate2a_select.py --candidates <frozen> --out <cases.json>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCHEMA = "snes01-gate2a/cases@1"
TRAY_BAND_Y = 3100


def _die(message: str):
    print(f"gate2a_select: ERROR: {message}", file=sys.stderr)
    sys.exit(2)


def centroid(box) -> list:
    return [box[0] + box[2] / 2.0, box[1] + box[3] / 2.0]


def persistent(event: list) -> list:
    return [r for r in event.get("persistent_regions", [])]


def _case(cid: str, rule: str, event: dict, src: dict,
          dest: dict) -> dict:
    return {
        "id": cid, "group": "blind", "selection_rule": rule,
        "event_id": event["id"],
        "source": {"frame": event["before_frame"],
                   "t_s": event["before_t"],
                   "bbox_xywh": src["bbox_xywh"],
                   "region_id": src["region_id"],
                   "note": "crop from BEFORE frame at disappearance box"},
        "dest": {"frame": event["after_frame"] if dest.get("same_event", True)
                 else dest["frame"],
                 "t_s": event["after_t"] if dest.get("same_event", True)
                 else dest["t_s"],
                 "bbox_xywh": dest["bbox_xywh"],
                 "region_id": dest.get("region_id"),
                 "note": "crop from AFTER frame at appearance box"},
        "context": {"before_frame": event["before_frame"],
                    "after_frame": event["after_frame"]},
        "info_sufficiency": "UNASSESSED",
    }


def select(doc: dict) -> list:
    events = [e for e in doc.get("events", []) if persistent(e)]
    if not events:
        _die("no events with persistent regions")
    by_id = {e["id"]: e for e in events}

    def tray_apps(event):
        return [r for r in persistent(event)
                if r["role"] == "appearance"
                and centroid(r["bbox_xywh"])[1] > TRAY_BAND_Y]

    def disappearances(event, non_occluded=False):
        out = [r for r in persistent(event)
               if r["role"] == "disappearance"]
        if non_occluded:
            out = [r for r in out if not r["occluded"]]
        return out

    def area(box):
        return box[2] * box[3]

    # A1: most tray-band appearances; largest boxes.
    a1_event = max(events, key=lambda e: (len(tray_apps(e)), e["id"]))
    a1_srcs = disappearances(a1_event, non_occluded=True) or \
        disappearances(a1_event)
    if not a1_srcs or not tray_apps(a1_event):
        _die("A1 rule found no source/dest boxes")
    a1_src = max(a1_srcs, key=lambda r: area(r["bbox_xywh"]))
    a1_dest = max(tray_apps(a1_event), key=lambda r: area(r["bbox_xywh"]))
    cases = [_case("A1", "A1 plausible (rule)", a1_event, a1_src,
                   {**a1_dest, "same_event": True})]

    # A2: highest-scoring ranked pair overall (nearest miss).
    best = None
    for event in events:
        for pair in event.get("ranked_pairs", []):
            if best is None or pair["score"] > best[0]["score"]:
                best = (pair, event)
    if best is None:
        _die("A2 rule found no ranked pairs")
    pair, event = best
    regions = {r["region_id"]: r for r in persistent(event)}
    cases.append(_case("A2", "A2 ambiguous nearest-miss (rule)", event,
                       regions[pair["src_region"]],
                       {**regions[pair["dest_region"]],
                        "same_event": True,
                        "gate1_score": pair["score"]}))

    # A3: A1 source + earliest-event appearance (temporal mismatch).
    first = min(events, key=lambda e: e["before_t"])
    apps = [r for r in persistent(first) if r["role"] == "appearance"]
    if not apps:
        _die("A3 rule found no early appearance box")
    a3_dest = max(apps, key=lambda r: area(r["bbox_xywh"]))
    a3 = _case("A3", "A3 negative temporal-mismatch (rule)", a1_event,
               a1_src, {**a3_dest, "same_event": False,
                        "frame": first["after_frame"],
                        "t_s": first["after_t"]})
    a3["context"] = {"before_frame": a1_event["before_frame"],
                     "after_frame": first["after_frame"],
                     "note": "cross-event control: frames differ"}
    cases.append(a3)

    # A4: A1 event's lowest-scoring ranked pair.
    pairs = a1_event.get("ranked_pairs", [])
    if not pairs:
        _die("A4 rule found no ranked pairs in A1 event")
    worst = min(pairs, key=lambda p: (p["score"], p["src_region"],
                                      p["dest_region"]))
    regions = {r["region_id"]: r for r in persistent(a1_event)}
    cases.append(_case(
        "A4", "A4 negative worst-pair (rule)", a1_event,
        regions[worst["src_region"]],
        {**regions[worst["dest_region"]], "same_event": True,
         "gate1_score": worst["score"]}))
    return cases


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Blind case selection.")
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        doc = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _die(f"cannot read candidates: {exc}")
    if doc.get("schema") != "snes01-gate1/candidates@1":
        _die("not frozen Gate 1 candidates")
    out = {"schema": SCHEMA, "group": "blind", "cases": select(doc)}
    Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps([c["id"] for c in out["cases"]]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
