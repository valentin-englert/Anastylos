"""Evidence, validation and manifest writers for one CAD revision.

Standard library only. Every artifact a revision produces is hashed into
manifest.json; feature-evidence.json maps each feature id to its basis,
uncertainty, support references, residuals and assumptions; validation.json
records the solid/round-trip gates and the unresolved-feature list.
"""

import hashlib
import json

BUNDLE_FILES = ("feature-model.json", "model.py", "model.step",
                "preview.svg", "feature-evidence.json", "validation.json")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, indent=2,
                      allow_nan=False) + "\n"


def build_feature_evidence(model, residuals=None):
    """feature-evidence.json content: per-feature provenance summary."""
    residuals = residuals or {}
    entries = {}
    for feat in model["features"]:
        fid = feat["feature_id"]
        entries[fid] = {
            "kind": feat["kind"],
            "basis": feat["basis"],
            "uncertainty_mm": feat["uncertainty_mm"],
            "evidence_ids": list(feat["evidence_ids"]),
            "assumptions": list(feat["assumptions"]),
            "residuals": residuals.get(fid),
        }
    return {"schema": "cad-feature-evidence-v1",
            "model_id": model["model_id"],
            "revision": model["revision"],
            "dataset_sha256": model["dataset_sha256"],
            "features": entries}


def build_validation(model, native_report, reimport_report, tool_versions,
                     compare, unresolved_features, evidence_class="synthetic"):
    """validation.json content: gates, comparisons, open unknowns."""
    return {
        "schema": "cad-validation-v1",
        "model_id": model["model_id"],
        "revision": model["revision"],
        "evidence_class": evidence_class,
        "native": native_report,
        "reimport": reimport_report,
        "roundtrip_compare": compare,
        "unresolved_features": list(unresolved_features),
        "tool_versions": dict(tool_versions),
    }


def build_manifest(entries, inputs, generator, seed, created_utc):
    """manifest.json content: hashes over the revision bundle + inputs."""
    return {
        "schema": "cad-manifest-v1",
        "generator": generator,
        "deterministic_seed": seed,
        "created_utc": created_utc,
        "inputs": dict(inputs),
        "artifacts": [
            {"path": name, "sha256": entries[name],
             "bytes": entries[name + ":bytes"]}
            for name in BUNDLE_FILES
        ],
    }


def missing_feature_envelope(model, validation):
    """CAD->Core handoff: underconstrained features needing another view.

    Data only (file-level handoff via `cad_context` on ViewRequest); the
    deterministic viewpoint planner is a later wave. A feature lands here
    when its uncertainty is unknown (null) or when validation left an
    unresolved entry naming it.
    """
    unresolved = {u.get("feature_id") for u in
                  validation.get("unresolved_features", [])}
    want = []
    for feat in model["features"]:
        fid = feat["feature_id"]
        reason = None
        if fid in unresolved:
            reason = "unresolved"
        elif feat["uncertainty_mm"] is None:
            reason = "uncertainty unknown"
        if reason is not None:
            want.append({"feature_id": fid, "kind": feat["kind"],
                         "basis": feat["basis"], "reason": reason,
                         "uncertainty_mm": feat["uncertainty_mm"]})
    return {"schema": "cad-missing-features-v1",
            "model_id": model["model_id"],
            "revision": model["revision"],
            "underconstrained_features": want}
