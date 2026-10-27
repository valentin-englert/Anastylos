"""CLI: one reproducible `build` from memory log to state JSON + SVG."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from anastylos_reconstruction.projector import build_state
from anastylos_reconstruction.render import render_svg
from anastylos_reconstruction.schema import validate_state


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cmd_build(args) -> int:
    memory = args.memory
    if not Path(memory).is_file():
        print(f"memory log not found: {memory}")
        return 2
    state = build_state(memory, results_dir=args.results_dir, graph_path=args.graph)
    validate_state(state)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    state_path = out / "assembly_state.json"
    svg_path = out / "exploded.svg"
    state_text = json.dumps(state, indent=2) + "\n"
    state_path.write_text(state_text, encoding="utf-8")
    svg_text = render_svg(state, svg_path=str(svg_path),
                          embed_crops=args.embed_crops)
    svg_path.write_text(svg_text, encoding="utf-8")
    print(json.dumps({
        "status": state["status"],
        "reason": state["reason"],
        "observations": len(state["components"]),
        "embedded_images": svg_text.count('<image id="cropimg-') if args.embed_crops else 0,
        "confirmed": len(state["relationships"].get("confirmed", [])),
        "candidates": len(state["relationships"].get("candidates", [])),
        "missing_evidence": len(state["missing_evidence"]),
        "warnings": len(state["warnings"]),
        "state": str(state_path),
        "state_sha256": _sha256_bytes(state_text.encode("utf-8")),
        "svg": str(svg_path),
        "svg_sha256": _sha256_bytes(svg_text.encode("utf-8")),
    }, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reconstruct",
        description="ANASTYLOS assembly reconstruction: memory -> "
                    "Assembly State v1 -> exploded-view SVG (read-only)")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="project state and render SVG")
    build.add_argument("memory", help="assembly-memory JSONL log (read-only)")
    build.add_argument("--results-dir", default=None,
                       help="change-detection result.json directory")
    build.add_argument("--graph", default=None,
                       help="read-only assembly_graph.json")
    build.add_argument("--out", default="reconstruction_out",
                       help="output directory (receives assembly_state.json "
                            "and exploded.svg)")
    build.add_argument("--embed-crops", action="store_true",
                       help="embed evidence images as data URIs so the SVG "
                            "is self-contained")
    build.set_defaults(func=cmd_build)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
