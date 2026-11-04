"""Gate 1 scoring: frozen candidates vs sealed human ground truth.

Reads the inference output (candidates.json, frozen before the GT
file is opened) and ONE sealed annotation file. Never run the
perception pipeline from here; never edit candidates here.

PASS/FAIL criteria (fixed a priori, see README):
  PASS iff per-target score >= 3/4 AND unmatched predictions <= 6.
A target scores when its matched prediction is within +-10 s,
its source centroid lands in the GT source box, and correspondence is
right (dest centroid in GT box) or correctly abstained (membrane,
GT disposition "ambiguous" only).

Without a sealed GT file there is no score: exit 2, NOT EVALUATED.

Usage:
    python gate1_score.py --candidates <candidates.json> ^
        --ground <gate1_ground_truth.sealed.json> --out <dir>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCORE_SCHEMA = "snes01-gate1/score@1"
GT_SCHEMA = "snes01-gate1/ground-truth@1"
CAND_SCHEMA = "snes01-gate1/candidates@1"

TIME_TOL_S = 10
PASS_TARGETS = 3
PASS_MAX_UNMATCHED = 6

TARGETS = ("rear_housing", "dpad", "face_buttons", "membrane_ambiguity")


def _die(message: str):
    print(f"gate1_score: ERROR: {message}", file=sys.stderr)
    sys.exit(2)


def _load(path: Path, schema: str, what: str) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _die(f"cannot read {what} {path}: {exc}")
    if data.get("schema") != schema:
        _die(f"{what} schema must be {schema!r}")
    return data


def centroid(box) -> list:
    return [box[0] + box[2] / 2.0, box[1] + box[3] / 2.0]


def inside(point, box) -> bool:
    return (box[0] <= point[0] <= box[0] + box[2]
            and box[1] <= point[1] <= box[1] + box[3])


def match_predictions(predictions: list, gt_window: dict):
    """Predictions whose [before_t, after_t] midpoint falls in the GT
    window (inclusive). Returns (in_window, timing_error_of_best)."""
    start, end = gt_window["start_s"], gt_window["end_s"]
    mid_gt = (start + end) / 2.0
    scored = []
    for pred in predictions:
        mid = (pred["before_t"] + pred["after_t"]) / 2.0
        scored.append((abs(mid - mid_gt), mid, pred))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [item for item in scored if start <= item[1] <= end]


def score_target(target: dict, predictions: list) -> dict:
    """Score one GT target. Returns per-target detail + hit bool."""
    window = target["event_window_s"]
    in_window = match_predictions(predictions, window)
    detail = {"target": target["id"], "disposition": target["disposition"],
              "n_window_predictions": len(in_window),
              "timing_error_s": None, "source_hit": False,
              "correspondence": "none", "hit": False}
    if not in_window:
        # Silence is a miss even for ambiguous targets: the removal is
        # a real event and should at least be seen. Only an explicit
        # abstention (event present, no correspondence) scores as
        # correct_abstention. The 3/4 PASS bar absorbs one hard target.
        detail["correspondence"] = "missed"
        return detail
    error, _, best = in_window[0]
    detail["timing_error_s"] = round(error, 1)
    detail["prediction_id"] = best["id"]
    if error > TIME_TOL_S:
        detail["correspondence"] = "timing_miss"
        return detail
    sources = [r for r in best["regions"]
               if r["role"] == "disappearance"]
    dest_box = target["destination_box_xywh"]
    src_box = target["source_box_xywh"]
    src_hit = any(inside(centroid(r["bbox_xywh"]), src_box)
                  for r in sources)
    detail["source_hit"] = src_hit
    if not src_hit:
        return detail
    top = best["correspondence_top1"]
    if target["disposition"] == "ambiguous":
        detail["correspondence"] = ("correct_abstention"
                                    if top is None else "forced_match")
        detail["hit"] = top is None
        return detail
    if top is None or dest_box is None:
        detail["correspondence"] = "abstained"
        return detail
    dest = next((r for r in best["regions"]
                 if r["region_id"] == top["dest_region"]
                 and r["frame"] == top["dest_frame"]), None)
    if dest is not None and inside(centroid(dest["bbox_xywh"]), dest_box):
        detail["correspondence"] = "correct"
        detail["hit"] = True
    else:
        detail["correspondence"] = "wrong_destination"
    return detail


def score_all(candidates: dict, ground: dict) -> dict:
    predictions = [e for e in candidates.get("events", [])
                   if e.get("persistent_regions")]
    targets = {t["id"]: t for t in ground.get("targets", [])}
    missing = [t for t in TARGETS if t not in targets]
    if missing:
        _die(f"sealed GT missing targets: {missing}")
    details = [score_target(targets[tid], predictions) for tid in TARGETS]
    hits = sum(1 for d in details if d["hit"])
    used_ids = {d.get("prediction_id") for d in details
                if d.get("prediction_id")}
    unmatched = [p["id"] for p in predictions if p["id"] not in used_ids]
    abstentions = sum(1 for p in predictions
                      if p["correspondence_top1"] is None)
    verdict = ("PASS" if hits >= PASS_TARGETS
               and len(unmatched) <= PASS_MAX_UNMATCHED else "FAIL")
    return {
        "schema": SCORE_SCHEMA,
        "candidates_schema": candidates.get("schema"),
        "verdict": verdict,
        "criteria": {"pass_targets": f">={PASS_TARGETS}/4",
                     "time_tol_s": TIME_TOL_S,
                     "max_unmatched": PASS_MAX_UNMATCHED},
        "targets_hit": hits,
        "per_target": details,
        "unmatched_predictions": unmatched,
        "n_abstentions": abstentions,
        "human_interventions": "NOT_YET_MEASURED",
        "note": "Unmatched predictions need human review: they may be "
                "true removals outside the four targets (screws, shell, "
                "rockers, PCB, insert) or false positives.",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Score frozen Gate 1 candidates vs sealed GT.")
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--ground", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    candidates = _load(Path(args.candidates), CAND_SCHEMA, "candidates")
    ground = _load(Path(args.ground), GT_SCHEMA, "ground truth")
    if ground.get("status") != "sealed":
        _die("ground truth is not sealed; refusing to score a template")
    for target in ground.get("targets", []):
        if target.get("annotator") in (None, ""):
            _die(f"target {target.get('id')}: no annotator recorded")
    report = score_all(candidates, ground)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "score.json").write_text(json.dumps(report, indent=2),
                                    encoding="utf-8")
    print(json.dumps({"verdict": report["verdict"],
                      "targets_hit": report["targets_hit"],
                      "unmatched": report["unmatched_predictions"]},
                     indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
