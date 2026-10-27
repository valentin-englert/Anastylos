"""CLI: read cables, compare paths, build assembly graphs, run eval."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2

from anastylos_cable.annotate import annotate
from anastylos_cable.cables import analyze
from anastylos_cable.compare import compare
from anastylos_cable.eval import evaluate_cables, evaluate_graph
from anastylos_cable.graph import build_graph, to_dot


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n",
                    encoding="utf-8")


def _read_roi(text: str | None):
    if text is None:
        return None
    parts = [int(value) for value in text.split(",")]
    if len(parts) != 4:
        raise ValueError("--roi needs x,y,w,h")
    return tuple(parts)


def _command_read(args) -> int:
    image = cv2.imread(args.image, cv2.IMREAD_COLOR)
    if image is None:
        print(f"cannot read image {args.image}")
        return 1
    metadata = None
    if args.metadata:
        metadata = json.loads(Path(args.metadata).read_text(encoding="utf-8"))
    try:
        result = analyze(image, roi=_read_roi(args.roi),
                         frame_metadata=metadata)
    except ValueError as exc:
        print(str(exc))
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "cable_result.json", result)
    x, y, _, _ = _read_roi(args.roi) or (0, 0, 0, 0)
    overlay = annotate(image, result, origin=(x, y))
    cv2.imwrite(str(out / "cable_overlay.png"), overlay)
    result["overlay"] = str(out / "cable_overlay.png")
    print(json.dumps({"status": result["status"],
                      "cables": len(result["cables"]),
                      "features": len(result["features"]),
                      "out": str(out)}, indent=2))
    return 0


def _command_compare(args) -> int:
    before = json.loads(Path(args.before).read_text(encoding="utf-8"))
    after = json.loads(Path(args.after).read_text(encoding="utf-8"))
    result = compare(before, after)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        _write_json(out / "cable_compare.json", result)
    print(json.dumps(result, indent=2))
    return 0


def _command_graph(args) -> int:
    graph = build_graph(args.memory, results_dir=args.results_dir,
                        confirmations_path=args.confirmations)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "assembly_graph.json", graph)
    (out / "assembly_graph.dot").write_text(to_dot(graph), encoding="utf-8")
    print(json.dumps({"status": graph["status"],
                      "nodes": len(graph["nodes"]),
                      "confirmed": len(graph["confirmed"]),
                      "candidates": len(graph["candidates"]),
                      "cycles": len(graph["cycles"]),
                      "out": str(out)}, indent=2))
    return 0


def _command_eval(args) -> int:
    if args.labels:
        report = evaluate_cables(args.labels, images_dir=args.images_dir)
    else:
        report = evaluate_graph(args.graph_fixtures)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        _write_json(out / "eval_report.json", report)
    print(json.dumps(report.get("summary", report), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cable",
        description="ANASTYLOS cable routing and assembly order")
    commands = parser.add_subparsers(dest="command", required=True)
    read = commands.add_parser("read", help="extract cable paths from a crop")
    read.add_argument("image", help="tightly framed cable photo")
    read.add_argument("--roi", default=None, help="x,y,w,h source-frame crop")
    read.add_argument("--metadata", default=None,
                      help="frame metadata JSON (pixels_per_mm)")
    read.add_argument("--out", default="cable_out", help="output directory")
    read.set_defaults(run=_command_read)
    compare_cmd = commands.add_parser("compare",
                                      help="compare two cable results")
    compare_cmd.add_argument("before", help="earlier cable_result.json")
    compare_cmd.add_argument("after", help="later cable_result.json")
    compare_cmd.add_argument("--out", default=None,
                             help="output directory (optional)")
    compare_cmd.set_defaults(run=_command_compare)
    graph = commands.add_parser("graph", help="build dependency graph")
    graph.add_argument("memory", help="assembly-memory JSONL log (read-only)")
    graph.add_argument("--results-dir", default=None,
                       help="change-detection result.json directory")
    graph.add_argument("--confirmations", default=None,
                       help="operator confirmations JSON")
    graph.add_argument("--out", default="graph_out", help="output directory")
    graph.set_defaults(run=_command_graph)
    evaluate = commands.add_parser("eval", help="run labelled evaluation")
    source = evaluate.add_mutually_exclusive_group(required=True)
    source.add_argument("--labels", default=None, help="cable labels JSON")
    source.add_argument("--graph-fixtures", default=None,
                        help="graph fixtures directory")
    evaluate.add_argument("--images-dir", default=None,
                          help="label image directory")
    evaluate.add_argument("--out", default=None, help="output directory")
    evaluate.set_defaults(run=_command_eval)
    args = parser.parse_args(argv)
    return args.run(args)
