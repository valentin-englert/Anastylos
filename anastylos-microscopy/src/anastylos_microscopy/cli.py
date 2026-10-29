"""Command line interface: python -m anastylos_microscopy <command> ...

Commands
  inspect   one image (--image), a folder (--batch-dir) or a capture manifest (--manifest)
  replay    re-run a stored inspection.json and compare digests
  validate  check inspection / manifest / calibration / dataset / review JSON against schemas
  review    write a separate operator review record for an inspection
  eval      score a MIC-01..05 dataset split
  readers   list reader modules, code hashes and OCR availability

Exit codes (v0.1.1; `inspect` single image and batch use the same rules):
  0  success - every inspected image completed (an abstention such as unknown /
     ambiguous / conflict is a completed inspection, not a failure)
  1  input/validation error - invalid input, empty batch, or no usable record at
     all (every entry input_error / reader_error / crashed / manifest_rejected)
  2  usage error
  3  environment error - ANASTYLOS_ROOT missing/invalid (fatal, checked before any
     image is processed, nothing written), or every entry 'unavailable'
     (a required reader dependency such as Tesseract is missing)
  4  partial failure - at least one usable record and at least one problem entry;
     batch.json "outcome_counts" says which
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from anastylos_microscopy import __version__, env
from anastylos_microscopy.util import read_json


def _roi(text):
    try:
        values = [int(v) for v in text.split(",")]
    except ValueError:
        raise argparse.ArgumentTypeError(f"--roi must be x,y,w,h, got {text!r}") from None
    if len(values) != 4:
        raise argparse.ArgumentTypeError(f"--roi must be x,y,w,h, got {text!r}")
    return values


def _json_arg(text, flag):
    if text is None:
        return None
    try:
        return read_json(text)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"{flag}: cannot read JSON {text}: {exc}")


def _options(args) -> dict:
    options = {}
    for name in ("view", "polarity_family", "washer_family", "reference_kind", "reference_angle",
                 "gallery", "enroll_id", "marking", "smd_aliases", "markings_mapping"):
        value = getattr(args, name, None)
        if value is not None:
            options[name] = value
    if getattr(args, "smd_aliases", None):
        aliases = {}
        for chunk in args.smd_aliases.split(","):
            letter, _, factor = chunk.partition("=")
            aliases[letter.strip().upper()] = factor.strip()
        options["smd_aliases"] = aliases
    if getattr(args, "enroll_id", None):
        options["fingerprint_action"] = "enroll"
        options["connector_action"] = "enroll"
    if getattr(args, "force", False):
        options["force"] = True
    if getattr(args, "top_k", None) is not None:
        options["top_k"] = args.top_k
    if getattr(args, "weights", None):
        options["weights"] = _json_arg(args.weights, "--weights")
    return options


def cmd_inspect(args) -> int:
    from anastylos_microscopy import runner
    from anastylos_microscopy.orchestrator import inspect_image

    sources = [s for s in (args.image, args.batch_dir, args.manifest) if s]
    if len(sources) != 1:
        print("give exactly one of --image, --batch-dir, --manifest", file=sys.stderr)
        return 2
    common = dict(mode=args.mode, readers=args.readers.split(",") if args.readers else None,
                  options=_options(args), hypothesis=args.hypothesis,
                  calibration_doc=_json_arg(args.calibration, "--calibration"),
                  calibration_origin=args.calibration, session_id=args.session_id,
                  clock=args.clock, anastylos_root=args.anastylos_root,
                  tesseract_cmd=args.tesseract_cmd)
    try:
        if args.image:
            record, path = inspect_image(args.image, args.out, roi=args.roi,
                                         component_observation_id=args.component_observation_id,
                                         capture={"camera_id": args.camera_id,
                                                  "validation_class": args.validation_class,
                                                  "optical_profile": args.optical_profile},
                                         **common)
            processing = runner.classify_record(record)
            code = runner.exit_code_for([processing])
            print(json.dumps({"inspection_json": str(path), "inspection_id": record["inspection_id"],
                              "overall_status": record["uncertainty"]["overall_status"],
                              "processing": processing,
                              "readers": {b["reader"]: b["status"] for b in record["recognizers"]},
                              "native_readers": {b["reader"]: b["native_status"]
                                                 for b in record["recognizers"]},
                              "digest": record["digest"]["value"], "exit_code": code}, indent=2))
            return code
        capture_ctx = {"camera_id": args.camera_id, "validation_class": args.validation_class,
                       "optical_profile": args.optical_profile}
        if args.batch_dir:
            batch, path = runner.inspect_directory(args.batch_dir, args.out, roi=args.roi,
                                                   capture=capture_ctx, **common)
        else:
            batch, path = runner.inspect_manifest(args.manifest, args.out, roi=args.roi, **common)
        print(json.dumps({"batch_json": str(path), "count": batch["count"],
                          "status_counts": batch["status_counts"], "outcome": batch["outcome"],
                          "outcome_counts": batch["outcome_counts"],
                          "exit_code": batch["exit_code"]}, indent=2))
        if batch["exit_code"] != 0:
            print(f"batch outcome {batch['outcome']}: {batch['outcome_counts']['problem_entries']} of "
                  f"{batch['count']} entries did not complete (see {path})", file=sys.stderr)
        return batch["exit_code"]
    except env.ReaderEnvironmentError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def cmd_replay(args) -> int:
    from anastylos_microscopy.runner import replay

    try:
        result = replay(args.inspection, args.out, image=args.image, anastylos_root=args.anastylos_root)
    except env.ReaderEnvironmentError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except (OSError, ValueError) as exc:
        print(f"replay refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result["identical"] else 1


def cmd_validate(args) -> int:
    from anastylos_microscopy import schema

    try:
        document = read_json(args.file)
    except (OSError, ValueError) as exc:
        print(f"cannot read {args.file}: {exc}", file=sys.stderr)
        return 1
    errors = schema.validate_named(document, args.kind)
    if args.kind == "capture_manifest" and not errors:
        # v0.1.1 (QA-02): the same lexical, confinement, single-read and hash checks that
        # `inspect --manifest` applies; a manifest is valid only if every frame is.
        from anastylos_microscopy import capture
        try:
            errors += capture.verify(args.file)
        except capture.ManifestError as exc:
            errors.append(str(exc))
    if args.kind == "inspection" and not errors:
        from anastylos_microscopy.util import canonical_json, sha256_bytes
        scope = {k: v for k, v in document.items() if k not in ("digest", "run")}
        if sha256_bytes(canonical_json(scope)) != document["digest"]["value"]:
            errors.append("$.digest.value does not match the record content (edited after writing?)")
        base = Path(args.file).parent
        from anastylos_microscopy.util import sha256_file
        for artifact in document.get("artifacts", []):
            target = base / artifact["path"]
            if not target.is_file():
                errors.append(f"artifact missing: {artifact['path']}")
            elif sha256_file(target) != artifact["sha256"]:
                errors.append(f"artifact hash mismatch: {artifact['path']}")
    print(json.dumps({"file": args.file, "kind": args.kind, "valid": not errors,
                      "errors": errors}, indent=2))
    return 0 if not errors else 1


def cmd_review(args) -> int:
    from anastylos_microscopy.review import write_review

    try:
        path = write_review(args.inspection, operator=args.operator, decision=args.decision,
                            identification=args.identification, notes=args.notes,
                            evidence=args.evidence, clock=args.clock, supersedes=args.supersedes)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps({"review_json": str(path)}, indent=2))
    return 0


def cmd_eval(args) -> int:
    from anastylos_microscopy.evaluation import evaluate, harness_fingerprint

    try:
        if args.print_fingerprint:
            print(harness_fingerprint(args.anastylos_root))
            return 0
        report, path = evaluate(args.dataset, args.out, split=args.split,
                                frozen_config=args.frozen_config, anastylos_root=args.anastylos_root,
                                tesseract_cmd=args.tesseract_cmd, clock=args.clock)
    except env.ReaderEnvironmentError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps({"report": str(path), "validation_class": report["validation_class"],
                      "hardware_validation": report["hardware_validation"],
                      "per_workflow": report["per_workflow"]}, indent=2))
    return 0


def cmd_readers(args) -> int:
    try:
        root = env.resolve_root(args.anastylos_root)
    except env.ReaderEnvironmentError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    env.activate(root)
    report = env.environment_report(root)
    try:
        from anastylos_pcb.backends import get_backend
        options = {"command": args.tesseract_cmd} if args.tesseract_cmd else {}
        backend = get_backend("tesseract", **options)
        report["ocr"] = {"backend": "tesseract", "available": backend.available(),
                         "version": backend.version()}
    except Exception as exc:  # noqa: BLE001
        report["ocr"] = {"backend": "tesseract", "available": False, "error": str(exc)}
    try:
        import cv2
        import numpy
        report["opencv"], report["numpy"] = cv2.__version__, numpy.__version__
    except ImportError as exc:
        report["opencv"] = f"unavailable: {exc}"
    print(json.dumps(report, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    from anastylos_microscopy.orchestrator import HYPOTHESES, MODES

    parser = argparse.ArgumentParser(prog="python -m anastylos_microscopy",
                                     description=f"ANASTYLOS Microscopy Core {__version__}")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    def root_args(p):
        p.add_argument("--anastylos-root", default=None, help="Anastylos checkout (default: $ANASTYLOS_ROOT)")
        p.add_argument("--tesseract-cmd", default=None, help="tesseract binary (default: PATH)")

    ins = sub.add_parser("inspect", help="inspect an image, folder or capture manifest")
    ins.add_argument("--image")
    ins.add_argument("--batch-dir")
    ins.add_argument("--manifest")
    ins.add_argument("--mode", default="auto", choices=MODES)
    ins.add_argument("--readers", default=None, help="comma list for --mode multi")
    ins.add_argument("--hypothesis", default=None, choices=sorted(HYPOTHESES),
                     help="operator component hypothesis for --mode auto")
    ins.add_argument("--roi", type=_roi, default=None, help="x,y,w,h in source pixels")
    ins.add_argument("--calibration", default=None, help="anastylos-microscopy-calibration/1 JSON")
    ins.add_argument("--view", choices=("top", "side"), default=None)
    ins.add_argument("--polarity-family", default=None)
    ins.add_argument("--washer-family", default=None)
    ins.add_argument("--reference-kind", default=None)
    ins.add_argument("--reference-angle", type=float, default=None)
    ins.add_argument("--gallery", default=None, help="fingerprint/connector gallery directory")
    ins.add_argument("--enroll-id", default=None, help="enroll into --gallery instead of matching")
    ins.add_argument("--force", action="store_true", help="allow re-enrolling an existing id")
    ins.add_argument("--marking", default=None, help="operator-supplied marking text (fingerprints)")
    ins.add_argument("--top-k", type=int, default=None)
    ins.add_argument("--weights", default=None)
    ins.add_argument("--smd-aliases", default=None, help="EIA-96 aliases, e.g. S=0.1,R=0.01")
    ins.add_argument("--markings-mapping", default=None, help="operator code mapping JSON")
    ins.add_argument("--session-id", default=None)
    ins.add_argument("--component-observation-id", default=None)
    ins.add_argument("--camera-id", default=None)
    ins.add_argument("--optical-profile", default=None)
    ins.add_argument("--validation-class", default="unspecified",
                     choices=("unspecified", "synthetic", "sample", "real_microscope"))
    ins.add_argument("--clock", default=None, help="fixed ingestion time (ISO UTC, ...Z) for replay")
    ins.add_argument("--out", required=True)
    root_args(ins)
    ins.set_defaults(func=cmd_inspect)

    rep = sub.add_parser("replay", help="re-run a stored inspection configuration")
    rep.add_argument("--inspection", required=True)
    rep.add_argument("--image", default=None, help="override image path (hash must still match)")
    rep.add_argument("--out", required=True)
    root_args(rep)
    rep.set_defaults(func=cmd_replay)

    val = sub.add_parser("validate", help="validate a JSON document against its schema")
    val.add_argument("--kind", required=True,
                     choices=("inspection", "calibration", "capture_manifest", "dataset", "review",
                              "batch"))
    val.add_argument("file")
    val.set_defaults(func=cmd_validate)

    rev = sub.add_parser("review", help="record an operator review (separate file)")
    rev.add_argument("--inspection", required=True)
    rev.add_argument("--operator", required=True)
    rev.add_argument("--decision", required=True,
                     choices=("confirmed", "rejected", "needs_recapture", "inconclusive"))
    rev.add_argument("--identification", default=None)
    rev.add_argument("--notes", default=None)
    rev.add_argument("--evidence", action="append", default=None,
                     help="operator evidence reference (repeatable)")
    rev.add_argument("--supersedes", default=None)
    rev.add_argument("--clock", default=None)
    rev.set_defaults(func=cmd_review)

    ev = sub.add_parser("eval", help="score a MIC-01..05 dataset split")
    ev.add_argument("--dataset")
    ev.add_argument("--split", default="dev", choices=("dev", "heldout"))
    ev.add_argument("--frozen-config", default=None, help="required for --split heldout")
    ev.add_argument("--print-fingerprint", action="store_true")
    ev.add_argument("--clock", default=None)
    ev.add_argument("--out", default="mic_eval_out")
    root_args(ev)
    ev.set_defaults(func=cmd_eval)

    rd = sub.add_parser("readers", help="list readers, code hashes and OCR availability")
    root_args(rd)
    rd.set_defaults(func=cmd_readers)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "eval" and not args.print_fingerprint and not args.dataset:
        print("--dataset is required", file=sys.stderr)
        return 2
    return args.func(args)
