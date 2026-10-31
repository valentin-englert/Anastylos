"""CLI: one reproducible `build` from memory log to state JSON + SVG."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from anastylos_reconstruction.projector import build_state
from anastylos_reconstruction.render import render_svg
from anastylos_reconstruction.schema import validate_state
from anastylos_reconstruction.schematic import write_schematic


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
    # Write bytes so the reported SHA-256 matches the file on every OS
    # (write_text would turn "\n" into "\r\n" on Windows).
    state_text = json.dumps(state, indent=2) + "\n"
    state_path.write_bytes(state_text.encode("utf-8"))
    svg_text = render_svg(state, svg_path=str(svg_path),
                          embed_crops=args.embed_crops)
    svg_path.write_bytes(svg_text.encode("utf-8"))
    components = state["components"]
    n_obs = sum(1 for c in components if c.get("kind", "observation") == "observation")
    n_dec = len(components) - n_obs
    print(json.dumps({
        "status": state["status"],
        "reason": state["reason"],
        "components": len(components),
        "observations": n_obs,
        "declared_removals": n_dec,
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


def cmd_schematic(args) -> int:
    package = Path(args.package)
    if not package.is_file():
        print(f"schematic package not found: {package}")
        return 2
    companion = write_schematic(
        str(package), args.out, explode_factor=args.explode_factor)
    print(json.dumps(companion, indent=2))
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
    schematic = sub.add_parser(
        "schematic",
        help="render assembled+exploded controller schematic from a "
             "controller-schematic-v1 package (read-only)")
    schematic.add_argument("package", help="controller_schematic_package.json")
    schematic.add_argument("--out", default="schematic_out",
                           help="output directory")
    schematic.add_argument("--explode-factor", type=float, default=1.0,
                           help="1.0=full explode offsets; 0.0=assembled only")
    schematic.set_defaults(func=cmd_schematic)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
