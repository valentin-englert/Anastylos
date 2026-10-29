#!/usr/bin/env python3
"""AgentCam/CAD Wave-1 acceptance harness (INTEGRATION-QA owned).

Standard library only, no network. Replays every frozen G0 vector and
emits a SHA-bound scorecard (acceptance-run.json). Physical gates are
always PENDING_EVIDENCE without attached hardware — synthetic replay
never passes them.

Usage (from anywhere; paths resolve from the repo root):
    python tools/agentcam-acceptance/accept.py run --candidate <sha> \\
        --expect-contract <sha> --out <dir>
    python tools/agentcam-acceptance/accept.py verify-contract \\
        --expect-contract <sha>
    python tools/agentcam-acceptance/accept.py replay [--vectors a,b]

Exit code: 0 unless a gate FAILs (PENDING_EVIDENCE / BLOCKED / NOT_TESTED
are honest non-pass states, not failures).
"""

from __future__ import annotations

import argparse
import datetime
import json
import platform
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import checks  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_CONTRACT = ROOT / "capture-companion" / "control" / "contracts"
PROFILE = "guidance-hypothesis-1"

HARNESS_FILES = ("accept.py", "checks.py", "README.md", "EVIDENCE.md",
                 "tests/test_acceptance.py")

# Capability scores E1-E12 (EVALUATION-HARNESS.md). Synthetic cross-refs name
# harness gates whose synthetic sub-evidence exists; the capability itself
# still needs physical evidence.
CAPABILITIES = (
    ("E1", "Marker registration", ()),
    ("E2", "Pose accuracy", ()),
    ("E3", "Guidance correctness", ("vector-guidance-signs",)),
    ("E4", "Tracking performance", ()),
    ("E5", "Stability/quality capture", ()),
    ("E6", "Request->capture linkage",
     ("vector-control-valid", "vector-control-negative",
      "vector-control-transitions")),
    ("E7", "Receiver delivery", ()),
    ("E8", "Spatial provenance", ("vector-spatial-valid",)),
    ("E9", "CAD validity", ("vector-cad-valid", "vector-cad-invalid")),
    ("E10", "CAD dimensional error", ()),
    ("E11", "Active viewpoint loop", ()),
    ("E12", "Unfamiliar-device E2E", ()),
)

PHYSICAL_GATES = (
    ("H0", "Android runtime"), ("H1", "fiducials"),
    ("H2", "physical pose"), ("H3", "guidance signs"),
    ("H4", "tracking latency"), ("H5", "stable capture"),
    ("H6", "blur/exposure/visibility"), ("H7", "milestone A"),
    ("H8", "milestone B"), ("H9", "upload/restart"),
    ("H10", "registration recovery"), ("H11", "consent/isolation"),
    ("H12", "CAD-0"), ("H13", "CAD-1"),
    ("H14", "milestone C artifact"), ("H15", "independent dimensions"),
    ("H16", "milestone D"), ("H17", "Observer/Memory provenance"),
    ("H18", "crash/finalization"),
)

PENDING_REASON = ("no hardware attached and candidate has no lane "
                  "implementation; synthetic replay cannot pass this gate")


def run_command(argv: list[str], cwd: Path) -> dict:
    try:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True,
                              text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"cmd": argv, "cwd": str(cwd), "exit_code": None,
                "error": f"{type(exc).__name__}: {exc}"}
    return {"cmd": argv, "cwd": str(cwd), "exit_code": proc.returncode,
            "stdout_tail": proc.stdout[-2000:],
            "stderr_tail": proc.stderr[-2000:]}


def hash_memory_files(root: Path) -> dict[str, str]:
    """SHA-256 of every memory.jsonl under root (H17 rehash support)."""
    found: dict[str, str] = {}
    stack = [root]
    while stack:
        here = stack.pop()
        try:
            entries = list(here.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                stack.append(entry)
            elif entry.name == "memory.jsonl":
                try:
                    found[str(entry.relative_to(root))] = \
                        checks.sha256_file(entry)[1]
                except OSError:
                    found[str(entry.relative_to(root))] = "UNREADABLE"
    return found


def gate(gate_id: str, capability: str, verdict: str, numerator: int,
         denominator: int, detail: str,
         evidence_class: str = "synthetic") -> dict:
    return {"id": gate_id, "capability": capability, "verdict": verdict,
            "evidence_class": evidence_class, "numerator": numerator,
            "denominator": denominator, "detail": detail}


COMPUTED_GATES = ("run-integrity", "contract-manifest", "contract-sha",
                  "vector-control-valid", "vector-control-negative",
                  "vector-control-transitions", "vector-spatial-valid",
                  "vector-guidance-signs", "vector-cad-valid",
                  "vector-cad-invalid", "mcp-bundle", "regression-compliance",
                  "regression-selftests", "regression-memory")
ALL_GATE_IDS = (COMPUTED_GATES
                + tuple(cid for cid, _, _ in CAPABILITIES)
                + tuple(gid for gid, _ in PHYSICAL_GATES))


def load_contract(contract_dir: Path) -> dict:
    """Load manifest, schemas and vectors; verify manifest file hashes."""
    manifest = json.loads(
        (contract_dir / "CONTRACT.json").read_text(encoding="utf-8"))
    files, failures = [], []
    for entry in manifest["files"]:
        path = contract_dir / entry["path"]
        try:
            size, digest = checks.sha256_file(path)
        except OSError as exc:
            failures.append(f"{entry['path']}: unreadable ({exc})")
            files.append({"path": entry["path"], "error": str(exc)})
            continue
        match = size == entry["bytes"] and digest == entry["sha256"]
        if not match:
            failures.append(f"{entry['path']}: bytes/sha mismatch")
        files.append({"path": entry["path"], "bytes": size,
                      "sha256": digest, "manifest_match": match})
    loaded = {"manifest": manifest, "files": files,
              "manifest_failures": failures, "schemas": {}, "vectors": {}}
    for name in ("control.schema.json", "spatial-evidence.schema.json",
                 "cad-feature-model.schema.json", "mcp-tools.json",
                 "mcp-tools.expanded.json"):
        loaded["schemas"][name] = checks.load_json_strict(
            contract_dir / "schemas" / name)
    for name in ("control-request-valid.json", "control-negative.json",
                 "control-transitions.json", "spatial-valid.json",
                 "guidance-signs.json", "cad-feature-valid.json",
                 "cad-feature-invalid.json"):
        loaded["vectors"][name] = checks.load_json_strict(
            contract_dir / "vectors" / name)
    return loaded


def g_contract_manifest(loaded: dict) -> dict:
    total = len(loaded["files"])
    bad = len(loaded["manifest_failures"])
    return gate("contract-manifest", "contract file manifest",
                "PASS" if not bad else "FAIL", total - bad, total,
                "; ".join(loaded["manifest_failures"])
                or f"{total}/{total} files match bytes+sha256",
                "provenance")


def g_contract_sha(loaded: dict, expected: str) -> dict:
    recomputed = checks.recompute_contract_sha(loaded["manifest"]["files"])
    manifest_value = loaded["manifest"]["contract_sha256"]
    ok = recomputed == manifest_value == expected
    return gate("contract-sha", "contract SHA-256 recompute",
                "PASS" if ok else "FAIL", 1 if ok else 0, 1,
                f"recomputed={recomputed} manifest={manifest_value} "
                f"expected={expected}", "provenance")


def g_vector_control_valid(loaded: dict) -> dict:
    violations = checks.check_view_request(
        loaded["vectors"]["control-request-valid.json"],
        loaded["schemas"]["control.schema.json"])
    return gate("vector-control-valid", "ViewRequest valid vector",
                "PASS" if not violations else "FAIL",
                0 if violations else 1, 1,
                "; ".join(violations[:5]) or "0 violations")


def g_vector_control_negative(loaded: dict) -> dict:
    results = checks.check_control_negative(
        loaded["vectors"]["control-request-valid.json"],
        loaded["vectors"]["control-negative.json"],
        loaded["schemas"]["control.schema.json"])
    fails = [r for r in results if not r["ok"]]
    detail = "; ".join(f"{r['id']}={r['outcome']}" for r in results)
    return gate("vector-control-negative", "control negative vectors",
                "PASS" if not fails else "FAIL",
                len(results) - len(fails), len(results), detail)


def g_vector_control_transitions(loaded: dict) -> dict:
    res = checks.check_control_transitions(
        loaded["vectors"]["control-transitions.json"],
        loaded["schemas"]["control.schema.json"])
    per_ok = sum(1 for c in res["per_case"] if c["ok"])
    ok = res["n_cases"] == 17 and res["ids_unique"] and per_ok == 17
    bad = [c["id"] for c in res["per_case"] if not c["ok"]]
    return gate("vector-control-transitions", "control transition vectors",
                "PASS" if ok else "FAIL", per_ok, 17,
                f"cases={res['n_cases']} unique_ids={res['ids_unique']} "
                f"unmapped={bad or 'none'}; {res['execution']}")


def g_vector_spatial_valid(loaded: dict) -> dict:
    vec = loaded["vectors"]["spatial-valid.json"]
    violations = checks.check_spatial(
        vec, loaded["schemas"]["spatial-evidence.schema.json"])
    note = ("quality.overall=accepted is candidacy only "
            "(Core validator + qualified Receipt required)")
    return gate("vector-spatial-valid", "spatial evidence valid vector",
                "PASS" if not violations else "FAIL",
                0 if violations else 1, 1,
                ("; ".join(violations[:5]) + "; " + note) if violations
                else f"0 violations; {note}")


def g_vector_guidance(loaded: dict) -> dict:
    res = checks.guidance_recompute(loaded["vectors"]["guidance-signs.json"])
    per_ok = sum(1 for c in res["per_case"] if c["ok"])
    ok = per_ok == len(res["per_case"]) == 2 and not res["notes"]
    return gate("vector-guidance-signs", "guidance analytic recompute",
                "PASS" if ok else "FAIL", per_ok, 2,
                f"notes={res['notes'] or 'none'}; "
                f"{len(res['required_extensions'])} required extensions: "
                f"{res['extensions_status']}")


def g_vector_cad_valid(loaded: dict) -> dict:
    vec = loaded["vectors"]["cad-feature-valid.json"]
    violations = checks.check_cad(
        vec, loaded["schemas"]["cad-feature-model.schema.json"])
    fired = checks.cad_semantics(vec) if not violations else []
    ok = not violations and not fired
    return gate("vector-cad-valid", "CAD feature valid vector",
                "PASS" if ok else "FAIL", 1 if ok else 0, 1,
                "; ".join(violations[:5] + fired)
                or "0 violations; det=+1, parents resolve, no cycle")


def g_vector_cad_invalid(loaded: dict) -> dict:
    results = checks.check_cad_invalid(
        loaded["vectors"]["cad-feature-valid.json"],
        loaded["vectors"]["cad-feature-invalid.json"],
        loaded["schemas"]["cad-feature-model.schema.json"])
    fails = [r for r in results if not r["ok"]]
    detail = "; ".join(f"{r['id']}={r['outcome']}" for r in results)
    return gate("vector-cad-invalid", "CAD feature invalid vectors",
                "PASS" if not fails else "FAIL",
                len(results) - len(fails), len(results), detail)


def g_mcp_bundle(loaded: dict) -> dict:
    control_sha = checks.sha256_file(
        Path(loaded["_contract_dir"]) / "schemas"
        / "control.schema.json")[1]
    res = checks.check_mcp_bundle(
        loaded["schemas"]["mcp-tools.expanded.json"],
        loaded["schemas"]["mcp-tools.json"], control_sha)
    subs = [res["names_match"],
            res["schemas_present"] == 18,
            not res["dangling"], not res["external"],
            res["source_control_match"]]
    return gate("mcp-bundle", "MCP tool bundle",
                "PASS" if res["ok"] else "FAIL", sum(subs), len(subs),
                f"tools={res['n_tools_expanded']} "
                f"schemas={res['schemas_present']}/18 refs={res['n_refs']} "
                f"dangling={res['dangling'] or 'none'} "
                f"external={res['external'] or 'none'} "
                f"control_src_match={res['source_control_match']} "
                f"spatial_src={res['source_spatial_noted']} "
                f"(embedded sidecar, informational)")


def g_regression_compliance(python: str) -> tuple[dict, dict]:
    rec = run_command([python, "-B", "tools/compliance/check.py"], ROOT)
    errors = None
    if rec["exit_code"] == 0:
        match = re.search(r"(\d+) error\(s\)", rec["stdout_tail"])
        errors = int(match.group(1)) if match else None
    ok = rec["exit_code"] == 0 and errors == 0
    return gate("regression-compliance", "compliance checker",
                "PASS" if ok else "FAIL",
                0 if errors else 1, 1,
                f"exit={rec['exit_code']} errors={errors}", "provenance"), rec


def g_regression_selftests(python: str) -> tuple[dict, dict]:
    rec = run_command([python, "-B", "-m", "unittest", "discover",
                       "-s", "tests"], HERE)
    combined = rec["stdout_tail"] + rec["stderr_tail"]
    ran = re.search(r"Ran (\d+) tests?", combined)
    total = int(ran.group(1)) if ran else 0
    failed = re.search(r"FAILED \(([^)]*)\)", combined)
    ok = rec["exit_code"] == 0 and "OK" in combined and not failed
    return gate("regression-selftests", "harness self-test suite",
                "PASS" if ok else "FAIL", total if ok else 0, total,
                f"exit={rec['exit_code']} "
                f"result={'OK' if ok else failed.group(0) if failed else 'no-result'}",
                "provenance"), rec


def g_run_integrity(candidate: str, head: str | None) -> dict:
    if head is None:
        return gate("run-integrity", "candidate SHA binding", "BLOCKED",
                    0, 1, "git rev-parse HEAD failed", "provenance")
    ok = head.lower() == candidate.lower()
    return gate("run-integrity", "candidate SHA binding",
                "PASS" if ok else "FAIL", 1 if ok else 0, 1,
                f"candidate={candidate} head={head}"
                + ("" if ok else " STALE CANDIDATE SHA"), "provenance")


def g_regression_memory(before: dict, after: dict) -> dict:
    ok = before == after
    keys = sorted(set(before) | set(after))
    changed = [k for k in keys if before.get(k) != after.get(k)]
    return gate("regression-memory", "assembly memory untouched",
                "PASS" if ok else "FAIL", len(keys) - len(changed),
                len(keys),
                f"{len(keys)} memory.jsonl file(s) rehashed; "
                f"changed={changed or 'none'}"
                + ("" if keys else " (no memory logs in worktree)"),
                "provenance")


def eh_gates() -> list[dict]:
    out = []
    for cid, label, xrefs in CAPABILITIES:
        detail = PENDING_REASON
        if xrefs:
            detail += f"; synthetic sub-evidence: {', '.join(xrefs)}"
        out.append(gate(cid, label, "PENDING_EVIDENCE", 0, 0, detail,
                        "physical"))
    for gid, label in PHYSICAL_GATES:
        out.append(gate(gid, label, "PENDING_EVIDENCE", 0, 0,
                        PENDING_REASON, "physical"))
    return out


def cmd_run(args: argparse.Namespace) -> int:
    contract_dir = Path(args.contract)
    selected = set(ALL_GATE_IDS)
    if args.gates:
        selected = set(args.gates.split(","))
    selected -= set(args.skip_gates.split(",")) if args.skip_gates else set()
    unknown = selected - set(ALL_GATE_IDS)
    if unknown:
        print(f"unknown gate ids: {sorted(unknown)}", file=sys.stderr)
        return 2

    commands: list[dict] = []
    python_rec = run_command([args.python, "--version"], ROOT)
    commands.append(python_rec)

    memory_before = (hash_memory_files(ROOT)
                     if "regression-memory" in selected else {})
    try:
        loaded = load_contract(contract_dir)
        loaded["_contract_dir"] = str(contract_dir)
        load_error = None
    except (OSError, ValueError, KeyError) as exc:
        loaded, load_error = None, f"{type(exc).__name__}: {exc}"

    expected = args.expect_contract or (
        loaded["manifest"]["contract_sha256"] if loaded else "unknown")
    expected_source = "dispatch-flag" if args.expect_contract \
        else "manifest-self"

    git_rec = run_command(["git", "rev-parse", "HEAD"], ROOT)
    commands.append(git_rec)
    head = git_rec["stdout_tail"].strip() \
        if git_rec["exit_code"] == 0 else None

    builders = {
        "run-integrity": lambda: g_run_integrity(args.candidate, head),
        "contract-manifest": lambda: g_contract_manifest(loaded),
        "contract-sha": lambda: g_contract_sha(loaded, expected),
        "vector-control-valid": lambda: g_vector_control_valid(loaded),
        "vector-control-negative":
            lambda: g_vector_control_negative(loaded),
        "vector-control-transitions":
            lambda: g_vector_control_transitions(loaded),
        "vector-spatial-valid": lambda: g_vector_spatial_valid(loaded),
        "vector-guidance-signs": lambda: g_vector_guidance(loaded),
        "vector-cad-valid": lambda: g_vector_cad_valid(loaded),
        "vector-cad-invalid": lambda: g_vector_cad_invalid(loaded),
        "mcp-bundle": lambda: g_mcp_bundle(loaded),
    }
    needs_contract = set(builders) - {"run-integrity"}

    gates: list[dict] = []
    for gid in COMPUTED_GATES:
        if gid in ("regression-compliance", "regression-selftests",
                   "regression-memory"):
            continue
        if gid not in selected:
            gates.append(gate(gid, gid, "NOT_TESTED", 0, 0,
                              "excluded by gate filter", "provenance"))
        elif loaded is None and gid in needs_contract:
            gates.append(gate(gid, gid, "BLOCKED", 0, 0,
                              f"contract load failed: {load_error}",
                              "provenance"))
        else:
            gates.append(builders[gid]())

    if "regression-compliance" in selected:
        g, rec = g_regression_compliance(args.python)
        gates.append(g)
        commands.append(rec)
    else:
        gates.append(gate("regression-compliance", "regression-compliance",
                          "NOT_TESTED", 0, 0, "excluded by gate filter",
                          "provenance"))
    if "regression-selftests" in selected:
        g, rec = g_regression_selftests(args.python)
        gates.append(g)
        commands.append(rec)
    else:
        gates.append(gate("regression-selftests", "regression-selftests",
                          "NOT_TESTED", 0, 0, "excluded by gate filter",
                          "provenance"))

    memory_after = (hash_memory_files(ROOT)
                    if "regression-memory" in selected else {})
    if "regression-memory" in selected:
        gates.append(g_regression_memory(memory_before, memory_after))
    else:
        gates.append(gate("regression-memory", "regression-memory",
                          "NOT_TESTED", 0, 0, "excluded by gate filter",
                          "provenance"))
    gates.extend(g for g in eh_gates() if g["id"] in selected)

    evaluator_files = {}
    for rel in HARNESS_FILES:
        path = HERE / rel
        if path.is_file():
            evaluator_files[rel] = checks.sha256_file(path)[1]
    evidence = ([{"path": f"capture-companion/control/contracts/"
                           f"{f['path']}",
                   "bytes": f.get("bytes"), "sha256": f.get("sha256")}
                  for f in loaded["files"]] if loaded else [])
    scorecard = {
        "scorecard": "anastylos-agentcam-acceptance-run@1",
        "candidate_commit": args.candidate,
        "git_head": head,
        "contract_sha256": checks.recompute_contract_sha(
            loaded["manifest"]["files"]) if loaded else None,
        "contract_manifest_value": loaded["manifest"]["contract_sha256"]
        if loaded else None,
        "contract_expected": expected,
        "contract_expected_source": expected_source,
        "contract_match": (checks.recompute_contract_sha(
            loaded["manifest"]["files"]) == expected
            if loaded else False),
        "profile_id": PROFILE,
        "evaluator": {"harness": "tools/agentcam-acceptance",
                      "files": evaluator_files},
        "hardware": {"status": "none-attached",
                     "detail": "no phone, print metrology, caliper or LAN "
                     "timing rig; all physical gates PENDING_EVIDENCE"},
        "tool_versions": {
            "python": python_rec["stdout_tail"].strip()
            + python_rec["stderr_tail"].strip(),
            "python_executable": args.python,
            "platform": platform.platform()},
        "commands": commands,
        "evidence": evidence,
        "memory_before": memory_before,
        "memory_after": memory_after,
        "gates": gates,
        "verdict_summary": _summarize(gates),
        "created_at": datetime.datetime.now(
            datetime.timezone.utc).isoformat(),
    }
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "acceptance-run.json"
    out_path.write_text(json.dumps(scorecard, indent=2) + "\n",
                        encoding="utf-8")
    for g in gates:
        print(f"{g['verdict']:16} {g['id']:28} "
              f"{g['numerator']}/{g['denominator']} [{g['evidence_class']}]")
    print(f"scorecard: {out_path}")
    print(f"summary: {scorecard['verdict_summary']}")
    return 1 if any(g["verdict"] == "FAIL" for g in gates) else 0


def _summarize(gates: list[dict]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for g in gates:
        summary[g["verdict"]] = summary.get(g["verdict"], 0) + 1
    return summary


def cmd_verify_contract(args: argparse.Namespace) -> int:
    contract_dir = Path(args.contract)
    try:
        loaded = load_contract(contract_dir)
    except (OSError, ValueError, KeyError) as exc:
        print(f"contract load failed: {exc}", file=sys.stderr)
        return 1
    expected = args.expect_contract or loaded["manifest"]["contract_sha256"]
    recomputed = checks.recompute_contract_sha(loaded["manifest"]["files"])
    bad = len(loaded["manifest_failures"])
    total = len(loaded["files"])
    print(f"manifest files: {total - bad}/{total} match")
    for failure in loaded["manifest_failures"]:
        print(f"  MISMATCH {failure}")
    print(f"recomputed: {recomputed}")
    print(f"manifest:   {loaded['manifest']['contract_sha256']}")
    print(f"expected:   {expected}")
    ok = not bad and recomputed == expected
    print("contract OK" if ok else "CONTRACT MISMATCH")
    return 0 if ok else 1


REPLAY_VECTORS = ("control-valid", "control-negative", "control-transitions",
                  "spatial-valid", "guidance-signs", "cad-valid",
                  "cad-invalid", "mcp-bundle")


def cmd_replay(args: argparse.Namespace) -> int:
    contract_dir = Path(args.contract)
    want = args.vectors.split(",") if args.vectors else list(REPLAY_VECTORS)
    unknown = set(want) - set(REPLAY_VECTORS)
    if unknown:
        print(f"unknown vectors: {sorted(unknown)}", file=sys.stderr)
        return 2
    try:
        loaded = load_contract(contract_dir)
        loaded["_contract_dir"] = str(contract_dir)
    except (OSError, ValueError, KeyError) as exc:
        print(f"contract load failed: {exc}", file=sys.stderr)
        return 1
    ctl = loaded["schemas"]["control.schema.json"]
    out: dict = {}
    if "control-valid" in want:
        out["control-valid"] = checks.check_view_request(
            loaded["vectors"]["control-request-valid.json"], ctl)
    if "control-negative" in want:
        out["control-negative"] = checks.check_control_negative(
            loaded["vectors"]["control-request-valid.json"],
            loaded["vectors"]["control-negative.json"], ctl)
    if "control-transitions" in want:
        out["control-transitions"] = checks.check_control_transitions(
            loaded["vectors"]["control-transitions.json"], ctl)
    if "spatial-valid" in want:
        out["spatial-valid"] = checks.check_spatial(
            loaded["vectors"]["spatial-valid.json"],
            loaded["schemas"]["spatial-evidence.schema.json"])
    if "guidance-signs" in want:
        out["guidance-signs"] = checks.guidance_recompute(
            loaded["vectors"]["guidance-signs.json"])
    if "cad-valid" in want:
        vec = loaded["vectors"]["cad-feature-valid.json"]
        out["cad-valid"] = {
            "violations": checks.check_cad(
                vec, loaded["schemas"]["cad-feature-model.schema.json"]),
            "semantics": checks.cad_semantics(vec)}
    if "cad-invalid" in want:
        out["cad-invalid"] = checks.check_cad_invalid(
            loaded["vectors"]["cad-feature-valid.json"],
            loaded["vectors"]["cad-feature-invalid.json"],
            loaded["schemas"]["cad-feature-model.schema.json"])
    if "mcp-bundle" in want:
        control_sha = checks.sha256_file(
            contract_dir / "schemas" / "control.schema.json")[1]
        out["mcp-bundle"] = checks.check_mcp_bundle(
            loaded["schemas"]["mcp-tools.expanded.json"],
            loaded["schemas"]["mcp-tools.json"], control_sha)
    print(json.dumps(out, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ANASTYLOS Guided Capture / CAD Reconstruction "
                        "Wave-1 acceptance harness")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run all gates, emit scorecard")
    run.add_argument("--candidate", required=True,
                     help="candidate commit SHA under evaluation")
    run.add_argument("--contract", default=str(DEFAULT_CONTRACT),
                     help="frozen contracts directory (read-only)")
    run.add_argument("--expect-contract", default=None,
                     help="expected contract SHA-256 (dispatch value)")
    run.add_argument("--out", required=True,
                     help="output directory for acceptance-run.json")
    run.add_argument("--python", default=sys.executable,
                     help="python for subprocess gates")
    run.add_argument("--gates", default=None,
                     help="comma-separated gate ids to run (rest NOT_TESTED)")
    run.add_argument("--skip-gates", default=None,
                     help="comma-separated gate ids to skip")
    run.set_defaults(func=cmd_run)
    verify = sub.add_parser("verify-contract",
                            help="verify manifest + recompute contract SHA")
    verify.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    verify.add_argument("--expect-contract", default=None)
    verify.set_defaults(func=cmd_verify_contract)
    replay = sub.add_parser("replay", help="replay vectors to stdout")
    replay.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    replay.add_argument("--vectors", default=None,
                        help=f"subset of {','.join(REPLAY_VECTORS)}")
    replay.set_defaults(func=cmd_replay)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

