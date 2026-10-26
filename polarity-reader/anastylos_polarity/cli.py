"""CLI: `read` one crop, `eval` a labelled set."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

from anastylos_polarity.annotate import annotate
from anastylos_polarity.eval import run_eval
from anastylos_polarity.vision import analyze


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
    reference = None
    if args.reference_angle is not None or args.reference_kind is not None:
        if args.reference_angle is None or args.reference_kind is None:
            print("reference needs both --reference-kind and "
                  "--reference-angle", file=sys.stderr)
            return 2
        reference = {"kind": args.reference_kind,
                     "angle_deg": args.reference_angle}
    try:
        metadata = _frame_metadata(args.frame_metadata)
    except argparse.ArgumentTypeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        result = analyze(image, args.roi, family=args.family,
                         reference=reference, frame_metadata=metadata)
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="polarity",
        description="ANASTYLOS polarity and orientation recognition")
    sub = parser.add_subparsers(dest="command", required=True)
    read = sub.add_parser("read", help="detect polarity markers in one crop")
    read.add_argument("--image", required=True)
    read.add_argument("--roi", type=_roi, default=None)
    read.add_argument("--family", default=None,
                      help="declared family: diode, aluminum_electrolytic, "
                           "tantalum, ic, connector, unknown")
    read.add_argument("--reference-kind", default=None,
                      help="explicit reference observation kind")
    read.add_argument("--reference-angle", type=float, default=None,
                      help="reference direction, deg clockwise from +x")
    read.add_argument("--frame-metadata", default=None,
                      help='JSON or file, e.g. {"pixels_per_mm": 4.0}')
    read.add_argument("--out", default="polarity_out")
    read.set_defaults(func=cmd_read)
    evaluation = sub.add_parser("eval", help="score readings against labels")
    evaluation.add_argument("--labels", required=True)
    evaluation.add_argument("--images-dir", required=True)
    evaluation.add_argument("--out", default="polarity_eval_out")
    evaluation.set_defaults(func=cmd_eval)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
