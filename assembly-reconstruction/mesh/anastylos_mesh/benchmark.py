"""Fixed-profile mesh visual-hull benchmark runner.

Splits (fixed profiles, see build_configs):
  dev:         box default @ resolutions 48/96/144 (quality/speed trade-off)
  validation:  cylinder + stepped defaults @ resolution 96
  held-out:    rotated-stepped (azimuth 37.5 deg) + tall-cylinder
               (r=15, h=70, azimuth 67.5 deg) @ resolution 96, eval seed 7

Held-out angles/seeds differ from everything used in dev/validation
(azimuth 0, eval seed 0) and from the unit-test ring (22.5 deg).
Rendering and carving are deterministic (no RNG); the recorded seed
drives only the evaluate() surface sampling.

Protocol: run_benchmark writes expected-outcomes.json (pre-registered
quantitative bounds for the held-out configs) BEFORE executing any
held-out run, then runs every config N times and reports pass/fail of
the held-out mean actuals vs those bounds without editing them.

Per-rep measurements: wall time total + per-stage render/carve/surface/
export (evaluate is measurement overhead, recorded separately as
eval_ms), peak Python allocation via tracemalloc (method recorded in
each record; numpy/native buffers are excluded by tracemalloc design),
view count, baseline diversity, mask provenance, voxel resolution +
occupied/total voxels, bbox dims, verts/faces, components, watertight,
file sizes, reopen check, and GT metrics (bbox err, symmetric Chamfer,
completeness@1mm, volume err).

Outputs under <out_dir>/ (default benchmark_out/): expected-outcomes.json,
benchmark_results.json, benchmark_results.csv, runs/<config>/rep<N>/
mesh.{ply,stl}. JSON/CSV carry no absolute paths and no wall-clock
timestamps, so bytes are deterministic given identical inputs.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path

from anastylos_mesh import ALGORITHM_VERSION
from anastylos_mesh.carve import VoxelGrid, carve
from anastylos_mesh.metrics import evaluate
from anastylos_mesh.surface import extract_surface
from anastylos_mesh.synthetic import make_scene, render_fixtures

__all__ = [
    "DEFAULT_REPEATS",
    "EVAL_SAMPLES",
    "EVAL_TAU_MM",
    "HELDOUT_BOUNDS",
    "HELDOUT_RATIONALE",
    "PEAK_METHOD",
    "build_configs",
    "write_expected_outcomes",
    "run_single",
    "run_benchmark",
]

DEFAULT_REPEATS = 3
EVAL_SAMPLES = 10000
EVAL_TAU_MM = 1.0

PEAK_METHOD = (
    "tracemalloc.get_traced_memory()[1] peak across render+carve+"
    "surface+export; Python-level allocations only, numpy/native "
    "buffers excluded by tracemalloc design"
)

HELDOUT_RATIONALE = (
    "Bounds pre-registered before any held-out execution, derived from "
    "res-32 unit-test observations at 320x240/8 views (cylinder: chamfer "
    "1.36mm, vol rel-err 0.095, height err 4mm; stepped: chamfer 3.25mm, "
    "bbox rel-err 0.186) with margin for the finer res-96 grid, unseen "
    "ring angles, and the taller cylinder (silhouette-weak height). "
    "Completeness@1mm has no prior observation, so its bounds are loose "
    "regression trips, not fit claims."
)

# metric -> (op, bound). lt/gt compare the across-rep mean; eq requires
# every rep to equal the bound (booleans / component counts).
HELDOUT_BOUNDS = {
    "heldout-rotated-stepped-r96": [
        {"metric": "chamfer_symmetric_mm", "op": "lt", "bound": 4.0},
        {"metric": "completeness_at_1mm", "op": "gt", "bound": 0.15},
        {"metric": "bbox_max_abs_err_mm", "op": "lt", "bound": 12.0},
        {"metric": "volume_rel_err", "op": "lt", "bound": 0.6},
        {"metric": "watertight", "op": "eq", "bound": True},
        {"metric": "components", "op": "eq", "bound": 1},
    ],
    "heldout-tall-cylinder-r96": [
        {"metric": "chamfer_symmetric_mm", "op": "lt", "bound": 3.0},
        {"metric": "completeness_at_1mm", "op": "gt", "bound": 0.25},
        {"metric": "bbox_max_abs_err_mm", "op": "lt", "bound": 10.0},
        {"metric": "volume_rel_err", "op": "lt", "bound": 0.35},
        {"metric": "watertight", "op": "eq", "bound": True},
        {"metric": "components", "op": "eq", "bound": 1},
    ],
}

CSV_COLUMNS = [
    "config", "split", "rep", "seed", "scene", "resolution",
    "voxel_size_mm", "dim_x", "dim_y", "dim_z",
    "total_voxels", "occupied_voxels", "views", "baseline_deg",
    "mask_provenance", "input_sha256",
    "render_ms", "carve_ms", "surface_ms", "export_ms", "total_ms",
    "eval_ms", "peak_bytes",
    "bbox_rec_x_mm", "bbox_rec_y_mm", "bbox_rec_z_mm",
    "verts", "faces", "components", "watertight",
    "ply_bytes", "stl_bytes", "reopen_ok",
    "bbox_max_abs_err_mm", "chamfer_symmetric_mm",
    "completeness_at_1mm", "volume_abs_err_mm3", "volume_rel_err",
    "mesh_relpath", "status",
]

STATS_FIELDS = [
    "render_ms", "carve_ms", "surface_ms", "export_ms", "total_ms",
    "eval_ms", "peak_bytes", "occupied_voxels",
    "bbox_max_abs_err_mm", "chamfer_symmetric_mm",
    "completeness_at_1mm", "volume_abs_err_mm3", "volume_rel_err",
    "verts", "faces", "ply_bytes", "stl_bytes",
]

_REOPEN_PROBE = (
    "import json,sys;"
    "import trimesh;"
    "m=trimesh.load(sys.argv[1],force='mesh');"
    "print(json.dumps({'vertices':int(m.vertices.shape[0]),"
    "'faces':int(m.faces.shape[0])}))"
)


def build_configs() -> list[dict]:
    """Fixed benchmark profiles (JSON-serializable, deterministic order)."""
    base = {
        "views": 8, "width": 320, "height": 240, "focal_px": None,
        "elevations_deg": [25.0, 45.0], "margin": 0.05, "min_observed": 2,
        "eval_samples": EVAL_SAMPLES, "eval_tau_mm": EVAL_TAU_MM,
    }

    def _cfg(name, split, scene, scene_params, azimuth, resolution, seed):
        cfg = dict(base)
        cfg.update({
            "name": name, "split": split, "scene": scene,
            "scene_params": dict(scene_params),
            "azimuth_start_deg": azimuth, "resolution": resolution,
            "seed": seed,
        })
        return cfg

    return [
        _cfg("dev-box-r48", "dev", "box", {}, 0.0, 48, 0),
        _cfg("dev-box-r96", "dev", "box", {}, 0.0, 96, 0),
        _cfg("dev-box-r144", "dev", "box", {}, 0.0, 144, 0),
        _cfg("val-cylinder-r96", "validation", "cylinder", {}, 0.0, 96, 0),
        _cfg("val-stepped-r96", "validation", "stepped", {}, 0.0, 96, 0),
        _cfg("heldout-rotated-stepped-r96", "held-out", "stepped", {},
             37.5, 96, 7),
        _cfg("heldout-tall-cylinder-r96", "held-out", "cylinder",
             {"r": 15.0, "h": 70.0}, 67.5, 96, 7),
    ]


def write_expected_outcomes(path: Path, configs: list[dict]) -> dict:
    """Write pre-registered held-out bounds. Call BEFORE executing runs."""
    heldout = [c["name"] for c in configs if c["split"] == "held-out"]
    missing = [n for n in heldout if n not in HELDOUT_BOUNDS]
    if missing:
        raise ValueError(f"HELDOUT_BOUNDS_MISSING:{missing}")
    doc = {
        "algorithm_version": ALGORITHM_VERSION,
        "created_before_execution": True,
        "rationale": HELDOUT_RATIONALE,
        "eval_tau_mm": EVAL_TAU_MM,
        "verdict_rule": (
            "lt/gt bounds compare the across-rep mean; eq bounds "
            "require every rep to equal the bound"
        ),
        "bounds": {n: HELDOUT_BOUNDS[n] for n in heldout},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    return doc


def _grid_for_scene(scene, resolution: int, margin: float) -> VoxelGrid:
    """ROI grid mirroring the CLI reconstruct convention (padded gt bounds)."""
    extents = [b[1] - b[0] for b in scene.bounds]
    pad = max(extents) * margin
    bounds = tuple((b[0] - pad, b[1] + pad) for b in scene.bounds)
    voxel = max(b[1] - b[0] for b in bounds) / resolution
    return VoxelGrid(bounds_mm=bounds, voxel_size=voxel)


def _combined_input_sha256(fixtures) -> str:
    ordered = "|".join(
        f"{c.image_sha256}:{m.sha256}"
        for c, m in zip(fixtures.cameras, fixtures.masks)
    )
    return hashlib.sha256(ordered.encode("ascii")).hexdigest()


def _reopen_counts(path: Path) -> dict:
    """Reload a mesh in a fresh subprocess (mirrors cli.verify-reopen)."""
    proc = subprocess.run(
        [sys.executable, "-c", _REOPEN_PROBE, str(path)],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"REOPEN_FAILED:{path}:{proc.stderr.strip()[-500:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _cpu_model() -> str:
    for candidate in (os.environ.get("PROCESSOR_IDENTIFIER", ""),
                      platform.processor()):
        if candidate and candidate.strip():
            return " ".join(candidate.split())
    return "unknown"


def run_single(config: dict, run_dir: Path, rep: int, out_root: Path) -> dict:
    """Execute one config rep; returns the JSON/CSV-serializable record."""
    run_dir.mkdir(parents=True, exist_ok=True)
    tracemalloc.start()
    try:
        t0 = time.perf_counter()
        scene = make_scene(config["scene"], **config["scene_params"])
        fixtures = render_fixtures(
            scene, n_views=config["views"], width=config["width"],
            height=config["height"], focal_px=config["focal_px"],
            elevations_deg=tuple(config["elevations_deg"]),
            azimuth_start_deg=config["azimuth_start_deg"],
        )
        t1 = time.perf_counter()
        input_sha = _combined_input_sha256(fixtures)
        grid = _grid_for_scene(scene, config["resolution"], config["margin"])
        result = carve(fixtures.cameras, fixtures.masks, grid,
                       min_observed=config["min_observed"])
        t2 = time.perf_counter()
        surface = extract_surface(result)
        t3 = time.perf_counter()
        mesh = surface.mesh
        ply_path = run_dir / "mesh.ply"
        stl_path = run_dir / "mesh.stl"
        ply_path.write_bytes(bytes(mesh.export(file_type="ply")))
        stl_path.write_bytes(bytes(mesh.export(file_type="stl")))
        t4 = time.perf_counter()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    t5 = time.perf_counter()
    report = evaluate(mesh, scene, n_samples=config["eval_samples"],
                      tau_mm=config["eval_tau_mm"], seed=config["seed"])
    t6 = time.perf_counter()
    ply_counts = _reopen_counts(ply_path)
    stl_counts = _reopen_counts(stl_path)
    reopen_ok = (
        ply_counts["vertices"] == surface.vertex_count
        and ply_counts["faces"] == surface.face_count
        and stl_counts["vertices"] == surface.vertex_count
        and stl_counts["faces"] == surface.face_count
    )
    rec_dims = report["bbox_dims_rec_mm"]
    return {
        "config": config["name"],
        "split": config["split"],
        "rep": rep,
        "seed": config["seed"],
        "scene": config["scene"],
        "resolution": config["resolution"],
        "voxel_size_mm": grid.voxel_size,
        "dim_x": grid.dims[0], "dim_y": grid.dims[1], "dim_z": grid.dims[2],
        "total_voxels": result.metrics["total_voxels"],
        "occupied_voxels": result.metrics["occupied_voxels"],
        "views": result.metrics["views"],
        "baseline_deg": result.metrics["baseline_deg"],
        "mask_provenance": fixtures.masks[0].provenance,
        "input_sha256": input_sha,
        "render_ms": (t1 - t0) * 1000.0,
        "carve_ms": (t2 - t1) * 1000.0,
        "surface_ms": (t3 - t2) * 1000.0,
        "export_ms": (t4 - t3) * 1000.0,
        "total_ms": (t4 - t0) * 1000.0,
        "eval_ms": (t6 - t5) * 1000.0,
        "peak_bytes": peak,
        "peak_method": PEAK_METHOD,
        "bbox_rec_x_mm": rec_dims[0],
        "bbox_rec_y_mm": rec_dims[1],
        "bbox_rec_z_mm": rec_dims[2],
        "bbox_dims_abs_err_mm": report["bbox_dims_abs_err_mm"],
        "verts": surface.vertex_count,
        "faces": surface.face_count,
        "components": surface.components,
        "watertight": surface.watertight,
        "ply_bytes": ply_path.stat().st_size,
        "stl_bytes": stl_path.stat().st_size,
        "reopen_ok": reopen_ok,
        "bbox_max_abs_err_mm": report["bbox_dims_max_abs_err_mm"],
        "chamfer_symmetric_mm": report["chamfer_symmetric_mm"],
        "completeness_at_1mm": report["completeness_within_tau"],
        "volume_abs_err_mm3": report["volume_abs_err_mm3"],
        "volume_rel_err": report["volume_rel_err"],
        "mesh_relpath": (run_dir.relative_to(out_root) / "mesh.ply").as_posix(),
        "status": "ok",
    }


def _stats(records: list[dict]) -> dict:
    out = {}
    for field in STATS_FIELDS:
        vals = [float(r[field]) for r in records]
        out[field] = {
            "n": len(vals),
            "mean": sum(vals) / len(vals),
            "min": min(vals),
            "max": max(vals),
        }
    return out


def _check_bounds(config_name: str, records: list[dict]) -> dict:
    checks = []
    overall = True
    for spec in HELDOUT_BOUNDS[config_name]:
        metric, op, bound = spec["metric"], spec["op"], spec["bound"]
        if op == "eq":
            actuals = [r[metric] for r in records]
            passed = all(a == bound for a in actuals)
            actual = actuals[0] if all(a == actuals[0] for a in actuals) else actuals
        else:
            actual = sum(float(r[metric]) for r in records) / len(records)
            passed = (actual < bound) if op == "lt" else (actual > bound)
        overall = overall and passed
        checks.append({"metric": metric, "op": op, "bound": bound,
                       "actual": actual, "pass": passed})
    return {"config": config_name, "pass": overall, "checks": checks}


def run_benchmark(out_dir: Path, repeats: int = DEFAULT_REPEATS) -> dict:
    """Run the full fixed-profile benchmark; returns the results document."""
    if repeats < 1:
        raise ValueError("BAD_REPEATS")
    out_dir = Path(out_dir)
    results_path = out_dir / "benchmark_results.json"
    if results_path.exists():
        raise FileExistsError(
            f"REFUSE_OVERWRITE: {results_path} exists "
            "(move it aside to re-run)"
        )
    configs = build_configs()
    # Pre-register held-out bounds BEFORE executing any run.
    expected_path = out_dir / "expected-outcomes.json"
    expected = write_expected_outcomes(expected_path, configs)
    runs_root = out_dir / "runs"
    wall0 = time.perf_counter()
    per_config: dict[str, dict] = {}
    for cfg in configs:
        records = []
        for rep in range(repeats):
            run_dir = runs_root / cfg["name"] / f"rep{rep}"
            records.append(run_single(cfg, run_dir, rep, out_dir))
        per_config[cfg["name"]] = {
            "profile": cfg,
            "repeats": records,
            "stats_mean_min_max": _stats(records),
        }
    wall_s = time.perf_counter() - wall0
    verdicts = {
        name: _check_bounds(name, per_config[name]["repeats"])
        for name in expected["bounds"]
    }
    doc = {
        "algorithm_version": ALGORITHM_VERSION,
        "cpu_model": _cpu_model(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "repeats": repeats,
        "determinism_note": (
            "render/carve/surface/export are deterministic; reps are "
            "expected bitwise-identical except wall-clock timing and "
            "tracemalloc peaks. Aggregates report mean/min/max, never "
            "fastest-only."
        ),
        "total_wall_s": wall_s,
        "configs": per_config,
        "heldout_verdicts": verdicts,
    }
    results_path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n",
                            encoding="utf-8")
    with open(out_dir / "benchmark_results.csv", "w", newline="",
              encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for cfg in configs:
            for rec in per_config[cfg["name"]]["repeats"]:
                writer.writerow(rec)
    return doc


def summarize(doc: dict) -> str:
    """Human-readable trade-off table + held-out verdicts for stdout."""
    lines = ["config\ttotal_ms_mean\tchamfer_sym_mean\tcompl@1mm_mean\t"
             "vol_rel_err_mean\tpeak_bytes_mean"]
    for name, entry in doc["configs"].items():
        s = entry["stats_mean_min_max"]
        lines.append(
            f"{name}\t{s['total_ms']['mean']:.1f}\t"
            f"{s['chamfer_symmetric_mm']['mean']:.3f}\t"
            f"{s['completeness_at_1mm']['mean']:.3f}\t"
            f"{s['volume_rel_err']['mean']:.4f}\t"
            f"{int(s['peak_bytes']['mean'])}"
        )
    lines.append("held-out verdicts:")
    for name, verdict in doc["heldout_verdicts"].items():
        lines.append(f"  {name}: {'PASS' if verdict['pass'] else 'FAIL'}")
        for check in verdict["checks"]:
            lines.append(
                f"    {check['metric']} {check['op']} {check['bound']}: "
                f"actual={check['actual']} "
                f"{'PASS' if check['pass'] else 'FAIL'}"
            )
    return "\n".join(lines)
