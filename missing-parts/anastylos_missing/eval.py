"""Labelled-pair evaluation for missing-parts detection.

Labels schema::

    {"px_per_mm": 4.0, "frame": "fixture_back",
     "pairs": [{"id": "p1", "original": "o.png", "current": "c.png",
                "context_file": "ctx.json",
                "truth_absent": [{"bbox": [x, y, w, h]}],
                "truth_occupied": [{"bbox": [...]}],
                "truth_present": [{"bbox": [...]}],
                "expected": [{"id": "e1", "bbox_px": [...]}],
                "expect_obligations": {"e1": "candidate_absent"},
                "expect_verdict": "ok"}]}

Tracks missing-region recall, false alerts (absent claims without
truth, including claims on must-stay-present boxes), unknown cases,
localization IoU, and obligation outcomes. Synthetic fixtures prove
the harness only.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2

from anastylos_missing.annotate import annotate
from anastylos_missing.compare import run_comparison
from anastylos_missing.context import load_context
from anastylos_missing.reconcile import run_reconciliation

IOU_MATCH = 0.3


def _iou(a: list, b: list) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def _inside(point: list, bbox: list) -> bool:
    return bbox[0] <= point[0] <= bbox[0] + bbox[2] \
        and bbox[1] <= point[1] <= bbox[1] + bbox[3]


def _match(claims: list[dict], truth: list[dict]) -> tuple[int, list[float]]:
    hits, ious, free = 0, [], list(range(len(truth)))
    for claim in claims:
        best, best_iou = None, IOU_MATCH
        for index in free:
            iou = _iou(claim["bbox"], truth[index]["bbox"])
            if iou >= best_iou:
                best, best_iou = index, iou
        if best is not None:
            free.remove(best)
            hits += 1
            ious.append(round(best_iou, 3))
    return hits, ious


def _false_on_present(claims: list[dict], present: list[dict]) -> list[str]:
    bad = []
    for claim in claims:
        for box in present:
            if _iou(claim["bbox"], box["bbox"]) >= IOU_MATCH \
                    or _inside(claim["centroid"], box["bbox"]):
                bad.append(claim["label"])
                break
    return bad


def _frame_meta(labels: dict):
    if labels.get("px_per_mm"):
        return {"px_per_mm": float(labels["px_per_mm"]),
                "view": labels.get("frame"), "origin": None, "provenance": "eval labels"}
    return None


def run_eval(labels_path: str | Path, pairs_dir: str | Path, out_dir: str | Path) -> dict:
    """Score comparisons (and reconciliations) against truth."""
    labels_path, pairs_dir, out_dir = Path(labels_path), Path(pairs_dir), Path(out_dir)
    try:
        labels = json.loads(labels_path.read_text(encoding="utf-8-sig"))
        pairs = labels["pairs"]
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read labels {labels_path}: {exc}") from exc
    out_dir.mkdir(parents=True, exist_ok=True)
    frame_meta = _frame_meta(labels)
    summary = {"pairs": 0, "truth_absent": 0, "recalled": 0, "false_alerts": 0,
               "false_on_present": 0, "unknown": 0, "unavailable": 0,
               "verdict_mismatch": 0, "ious": [], "obligations_scored": 0,
               "obligations_exact": 0}
    rows = []
    for entry in pairs:
        context = load_context(str(labels_path.parent / entry["context_file"])
                               if entry.get("context_file") else None)
        if entry.get("detection"):
            result = run_comparison(detection=str(pairs_dir / entry["detection"]),
                                    context=context, comparison_label=entry["id"])
            pair_dir = pairs_dir / Path(entry["detection"]).parent
            original = cv2.imread(str(pair_dir / "before_rectified.jpg"))
            current = cv2.imread(str(pair_dir / "after_rectified.jpg"))
        else:
            opath, cpath = pairs_dir / entry["original"], pairs_dir / entry["current"]
            result = run_comparison(original=str(opath), current=str(cpath),
                                    context=context, frame_meta=frame_meta,
                                    comparison_label=entry["id"])
            original, current = cv2.imread(str(opath)), cv2.imread(str(cpath))
        absent_claims = [r for r in result["regions"]
                         if r["classification"] in ("candidate_absent_region", "candidate_displaced")]
        occupied_claims = [r for r in result["regions"]
                           if r["classification"] in ("candidate_newly_occupied", "candidate_displaced")]
        truth_absent = entry.get("truth_absent") or []
        truth_occupied = entry.get("truth_occupied") or []
        truth_present = entry.get("truth_present") or []
        hits, ious = _match(absent_claims, truth_absent)
        matched_occ, _ = _match(occupied_claims, truth_occupied)
        false_absent = len(absent_claims) - hits
        false_occ = len(occupied_claims) - matched_occ
        bad_present = _false_on_present(absent_claims, truth_present)
        summary["pairs"] += 1
        summary["truth_absent"] += len(truth_absent)
        summary["recalled"] += hits
        summary["false_alerts"] += false_absent + false_occ
        summary["false_on_present"] += len(bad_present)
        summary["ious"].extend(ious)
        if result["verdict"] == "unknown":
            summary["unknown"] += 1
        if result["verdict"] == "unavailable":
            summary["unavailable"] += 1
        if entry.get("expect_verdict") and result["verdict"] != entry["expect_verdict"]:
            summary["verdict_mismatch"] += 1
        row = {"id": entry["id"], "verdict": result["verdict"],
               "recalled_absent": hits, "truth_absent": len(truth_absent),
               "false_alerts": false_absent + false_occ,
               "false_on_present": bad_present, "mean_iou": None}
        if ious:
            row["mean_iou"] = round(sum(ious) / len(ious), 3)
        if entry.get("expected") and frame_meta:
            ppm = frame_meta["px_per_mm"]
            expected = [{"id": item["id"],
                         "bbox_mm": [round(item["bbox_px"][0] / ppm, 2),
                                     round(item["bbox_px"][1] / ppm, 2),
                                     round(item["bbox_px"][2] / ppm, 2),
                                     round(item["bbox_px"][3] / ppm, 2)],
                         "frame": labels.get("frame", "undeclared_image_space"),
                         "source": f"eval labels {entry['id']}", "note": None}
                        for item in entry["expected"]]
            recon = run_reconciliation(result, expected,
                                       {"source": "eval labels", "entries": len(expected)},
                                       entry.get("confirmations"))
            row["reconciliation"] = {item["id"]: item["status"] for item in recon["obligations"]}
            for key, want in (entry.get("expect_obligations") or {}).items():
                summary["obligations_scored"] += 1
                if row["reconciliation"].get(key) == want:
                    summary["obligations_exact"] += 1
        rows.append(row)
        cv2.imwrite(str(out_dir / f"{entry['id']}_overlay.png"), annotate(result, original, current))
        (out_dir / f"{entry['id']}_result.json").write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8")
    report = {**summary,
              "recall": round(summary["recalled"] / summary["truth_absent"], 3)
              if summary["truth_absent"] else None,
              "mean_iou": round(sum(summary["ious"]) / len(summary["ious"]), 3)
              if summary["ious"] else None,
              "rows": rows,
              "note": ("Scores describe this labelled set only; synthetic fixtures "
                       "prove the harness, not real-world accuracy.")}
    del report["ious"]
    (out_dir / "eval_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
