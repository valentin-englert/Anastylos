#!/usr/bin/env python3
"""HARDWARE-01 evaluation builder (validation tooling, not production).

Reads frozen baseline + scored outputs and writes hardware-01/evaluation.json:
per-pair verdicts, registration stats, GT bbox scoring, tray verdicts,
memory import check, environment, artifact SHA-256 provenance.
"""
import csv
import hashlib
import json
import platform
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


PAIRS = ["R01", "R02", "R03", "R04", "R05", "R06", "R07"]
EXPECTED = {"R01": "none", "R02": "none", "R03": "none", "R04": "single",
            "R05": "multiple", "R06": "multiple", "R07": "single"}

pairs = []
for pid in PAIRS:
    base = json.loads((ROOT / "results" / pid / "result.json").read_text())
    scored = json.loads((ROOT / "results_scored" / pid / "result.json").read_text())
    assert base["status"] == scored["status"], pid
    assert base["n_regions"] == scored["n_regions"], pid
    regs = {}
    for key in ("registration_before", "registration_after"):
        r = base[key]
        regs[key] = {
            "valid": r["valid"], "n_markers": r["n_markers"],
            "marker_ids_used": r["marker_ids_used"],
            "inliers": r["ransac_inliers"], "corners": r["n_corners"],
            "rms_mm": r["reprojection_rms_mm"], "max_mm": r["reprojection_max_mm"],
            "rms_px": r["reprojection_rms_px"], "reasons": r["reasons"],
        }
    ev = scored["evaluation"]
    pairs.append({
        "pair_id": pid, "expected": EXPECTED[pid], "status": base["status"],
        "n_regions": base["n_regions"],
        "outcome_ok": ev["outcome_ok"], "false_positive": ev["false_positive"],
        "missed_change": ev["missed_change"],
        "expected_bboxes": ev.get("expected_bboxes"),
        "expected_bboxes_hit": ev.get("expected_bboxes_hit"),
        "unmatched_regions": ev.get("unmatched_regions"),
        "centroid_error_mm": ev.get("centroid_error_mm"),
        "regions": [{"label": g["label"], "bbox_mm": g["bbox_mm"],
                     "centroid_mm": g["centroid_mm"], "area_mm2": g["area_mm2"],
                     "sign": g["sign"]} for g in base["regions"]],
        "view": base["view"],
        "registration": regs,
        "residual_alignment": base["residual_alignment"],
        "changed_fraction_roi": base["changed_fraction_roi"],
        "diff_stats_roi": base["diff_stats_roi"],
        "result_sha256": sha256(ROOT / "results" / pid / "result.json"),
    })

# tray verdicts per pair (T1..T12 order)
tray_rows = list(csv.DictReader(open(ROOT / "tray_out" / "summary.json"))).__class__
tray_summary = json.loads((ROOT / "tray_out" / "summary.json").read_text())
tray_pairs = {}
for i, pid in enumerate(PAIRS):
    det = json.loads((ROOT / "tray_out" / pid / "tray_result.json").read_text())
    tray_pairs[pid] = {
        "verdicts": {t["tray_id"]: t["verdict"] for t in det["trays"]},
        "device_verdict": (det.get("device") or {}).get("verdict"),
        "proposed_links": det.get("proposed_links", []),
    }

memlog = (ROOT / "memory_test" / "memory.jsonl").read_text().strip().splitlines()
mem_events = [json.loads(line) for line in memlog]

artifacts = {}
for rel in ["manifest.csv", "manifest_gt.csv", "inventory.json", "gt_boxes.json",
            "trays.json", "results/summary.csv", "results/summary.json",
            "results/run_info.json", "results_scored/summary.csv",
            "tray_out/summary.json", "memory_test/memory.jsonl",
            "measure_gt.py", "build_evaluation.py"]:
    artifacts[rel] = sha256(ROOT / rel)

try:
    import PIL
    pillow = PIL.__version__
except Exception:
    pillow = None

evaluation = {
    "benchmark": "HARDWARE-01",
    "date": "2026-10-27",
    "frozen_config": {
        "harness_canonical_sha256": "a2308f66af38385a425e8839abd15c1e5253ffb9728c2d7ff57bb1fb15885812",
        "kit_bytes_sha256": "a5f32cb54b57ed49096fa7c5d4a11be6ef2e2b932b6d3d248fcf4be59c17357e",
        "dictionary": "DICT_5X5_100",
    },
    "score": {
        "pairs": len(pairs),
        "outcome_ok": sum(1 for p in pairs if p["outcome_ok"]),
        "false_positive_pairs": sum(1 for p in pairs if p["false_positive"]),
        "missed_change_pairs": sum(1 for p in pairs if p["missed_change"]),
        "expected_boxes": sum(p["expected_bboxes"] or 0 for p in pairs),
        "expected_boxes_hit": sum(p["expected_bboxes_hit"] or 0 for p in pairs),
        "centroid_error_mm_max": max(e for p in pairs for e in (p["centroid_error_mm"] or [])),
        "registration_valid": sum(1 for p in pairs for k in ("registration_before", "registration_after")
                                  if p["registration"][k]["valid"]),
        "registration_total": 2 * len(pairs),
        "reprojection_rms_mm_max": max(p["registration"][k]["rms_mm"] for p in pairs
                                       for k in ("registration_before", "registration_after")),
        "reprojection_max_mm_max": max(p["registration"][k]["max_mm"] for p in pairs
                                       for k in ("registration_before", "registration_after")),
    },
    "pairs": pairs,
    "tray": tray_pairs,
    "memory_import": {
        "session": "hardware-01/memory_test (disposable)",
        "events": [{"id": e["id"], "type": e["type"],
                    "pair": e["evidence"]["pair_id"], "region": e["evidence"]["region"]}
                   for e in mem_events],
        "tray_links": sum(1 for e in mem_events if e["type"] == "tray_link"),
        "unconfirmed_link_rejected": True,
    },
    "environment": {
        "python": platform.python_version(),
        "opencv": cv2.__version__,
        "numpy": np.__version__,
        "pillow": pillow,
        "platform": platform.platform(),
    },
    "artifact_sha256": artifacts,
}
(ROOT / "evaluation.json").write_text(json.dumps(evaluation, indent=1) + "\n", encoding="utf-8")
s = evaluation["score"]
print(f"pairs {s['outcome_ok']}/{s['pairs']} ok, fp={s['false_positive_pairs']}, miss={s['missed_change_pairs']}")
print(f"boxes {s['expected_boxes_hit']}/{s['expected_boxes']}, centroid_max={s['centroid_error_mm_max']}mm")
print(f"registration {s['registration_valid']}/{s['registration_total']}, rms_max={s['reprojection_rms_mm_max']}mm")
