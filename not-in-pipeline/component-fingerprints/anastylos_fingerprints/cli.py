"""CLI: `enroll` prints, `match` queries, `eval` labels."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

from anastylos_fingerprints.annotate import annotate
from anastylos_fingerprints.describe import fingerprint
from anastylos_fingerprints.eval import run_eval
from anastylos_fingerprints.match import match_gallery, resolve_weights
from anastylos_fingerprints.store import load_gallery, save_record


def _roi(text: str):
    try:
        x, y, w, h = (int(part) for part in text.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(f"--roi must be x,y,w,h, got {text!r}")
    return x, y, w, h


def _json_file(text, flag):
    if text is None:
        return None
    try:
        return json.loads(Path(text).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise argparse.ArgumentTypeError(f"{flag} is not readable JSON: {exc}") from exc


def _read_image(path):
    image = cv2.imread(path)
    if image is None:
        print(f"cannot read image {path}", file=sys.stderr)
    return image


def _write_bundle(out, result, image, roi):
    directory = Path(out)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    cv2.imwrite(str(directory / "annotated.png"), annotate(image, result, roi))


def cmd_enroll(args) -> int:
    image = _read_image(args.image)
    if image is None:
        return 1
    try:
        record = fingerprint(image, args.roi, _json_file(args.frame_meta, "--frame-meta"),
                             marking=args.marking,
                             source={"image": Path(args.image).name}, record_id=args.id)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        saved = save_record(args.gallery, record, overwrite=args.force)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    result = {"status": "enrolled", "id": args.id, "gallery": str(saved),
              "quality": record["quality"], "size_source": record["size_source"],
              "candidates": []}
    _write_bundle(args.out, result, image, args.roi)
    print(json.dumps(result, indent=2))
    return 0


def cmd_match(args) -> int:
    image = _read_image(args.image)
    if image is None:
        return 1
    try:
        weights = resolve_weights(_json_file(args.weights, "--weights"))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        query = fingerprint(image, args.roi, _json_file(args.frame_meta, "--frame-meta"),
                            marking=args.marking,
                            source={"image": Path(args.image).name})
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        gallery = load_gallery(args.gallery)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    result = match_gallery(query, gallery["records"], gallery["excluded"],
                           top_k=args.top_k, weights=weights)
    result = {"image": Path(args.image).name,
              "roi": list(args.roi) if args.roi else None, **result}
    _write_bundle(args.out, result, image, args.roi)
    print(json.dumps(result, indent=2))
    return 0


def cmd_eval(args) -> int:
    try:
        report = run_eval(args.labels, args.images_dir, args.gallery, args.out,
                          top_k=args.top_k)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps({key: report[key] for key in
                      ("images", "exact", "misses", "false_confident", "undecided")}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fingerprints",
                                     description="ANASTYLOS component fingerprinting")
    sub = parser.add_subparsers(dest="command", required=True)
    enroll = sub.add_parser("enroll", help="fingerprint one crop into a gallery")
    enroll.add_argument("--image", required=True)
    enroll.add_argument("--roi", type=_roi, default=None)
    enroll.add_argument("--id", required=True)
    enroll.add_argument("--gallery", required=True)
    enroll.add_argument("--frame-meta", default=None, help="JSON file with px_per_mm")
    enroll.add_argument("--marking", default=None)
    enroll.add_argument("--force", action="store_true")
    enroll.add_argument("--out", default="fingerprints_out")
    enroll.set_defaults(func=cmd_enroll)
    match = sub.add_parser("match", help="rank gallery fingerprints for one query")
    match.add_argument("--image", required=True)
    match.add_argument("--roi", type=_roi, default=None)
    match.add_argument("--gallery", required=True)
    match.add_argument("--frame-meta", default=None)
    match.add_argument("--marking", default=None)
    match.add_argument("--top-k", type=int, default=5)
    match.add_argument("--weights", default=None, help="JSON file with component weights")
    match.add_argument("--out", default="fingerprints_out")
    match.set_defaults(func=cmd_match)
    evaluation = sub.add_parser("eval", help="score retrieval against ground truth")
    evaluation.add_argument("--labels", required=True)
    evaluation.add_argument("--images-dir", required=True)
    evaluation.add_argument("--gallery", required=True)
    evaluation.add_argument("--top-k", type=int, default=5)
    evaluation.add_argument("--out", default="fingerprints_eval_out")
    evaluation.set_defaults(func=cmd_eval)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
