"""CLI: `measure` one crop, `eval` a labelled set, `compare` two results."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

from anastylos_screw_measure.annotate import annotate
from anastylos_screw_measure.calibration import blank, load_calibration
from anastylos_screw_measure.compare import compare_measurements
from anastylos_screw_measure.eval import run_eval
from anastylos_screw_measure.vision import MODES, analyze


def _roi(text: str) -> tuple[int, int, int, int]:
    try:
        x, y, w, h = (int(part) for part in text.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(f"--roi must be x,y,w,h, got {text!r}")
    return x, y, w, h


def _calibration(path: str | None):
    if path is None:
        return blank("no calibration supplied; pixel-only output")
    try:
        return load_calibration(path)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)


def cmd_measure(args) -> int:
    image = cv2.imread(args.image)
    if image is None:
        print(f"cannot read image {args.image}", file=sys.stderr)
        return 1
    calibration = _calibration(args.calibration)
    if not calibration.get("valid"):
        print(f"warning: {'; '.join(calibration['reasons'])}; pixel-only output",
              file=sys.stderr)
    try:
        result = analyze(image, mode=args.mode, roi=args.roi, calibration=calibration)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    result = {"image": str(Path(args.image).name),
              "source": str(args.image), **result,
              "evidence_refs": result["evidence_refs"] + ["annotated:annotated.png"]}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    cv2.imwrite(str(out / "annotated.png"), annotate(image, result))
    print(json.dumps(result, indent=2))
    return 0


def cmd_eval(args) -> int:
    calibration = _calibration(args.calibration)
    try:
        report = run_eval(args.labels, args.images_dir, args.out, calibration)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps({key: report[key] for key in
                      ("images", "verdicts", "mm_abs_err", "px_abs_err")}, indent=2))
    return 0


def cmd_compare(args) -> int:
    try:
        left = json.loads(Path(args.a).read_text(encoding="utf-8-sig"))
        right = json.loads(Path(args.b).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read comparison inputs: {exc}", file=sys.stderr)
        return 1
    report = compare_measurements(left, right, args.measurement)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "comparison.json").write_text(json.dumps(report, indent=2) + "\n",
                                         encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="measure",
                                     description="ANASTYLOS calibrated screw measurement")
    sub = parser.add_subparsers(dest="command", required=True)
    measure = sub.add_parser("measure", help="measure one screw crop")
    measure.add_argument("--image", required=True)
    measure.add_argument("--mode", required=True, choices=MODES,
                         help="top: head disc; side: silhouette length/shaft")
    measure.add_argument("--roi", type=_roi, default=None)
    measure.add_argument("--calibration", default=None,
                         help="JSON with pixels_per_mm or a reference (else pixel-only)")
    measure.add_argument("--out", default="measure_out")
    measure.set_defaults(func=cmd_measure)
    evaluation = sub.add_parser("eval", help="score measurements against ground truth")
    evaluation.add_argument("--labels", required=True)
    evaluation.add_argument("--images-dir", required=True)
    evaluation.add_argument("--calibration", default=None)
    evaluation.add_argument("--out", default="measure_eval_out")
    evaluation.set_defaults(func=cmd_eval)
    compare = sub.add_parser("compare", help="compare two result.json files")
    compare.add_argument("--a", required=True)
    compare.add_argument("--b", required=True)
    compare.add_argument("--measurement", default="visible_head_diameter")
    compare.add_argument("--out", default="measure_compare_out")
    compare.set_defaults(func=cmd_compare)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
