"""Export full-resolution keyframe pairs for change-detection, and bind its regions back to zones.

Keyframes are decoded from the original video (sequential, exact frame
indices). For each side of an event the first candidate frame that passes
the unchanged change-detection registration gate is used; every attempt
is recorded. Written images carry SHA-256 provenance.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from anastylos_diff import engine
from anastylos_diff.config import sha256_file
from anastylos_custody import footage
from anastylos_custody.sheet import SheetReader

KEYFRAMES_SCHEMA = "custody-keyframes@1"
BINDINGS_SCHEMA = "custody-bindings@1"


def export_pairs(video: Path, events_doc: dict, event_ids: list[str], reader: SheetReader, out_dir: Path) -> dict:
    by_id = {event["id"]: event for event in events_doc["events"]}
    missing = [event_id for event_id in event_ids if event_id not in by_id]
    if missing:
        raise SystemExit(f"unknown event ids: {missing}")
    chosen = [by_id[event_id] for event_id in event_ids]
    sides = {event["id"]: {"before": event.get("before_keyframe_candidates") or [event["frame_before"]],
                           "after": event.get("after_keyframe_candidates") or [event["frame_after"]]} for event in chosen}
    wanted = {frame for side in sides.values() for frames in side.values() for frame in frames}
    images = {sample.frame_index: sample for sample in footage.sample_frames(video, 1, wanted)}

    pairs_dir = out_dir / "pairs"
    pairs_dir.mkdir(parents=True, exist_ok=True)
    records, manifest_rows = [], []
    for event in chosen:
        pair_id = f"{event['id']}_{event['zone']}"
        record = {"pair_id": pair_id, "event": event["id"], "zone": event["zone"], "kind": event["kind"]}
        for side in ("before", "after"):
            pick, attempts = _first_registered(images, sides[event["id"]][side], reader)
            record[f"{side}_attempts"] = attempts
            if pick is None:
                record[side] = None
                continue
            path = pairs_dir / f"{pair_id}_{side}.jpg"
            engine.write_image(path, pick.image, quality=95)
            record[side] = {"file": path.name, "sha256": sha256_file(path), "frame_index": pick.frame_index, "t_s": round(pick.t_s, 3)}
        records.append(record)
        if record["before"] and record["after"]:
            manifest_rows.append({"pair_id": pair_id, "expected": "", "expected_bbox_mm": "", "view": reader.view, "assert": "0",
                                  "notes": f"custody keyframes for {event['id']} ({event['zone']}, {event['kind']})"})
    with (pairs_dir / "manifest.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["pair_id", "expected", "expected_bbox_mm", "view", "assert", "notes"])
        writer.writeheader()
        writer.writerows(manifest_rows)
    doc = {
        "schema": KEYFRAMES_SCHEMA,
        "video": events_doc["provenance"]["video"],
        "events_custody_config_sha256": events_doc["provenance"]["custody_config_canonical_sha256"],
        "pairs": records,
    }
    (out_dir / "keyframes.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return doc


def declared_as_events(declared: dict, fps: float, spread_s: float = 1.0, step_s: float = 0.2) -> list[dict]:
    """Operator-declared removals as exportable events: candidates walk away from the declared change.

    Explicit ``frame_before`` / ``frame_after`` indices, when declared, are used as the only candidates.
    """
    steps = int(round(spread_s / step_s))
    events = []
    for item in declared["removals"]:
        before = [int(item["frame_before"])] if "frame_before" in item else [int(round((float(item["t_before_s"]) - k * step_s) * fps)) for k in range(steps + 1)]
        after = [int(item["frame_after"])] if "frame_after" in item else [int(round((float(item["t_after_s"]) + k * step_s) * fps)) for k in range(steps + 1)]
        events.append({"id": item["id"], "zone": item["site"], "zone_kind": "site", "kind": "declared_removal",
                       "frame_before": before[0], "frame_after": after[0],
                       "before_keyframe_candidates": [frame for frame in before if frame >= 0], "after_keyframe_candidates": after})
    return events


def _first_registered(images: dict, frames: list[int], reader: SheetReader):
    attempts = []
    for frame in frames:
        sample = images.get(frame)
        if sample is None:
            attempts.append({"frame_index": frame, "valid": False, "reasons": ["not decoded"]})
            continue
        report, homography, _corners = reader.register(sample.image)
        attempts.append({"frame_index": frame, "valid": homography is not None, "reasons": report["reasons"],
                         "reprojection_max_mm": report.get("reprojection_max_mm")})
        if homography is not None:
            return sample, attempts
    return None, attempts


def bind_regions(results_dir: Path, keyframes: dict, zone_rects: dict[str, list[float]], tolerance_mm: float) -> dict:
    """For each exported pair, list change-detection regions that overlap the event's zone."""
    bindings = []
    for record in keyframes["pairs"]:
        result_path = results_dir / record["pair_id"] / "result.json"
        entry = {"pair_id": record["pair_id"], "event": record["event"], "zone": record["zone"], "result_json": str(result_path)}
        if not result_path.is_file():
            bindings.append({**entry, "status": "no_result"})
            continue
        result = json.loads(result_path.read_text(encoding="utf-8"))
        entry.update({"result_sha256": sha256_file(result_path), "detection_status": result.get("status")})
        rect = zone_rects[record["zone"]]
        grown = [rect[0] - tolerance_mm, rect[1] - tolerance_mm, rect[2] + 2 * tolerance_mm, rect[3] + 2 * tolerance_mm]
        overlapping = [{"region": region["label"], "bbox_mm": region["bbox_mm"], "centroid_mm": region["centroid_mm"],
                        "area_mm2": region["area_mm2"], "overlap_mm2": round(_intersection(region["bbox_mm"], grown), 2)}
                       for region in result.get("regions") or []]
        overlapping = sorted((item for item in overlapping if item["overlap_mm2"] > 0), key=lambda item: -item["overlap_mm2"])
        status = "bound" if len(overlapping) == 1 else ("no_region" if not overlapping else "several_regions")
        bindings.append({**entry, "status": status, "regions": overlapping})
    return {"schema": BINDINGS_SCHEMA, "tolerance_mm": tolerance_mm, "bindings": bindings}


def _intersection(a: list[float], b: list[float]) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)
