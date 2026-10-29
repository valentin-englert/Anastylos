"""Trusted CAD worker CLI: fixed args, per-job workdir, bounded inputs.

Subcommands:
  validate --spec <model.json>              stdlib-only; no kernel import.
  build    --spec <model.json> --out <dir>  one revision, native checks only.
  reopen   --step <model.step>               fresh-process STEP import report.
  run      --spec <model.json> --out <dir>   build + fresh reopen + evidence.

Exit codes: 0 ok, 2 pre-kernel validation reject, 3 kernel/round-trip
failure, 4 usage or I/O error. The worker never overwrites: --out must
be absent or an empty directory. Specs over 1 MiB and STEP files over
256 MiB are refused. Reopen runs as a fixed-argument subprocess of the
same interpreter (no caller-supplied executable, no shell).
"""

import argparse
import datetime
import json
import os
import subprocess
import sys

from anastylos_cad import compile as compiler
from anastylos_cad import evidence as ev
from anastylos_cad import preview as pv
from anastylos_cad import validate as val

SPEC_MAX_BYTES = 1024 * 1024
STEP_MAX_BYTES = 256 * 1024 * 1024
REOPEN_TIMEOUT_S = 120
STEP_TOL_MM = 0.01
VOL_REL_TOL = 1e-5
TESSELLATION_TOL = 0.5
SEED = 20261029


def _read_spec(path):
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, "cannot stat spec: %s" % exc
    if size > SPEC_MAX_BYTES:
        return None, "spec exceeds %d bytes" % SPEC_MAX_BYTES
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle), None
    except (OSError, ValueError) as exc:
        return None, "cannot parse spec JSON: %s" % exc


def _prepare_outdir(path):
    if os.path.exists(path):
        if not os.path.isdir(path):
            return "--out exists and is not a directory"
        if os.listdir(path):
            return "--out is not empty; refusing to overwrite a revision"
        return None
    try:
        os.makedirs(path)
    except OSError as exc:
        return "cannot create --out: %s" % exc
    return None


def _unresolved(model):
    found = []
    for feat in model["features"]:
        if feat["uncertainty_mm"] is None:
            found.append({"feature_id": feat["feature_id"],
                          "reason": "uncertainty unquantified"})
    return found


def cmd_validate(spec_path):
    model, err = _read_spec(spec_path)
    if err:
        print(json.dumps({"ok": False, "error": err}))
        return 4
    result = val.validate_model(model)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 2


def _kernel_build(model):
    from anastylos_cad import builder
    return builder.build_solids(model["features"])


def _build_revision(model, outdir):
    """Kernel build + STEP/SVG/script bundle. Model must be validated."""
    from anastylos_cad import builder
    problem = _prepare_outdir(outdir)
    if problem:
        return {"ok": False, "error": problem}, 4
    try:
        solids = _kernel_build(model)
    except (ValueError, RuntimeError) as exc:
        return {"ok": False, "error": "kernel build: %s" % exc}, 3
    if len(solids) != 1:
        return {"ok": False, "error":
                "v1 worker builds one solid, got %d" % len(solids)}, 3
    fid, solid = next(iter(solids.items()))
    report = builder.solid_report(solid)
    if not report["is_valid"] or not report["volume"] > 0:
        return {"ok": False, "error":
                "solid failed validity/positive-volume gate"}, 3
    step_path = os.path.join(outdir, "model.step")
    builder.export_step(solid, step_path)
    step_sha = ev.sha256_file(step_path)
    verts, tris = builder.tessellate(solid, TESSELLATION_TOL)
    svg = pv.render_wireframe_svg(pv.project_xy(verts), tris, step_sha)
    with open(os.path.join(outdir, "preview.svg"), "w",
              encoding="utf-8") as handle:
        handle.write(svg)
    with open(os.path.join(outdir, "feature-model.json"), "w",
              encoding="utf-8") as handle:
        handle.write(ev.canonical_json(compiler.canonical_params(model)))
    with open(os.path.join(outdir, "model.py"), "w",
              encoding="utf-8") as handle:
        handle.write(compiler.generate_model_py(model))
    return {"ok": True, "solid_feature": fid, "report": report,
            "step_sha256": step_sha,
            "step_bytes": os.path.getsize(step_path),
            "versions": builder.versions()}, 0


def cmd_build(spec_path, outdir):
    model, err = _read_spec(spec_path)
    if err:
        print(json.dumps({"ok": False, "error": err}))
        return 4
    checked = val.validate_model(model)
    if not checked["ok"]:
        print(json.dumps(checked, indent=2))
        return 2
    result, rc = _build_revision(model, outdir)
    print(json.dumps(result, indent=2))
    return rc


def cmd_reopen(step_path):
    from anastylos_cad import builder
    try:
        size = os.path.getsize(step_path)
    except OSError as exc:
        print(json.dumps({"ok": False, "error": "cannot stat STEP: %s" % exc}))
        return 4
    if size > STEP_MAX_BYTES:
        print(json.dumps({"ok": False, "error": "STEP exceeds size bound"}))
        return 4
    try:
        solids = builder.import_step_solids(step_path)
    except Exception as exc:  # kernel import failure is a gate failure, not a crash
        print(json.dumps({"ok": False, "error": "STEP import: %s" % exc}))
        return 3
    print(json.dumps({
        "ok": True, "solids": len(solids),
        "reports": [builder.solid_report(s) for s in solids],
        "step_sha256": ev.sha256_file(step_path),
        "versions": builder.versions(),
    }, indent=2))
    return 0


def _fresh_reopen(step_path):
    cmd = [sys.executable, "-m", "anastylos_cad.worker", "reopen",
           "--step", step_path]
    env = dict(os.environ)
    cad_root = os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))
    env["PYTHONPATH"] = cad_root + os.pathsep + env.get("PYTHONPATH", "")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=REOPEN_TIMEOUT_S, env=env)
    except subprocess.TimeoutExpired:
        return None, "reopen subprocess timed out"
    if proc.returncode != 0:
        return None, "reopen failed rc=%d: %s" % (
            proc.returncode, (proc.stdout + proc.stderr)[-2000:])
    try:
        return json.loads(proc.stdout), None
    except ValueError:
        return None, "reopen emitted non-JSON"


def _compare(native, reimported):
    dims_ok = all(abs(a - b) <= STEP_TOL_MM for a, b in
                  zip(native["bbox_size"], reimported["bbox_size"]))
    vol_ok = (abs(native["volume"] - reimported["volume"])
              / native["volume"] <= VOL_REL_TOL)
    return {"solids_equal_1": True, "native_valid": native["is_valid"],
            "reimport_valid": reimported["is_valid"],
            "dims_within_0v01mm": dims_ok,
            "volume_rel_err_le_1e-5": vol_ok,
            "volume_rel_err": abs(native["volume"] - reimported["volume"])
            / native["volume"]}


def cmd_run(spec_path, outdir):
    model, err = _read_spec(spec_path)
    if err:
        print(json.dumps({"ok": False, "error": err}))
        return 4
    checked = val.validate_model(model)
    if not checked["ok"]:
        print(json.dumps(checked, indent=2))
        return 2
    built, rc = _build_revision(model, outdir)
    if rc != 0:
        print(json.dumps(built, indent=2))
        return rc
    step_path = os.path.join(outdir, "model.step")
    reopened, err = _fresh_reopen(step_path)
    if err:
        print(json.dumps({"ok": False, "error": err}))
        return 3
    if reopened["solids"] != 1:
        print(json.dumps({"ok": False, "error":
                          "reimport found %d solids, want 1"
                          % reopened["solids"]}))
        return 3
    native_report = built["report"]
    compare = _compare(native_report, reopened["reports"][0])
    if not all([compare["native_valid"], compare["reimport_valid"],
                compare["dims_within_0v01mm"],
                compare["volume_rel_err_le_1e-5"]]):
        print(json.dumps({"ok": False, "error":
                          "round-trip gate failed", "compare": compare}))
        return 3
    unresolved = _unresolved(model)
    validation = ev.build_validation(
        model, native_report, reopened["reports"][0],
        reopened["versions"], compare, unresolved)
    with open(os.path.join(outdir, "feature-evidence.json"), "w",
              encoding="utf-8") as handle:
        handle.write(ev.canonical_json(ev.build_feature_evidence(model)))
    with open(os.path.join(outdir, "validation.json"), "w",
              encoding="utf-8") as handle:
        handle.write(ev.canonical_json(validation))
    entries = {}
    for name in ev.BUNDLE_FILES:
        path = os.path.join(outdir, name)
        entries[name] = ev.sha256_file(path)
        entries[name + ":bytes"] = os.path.getsize(path)
    manifest = ev.build_manifest(
        entries, {"feature_model_sha256": ev.sha256_file(spec_path)},
        compiler.GENERATOR, SEED,
        datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"))
    with open(os.path.join(outdir, "manifest.json"), "w",
              encoding="utf-8") as handle:
        handle.write(ev.canonical_json(manifest))
    print(json.dumps({"ok": True, "compare": compare,
                      "unresolved": unresolved,
                      "manifest_sha256": ev.sha256_file(
                          os.path.join(outdir, "manifest.json"))}, indent=2))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="cadwork",
                                     description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate")
    v.add_argument("--spec", required=True)
    b = sub.add_parser("build")
    b.add_argument("--spec", required=True)
    b.add_argument("--out", required=True)
    r = sub.add_parser("reopen")
    r.add_argument("--step", required=True)
    g = sub.add_parser("run")
    g.add_argument("--spec", required=True)
    g.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if args.cmd == "validate":
        return cmd_validate(args.spec)
    if args.cmd == "build":
        return cmd_build(args.spec, args.out)
    if args.cmd == "reopen":
        return cmd_reopen(args.step)
    return cmd_run(args.spec, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
