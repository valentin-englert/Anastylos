"""CLI: `read` crops, `eval` a labelled set, `compare` two results."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

from anastylos_washer.annotate import annotate
from anastylos_washer.compare import compare_files
from anastylos_washer.eval import run_eval
from anastylos_washer.vision import analyze


def _roi(text: str) -> tuple[int, int, int, int]:
    try:
        x, y, w, h = (int(part) for part in text.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(f"--roi must be x,y,w,h, got {text!r}")
    return x, y, w, h


def _frame_metadata(text: str | None) -> dict | None:
    if text is None:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        path = Path(text)
        if not path.is_file():
            raise argparse.ArgumentTypeError(
                f"--frame-metadata must be JSON or a file, got {text!r}")
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise argparse.ArgumentTypeError("--frame-metadata must be an object")
    return data


def cmd_read(args) -> int:
    image = cv2.imread(args.image)
    if image is None:
        print(f"cannot read image {args.image}", file=sys.stderr)
        return 1
    try:
        metadata = _frame_metadata(args.frame_metadata)
    except argparse.ArgumentTypeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        result = analyze(image, args.view, args.roi, family=args.family,
                         frame_metadata=metadata)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    result = {"image": str(Path(args.image).name),
              "roi": list(args.roi) if args.roi else None,
              "family_hint": args.family, **result}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n",
                                     encoding="utf-8")
    cv2.imwrite(str(out / "annotated.png"), annotate(image, result))
    print(json.dumps(result, indent=2))
    return 0


def cmd_eval(args) -> int:
    try:
        report = run_eval(args.labels, args.images_dir, args.out)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps({key: report[key] for key in
                      ("images", "exact", "misses", "false_confident",
                       "undecided")}, indent=2))
    return 0


def cmd_compare(args) -> int:
    try:
        report = compare_files(args.baseline, args.candidate)
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "comparison.json").write_text(json.dumps(report, indent=2) + "\n",
                                         encoding="utf-8")
    print(json.dumps({key: report[key] for key in
                      ("pairs", "unmatched_baseline",
                       "unmatched_candidate", "unit")}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="washer", description="ANASTYLOS washer and spacer recognition")
    sub = parser.add_subparsers(dest="command", required=True)
    read = sub.add_parser("read", help="detect and measure parts in one crop")
    read.add_argument("--image", required=True)
    read.add_argument("--view", required=True, choices=("top", "side"))
    read.add_argument("--roi", type=_roi, default=None)
    read.add_argument("--family", default=None,
                      help="declared family: flat_washer, split_washer, "
                           "spacer, standoff, unknown")
    read.add_argument("--frame-metadata", default=None,
                      help='JSON or file, e.g. {"pixels_per_mm": 10.0}')
    read.add_argument("--out", default="washer_out")
    read.set_defaults(func=cmd_read)
    evaluation = sub.add_parser("eval", help="score readings against labels")
    evaluation.add_argument("--labels", required=True)
    evaluation.add_argument("--images-dir", required=True)
    evaluation.add_argument("--out", default="washer_eval_out")
    evaluation.set_defaults(func=cmd_eval)
    compare = sub.add_parser("compare", help="match parts across two results")
    compare.add_argument("--baseline", required=True)
    compare.add_argument("--candidate", required=True)
    compare.add_argument("--out", default="washer_out")
    compare.set_defaults(func=cmd_compare)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
