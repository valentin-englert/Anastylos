"""CLI: `compare` two states, `reconcile` an inventory, `eval` a set."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

from anastylos_missing.annotate import annotate, annotate_reconciliation
from anastylos_missing.compare import run_comparison
from anastylos_missing.context import load_context
from anastylos_missing.eval import run_eval
from anastylos_missing.inputs import (load_associations, load_confirmations,
                                      load_expected, load_frame_meta)
from anastylos_missing.reconcile import run_reconciliation


def _load_pair_images(result: dict, args) -> tuple:
    if args.detection:
        pair_dir = Path(args.detection).parent
        original = cv2.imread(str(pair_dir / "before_rectified.jpg"))
        current = cv2.imread(str(pair_dir / "after_rectified.jpg"))
        return original, current
    original = cv2.imread(args.original) if args.original else None
    current = cv2.imread(args.current) if args.current else None
    return original, current


def cmd_compare(args) -> int:
    try:
        context = load_context(args.context)
        frame_meta = load_frame_meta(args.frame_meta)
        associations = load_associations(args.associations)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        result = run_comparison(original=args.original, current=args.current,
                                context=context, detection=args.detection,
                                frame_meta=frame_meta, associations=associations,
                                comparison_label=args.label or "")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    original, current = _load_pair_images(result, args)
    cv2.imwrite(str(out / "overlay.png"), annotate(result, original, current))
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


def cmd_reconcile(args) -> int:
    if args.expected is not None and args.memory_log is not None:
        print("--expected and --memory-log are mutually exclusive", file=sys.stderr)
        return 2
    try:
        comparison = json.loads(Path(args.comparison).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read comparison {args.comparison}: {exc}", file=sys.stderr)
        return 1
    if not isinstance(comparison, dict) or "regions" not in comparison:
        print(f"{args.comparison} is not a comparison result", file=sys.stderr)
        return 1
    try:
        expected, provenance = load_expected(args.expected or args.memory_log)
        confirmations = load_confirmations(args.confirmations)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        report = run_reconciliation(comparison, expected, provenance, confirmations)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    overlay = None
    sibling = Path(args.comparison).parent / "overlay.png"
    if sibling.is_file():
        overlay = cv2.imread(str(sibling))
    if overlay is None:
        overlay = annotate(comparison)
    cv2.imwrite(str(out / "reconciliation.png"),
                annotate_reconciliation(comparison, overlay, report))
    (out / "reconciliation.json").write_text(json.dumps(report, indent=2) + "\n",
                                             encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


def cmd_eval(args) -> int:
    try:
        report = run_eval(args.labels, args.pairs_dir, args.out)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps({key: report[key] for key in
                      ("pairs", "recall", "false_alerts", "false_on_present",
                       "unknown", "unavailable", "verdict_mismatch", "mean_iou",
                       "obligations_scored", "obligations_exact")}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="missing",
                                     description="ANASTYLOS evidence-backed missing parts detection")
    sub = parser.add_subparsers(dest="command", required=True)
    compare = sub.add_parser("compare", help="compare an original state with a current state")
    compare.add_argument("--original", default=None)
    compare.add_argument("--current", default=None)
    compare.add_argument("--detection", default=None,
                         help="change-detection result.json (preferred over raw images)")
    compare.add_argument("--context", default=None,
                         help="declared original/current context (else neutral differences)")
    compare.add_argument("--frame-meta", default=None)
    compare.add_argument("--associations", default=None)
    compare.add_argument("--label", default="")
    compare.add_argument("--out", default="missing_out")
    compare.set_defaults(func=cmd_compare)
    reconcile = sub.add_parser("reconcile", help="reconcile expected observations with evidence")
    reconcile.add_argument("--comparison", required=True)
    reconcile.add_argument("--expected", default=None)
    reconcile.add_argument("--memory-log", default=None,
                           help="read-only memory.jsonl as the expectation source")
    reconcile.add_argument("--confirmations", default=None)
    reconcile.add_argument("--out", default="missing_reconcile_out")
    reconcile.set_defaults(func=cmd_reconcile)
    evaluation = sub.add_parser("eval", help="score pairs against ground truth")
    evaluation.add_argument("--labels", required=True)
    evaluation.add_argument("--pairs-dir", required=True)
    evaluation.add_argument("--out", default="missing_eval_out")
    evaluation.set_defaults(func=cmd_eval)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
