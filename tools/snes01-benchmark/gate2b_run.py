"""Gate 2B runner: multi-image reliability + sealed-key scoring.

Modes per pair (same evidence, same question):
  separate   two images in one request (Gate 2A Task-B style).
  composite  one A/B-labeled panel from the identical crops
             (documented scale + layout, content otherwise unmodified).
  single     each crop alone with a visibility probe (distinguishes
             recognition failure from multi-image failure).

Subcommands: run (needs sealed key on disk, never shown to the model;
dry-run supported), score (results + sealed key -> confusion report),
sheet (evidence side-by-side). Budget-guarded, no retries.

The sealed key proves annotation happened; the runner refuses
status != sealed or missing annotator. Annotate BEFORE inference.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

try:
    import cv2
    import numpy as np
except ImportError as exc:  # pragma: no cover
    sys.exit(f"gate2b_run needs OpenCV + NumPy: {exc}")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate2a_client as client
import gate2a_run as g2a

SCHEMA_RESULTS = "snes01-gate2b/results@1"
KEY_SCHEMA = "snes01-gate2b/answer-key@1"
PANEL_H = 512
PANEL_DIV = 16
MODEL_VERDICTS = ("same_instance_candidate", "different_instance_candidate",
                  "insufficient_evidence")
GT_VERDICTS = ("same", "different", "ambiguous")


def _die(message: str):
    print(f"gate2b_run: ERROR: {message}", file=sys.stderr)
    sys.exit(2)


def load_key(path: Path) -> dict:
    try:
        key = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _die(f"cannot read answer key: {exc}")
    if key.get("schema") != KEY_SCHEMA:
        _die("not a Gate 2B answer key")
    if key.get("status") != "sealed":
        _die("answer key is not sealed; annotate first")
    if not key.get("annotator"):
        _die("sealed key has no annotator")
    return key


def build_panel(src_path: Path, dest_path: Path, out_path: Path) -> dict:
    """Deterministic A/B panel: both crops scaled to height 512
    (aspect kept), 16 px divider, A/B labels. Returns transform log."""
    halves = []
    for label, path in (("A", src_path), ("B", dest_path)):
        image = cv2.imread(str(path))
        if image is None:
            _die(f"cannot decode {path}")
        height, width = image.shape[:2]
        scale = PANEL_H / max(1, height)
        resized = cv2.resize(image, (max(1, int(width * scale)), PANEL_H),
                             interpolation=cv2.INTER_AREA)
        cv2.putText(resized, label, (10, 36),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 255), 3)
        halves.append(resized)
    divider = np.full((PANEL_H, PANEL_DIV, 3), 255, np.uint8)
    panel = np.hstack([halves[0], divider, halves[1]])
    cv2.imwrite(str(out_path), panel)
    return {"layout": "A-left|B-right", "panel_h": PANEL_H,
            "divider_px": PANEL_DIV, "scale": "height->512 aspect-kept",
            "panel_sha256": hashlib.sha256(
                out_path.read_bytes()).hexdigest()}


def cmd_run(args) -> int:
    import os
    key = ""
    if not args.dry_run:
        if args.api_key_stdin:
            key = sys.stdin.read().strip()
            if not key:
                _die("no key on stdin")
        else:
            key = os.environ.get("MODEL_API_KEY", "").strip()
            if not key:
                _die("set MODEL_API_KEY or use --api-key-stdin")
    pairs = json.loads(Path(args.pairs).read_text(encoding="utf-8"))
    if args.dry_run:
        answer = {"annotation_set_id": "DRY-RUN-NO-KEY",
                  "verdicts": []}
    else:
        answer = load_key(Path(args.key))
    pair_dir = Path(args.pairs).parent
    prompt_pair = Path(args.prompt_pair).read_text(encoding="utf-8")
    prompt_panel = Path(args.prompt_panel).read_text(encoding="utf-8")
    prompt_single = Path(args.prompt_single).read_text(encoding="utf-8")
    if not args.dry_run:
        gt_ids = {v["pair_id"] for v in answer["verdicts"]}
        missing = [p["id"] for p in pairs["pairs"] if p["id"] not in gt_ids]
        if missing:
            _die(f"key lacks verdicts for: {missing}")
    out = Path(args.out)
    (out / "raw").mkdir(parents=True, exist_ok=True)
    (out / "panels").mkdir(parents=True, exist_ok=True)
    ledger = out / "ledger.jsonl"
    spent, results = 0.0, []
    modes = [m for m in args.modes.split(",") if m in
             ("separate", "composite", "single")]
    if not modes:
        _die("no valid modes requested")
    for pair in pairs["pairs"]:
        entry = {"id": pair["id"], "sourcing": pair["sourcing"],
                 "calls": {}}
        src_p = pair_dir / pair["src"]["file"]
        dest_p = pair_dir / pair["dest"]["file"]
        panel_p = out / "panels" / f"{pair['id']}_panel.png"
        transform = build_panel(src_p, dest_p, panel_p)
        entry["panel_transform"] = transform
        base = {"src_frame": pair["src"]["frame"],
                "src_t": pair["src"]["t_s"],
                "src_box": pair["src"]["bbox_xywh"],
                "dest_frame": pair["dest"]["frame"],
                "dest_t": pair["dest"]["t_s"],
                "dest_box": pair["dest"]["bbox_xywh"]}
        jobs = []
        if "separate" in modes:
            jobs.append(("separate",
                         g2a.fill_template(prompt_pair, base),
                         [src_p, dest_p]))
        if "composite" in modes:
            jobs.append(("composite",
                         g2a.fill_template(prompt_panel, base),
                         [panel_p]))
        if "single" in modes:
            for role, path in (("src", src_p), ("dest", dest_p)):
                jobs.append((
                    f"single-{role}",
                    g2a.fill_template(
                        prompt_single,
                        {"frame": pair[role]["frame"],
                         "t": pair[role]["t_s"],
                         "box": pair[role]["bbox_xywh"]}),
                    [path]))
        for mode, prompt_text, image_paths in jobs:
            if args.dry_run:
                entry["calls"][mode] = {"dry_run": True}
                continue
            worst = g2a.estimate(4000, args.max_tokens, args.price_in,
                                 args.price_out)
            if spent + worst > args.budget_usd:
                _die(f"budget: stopping before {pair['id']}-{mode} "
                     f"(spent ~${spent:.4f} of ${args.budget_usd})")
            response = g2a.one_call(key, args.model, prompt_text,
                                    [str(p) for p in image_paths],
                                    args.max_tokens, args.max_side)
            (out / "raw" / f"{pair['id']}_{mode}.json").write_text(
                json.dumps({"prompt": prompt_text,
                            "response": response["raw"],
                            "http": response["http"]}, indent=2),
                encoding="utf-8")
            if response["http"] != 200 or response["raw"] is None:
                call = {"http": response["http"],
                        "error": response.get("error"),
                        "latency_s": response["latency_s"]}
            else:
                parsed, method = g2a.extract_json(response.get("text"))
                cost = g2a.estimate(response["tokens_in"] or 0,
                                    response["tokens_out"] or 0,
                                    args.price_in, args.price_out)
                spent += cost
                call = {"http": response["http"],
                        "request_id": response["request_id"],
                        "model": response["model"],
                        "finish_reason": response["finish_reason"],
                        "tokens_in": response["tokens_in"],
                        "tokens_out": response["tokens_out"],
                        "reasoning_tokens": response["reasoning_tokens"],
                        "latency_s": response["latency_s"],
                        "est_cost_usd": round(cost, 6),
                        "parse_method": method, "parsed": parsed}
                client.append_ledger(
                    ledger, {"kind": f"pair-{mode}", "pair": pair["id"],
                             "http": response["http"],
                             "request_id": response["request_id"],
                             "model": response["model"],
                             "tokens_in": response["tokens_in"],
                             "tokens_out": response["tokens_out"],
                             "latency_s": response["latency_s"],
                             "est_cost_usd": round(cost, 6)})
            entry["calls"][mode] = call
            print(f"done: {pair['id']}-{mode} "
                  f"http={call.get('http')}", flush=True)
        results.append(entry)
    report = {
        "schema": SCHEMA_RESULTS, "model": args.model,
        "answer_key_id": answer["annotation_set_id"],
        "answer_key_sha256": "DRY-RUN" if args.dry_run else hashlib.sha256(
            Path(args.key).read_bytes()).hexdigest(),
        "est_spent_usd": round(spent, 6),
        "price_basis": "ESTIMATED_FROM_PUBLIC_RATES",
        "budget_usd": args.budget_usd,
        "modes": modes,
        "results": results,
    }
    (out / "results.json").write_text(json.dumps(report, indent=2),
                                      encoding="utf-8")
    print(json.dumps({"pairs": len(results),
                      "est_spent_usd": round(spent, 6)}, indent=2))
    return 0


def _model_verdict(call: dict) -> str:
    verdict = (call.get("parsed") or {}).get("verdict")
    return verdict if verdict in MODEL_VERDICTS else "INVALID"


def cmd_score(args) -> int:
    results = json.loads(Path(args.results).read_text(encoding="utf-8"))
    pairs = json.loads(Path(args.pairs).read_text(encoding="utf-8"))
    answer = load_key(Path(args.key))
    scored = set(pairs.get("scored_pairs", []))
    gt_map = {v["pair_id"]: v for v in answer["verdicts"]}
    bad = [pid for pid, v in gt_map.items()
           if pid in scored and v.get("verdict") not in GT_VERDICTS]
    if bad:
        _die(f"key has non-verdict entries for scored pairs: {bad}")
    by_id = {e["id"]: e for e in results["results"]}
    per_mode: dict[str, dict] = {}
    for mode in results.get("modes", []):
        if mode.startswith("single"):
            continue
        cells: dict[str, int] = {}
        rows = []
        for pid in sorted(scored):
            gt_verdict = gt_map[pid]["verdict"]
            model = _model_verdict(
                by_id[pid]["calls"].get(mode, {}))
            cells[f"{gt_verdict}/{model}"] = \
                cells.get(f"{gt_verdict}/{model}", 0) + 1
            rows.append({"pair": pid, "gt": gt_verdict, "model": model})
        tp = cells.get("same/same_instance_candidate", 0)
        fn_d = cells.get("same/different_instance_candidate", 0)
        fp = (cells.get("different/same_instance_candidate", 0)
              + cells.get("ambiguous/same_instance_candidate", 0))
        tn = cells.get("different/different_instance_candidate", 0)
        abs_same = cells.get("same/insufficient_evidence", 0)
        abs_diff = cells.get("different/insufficient_evidence", 0)
        abs_amb = cells.get("ambiguous/insufficient_evidence", 0)
        invalid = sum(v for k, v in cells.items()
                      if k.endswith("/INVALID"))
        decisive = tp + fn_d + fp + tn + cells.get(
            "ambiguous/different_instance_candidate", 0)
        total = len(scored)
        per_mode[mode] = {
            "n_scored": total,
            "confusion_gt_over_model": cells,
            "TP": tp, "FP": fp, "TN": tn, "FN_decisive": fn_d,
            "abstain_on_same": abs_same,
            "abstain_on_different": abs_diff,
            "abstain_on_ambiguous": abs_amb,
            "invalid": invalid,
            "coverage_decisive": round(decisive / max(1, total), 3),
            "recall_decisive_only":
                round(tp / (tp + fn_d), 3) if tp + fn_d else "n/a",
            "recall_abstain_as_miss":
                round(tp / (tp + fn_d + abs_same), 3)
                if tp + fn_d + abs_same else "n/a",
            "precision_decisive_only":
                round(tp / (tp + fp), 3) if tp + fp else "n/a",
            "rows": rows,
        }
    agree = disagree = unavailable = 0
    if "separate" in per_mode and "composite" in per_mode:
        for pid in sorted(scored):
            a = _model_verdict(by_id[pid]["calls"].get("separate", {}))
            b = _model_verdict(by_id[pid]["calls"].get("composite", {}))
            if a == "INVALID" or b == "INVALID":
                unavailable += 1
            elif a == b:
                agree += 1
            else:
                disagree += 1
    sanity = {}
    for entry in results["results"]:
        if entry["id"] not in scored:
            sanity[entry["id"]] = {
                mode: _model_verdict(call)
                for mode, call in entry["calls"].items()
                if not mode.startswith("single")}
    report = {"scored_modes": per_mode,
              "separate_vs_composite": {
                  "agree": agree, "disagree": disagree,
                  "unavailable": unavailable},
              "sanity_excluded_from_scoring": sanity,
              "note": "Small-n: raw counts above are the result; "
                      "rates are descriptive, not statistical claims."}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "score.json").write_text(json.dumps(report, indent=2),
                                    encoding="utf-8")
    print(json.dumps({m: {k: v for k, v in rep.items() if k != "rows"}
                      for m, rep in per_mode.items()}, indent=2))
    return 0


def cmd_sheet(args) -> int:
    results = json.loads(Path(args.results).read_text(encoding="utf-8"))
    pairs = json.loads(Path(args.pairs).read_text(encoding="utf-8"))
    pair_dir = Path(args.pairs).parent
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pmap = {p["id"]: p for p in pairs["pairs"]}
    rows = []
    for entry in results["results"]:
        pair = pmap[entry["id"]]
        cells = []
        for role in ("src", "dest"):
            image = cv2.imread(str(pair_dir / pair[role]["file"]))
            height, width = image.shape[:2]
            scale = min(1.0, 360 / max(height, width))
            if scale < 1.0:
                image = cv2.resize(image, (int(width * scale),
                                           int(height * scale)))
            canvas = np.full((360, 480, 3), 20, np.uint8)
            h, w = image.shape[:2]
            canvas[:h, :w] = image
            cells.append(canvas)
        panel = np.full((360, 480, 3), 20, np.uint8)
        cv2.putText(panel, f"{entry['id']} [{pair['sourcing']}]", (8, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        line = 70
        for mode, call in sorted(entry["calls"].items()):
            text = f"{mode}: {_model_verdict(call)}"
            cv2.putText(panel, text[:44], (8, line),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            line += 34
        cells.append(panel)
        rows.append(np.hstack(cells))
    cv2.imwrite(str(out / "contact_sheet_gate2b.jpg"), np.vstack(rows),
                [cv2.IMWRITE_JPEG_QUALITY, 88])
    with open(out / "review_gate2b.csv", "w", newline="",
              encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["pair_id", "sourcing", "mode", "auto_verdict",
                         "gt_verdict", "hallucinated_feature",
                         "operator_note"])
        for entry in results["results"]:
            for mode, call in sorted(entry["calls"].items()):
                writer.writerow([entry["id"], pmap[entry["id"]]["sourcing"],
                                 mode, _model_verdict(call), "", "", ""])
    print(json.dumps({"rows": len(rows), "out": str(out)}, indent=2))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Gate 2B runner.")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--pairs", required=True)
    run.add_argument("--key", required=True)
    run.add_argument("--prompt-pair", required=True)
    run.add_argument("--prompt-panel", required=True)
    run.add_argument("--prompt-single", required=True)
    run.add_argument("--model", required=True)
    run.add_argument("--out", required=True)
    run.add_argument("--modes", default="separate,composite,single")
    run.add_argument("--max-tokens", type=int, default=4000)
    run.add_argument("--max-side", type=int, default=512)
    run.add_argument("--budget-usd", type=float, default=2.0)
    run.add_argument("--price-in", type=float, default=1.25)
    run.add_argument("--price-out", type=float, default=4.25)
    run.add_argument("--api-key-stdin", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    score = sub.add_parser("score")
    score.add_argument("--results", required=True)
    score.add_argument("--pairs", required=True)
    score.add_argument("--key", required=True)
    score.add_argument("--out", required=True)
    sheet = sub.add_parser("sheet")
    sheet.add_argument("--results", required=True)
    sheet.add_argument("--pairs", required=True)
    sheet.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if args.command == "run":
        return cmd_run(args)
    if args.command == "score":
        return cmd_score(args)
    return cmd_sheet(args)


if __name__ == "__main__":
    sys.exit(main())
