"""Evidence export: PLY+STL meshes plus a NON-NORMATIVE manifest.

Manifest schema ``mesh-evidence-v0`` (JSON Schema draft 2020-12, see
``assembly-reconstruction/mesh/manifest-schema.json``) is explicitly
NON-NORMATIVE: an experiment interchange record, not a system contract.
Every manifest carries provenance (algorithm version, params, input
hashes, voxel resolution, timing) and output sha256 pins.

Overwrite policy: existing outputs are never silently replaced. Without
``force`` an existing path raises FileExistsError; with ``force`` the
write lands on a fresh ``_r<N>`` revision suffix, leaving the original
bytes untouched.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import trimesh

from anastylos_mesh import ALGORITHM_VERSION
from anastylos_mesh.geometry import MeshCalibrationError, MeshCoverageError

__all__ = [
    "SCHEMA_ID",
    "SCHEMA_TITLE",
    "COVERAGE_CLASSES",
    "MIN_VIEWS_FOR_COMPLETE",
    "resolve_output",
    "save_mesh",
    "sha256_file",
    "write_manifest",
    "build_manifest",
]

SCHEMA_ID = "mesh-evidence-v0"
SCHEMA_TITLE = "NON-NORMATIVE mesh-evidence-v0"
COVERAGE_CLASSES = ("complete", "partial")
MIN_VIEWS_FOR_COMPLETE = 4


def sha256_file(path: str | Path) -> str:
    """Hex sha256 of a file's bytes."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_output(path: str | Path, force: bool = False) -> Path:
    """Resolve an output path under the no-silent-overwrite policy.

    Missing path -> itself. Existing path without force -> FileExistsError.
    Existing path with force -> first free ``stem_r<N>`` sibling (N >= 2).
    """
    target = Path(path)
    if not target.exists():
        return target
    if not force:
        raise FileExistsError(f"REFUSE_OVERWRITE: {target} exists (pass force)")
    n = 2
    while True:
        candidate = target.with_name(f"{target.stem}_r{n}{target.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def save_mesh(mesh: trimesh.Trimesh, path: str | Path, force: bool = False) -> Path:
    """Export a mesh to PLY or STL (chosen by suffix); returns final path."""
    target = resolve_output(path, force=force)
    suffix = target.suffix.lower()
    if suffix == ".ply":
        payload = mesh.export(file_type="ply")
    elif suffix == ".stl":
        payload = mesh.export(file_type="stl")
    else:
        raise ValueError(f"UNSUPPORTED_MESH_SUFFIX: {target.suffix}")
    target.parent.mkdir(parents=True, exist_ok=True)
    data = bytes(payload) if not isinstance(payload, (bytes, bytearray)) else bytes(payload)
    target.write_bytes(data)
    return target


def _jsonable(value: object) -> object:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def build_manifest(
    *,
    params: dict,
    image_hashes: list[str],
    mask_hashes: list[str],
    pose_epochs: list[str],
    mask_provenance: str,
    voxel_size_mm: float,
    dims: list[int],
    bounds_mm: list[list[float]],
    timing_ms: dict,
    metrics: dict,
    outputs: dict,
    extra: dict | None = None,
    declared_units: str = "mm",
    strict_metric_units: bool = False,
    coverage_class: str | None = None,
) -> dict:
    """Assemble the NON-NORMATIVE mesh-evidence-v0 manifest dict.

    Every manifest is marked ``extra.reconstruction_class ==
    "silhouette-constrained"``: visual-hull occupancy can fill
    concavities, never recover them.

    Declared-units gate: Camera/VoxelGrid are mm-only, so a scene that
    declares anything but ``"mm"`` cannot carry valid metric claims.
    Non-strict mode records ``extra.metric_status == "invalid"`` (with
    ``extra.declared_units``); strict mode raises MeshCalibrationError
    (WRONG_LENGTH_UNIT) instead of writing a metric manifest.

    Coverage gate: ``extra.coverage_class`` is "complete" or "partial".
    When omitted it is derived deterministically (fewer than
    MIN_VIEWS_FOR_COMPLETE views -> "partial"); an explicit "complete"
    on a sparse run is an overclaim and raises MeshCoverageError.
    """
    if declared_units == "mm" and extra and "declared_units" in extra:
        declared_units = extra["declared_units"]
    unit = str(declared_units)
    metric_ok = (unit == "mm")
    if strict_metric_units and not metric_ok:
        raise MeshCalibrationError(f"WRONG_LENGTH_UNIT:{unit}")
    view_count = len(image_hashes)
    if coverage_class is None and extra and "coverage_class" in extra:
        coverage_class = extra["coverage_class"]
    if coverage_class is None:
        coverage_class = (
            "complete" if view_count >= MIN_VIEWS_FOR_COMPLETE else "partial"
        )
    elif coverage_class not in COVERAGE_CLASSES:
        raise MeshCoverageError(f"BAD_COVERAGE_CLASS:{coverage_class}")
    elif coverage_class == "complete" and view_count < MIN_VIEWS_FOR_COMPLETE:
        raise MeshCoverageError("COVERAGE_OVERCLAIM")
    manifest = {
        "schema": SCHEMA_ID,
        "title": SCHEMA_TITLE,
        "non_normative": True,
        "algorithm_version": ALGORITHM_VERSION,
        "params": _jsonable(params),
        "inputs": {
            "image_sha256": list(image_hashes),
            "mask_sha256": list(mask_hashes),
            "pose_epochs": list(pose_epochs),
            "mask_provenance": mask_provenance,
            "view_count": len(image_hashes),
        },
        "voxels": {
            "voxel_size_mm": float(voxel_size_mm),
            "dims": [int(d) for d in dims],
            "bounds_mm": [[float(v) for v in axis] for axis in bounds_mm],
        },
        "timing_ms": {str(k): float(v) for k, v in timing_ms.items()},
        "metrics": _jsonable(metrics),
        "outputs": _jsonable(outputs),
    }
    merged_extra = {str(k): v for k, v in extra.items()} if extra else {}
    merged_extra["reconstruction_class"] = "silhouette-constrained"
    merged_extra["declared_units"] = unit
    merged_extra["metric_status"] = "valid" if metric_ok else "invalid"
    merged_extra["coverage_class"] = coverage_class
    manifest["extra"] = _jsonable(merged_extra)
    return manifest


def write_manifest(manifest: dict, path: str | Path, force: bool = False) -> Path:
    """Write a manifest JSON file (sorted keys); returns the final path."""
    target = resolve_output(path, force=force)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    return target
