"""CLI: read one resistor photo, write result.json and annotated.png."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

from anastylos_resistor.vision import analyze, annotate


def _roi(text: str) -> tuple[int, int, int, int]:
    try:
        x, y, w, h = (int(part) for part in text.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(f"--roi must be x,y,w,h, got {text!r}")
    return x, y, w, h


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="read", description="ANASTYLOS resistor color-code reader (experimental)")
    parser.add_argument("--image", required=True, help="resistor macro photo")
    parser.add_argument("--roi", type=_roi, default=None, help="crop x,y,w,h in pixels")
    parser.add_argument("--out", default="resistor_out", help="output directory")
    args = parser.parse_args(argv)
    image = cv2.imread(args.image)
    if image is None:
        print(f"cannot read image {args.image}", file=sys.stderr)
        return 1
    try:
        result = analyze(image, args.roi)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    result = {"image": str(Path(args.image).name), "roi": list(args.roi) if args.roi else None, **result}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    cv2.imwrite(str(out / "annotated.png"), annotate(image, result))
    print("EXPERIMENTAL: synthetic-only validation, not verified on real photos.", file=sys.stderr)
    print(json.dumps(result, indent=2))
    return 0
