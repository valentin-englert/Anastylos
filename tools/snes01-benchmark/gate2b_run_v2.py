"""Gate 2B.1 experiment extension (ADDITIVE; frozen files untouched).

Adapts v2 human-curated pairs + v2 answer keys to the FROZEN Gate 2B
runner without modifying it:

- Validates the v2 sealed key (status, annotator, verdicts in
  {same_physical_instance, different_physical_instance, ambiguous,
  unverifiable}, evidence_type present).
- Maps v2 verdicts to the runner's v1 enum for scored pairs only:
  same->same, different->different, ambiguous->ambiguous.
- EXCLUDES `unverifiable` pairs from inference AND scoring (reported
  separately, never pooled, never defaulted).
- Delegates run/score/sheet to gate2b_run with filtered pairs/key
  staged under the output dir (kept as `staged/` for audit).

v1 algorithm-selected cases and v2 human-curated cases are NEVER
pooled into one metric: score sections stay per-set.

Usage mirrors gate2b_run; --key takes the v2 sealed key:
    python gate2b_run_v2.py run --pairs <candidate_manifest> ^
        --key <gate2b_v2_answerkey.sealed.json> --prompt-pair ... ^
        --prompt-panel ... --prompt-single ... ^
        --model muse-spark-1.3-contributor --out <run dir> [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate2b_run as v1

V2_VERDICTS = ("same_physical_instance", "different_physical_instance",
               "ambiguous", "unverifiable")
MAP_TO_V1 = {"same_physical_instance": "same",
             "different_physical_instance": "different",
             "ambiguous": "ambiguous"}
KEY_SCHEMA_V2 = "snes01-gate2b/answer-key@2"


def _die(message: str):
    print(f"gate2b_run_v2: ERROR: {message}", file=sys.stderr)
    sys.exit(2)


def load_v2_key(path: Path) -> dict:
    try:
        key = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _die(f"cannot read v2 key: {exc}")
    if key.get("schema") != KEY_SCHEMA_V2:
        _die("not a v2 answer key")
    if key.get("status") != "sealed":
        _die("v2 key is not sealed; annotate first")
    if not key.get("annotator"):
        _die("v2 sealed key has no annotator")
    for entry in key.get("verdicts", []):
        if entry.get("verdict") not in V2_VERDICTS:
            _die(f"pair {entry.get('pair_id')}: verdict "
                 f"{entry.get('verdict')!r} not in v2 enum")
        if not entry.get("evidence_type"):
            _die(f"pair {entry.get('pair_id')}: no evidence_type")
    return key


def to_v1_pairs(manifest: dict, scored_ids: set) -> dict:
    """Rewrite the v2 manifest into the frozen v1 pairs shape for the
    scored subset. unverifiable pairs are dropped (reported by caller).
    Field mapping: source->src, dest->dest (v1 names)."""
    pairs = []
    for item in manifest["pairs"]:
        if item["id"] not in scored_ids:
            continue
        pairs.append({
            "id": item["id"], "sourcing": "human-curated-v2",
            "src": {"frame": item["src"]["frame"],
                    "t_s": item["src"]["t_s"],
                    "bbox_xywh": item["src"]["bbox_xywh"],
                    "file": item["src"]["file"]},
            "dest": {"frame": item["dest"]["frame"],
                     "t_s": item["dest"]["t_s"],
                     "bbox_xywh": item["dest"]["bbox_xywh"],
                     "file": item["dest"]["file"]},
        })
    return {"schema": "snes01-gate2b/pairs@1",
            "video_sha256": manifest["video_sha256"],
            "scored_pairs": sorted(scored_ids), "pairs": pairs}


def to_v1_key(key: dict, scored_ids: set) -> dict:
    return {"schema": "snes01-gate2b/answer-key@1",
            "annotation_set_id": key["annotation_set_id"] + "+v1map",
            "status": "sealed", "annotator": key["annotator"],
            "annotated_date": key.get("annotated_date", ""),
            "verdicts": [
                {"pair_id": v["pair_id"],
                 "verdict": MAP_TO_V1[v["verdict"]]}
                for v in key["verdicts"]
                if v["pair_id"] in scored_ids]}


def stage(args, key: dict, manifest: dict, out: Path) -> tuple[Path, Path,
                                                               list]:
    scored = {v["pair_id"] for v in key["verdicts"]
              if v["verdict"] != "unverifiable"}
    excluded = sorted({v["pair_id"] for v in key["verdicts"]}
                      - scored)
    known = {p["id"] for p in manifest["pairs"]}
    unknown = sorted(scored - known)
    if unknown:
        _die(f"key verdicts reference unknown pairs: {unknown}")
    staged = out / "staged"
    staged.mkdir(parents=True, exist_ok=True)
    pairs_path = staged / "pairs_v1mapped.json"
    key_path = staged / "key_v1mapped.json"
    pairs_path.write_text(json.dumps(to_v1_pairs(manifest, scored),
                                     indent=2), encoding="utf-8")
    key_path.write_text(json.dumps(to_v1_key(key, scored), indent=2),
                        encoding="utf-8")
    # Crops live next to the v1-mapped pairs file: link by copy of the
    # small files the frozen runner needs (tight crops only).
    manifest_dir = Path(args.pairs).parent
    for item in manifest["pairs"]:
        if item["id"] not in scored:
            continue
        for role in ("src", "dest"):
            rel = item[role]["file"]
            dest = staged / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.is_file():
                dest.write_bytes((manifest_dir / rel).read_bytes())
    return pairs_path, key_path, excluded


def cmd_run(args) -> int:
    manifest = json.loads(Path(args.pairs).read_text(encoding="utf-8"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.dry_run:
        key = {"annotation_set_id": "DRY-RUN-NO-KEY",
               "annotator": "dry-run",
               "verdicts": [
                   {"pair_id": p["id"],
                    "verdict": "ambiguous",
                    "evidence_type": "dry-run-placeholder"}
                   for p in manifest["pairs"]]}
    else:
        key = load_v2_key(Path(args.key))
    pairs_path, key_path, excluded = stage(args, key, manifest, out)
    (out / "excluded_unverifiable.json").write_text(
        json.dumps({"excluded_pair_ids": excluded,
                    "note": "Unverifiable pairs: no inference, no "
                            "scoring, reported only."}, indent=2),
        encoding="utf-8")
    argv = ["run", "--pairs", str(pairs_path), "--key", str(key_path),
           "--prompt-pair", args.prompt_pair, "--prompt-panel",
           args.prompt_panel, "--prompt-single", args.prompt_single,
           "--model", args.model, "--out", str(out / "v1run"),
           "--modes", args.modes, "--max-tokens", str(args.max_tokens),
           "--max-side", str(args.max_side), "--budget-usd",
           str(args.budget_usd), "--price-in", str(args.price_in),
           "--price-out", str(args.price_out)]
    if args.api_key_stdin:
        argv.append("--api-key-stdin")
    if args.dry_run:
        argv.append("--dry-run")
    code = v1.main(argv)
    print(json.dumps({"excluded_unverifiable": excluded,
                      "sets": "v2-human-curated (never pooled with v1)"}),
          flush=True)
    return code


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Gate 2B.1 v2 extension wrapper.")
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
    args = parser.parse_args(argv)
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
