"""CLI: `observe` crops, `match` queries, `eval` labels."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

from anastylos_connectors.annotate import annotate
from anastylos_connectors.eval import run_eval
from anastylos_connectors.match import match_gallery, resolve_weights
from anastylos_connectors.observe import observe
from anastylos_connectors.store import load_gallery, save_record


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


def _write_bundle(out, result, image, roi, observation=None):
    directory = Path(out)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    cv2.imwrite(str(directory / "annotated.png"), annotate(image, result, roi, observation))


def cmd_observe(args) -> int:
    image = _read_image(args.image)
    if image is None:
        return 1
    try:
        record = observe(image, args.roi, _json_file(args.frame_meta, "--frame-meta"),
                         marking=args.marking,
                         source={"image": Path(args.image).name}, record_id=args.id)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.gallery:
        try:
            saved = save_record(args.gallery, record, overwrite=args.force)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        saved_note = str(saved)
    else:
        saved_note = None
    result = {"status": "observed", "id": args.id, "gallery": saved_note,
              "quality": record["quality"], "role": record["role"],
              "contact_count": record["contact_count"],
              "mating_face_visible": record["mating_face_visible"],
              "observation": record, "candidates": []}
    _write_bundle(args.out, result, image, args.roi, record)
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
        query = observe(image, args.roi, _json_file(args.frame_meta, "--frame-meta"),
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
    if gallery["records"] or not gallery["excluded"]:
        result = match_gallery(query, gallery["records"], gallery["excluded"],
                               top_k=args.top_k, weights=weights)
    else:
        result = {"status": "unavailable",
                  "reason": "gallery holds no comparable observations",
                  "candidates": [], "excluded": gallery["excluded"],
                  "query_quality": query["quality"]}
    result = {"image": Path(args.image).name,
              "roi": list(args.roi) if args.roi else None, **result}
    _write_bundle(args.out, result, image, args.roi, query)
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
                      ("images", "exact", "misses", "false_confident", "undecided")
                      if key in report}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="connectors",
                                     description="ANASTYLOS connector matching")
    sub = parser.add_subparsers(dest="command", required=True)
    observe_cmd = sub.add_parser("observe", help="analyze one crop, optionally saving it")
    observe_cmd.add_argument("--image", required=True)
    observe_cmd.add_argument("--roi", type=_roi, default=None)
    observe_cmd.add_argument("--id", required=True)
    observe_cmd.add_argument("--gallery", default=None)
    observe_cmd.add_argument("--frame-meta", default=None, help="JSON file with px_per_mm")
    observe_cmd.add_argument("--marking", default=None)
    observe_cmd.add_argument("--force", action="store_true")
    observe_cmd.add_argument("--out", default="connectors_out")
    observe_cmd.set_defaults(func=cmd_observe)
    match = sub.add_parser("match", help="rank gallery counterparts for one query")
    match.add_argument("--image", required=True)
    match.add_argument("--roi", type=_roi, default=None)
    match.add_argument("--gallery", required=True)
    match.add_argument("--frame-meta", default=None)
    match.add_argument("--marking", default=None)
    match.add_argument("--top-k", type=int, default=5)
    match.add_argument("--weights", default=None, help="JSON file with component weights")
    match.add_argument("--out", default="connectors_out")
    match.set_defaults(func=cmd_match)
    evaluation = sub.add_parser("eval", help="score retrieval against ground truth")
    evaluation.add_argument("--labels", required=True)
    evaluation.add_argument("--images-dir", required=True)
    evaluation.add_argument("--gallery", required=True)
    evaluation.add_argument("--top-k", type=int, default=5)
    evaluation.add_argument("--out", default="connectors_eval_out")
    evaluation.set_defaults(func=cmd_eval)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
