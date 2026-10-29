"""CAD-0 replay evaluator: frozen vectors + owned negative vectors in, hashed verdicts out.

Replays, without modification, the frozen contract vectors
(``spatial-valid.json``, ``guidance-signs.json``), the spatial-owned
guidance extensions, every negative vector under ``vectors/``, and the
synthetic calibration profile. A run is green when every vector meets
its expectation and the contract manifest re-verifies. Standard library
only; evidence_class=synthetic throughout.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from . import VALIDATOR_VERSION
from .geometry import (
    cues_from_delta,
    det3,
    guidance_delta,
    max_abs_diff,
    project_board_point,
    tcb_from_tbc,
)
from .profiles import validate_profile
from .validator import NonFiniteError, load_json_strict, validate_sidecar, _finite_list

REPLAY_SCHEMA_ID = "anastylos-spatial-replay@1"
DELTA_TOL_MM = 1e-9


def verify_contract(contracts_dir: str | Path) -> tuple[str, list[str]]:
    """Re-hash every manifest file. Returns (contract_sha256, mismatches)."""
    base = Path(contracts_dir)
    manifest = json.loads((base / "CONTRACT.json").read_text(encoding="utf-8"))
    mismatches = []
    for entry in manifest["files"]:
        raw = (base / entry["path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry["sha256"] or len(raw) != entry["bytes"]:
            mismatches.append(entry["path"])
    return manifest["contract_sha256"], mismatches


def _apply_mutation(doc: dict, mutation: dict) -> None:
    target = doc
    path = mutation["path"]
    for segment in path[:-1]:
        target = target[segment]
    if mutation["op"] == "delete":
        del target[path[-1]]
    elif mutation["op"] == "set":
        target[path[-1]] = mutation["value"]
    else:
        raise ValueError(f"unknown mutation op: {mutation['op']}")


def replay_negative_vector(path: str | Path, base_doc: dict, base_sha: str) -> dict:
    """Apply one owned negative vector's mutations to the frozen valid sidecar
    and compare the validator verdict against the expectation."""
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    mutated = copy.deepcopy(base_doc)
    for mutation in spec["mutations"]:
        _apply_mutation(mutated, mutation)
    ctx = spec.get("ctx") or {}
    try:
        result = validate_sidecar(mutated, ctx)
    except (NonFiniteError, ValueError, TypeError, KeyError) as exc:
        result = {"verdict": "reject", "reasons": [f"VALIDATOR_EXCEPTION:{type(exc).__name__}"],
                  "reviews": [], "notes": [], "metric_ready": False}
    expected = spec["expect_verdict"]
    reasons_any = spec.get("expect_reasons_any") or []
    matched = result["verdict"] == expected and all(
        any(code == want or code.startswith(want.rstrip("*")) for code in result["reasons"] + result["reviews"])
        for want in reasons_any)
    if "expect_metric_ready" in spec:
        matched = matched and result["metric_ready"] == spec["expect_metric_ready"]
    return {"name": spec["name"], "vector_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            "base_sha256": base_sha, "verdict": result["verdict"], "metric_ready": result["metric_ready"],
            "reasons": result["reasons"], "reviews": result["reviews"],
            "expected_verdict": expected, "expected_reasons_any": reasons_any, "matched": matched}


def replay_guidance(signs_doc: dict) -> dict:
    """Recompute every analytic guidance case; cues must match exactly."""
    current = [float(v) for v in signs_doc["current_T_B_C"]]
    cases = []
    for index, case in enumerate(signs_doc["cases"]):
        desired = tuple(float(v) for v in case["desired_center_B_mm"])
        delta = guidance_delta(current, desired)
        expected_delta = [float(v) for v in case["expected_camera_delta_mm"]]
        cues = cues_from_delta(delta)
        cases.append({"index": index,
                      "delta_mm": list(delta),
                      "delta_matched": max_abs_diff(list(delta), expected_delta) <= DELTA_TOL_MM,
                      "cues": cues, "expected_cues": case["expected_cues"],
                      "cues_matched": cues == case["expected_cues"]})
    matched = sum(1 for c in cases if c["delta_matched"] and c["cues_matched"])
    return {"cases": cases, "matched": matched, "total": len(cases)}


def replay_spatial_extensions(doc: dict) -> dict:
    """Replay the spatial-owned guidance extensions (rotations, yaw,
    identity, refusals). Each case carries its expectation."""
    cases = []
    for case in doc["cases"]:
        name = case["name"]
        if case.get("expect_refusal"):
            if case["kind"] == "mirror_transform":
                matrix = case["reference_to_image"]
                refused = _finite_list(matrix, 9) and det3([float(v) for v in matrix]) < 0
                cases.append({"name": name, "kind": case["kind"], "refused": refused,
                              "expected_refusal": case["expect_refusal"],
                              "matched": refused and case["expect_refusal"] == "MIRRORED_IMAGE_TRANSFORM"})
            elif case["kind"] == "negative_depth":
                t_b_c = [float(v) for v in case["current_T_B_C"]]
                point = tuple(float(v) for v in case["board_point_B_mm"])
                pixel = project_board_point(point, tcb_from_tbc(t_b_c),
                                            [float(v) for v in case["K"]],
                                            [float(v) for v in case["distortion"]])
                cases.append({"name": name, "kind": case["kind"], "projection": pixel,
                              "expected_refusal": case["expect_refusal"],
                              "matched": pixel is None and case["expect_refusal"] == "NEGATIVE_DEPTH"})
            else:
                cases.append({"name": name, "kind": case["kind"], "matched": False,
                              "error": "UNKNOWN_REFUSAL_KIND"})
            continue
        current = [float(v) for v in case["current_T_B_C"]]
        desired = tuple(float(v) for v in case["desired_center_B_mm"])
        delta = guidance_delta(current, desired)
        rotation = case.get("display_rotation_deg", 0)
        cues = cues_from_delta(delta, rotation)
        cases.append({"name": name, "kind": case.get("kind", "cue"),
                      "delta_mm": list(delta), "display_rotation_deg": rotation,
                      "cues": cues, "expected_cues": case["expected_cues"],
                      "matched": cues == case["expected_cues"]})
    matched = sum(1 for c in cases if c["matched"])
    return {"cases": cases, "matched": matched, "total": len(cases)}


def replay(contracts_dir: str | Path, spatial_dir: str | Path, candidate_commit: str) -> dict:
    """Run the full Wave-1 replay. Returns the report dict (schema
    ``anastylos-spatial-replay@1``)."""
    contracts, spatial = Path(contracts_dir), Path(spatial_dir)
    contract_sha, mismatches = verify_contract(contracts)

    sidecar_doc, sidecar_sha = load_json_strict(contracts / "vectors" / "spatial-valid.json")
    sidecar_result = validate_sidecar(sidecar_doc, {})
    sidecar_matched = sidecar_result["verdict"] == "accept" and sidecar_result["metric_ready"]

    signs_doc, signs_sha = load_json_strict(contracts / "vectors" / "guidance-signs.json")
    guidance = replay_guidance(signs_doc)

    extensions_doc, extensions_sha = load_json_strict(spatial / "guidance-signs.spatial-v1.json")
    extensions = replay_spatial_extensions(extensions_doc)

    negatives = []
    for path in sorted((spatial / "vectors").glob("*.json")):
        negatives.append(replay_negative_vector(path, sidecar_doc, sidecar_sha))
    negatives_matched = sum(1 for n in negatives if n["matched"])

    profile_doc, profile_sha = load_json_strict(spatial / "calibration-profile.synthetic-v1.json")
    profile_result = validate_profile(profile_doc)
    profile_matched = profile_result["verdict"] == "accept"

    green = (not mismatches and sidecar_matched
             and guidance["matched"] == guidance["total"] and guidance["total"] > 0
             and extensions["matched"] == extensions["total"] and extensions["total"] > 0
             and negatives_matched == len(negatives) and len(negatives) > 0
             and profile_matched)
    return {
        "schema": REPLAY_SCHEMA_ID,
        "evidence_class": "synthetic",
        "validator_version": VALIDATOR_VERSION,
        "candidate_commit": candidate_commit,
        "contract_sha256": contract_sha,
        "contract_files_ok": not mismatches,
        "contract_mismatches": mismatches,
        "inputs": {
            "spatial_valid_sha256": sidecar_sha,
            "guidance_signs_sha256": signs_sha,
            "spatial_extensions_sha256": extensions_sha,
            "synthetic_profile_sha256": profile_sha,
        },
        "sidecar_valid": {"verdict": sidecar_result["verdict"],
                          "metric_ready": sidecar_result["metric_ready"],
                          "reasons": sidecar_result["reasons"],
                          "reviews": sidecar_result["reviews"],
                          "notes": sidecar_result["notes"],
                          "matched": sidecar_matched},
        "guidance_frozen": guidance,
        "guidance_extensions": extensions,
        "negatives": {"vectors": negatives, "matched": negatives_matched, "total": len(negatives)},
        "synthetic_profile": {"verdict": profile_result["verdict"],
                              "reasons": profile_result["reasons"],
                              "reviews": profile_result["reviews"],
                              "matched": profile_matched},
        "green": green,
    }


def report_bytes(report: dict) -> bytes:
    return (json.dumps(report, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def report_sha256(report: dict) -> str:
    return hashlib.sha256(report_bytes(report)).hexdigest()


__all__ = ["replay", "replay_guidance", "replay_negative_vector",
           "replay_spatial_extensions", "report_bytes", "report_sha256", "verify_contract"]
