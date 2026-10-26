"""CLI: `run` for real pairs, `selftest` for the synthetic set."""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from anastylos_diff.config import load_configs
from anastylos_diff.engine import Kit, make_detector, run_folder
from anastylos_diff.synthetic import make_dataset

HERE = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = HERE / "harness-config.json"


def _environment() -> dict:
    return {
        "python": platform.python_version(),
        "opencv": cv2.__version__,
        "numpy": np.__version__,
        "platform": platform.platform(),
    }


def cmd_run(args, quiet: bool = False):
    harness, kit_doc, meta = load_configs(args.config)
    if args.require_config_sha256 and args.require_config_sha256 != meta["harness_config_canonical_sha256"]:
        raise SystemExit(
            f"Harness config sha256 {meta['harness_config_canonical_sha256']} "
            f"!= required {args.require_config_sha256}. Aborting."
        )
    kit = Kit(kit_doc, harness["registration"]["dictionary"])
    if args.view and args.view not in kit.views:
        raise SystemExit(f"Unknown --view {args.view}. Known: {sorted(kit.views)}")
    return run_folder(
        Path(args.input),
        Path(args.out),
        harness,
        kit,
        meta,
        make_detector(harness),
        args.view,
        args.manifest,
        quiet=quiet,
    )


def cmd_selftest(args) -> int:
    harness, kit_doc, meta = load_configs(args.config)
    kit = Kit(kit_doc, harness["registration"]["dictionary"])
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    started = time.perf_counter()
    manifest_rows = make_dataset(out / "synthetic_input", kit_doc, kit, harness, seed=args.seed)
    generated_s = time.perf_counter() - started
    namespace = argparse.Namespace(
        config=args.config,
        input=str(out / "synthetic_input"),
        out=str(out / "results"),
        view=None,
        manifest=None,
        require_config_sha256=None,
    )
    results, rows, totals = cmd_run(namespace, quiet=False)
    checks = []
    for result in results:
        evaluation = result["evaluation"]
        if not evaluation.get("assert", True):
            continue
        if evaluation.get("expected") is None:
            continue
        ok = bool(evaluation.get("outcome_ok"))
        if evaluation.get("expected") in {"single", "multiple"}:
            ok = ok and not evaluation.get("missed_change") and not evaluation.get("false_positive")
        checks.append({
            "pair_id": result["pair_id"],
            "expected": evaluation.get("expected"),
            "status": result["status"],
            "passed": ok,
            "false_positive": bool(evaluation.get("false_positive")),
            "missed_change": bool(evaluation.get("missed_change")),
            "note": evaluation.get("note", ""),
            "n_regions": result.get("n_regions"),
        })
    nochange = [row for row in rows if str(row["pair_id"]).startswith("nochange_")]
    changes = [result for result in results if str(result["pair_id"]).startswith(("single_", "multiple_", "surface_single"))]
    valid_regs = [result[key] for result in results for key in ("registration_before", "registration_after") if result.get(key, {}).get("valid")]
    centroids = [value for result in changes for value in result["evaluation"].get("centroid_error_mm") or []]
    stress = {row["pair_id"]: {"status": row["status"], "n_regions": row["n_regions"], "note": row["note"], "false_positive": row["false_positive"]} for row in rows if str(row["pair_id"]).startswith("stress_")}
    metrics = {
        "label": "SYNTHETIC planar scenes. Not a real camera, print, or controller test.",
        "seed": args.seed,
        "pairs_total": len(rows),
        "asserted": len(checks),
        "asserted_passed": sum(1 for check in checks if check["passed"]),
        "no_change_pairs": len(nochange),
        "no_change_false_positive_pairs": sum(int(row["false_positive"]) for row in nochange),
        "change_pairs": len(changes),
        "change_expected_boxes": sum(result["evaluation"].get("expected_bboxes") or 0 for result in changes),
        "change_expected_boxes_hit": sum(result["evaluation"].get("expected_bboxes_hit") or 0 for result in changes),
        "change_unmatched_regions": sum(len(result["evaluation"].get("unmatched_regions") or []) for result in changes),
        "centroid_error_mm_max": round(float(max(centroids)), 3) if centroids else None,
        "registration_valid_images": len(valid_regs),
        "reprojection_rms_mm_max": round(float(max(item["reprojection_rms_mm"] for item in valid_regs)), 4) if valid_regs else None,
        "stress": stress,
        "generation_s": round(generated_s, 2),
        "threshold": harness["change"]["threshold"],
        "normalization": harness["normalization"]["method"],
    }
    passed = all(check["passed"] for check in checks) and len(checks) > 0
    report = {
        "summary": {"asserted_cases": len(checks), "passed": metrics["asserted_passed"], "all_passed": passed},
        "metrics": metrics,
        "checks": checks,
        "totals": totals,
        "config_meta": meta,
        "environment": _environment(),
        "manifest_rows": len(manifest_rows),
    }
    (out / "test-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# ANASTYLOS change-detection selftest",
        "",
        "SYNTHETIC planar scenes rendered from the calibration config. This is not a real camera test.",
        "",
        f"Result: **{metrics['asserted_passed']}/{len(checks)}** asserted cases passed.",
        "",
        f"- Harness config sha256: `{meta['harness_config_canonical_sha256']}`",
        f"- Calibration config sha256: `{meta['kit_config_sha256']}`",
        f"- Python {platform.python_version()}, OpenCV {cv2.__version__}, NumPy {np.__version__}",
        "",
        "## Metrics",
        "",
    ]
    for key, value in metrics.items():
        lines.append(f"- **{key}**: {json.dumps(value, ensure_ascii=False)}")
    lines += ["", "## Cases", "", "| Pair | Expected | Status | Passed | Note |", "|---|---|---|---|---|"]
    for check in checks:
        lines.append(f"| {check['pair_id']} | {check['expected']} | {check['status']} | {'yes' if check['passed'] else 'NO'} | {check['note']} |")
    for row in rows:
        if str(row["pair_id"]).startswith("stress_"):
            lines.append(f"| {row['pair_id']} (not scored) | none | {row['status']} | — | {row['note']} |")
    lines += [
        "",
        "False alarms and misses above are the measured results. Do not edit expected labels to hide them.",
        "",
    ]
    (out / "test-report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\nSELFTEST {metrics['asserted_passed']}/{len(checks)} asserted  synthetic=yes  -> {out / 'test-report.md'}")
    print(f"harness sha256 {meta['harness_config_canonical_sha256']}")
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ANASTYLOS change detection")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="compare <id>_before and <id>_after images")
    run.add_argument("--input", required=True)
    run.add_argument("--out", required=True)
    run.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    run.add_argument("--view", default=None, help="require this view; a mismatch is registration_invalid")
    run.add_argument("--manifest", default=None)
    run.add_argument("--require-config-sha256", default=None, help="abort unless the harness config canonical sha256 matches")
    selftest = sub.add_parser("selftest", help="build a synthetic set and score it")
    selftest.add_argument("--out", type=Path, default=HERE / "selftest_output")
    selftest.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    selftest.add_argument("--seed", type=int, default=20261026)
    args = parser.parse_args(argv)
    if args.command == "run":
        cmd_run(args)
        return 0
    return cmd_selftest(args)


if __name__ == "__main__":
    sys.exit(main())
