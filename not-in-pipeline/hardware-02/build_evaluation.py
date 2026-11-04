#!/usr/bin/env python3
"""HARDWARE-02 evaluation builder (validation tooling, not production)."""
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import platform as _platform

ROOT = Path(__file__).resolve().parent


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


PAIRS = ["AB", "BC", "AC", "CD"]
EXPECTED = {"AB": "single", "BC": "single", "AC": "multiple", "CD": "multiple"}

pairs = []
for pid in PAIRS:
    base = json.loads((ROOT / "results" / pid / "result.json").read_text())
    scored = json.loads((ROOT / "results_scored" / pid / "result.json").read_text())
    assert base["status"] == scored["status"], pid
    regs = {}
    for key in ("registration_before", "registration_after"):
        r = base[key]
        regs[key] = {
            "valid": r["valid"], "n_markers": r["n_markers"],
            "marker_ids_used": r["marker_ids_used"],
            "inliers": r["ransac_inliers"], "corners": r["n_corners"],
            "rms_mm": r["reprojection_rms_mm"], "max_mm": r["reprojection_max_mm"],
            "reasons": r["reasons"],
        }
    ev = scored["evaluation"]
    pairs.append({
        "pair_id": pid, "expected": EXPECTED[pid], "status": base["status"],
        "n_regions": base.get("n_regions"),
        "outcome_ok": ev["outcome_ok"], "false_positive": ev["false_positive"],
        "missed_change": ev["missed_change"], "note": ev.get("note", ""),
        "expected_bboxes": ev.get("expected_bboxes"),
        "expected_bboxes_hit": ev.get("expected_bboxes_hit"),
        "unmatched_regions": ev.get("unmatched_regions"),
        "centroid_error_mm": ev.get("centroid_error_mm"),
        "regions": [{"label": g["label"], "bbox_mm": g["bbox_mm"],
                     "centroid_mm": g["centroid_mm"], "area_mm2": g["area_mm2"],
                     "sign": g["sign"], "mean_diff": g.get("mean_diff"),
                     "max_diff": g.get("max_diff")} for g in base.get("regions", [])],
        "view": base.get("view"),
        "registration": regs,
        "residual_alignment": base.get("residual_alignment"),
        "changed_fraction_roi": base.get("changed_fraction_roi"),
        "result_sha256": sha256(ROOT / "results" / pid / "result.json"),
    })

tray_pairs = {}
for pid in PAIRS:
    det = json.loads((ROOT / "tray_out" / pid / "tray_result.json").read_text())
    tray_pairs[pid] = {
        "detection_status": det.get("detection_status"),
        "verdicts": {t["tray_id"]: t["verdict"] for t in det["trays"]},
        "device_verdict": (det.get("device") or {}).get("verdict"),
        "device_reason": (det.get("device") or {}).get("reason"),
        "proposed_links": det.get("proposed_links", []),
    }

memlog = (ROOT / "memory_test" / "memory.jsonl").read_text().strip().splitlines()
mem_events = [json.loads(line) for line in memlog]

artifacts = {}
for rel in ["manifest.csv", "manifest_gt.csv", "inventory.json", "gt_boxes.json",
            "trays.json", "results/summary.csv", "results/summary.json",
            "results_scored/summary.csv", "tray_out/summary.json",
            "memory_test/memory.jsonl", "measure_gt.py", "build_evaluation.py"]:
    artifacts[rel] = sha256(ROOT / rel)

evaluation = {
    "benchmark": "HARDWARE-02",
    "date": "2026-10-27",
    "frozen_config": {
        "harness_canonical_sha256": "a2308f66af38385a425e8839abd15c1e5253ffb9728c2d7ff57bb1fb15885812",
        "kit_bytes_sha256": "a5f32cb54b57ed49096fa7c5d4a11be6ef2e2b932b6d3d248fcf4be59c17357e",
    },
    "score": {
        "pairs": len(pairs),
        "outcome_ok": sum(1 for p in pairs if p["outcome_ok"]),
        "registration_invalid_pairs": sum(1 for p in pairs if p["status"] == "registration_invalid"),
        "false_positive_pairs": sum(1 for p in pairs if p["false_positive"]),
        "expected_boxes_hit": sum(p["expected_bboxes_hit"] or 0 for p in pairs),
        "expected_boxes": sum(p["expected_bboxes"] or 0 for p in pairs),
    },
    "pairs": pairs,
    "tray": tray_pairs,
    "memory_import": {
        "session": "hardware-02/memory_test (disposable)",
        "ab_import_refused": True,
        "events": [{"id": e["id"], "type": e["type"],
                    "pair": (e.get("evidence") or {}).get("pair_id"),
                    "region": (e.get("evidence") or {}).get("region"),
                    "observation_id": e.get("observation_id"),
                    "confirmed_by": ((e.get("basis") or {}).get("confirmed_by"))}
                   for e in mem_events],
    },
    "environment": {
        "python": _platform.python_version(), "opencv": cv2.__version__,
        "numpy": np.__version__, "platform": _platform.platform(),
    },
    "artifact_sha256": artifacts,
}
(ROOT / "evaluation.json").write_text(json.dumps(evaluation, indent=1) + "\n", encoding="utf-8")
print("wrote evaluation.json:", evaluation["score"])
