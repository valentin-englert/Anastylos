"""Score observed events against an annotated ground-truth file (rules fixed before the first run).

A detection matches a GT event when it is for the same zone and its
[t_before, t_after] interval overlaps the GT interval widened by the
tolerance. Matching is one-to-one, earliest first. Detections matched to
uncertain GT count neither way; unmatched detections inside a don't-care
window are ignored; every other unmatched detection is a false positive.
"""

from __future__ import annotations

GT_SCHEMA = "custody-gt/events@1"
CELL_CHANGE_KINDS = ("appeared", "changed_while_occupied", "vacated")


def score(events_doc: dict, gt: dict, tolerance_s: float = 2.0) -> dict:
    if gt.get("schema") != GT_SCHEMA:
        raise SystemExit(f"ground truth is not {GT_SCHEMA}")
    events = events_doc["events"]
    cells = [event for event in events if event["zone_kind"] == "tray_cell" and event["kind"] in CELL_CHANGE_KINDS]
    sites = [event for event in events if event["zone_kind"] == "site" and event["kind"] in ("site_changed", "site_restored")]
    breaks = [event for event in events if event["kind"] == "pose_epoch_break"]
    cell_truth = [{**item, "zone": f"cell-{item['cell']}"} for item in gt["cell_events"]]
    site_truth = [{**item, "zone": item["site"]} for item in gt["site_events"]]
    dont_care = [{**item, "zone": f"cell-{item['cell']}"} for item in gt.get("cell_dont_care", [])]
    return {
        "gt_status": gt.get("status"),
        "tolerance_s": tolerance_s,
        "u2_tray_cells": _match(cells, cell_truth, dont_care, tolerance_s),
        "u1_device_sites": {**_match(sites, site_truth, [], tolerance_s), "epoch_breaks": [_brief(event) for event in breaks],
                            "abstained_by_epoch_break": _abstentions(site_truth, breaks, sites)},
    }


def _match(detections: list[dict], truth: list[dict], dont_care: list[dict], tolerance_s: float) -> dict:
    unmatched_truth = sorted(truth, key=lambda item: item["t_after"])
    matches, false_positives, neutral, ignored = [], [], [], []
    for event in sorted(detections, key=lambda item: item["t_after_s"]):
        hit = next((item for item in unmatched_truth if item["zone"] == event["zone"] and _overlaps(event, item, tolerance_s)), None)
        if hit is not None:
            unmatched_truth.remove(hit)
            row = {"detection": _brief(event), "gt": hit["id"],
                   "detected_interval_s": round(event["t_after_s"] - event["t_before_s"], 2),
                   "gt_interval_s": hit["t_after"] - hit["t_before"],
                   "confirm_latency_s": round(event["t_confirmed_s"] - hit["t_after"], 2) if "t_confirmed_s" in event else None}
            (matches if hit.get("certain", True) else neutral).append(row)
        elif any(item["zone"] == event["zone"] and item["t_from"] <= event["t_after_s"] <= item["t_to"] for item in dont_care):
            ignored.append(_brief(event))
        else:
            false_positives.append({**_brief(event), "same_time_gt_other_zone": [item["id"] for item in truth if item["zone"] != event["zone"] and _overlaps(event, item, tolerance_s)]})
    certain = [item for item in truth if item.get("certain", True)]
    missed = [item["id"] for item in unmatched_truth if item.get("certain", True)]
    return {
        "certain_gt": len(certain),
        "detected_true": len(matches),
        "recall": round(len(matches) / len(certain), 3) if certain else None,
        "false_positives": false_positives,
        "missed": missed,
        "matches": matches,
        "matched_uncertain": neutral,
        "ignored_dont_care": ignored,
    }


def _abstentions(site_truth: list[dict], breaks: list[dict], detections: list[dict]) -> list[str]:
    detected_zones = {event["zone"] for event in detections}
    broken = {event["zone"]: event["t_after_s"] for event in breaks}
    return [item["id"] for item in site_truth if item["zone"] not in detected_zones and item["zone"] in broken]


def _overlaps(event: dict, item: dict, tolerance_s: float) -> bool:
    return event["t_before_s"] <= item["t_after"] + tolerance_s and event["t_after_s"] >= item["t_before"] - tolerance_s


def _brief(event: dict) -> dict:
    return {key: event[key] for key in ("id", "zone", "kind", "t_before_s", "t_after_s") if key in event}
