"""Spatial evidence validator CLI (stdlib only).

    python validate.py sidecar --sidecar sidecar.json [--layout config.json ...]
    python validate.py profile --profile profile.json
    python validate.py board --board board.json
    python validate.py replay --contracts <contracts-dir> --out report.json
    python validate.py dataset --inputs inputs.json --out manifest.json

Exit codes: 0 accept/green/consistent, 1 reject/needs-review/red/issues,
2 usage or I/O error. Every command prints the artifact SHA-256 it wrote.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from anastylos_spatial.dataset import (
    build_dataset,
    manifest_bytes,
    validate_dataset_manifest,
)
from anastylos_spatial.profiles import validate_board_instance, validate_profile
from anastylos_spatial.replay import replay, report_bytes
from anastylos_spatial.validator import load_json_strict, validate_sidecar


def _read_json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha_of_file(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def cmd_sidecar(args: argparse.Namespace) -> int:
    try:
        doc, sha = load_json_strict(args.sidecar)
    except (OSError, ValueError) as exc:
        print(f"error: cannot load sidecar: {exc}", file=sys.stderr)
        return 2
    ctx: dict = {}
    try:
        if args.layout:
            ctx["layout"] = _read_json(args.layout)
            ctx["layout_sha256"] = args.layout_sha256 or _sha_of_file(args.layout)
        if args.profile:
            ctx["profile"] = _read_json(args.profile)
            ctx["profile_sha256"] = args.profile_sha256 or _sha_of_file(args.profile)
        if args.epochs:
            ctx["epochs"] = _read_json(args.epochs)
        if args.assets:
            ctx["image_shas"] = _read_json(args.assets)
    except (OSError, ValueError) as exc:
        print(f"error: cannot load context: {exc}", file=sys.stderr)
        return 2
    result = validate_sidecar(doc, ctx)
    result["sidecar_sha256"] = sha
    print(json.dumps(result, indent=2))
    return 0 if result["verdict"] == "accept" else 1


def cmd_profile(args: argparse.Namespace) -> int:
    try:
        doc = _read_json(args.profile)
    except (OSError, ValueError) as exc:
        print(f"error: cannot load profile: {exc}", file=sys.stderr)
        return 2
    result = validate_profile(doc)
    result["profile_sha256"] = _sha_of_file(args.profile)
    print(json.dumps(result, indent=2))
    return 0 if result["verdict"] == "accept" else 1


def cmd_board(args: argparse.Namespace) -> int:
    try:
        doc = _read_json(args.board)
    except (OSError, ValueError) as exc:
        print(f"error: cannot load board record: {exc}", file=sys.stderr)
        return 2
    result = validate_board_instance(doc)
    result["board_sha256"] = _sha_of_file(args.board)
    print(json.dumps(result, indent=2))
    return 0 if result["verdict"] == "accept" else 1


def cmd_replay(args: argparse.Namespace) -> int:
    try:
        report = replay(args.contracts, Path(__file__).resolve().parent, args.candidate)
    except (OSError, ValueError) as exc:
        print(f"error: replay failed: {exc}", file=sys.stderr)
        return 2
    raw = report_bytes(report)
    Path(args.out).write_bytes(raw)
    print(f"report sha256 {hashlib.sha256(raw).hexdigest()}")
    summary = report["negatives"]
    print(json.dumps({"green": report["green"],
                      "sidecar_valid_matched": report["sidecar_valid"]["matched"],
                      "guidance_frozen": f"{report['guidance_frozen']['matched']}/{report['guidance_frozen']['total']}",
                      "guidance_extensions": f"{report['guidance_extensions']['matched']}/{report['guidance_extensions']['total']}",
                      "negatives": f"{summary['matched']}/{summary['total']}",
                      "synthetic_profile_matched": report["synthetic_profile"]["matched"]}))
    return 0 if report["green"] else 1


def cmd_dataset(args: argparse.Namespace) -> int:
    try:
        inputs = _read_json(args.inputs)
    except (OSError, ValueError) as exc:
        print(f"error: cannot load inputs: {exc}", file=sys.stderr)
        return 2
    try:
        evaluated = []
        for entry in inputs["sidecars"]:
            doc, sha = load_json_strict(entry["sidecar"])
            report = _read_json(entry["report"]) if entry.get("report") else validate_sidecar(doc, {})
            evaluated.append({"sidecar": doc, "sidecar_sha256": sha,
                              "verdict": report["verdict"], "metric_ready": report.get("metric_ready", False),
                              "reasons": report.get("reasons", []), "reviews": report.get("reviews", [])})
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: cannot load dataset entries: {exc}", file=sys.stderr)
        return 2
    multi = {tuple(t) for t in inputs.get("multi_image_targets", [])}
    manifest, issues = build_dataset(
        evaluated, inputs.get("delivery"), inputs.get("required", []),
        inputs.get("skipped", []), multi, inputs.get("capture_set_id"),
        inputs.get("created_at"), inputs.get("session_id"), inputs.get("target_plan_sha256"))
    issues.extend(validate_dataset_manifest(manifest))
    raw = manifest_bytes(manifest)
    Path(args.out).write_bytes(raw)
    print(f"manifest sha256 {hashlib.sha256(raw).hexdigest()}")
    print(json.dumps({"result": manifest["result"], "geometry_ready": manifest["geometry_ready"],
                      "issues": issues}))
    return 0 if not issues else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="ANASTYLOS spatial evidence validator (CAD-0 replay).")
    sub = parser.add_subparsers(dest="command", required=True)

    sidecar = sub.add_parser("sidecar", help="validate one spatial sidecar")
    sidecar.add_argument("--sidecar", required=True)
    sidecar.add_argument("--layout", default=None, help="calibration-config.json (read-only)")
    sidecar.add_argument("--layout-sha256", default=None)
    sidecar.add_argument("--profile", default=None)
    sidecar.add_argument("--profile-sha256", default=None)
    sidecar.add_argument("--epochs", default=None, help="JSON list of {epoch_id,start_utc,end_utc}")
    sidecar.add_argument("--assets", default=None, help="JSON map sha256 -> byte_length")
    sidecar.set_defaults(func=cmd_sidecar)

    profile = sub.add_parser("profile", help="validate one calibration profile")
    profile.add_argument("--profile", required=True)
    profile.set_defaults(func=cmd_profile)

    board = sub.add_parser("board", help="validate one board print-instance record")
    board.add_argument("--board", required=True)
    board.set_defaults(func=cmd_board)

    replay_cmd = sub.add_parser("replay", help="replay frozen + owned vectors, write report")
    replay_cmd.add_argument("--contracts", required=True, help="frozen contracts dir (read-only)")
    replay_cmd.add_argument("--candidate", required=True, help="candidate commit SHA")
    replay_cmd.add_argument("--out", required=True)
    replay_cmd.set_defaults(func=cmd_replay)

    dataset = sub.add_parser("dataset", help="build an immutable spatial dataset manifest")
    dataset.add_argument("--inputs", required=True)
    dataset.add_argument("--out", required=True)
    dataset.set_defaults(func=cmd_dataset)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
