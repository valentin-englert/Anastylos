"""CLI: `read` one crop, `eval` a labelled set."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

from anastylos_screw.annotate import annotate
from anastylos_screw.eval import run_eval
from anastylos_screw.vision import analyze


def _roi(text: str) -> tuple[int, int, int, int]:
    try:
        x, y, w, h = (int(part) for part in text.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(f"--roi must be x,y,w,h, got {text!r}")
    return x, y, w, h


def cmd_read(args) -> int:
    image = cv2.imread(args.image)
    if image is None:
        print(f"cannot read image {args.image}", file=sys.stderr)
        return 1
    try:
        result = analyze(image, args.roi)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    result = {"image": str(Path(args.image).name),
              "roi": list(args.roi) if args.roi else None, **result}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
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
                      ("images", "exact", "misses", "false_confident", "undecided")}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="screw", description="ANASTYLOS screw head recognition")
    sub = parser.add_subparsers(dest="command", required=True)
    read = sub.add_parser("read", help="classify one screw-head crop")
    read.add_argument("--image", required=True)
    read.add_argument("--roi", type=_roi, default=None)
    read.add_argument("--out", default="screw_out")
    read.set_defaults(func=cmd_read)
    evaluation = sub.add_parser("eval", help="score classifications against ground truth")
    evaluation.add_argument("--labels", required=True)
    evaluation.add_argument("--images-dir", required=True)
    evaluation.add_argument("--out", default="screw_eval_out")
    evaluation.set_defaults(func=cmd_eval)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
