"""CLI: `decode` text without an engine, `read` crops, `eval` labels."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pcb-ocr"))

import cv2
from anastylos_pcb.backends import get_backend

from anastylos_smd.annotate import annotate
from anastylos_smd.decode import decode
from anastylos_smd.eval import run_eval
from anastylos_smd.vision import analyze


def _roi(text: str) -> tuple[int, int, int, int]:
    try:
        x, y, w, h = (int(part) for part in text.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(f"--roi must be x,y,w,h, got {text!r}")
    return x, y, w, h


def _aliases(text: str | None) -> dict | None:
    if not text:
        return None
    mapping = {}
    for chunk in text.split(","):
        try:
            letter, factor = chunk.split("=", 1)
        except ValueError:
            raise argparse.ArgumentTypeError(f"--aliases must be LETTER=value,... got {text!r}")
        letter = letter.strip().upper()
        if len(letter) != 1 or not letter.isalpha():
            raise argparse.ArgumentTypeError(f"alias key must be one letter, got {letter!r}")
        mapping[letter] = factor.strip()
    return mapping


def _backend(args):
    options = {"command": args.tesseract_cmd} if args.tesseract_cmd else {}
    return get_backend(args.backend, **options)


def cmd_decode(args) -> int:
    print(json.dumps(decode(args.code, _aliases(args.aliases)), indent=2))
    return 0


def cmd_read(args) -> int:
    try:
        backend = _backend(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    image = cv2.imread(args.image)
    if image is None:
        print(f"cannot read image {args.image}", file=sys.stderr)
        return 1
    try:
        result = analyze(image, backend, args.roi, _aliases(args.aliases), Path(args.image).name)
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
        backend = _backend(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        report = run_eval(args.labels, args.images_dir, args.out, backend, _aliases(args.aliases))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps({key: report[key] for key in
                      ("images", "exact", "misses", "false_confident", "undecided")}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="smd", description="ANASTYLOS SMD resistor marking reader")
    sub = parser.add_subparsers(dest="command", required=True)
    decode_cmd = sub.add_parser("decode", help="decode marking text (no OCR engine)")
    decode_cmd.add_argument("code")
    decode_cmd.add_argument("--aliases", default=None, help="EIA-96 letter map, e.g. S=0.1,R=0.01")
    decode_cmd.set_defaults(func=cmd_decode)
    read = sub.add_parser("read", help="read a cropped component image")
    read.add_argument("--image", required=True)
    read.add_argument("--roi", type=_roi, default=None)
    read.add_argument("--backend", default="tesseract")
    read.add_argument("--tesseract-cmd", default=None)
    read.add_argument("--aliases", default=None)
    read.add_argument("--out", default="smd_out")
    read.set_defaults(func=cmd_read)
    evaluation = sub.add_parser("eval", help="score readings against ground-truth markings")
    evaluation.add_argument("--labels", required=True)
    evaluation.add_argument("--images-dir", required=True)
    evaluation.add_argument("--backend", default="tesseract")
    evaluation.add_argument("--tesseract-cmd", default=None)
    evaluation.add_argument("--aliases", default=None)
    evaluation.add_argument("--out", default="smd_eval_out")
    evaluation.set_defaults(func=cmd_eval)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
