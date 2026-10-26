"""CLI: `read` one image, `eval` a labelled set."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

from anastylos_pcb.annotate import annotate
from anastylos_pcb.backends import get_backend
from anastylos_pcb.eval import run_eval
from anastylos_pcb.pipeline import analyze


def _roi(text: str) -> tuple[int, int, int, int]:
    try:
        x, y, w, h = (int(part) for part in text.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(f"--roi must be x,y,w,h, got {text!r}")
    return x, y, w, h


def _frame_meta(path: str | None) -> dict | None:
    if path is None:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot read frame metadata {path}: {exc}")


def cmd_read(args) -> int:
    try:
        options = {"command": args.tesseract_cmd} if args.tesseract_cmd else {}
        backend = get_backend(args.backend, **options)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    image = cv2.imread(args.image)
    if image is None:
        print(f"cannot read image {args.image}", file=sys.stderr)
        return 1
    try:
        result = analyze(image, backend, args.roi, _frame_meta(args.frame_meta))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    result = {"image": str(Path(args.image).name),
              "roi": list(args.roi) if args.roi else None, **result}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    cv2.imwrite(str(out / "annotated.png"), annotate(image, result))
    if result["status"] == "unavailable":
        print(f"OCR backend {args.backend!r} is not installed.", file=sys.stderr)
    print(json.dumps(result, indent=2))
    return 0


def cmd_eval(args) -> int:
    try:
        options = {"command": args.tesseract_cmd} if args.tesseract_cmd else {}
        backend = get_backend(args.backend, **options)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        report = run_eval(args.labels, args.images_dir, args.out, backend,
                           _frame_meta(args.frame_meta))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps({key: report[key] for key in
                      ("images", "labels", "predicted", "hits", "recall", "precision")}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ocr", description="ANASTYLOS PCB silkscreen OCR")
    sub = parser.add_subparsers(dest="command", required=True)
    read = sub.add_parser("read", help="extract designators from one image")
    read.add_argument("--image", required=True)
    read.add_argument("--roi", type=_roi, default=None)
    read.add_argument("--frame-meta", default=None, help="JSON with view/px_per_mm/origin_frame_mm")
    read.add_argument("--backend", default="tesseract")
    read.add_argument("--tesseract-cmd", default=None, help="tesseract binary path (default: PATH)")
    read.add_argument("--out", default="ocr_out")
    read.set_defaults(func=cmd_read)
    evaluation = sub.add_parser("eval", help="score predictions against ground-truth labels")
    evaluation.add_argument("--labels", required=True)
    evaluation.add_argument("--images-dir", required=True)
    evaluation.add_argument("--frame-meta", default=None)
    evaluation.add_argument("--backend", default="tesseract")
    evaluation.add_argument("--tesseract-cmd", default=None, help="tesseract binary path (default: PATH)")
    evaluation.add_argument("--out", default="ocr_eval_out")
    evaluation.set_defaults(func=cmd_eval)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
