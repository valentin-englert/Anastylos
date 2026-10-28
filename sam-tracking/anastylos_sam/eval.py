"""Scoring of SAM evidence against frozen GT. Stdlib only.

Thresholds are frozen a priori (see FROZEN header); every output cites
the GT SHA-256 it was scored against. Baseline numbers from Gate 1 /
Gate 1B are quoted verbatim from their committed outputs, never
re-scored or restated as our own.
"""

from __future__ import annotations

from typing import Any

# Frozen a priori 2026-10-28 for SAM-01.
IOU_HIT = 0.5
GT_SCHEMA = "sam-gt-v1/image@1"
GT_VIDEO_SCHEMA = "sam-gt-v1/video@1"


def iou(a: dict, b: dict) -> float:
    left = max(a["left"], b["left"])
    top = max(a["top"], b["top"])
    right = min(a["right"], b["right"])
    bottom = min(a["bottom"], b["bottom"])
    if right <= left or bottom <= top:
        return 0.0
    inter = (right - left) * (bottom - top)
    union = ((a["right"] - a["left"]) * (a["bottom"] - a["top"]) +
             (b["right"] - b["left"]) * (b["bottom"] - b["top"]) - inter)
    return inter / union if union > 0 else 0.0


def score_segmentation(records: list[dict], gt_boxes: list[dict]) -> dict[str, Any]:
    """Greedy one-to-one box matching at IOU_HIT. Records and GT boxes
    are {left,top,right,bottom} (+ label on GT)."""
    matched_gt: set[int] = set()
    hits = 0
    for item in records:
        best, best_iou = -1, 0.0
        for index, gt_box in enumerate(gt_boxes):
            if index in matched_gt:
                continue
            value = iou(item["box"], gt_box)
            if value > best_iou:
                best, best_iou = index, value
        if best_iou >= IOU_HIT:
            matched_gt.add(best)
            hits += 1
    misses = [gt for i, gt in enumerate(gt_boxes) if i not in matched_gt]
    return {"n_pred": len(records), "n_gt": len(gt_boxes), "hits": hits,
            "false_positives": len(records) - hits,
            "misses": misses,
            "coverage": hits / len(gt_boxes) if gt_boxes else None,
            "iou_threshold": IOU_HIT}


def score_tracking(track_desc: dict, *,
                   transport_frames: list[int],
                   occlusion_frames: list[int]) -> dict[str, Any]:
    """Fragmentation/switch/coverage summary from a fuse.describe_track."""
    present = set(track_desc["frames"])
    transport = set(transport_frames)
    occluded = set(occlusion_frames)
    covered = sorted(present & transport)
    return {
        "segments": track_desc["segments"],
        "gaps": track_desc["gaps"],
        "jumps": track_desc["jumps"],
        "transport_coverage": (len(covered) / len(transport) if transport else None),
        "transport_frames_covered": covered,
        "records_during_occlusion": sorted(present & occluded),
        "occlusion_frames_total": len(occluded),
    }


def score_correspondence(hypotheses: list[dict],
                         gt_pairs: list[dict]) -> dict[str, Any]:
    """hypotheses: [{key, verdict}]; gt_pairs: [{key, expected:
    same|different|unverifiable}]. verdict HYPOTHESIS_SUPPORTED counts
    as a positive claim; UNKNOWN counts as abstention."""
    by_key = {item["key"]: item for item in hypotheses}
    correct = incorrect = abstain = 0
    rows = []
    for pair in gt_pairs:
        got = by_key.get(pair["key"], {})
        verdict = got.get("verdict", "UNKNOWN")
        if pair["expected"] == "unverifiable":
            outcome = "excluded"
        elif verdict == "HYPOTHESIS_SUPPORTED":
            outcome = "correct" if pair["expected"] == "same" else "incorrect"
        else:
            outcome = "abstain"
        rows.append({"key": pair["key"], "expected": pair["expected"],
                     "verdict": verdict, "outcome": outcome})
        if outcome == "correct":
            correct += 1
        elif outcome == "incorrect":
            incorrect += 1
        elif outcome == "abstain":
            abstain += 1
    return {"correct": correct, "incorrect": incorrect, "abstain": abstain,
            "excluded": sum(1 for row in rows if row["outcome"] == "excluded"),
            "rows": rows}
