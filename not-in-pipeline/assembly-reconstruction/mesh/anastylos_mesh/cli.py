"""Mesh experiment CLI: fixtures, reconstruction, evaluation, reopen check.

Subcommands:
  render-fixtures  render a synthetic scene's Camera+Mask fixture set
  reconstruct      carve + extract + export PLY/STL/manifest from fixtures
  evaluate         score a reconstructed mesh against fixture ground truth
  verify-reopen    reload PLY/STL in a fresh subprocess; compare counts+sha256
  benchmark        run the fixed-profile benchmark (dev/val/held-out splits)
  ingest-spatial   build a request from sealed Spatial evidence, then carve +
                   export (exit 0 ready, 1 pending, 2 error)

Fixture directory layout (all paths relative to the fixture dir):
  scene.json cameras.json masks.json ring.json images/NNN.png masks/NNN.png
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import trimesh

from anastylos_mesh import ALGORITHM_VERSION
from anastylos_mesh.carve import VoxelGrid, carve
from anastylos_mesh.cameras import Camera
from anastylos_mesh.export import build_manifest, save_mesh, sha256_file, write_manifest
from anastylos_mesh.masks import Mask, preprocess
from anastylos_mesh.metrics import evaluate
from anastylos_mesh.surface import extract_surface
from anastylos_mesh.synthetic import (
    PROVENANCE,
    decode_png_gray,
    make_scene,
    render_fixtures,
)

__all__ = ["main"]


# ---------------------------------------------------------------------------
# Fixture IO.
# ---------------------------------------------------------------------------

def _camera_to_json(cam: Camera) -> dict:
    return {
        "K": list(cam.K),
        "dist": list(cam.dist),
        "width": cam.width,
        "height": cam.height,
        "T_board_camera": list(cam.T_board_camera),
        "image_sha256": cam.image_sha256,
        "pose_epoch": cam.pose_epoch,
        "reference_image_size_px": list(cam.reference_image_size_px),
        "reference_to_image": list(cam.reference_to_image),
    }


def _camera_from_json(d: dict) -> Camera:
    return Camera(
        K=tuple(d["K"]),
        dist=tuple(d["dist"]),
        width=int(d["width"]),
        height=int(d["height"]),
        T_board_camera=tuple(d["T_board_camera"]),
        image_sha256=str(d["image_sha256"]),
        pose_epoch=str(d["pose_epoch"]),
        reference_image_size_px=tuple(d.get("reference_image_size_px", (0, 0))),
        reference_to_image=tuple(
            d.get("reference_to_image", (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0))
        ),
    ).validate()


def write_fixtures(fixtures, out_dir: Path) -> dict:
    """Write a FixtureSet to disk; returns a JSON-serializable summary."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "images").mkdir(exist_ok=True)
    (out_dir / "masks").mkdir(exist_ok=True)
    scene = fixtures.scene
    (out_dir / "scene.json").write_text(
        json.dumps({"name": scene.name, "params": scene.params,
                    "units": scene.units,
                    "gt_bbox_dims_mm": list(scene.gt_bbox_dims),
                    "gt_volume_mm3": scene.gt_volume,
                    "bounds_mm": [list(a) for a in scene.bounds]},
                   indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (out_dir / "ring.json").write_text(
        json.dumps(fixtures.params, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (out_dir / "cameras.json").write_text(
        json.dumps([_camera_to_json(c) for c in fixtures.cameras],
                   indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    mask_rows = []
    for i, (mask, png) in enumerate(zip(fixtures.masks, fixtures.image_pngs)):
        img_name = f"images/{i:03d}.png"
        mask_name = f"masks/{i:03d}.png"
        (out_dir / img_name).write_bytes(png)
        (out_dir / mask_name).write_bytes(bytes(mask.png_bytes))
        mask_rows.append({
            "file": mask_name,
            "image_file": img_name,
            "sha256": mask.sha256,
            "image_sha256": mask.image_sha256,
            "provenance": mask.provenance,
            "width": mask.width,
            "height": mask.height,
            "foreground_px": int(np.asarray(mask.foreground).sum()),
        })
    (out_dir / "masks.json").write_text(
        json.dumps(mask_rows, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {
        "scene": scene.name,
        "views": len(fixtures.cameras),
        "out_dir": str(out_dir),
        "mask_provenance": PROVENANCE,
    }


def load_fixtures(fixture_dir: Path) -> tuple:
    """Load (scene, cameras, masks, ring_params) with full validation."""
    scene_d = json.loads((fixture_dir / "scene.json").read_text(encoding="utf-8"))
    scene = make_scene(scene_d["name"], units=scene_d.get("units", "mm"),
                       **scene_d["params"])
    ring = json.loads((fixture_dir / "ring.json").read_text(encoding="utf-8"))
    cameras = [_camera_from_json(d) for d in
               json.loads((fixture_dir / "cameras.json").read_text(encoding="utf-8"))]
    mask_rows = json.loads((fixture_dir / "masks.json").read_text(encoding="utf-8"))
    if len(cameras) != len(mask_rows):
        raise ValueError("CAMERA_MASK_COUNT_MISMATCH")
    masks = []
    for row, cam in zip(mask_rows, cameras):
        raw = (fixture_dir / row["file"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["sha256"]:
            raise ValueError(f"MASK_SHA256_MISMATCH:{row['file']}")
        img_raw = (fixture_dir / row["image_file"]).read_bytes()
        if hashlib.sha256(img_raw).hexdigest() != row["image_sha256"]:
            raise ValueError(f"IMAGE_SHA256_MISMATCH:{row['image_file']}")
        fg = preprocess(decode_png_gray(raw))
        mask = Mask(png_bytes=raw, sha256=row["sha256"],
                    image_sha256=row["image_sha256"],
                    provenance=row["provenance"], width=int(row["width"]),
                    height=int(row["height"]), foreground=fg)
        mask.validate(cam)
        masks.append(mask)
    return scene, cameras, masks, ring


# ---------------------------------------------------------------------------
# Subcommands.
# ---------------------------------------------------------------------------

def cmd_render_fixtures(args: argparse.Namespace) -> int:
    scene = make_scene(args.scene, **dict(args.param))
    fixtures = render_fixtures(
        scene, n_views=args.views, width=args.width, height=args.height,
        focal_px=args.focal_px, elevations_deg=tuple(args.elevations),
    )
    summary = write_fixtures(fixtures, Path(args.out))
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def _grid_for_scene(scene, resolution: int, margin: float) -> VoxelGrid:
    extents = [b[1] - b[0] for b in scene.bounds]
    pad = max(extents) * margin
    bounds = tuple((b[0] - pad, b[1] + pad) for b in scene.bounds)
    voxel = max(b[1] - b[0] for b in bounds) / resolution
    return VoxelGrid(bounds_mm=bounds, voxel_size=voxel)


def cmd_reconstruct(args: argparse.Namespace) -> int:
    t0 = time.perf_counter()
    scene, cameras, masks, ring = load_fixtures(Path(args.fixtures))
    grid = _grid_for_scene(scene, args.resolution, args.margin)
    t1 = time.perf_counter()
    result = carve(cameras, masks, grid, min_observed=args.min_observed)
    t2 = time.perf_counter()
    surface = extract_surface(result)
    t3 = time.perf_counter()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    mesh = surface.mesh
    ply_path = save_mesh(mesh, out_dir / "mesh.ply", force=args.force)
    stl_path = save_mesh(mesh, out_dir / "mesh.stl", force=args.force)
    timing_ms = {
        "load_ms": (t1 - t0) * 1000.0,
        "carve_ms": (t2 - t1) * 1000.0,
        "surface_ms": (t3 - t2) * 1000.0,
        "export_ms": (time.perf_counter() - t3) * 1000.0,
    }
    timing_ms["total_ms"] = sum(timing_ms.values())
    metrics = dict(result.metrics)
    metrics["surface"] = surface.stats
    outputs = {
        "ply": {"path": ply_path.name, "sha256": sha256_file(ply_path),
                "vertices": surface.vertex_count, "faces": surface.face_count},
        "stl": {"path": stl_path.name, "sha256": sha256_file(stl_path),
                "vertices": surface.vertex_count, "faces": surface.face_count},
    }
    manifest = build_manifest(
        params={"scene": scene.name, "scene_params": scene.params,
                "scene_units": scene.units, "ring": ring,
                "resolution": args.resolution, "margin": args.margin,
                "min_observed": args.min_observed,
                "strict_metric_units": args.strict_units},
        image_hashes=[c.image_sha256 for c in cameras],
        mask_hashes=[m.sha256 for m in masks],
        pose_epochs=[c.pose_epoch for c in cameras],
        mask_provenance=masks[0].provenance if masks else PROVENANCE,
        voxel_size_mm=grid.voxel_size,
        dims=list(grid.dims),
        bounds_mm=[list(a) for a in grid.bounds_mm],
        timing_ms=timing_ms,
        metrics=metrics,
        outputs=outputs,
        declared_units=scene.units,
        strict_metric_units=args.strict_units,
    )
    manifest_path = write_manifest(manifest, out_dir / "manifest.json",
                                   force=args.force)
    print(json.dumps({"manifest": str(manifest_path),
                      "algorithm_version": ALGORITHM_VERSION,
                      "outputs": outputs, "timing_ms": timing_ms,
                      "occupied_voxels": result.metrics["occupied_voxels"],
                      "watertight": surface.watertight,
                      "components": surface.components},
                     indent=2, sort_keys=True))
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    scene, _, _, _ = load_fixtures(Path(args.fixtures))
    mesh = trimesh.load(Path(args.mesh), force="mesh")
    report = evaluate(mesh, scene, n_samples=args.samples, tau_mm=args.tau,
                      seed=args.seed)
    out = Path(args.out) if args.out else None
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
        report = dict(report)
        report["report_path"] = str(out)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


_REOPEN_PROBE = (
    "import json,sys;"
    "import trimesh;"
    "m=trimesh.load(sys.argv[1],force='mesh');"
    "print(json.dumps({'vertices':int(m.vertices.shape[0]),"
    "'faces':int(m.faces.shape[0])}))"
)


def _reopen_counts(path: Path) -> dict:
    proc = subprocess.run(
        [sys.executable, "-c", _REOPEN_PROBE, str(path)],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"REOPEN_FAILED:{path}:{proc.stderr.strip()[-500:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def cmd_verify_reopen(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest)
    base = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    ok = True
    files: dict = {}
    for key, entry in manifest.get("outputs", {}).items():
        path = base / entry["path"]
        sha = sha256_file(path) if path.exists() else None
        sha_match = (sha == entry["sha256"])
        try:
            counts = _reopen_counts(path)
            counts_match = (counts["vertices"] == entry["vertices"]
                            and counts["faces"] == entry["faces"])
            error = None
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            counts = None
            counts_match = False
            error = f"{type(exc).__name__}:{exc}"
        file_ok = sha_match and counts_match
        ok = ok and file_ok
        files[key] = {"path": str(path), "sha256_match": sha_match,
                      "counts_match": counts_match,
                      "manifest_counts": {"vertices": entry["vertices"],
                                          "faces": entry["faces"]},
                      "reopened_counts": counts, "error": error, "ok": file_ok}
    print(json.dumps({"manifest": str(manifest_path), "ok": ok, "files": files},
                     indent=2, sort_keys=True))
    return 0 if ok else 1


def cmd_benchmark(args: argparse.Namespace) -> int:
    from anastylos_mesh.benchmark import run_benchmark, summarize
    doc = run_benchmark(Path(args.out), repeats=args.repeats)
    print(summarize(doc))
    print(json.dumps({"out_dir": str(Path(args.out)),
                      "verdicts": {n: v["pass"]
                                   for n, v in doc["heldout_verdicts"].items()}},
                     indent=2, sort_keys=True))
    return 0


def cmd_ingest_spatial(args: argparse.Namespace) -> int:
    """Build a mesh request from sealed Spatial evidence, then reconstruct.

    Exit 0: READY request carved + mesh-evidence-v0 manifest written.
    Exit 1: PENDING_EVIDENCE / PENDING_MASKS (ingest-status.json written,
    no manifest: mesh-evidence-v0 requires real mesh outputs, which do
    not exist yet -- never fabricated).
    Exit 2: usage, I/O, or typed mesh validation failure (JSON error).
    """
    from anastylos_mesh.export import resolve_output
    from anastylos_mesh.geometry import MeshError
    from anastylos_mesh.spatial_adapter import build_mesh_request

    try:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "ERROR",
                          "error": f"CONFIG_UNREADABLE:{type(exc).__name__}"},
                         indent=2, sort_keys=True))
        return 2
    t0 = time.perf_counter()
    try:
        result = build_mesh_request(args.dataset, args.masks, config)
    except MeshError as exc:
        print(json.dumps({"status": "ERROR",
                          "error": f"{type(exc).__name__}:{exc}"},
                         indent=2, sort_keys=True))
        return 2
    t1 = time.perf_counter()
    out_dir = Path(args.out)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(json.dumps({"status": "ERROR",
                          "error": f"OUT_DIR_UNWRITABLE:{exc}"},
                         indent=2, sort_keys=True))
        return 2
    if result.status != "READY":
        payload = result.to_dict()
        try:
            status_path = resolve_output(out_dir / "ingest-status.json",
                                         force=args.force)
            status_path.write_text(json.dumps(payload, indent=2,
                                              sort_keys=True) + "\n",
                                   encoding="utf-8")
        except (OSError, FileExistsError) as exc:
            print(json.dumps({"status": "ERROR",
                              "error": f"STATUS_UNWRITABLE:{exc}"},
                             indent=2, sort_keys=True))
            return 2
        print(json.dumps({"status": result.status,
                          "status_file": str(status_path),
                          "timing_ms": {"ingest_ms": (t1 - t0) * 1000.0}},
                         indent=2, sort_keys=True))
        return 1
    try:
        carve_result = carve(
            list(result.cameras), list(result.masks), result.grid,
            min_observed=result.carve_params["min_observed"],
            min_views=result.carve_params["min_views"],
            min_baseline_deg=result.carve_params["min_baseline_deg"],
        )
        t2 = time.perf_counter()
        surface = extract_surface(carve_result)
        t3 = time.perf_counter()
        ply_path = save_mesh(surface.mesh, out_dir / "mesh.ply",
                             force=args.force)
        stl_path = save_mesh(surface.mesh, out_dir / "mesh.stl",
                             force=args.force)
        timing_ms = {
            "ingest_ms": (t1 - t0) * 1000.0,
            "carve_ms": (t2 - t1) * 1000.0,
            "surface_ms": (t3 - t2) * 1000.0,
            "export_ms": (time.perf_counter() - t3) * 1000.0,
        }
        timing_ms["total_ms"] = sum(timing_ms.values())
        metrics = dict(carve_result.metrics)
        metrics["surface"] = surface.stats
        outputs = {
            "ply": {"path": ply_path.name,
                    "sha256": sha256_file(ply_path),
                    "vertices": surface.vertex_count,
                    "faces": surface.face_count},
            "stl": {"path": stl_path.name,
                    "sha256": sha256_file(stl_path),
                    "vertices": surface.vertex_count,
                    "faces": surface.face_count},
        }
        manifest = build_manifest(
            params={"dataset": str(args.dataset), "masks": str(args.masks),
                    "config": config,
                    "grid": {"bounds_mm": [list(a)
                                           for a in result.grid.bounds_mm],
                             "voxel_size_mm": result.grid.voxel_size},
                    "carve": dict(result.carve_params)},
            image_hashes=[c.image_sha256 for c in result.cameras],
            mask_hashes=[m.sha256 for m in result.masks],
            pose_epochs=[c.pose_epoch for c in result.cameras],
            mask_provenance=result.masks[0].provenance,
            voxel_size_mm=result.grid.voxel_size,
            dims=list(result.grid.dims),
            bounds_mm=[list(a) for a in result.grid.bounds_mm],
            timing_ms=timing_ms,
            metrics=metrics,
            outputs=outputs,
            extra={"spatial_provenance": result.provenance},
        )
        manifest_path = write_manifest(manifest, out_dir / "manifest.json",
                                       force=args.force)
    except (MeshError, OSError, ValueError, FileExistsError) as exc:
        print(json.dumps({"status": "ERROR",
                          "error": f"{type(exc).__name__}:{exc}"},
                         indent=2, sort_keys=True))
        return 2
    print(json.dumps({"status": "READY",
                      "manifest": str(manifest_path),
                      "algorithm_version": ALGORITHM_VERSION,
                      "outputs": outputs, "timing_ms": timing_ms,
                      "occupied_voxels":
                          carve_result.metrics["occupied_voxels"],
                      "watertight": surface.watertight,
                      "components": surface.components},
                     indent=2, sort_keys=True))
    return 0


def _kv_pair(text: str) -> tuple[str, float]:
    key, _, value = text.partition("=")
    if not key or not value:
        raise argparse.ArgumentTypeError(f"expected key=value, got {text!r}")
    return key, float(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="anastylos-mesh",
                                     description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("render-fixtures", help="render synthetic fixtures")
    p.add_argument("--scene", default="box",
                   choices=["box", "cylinder", "stepped", "cavity"])
    p.add_argument("--param", action="append", type=_kv_pair, default=[],
                   metavar="key=value", help="scene dimension override")
    p.add_argument("--views", type=int, default=8)
    p.add_argument("--width", type=int, default=320)
    p.add_argument("--height", type=int, default=240)
    p.add_argument("--focal-px", type=float, default=None)
    p.add_argument("--elevations", type=float, nargs=2, default=[25.0, 45.0])
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_render_fixtures)

    p = sub.add_parser("reconstruct", help="carve + extract + export")
    p.add_argument("--fixtures", required=True)
    p.add_argument("--resolution", type=int, default=96)
    p.add_argument("--margin", type=float, default=0.05)
    p.add_argument("--min-observed", type=int, default=2)
    p.add_argument("--out", required=True)
    p.add_argument("--force", action="store_true",
                   help="write revisions (_rN) instead of refusing overwrite")
    p.add_argument("--strict-units", action="store_true",
                   help="reject non-mm scenes instead of metric_status=invalid")
    p.set_defaults(func=cmd_reconstruct)

    p = sub.add_parser("evaluate", help="score a mesh vs fixture ground truth")
    p.add_argument("--fixtures", required=True)
    p.add_argument("--mesh", required=True)
    p.add_argument("--samples", type=int, default=20000)
    p.add_argument("--tau", type=float, default=1.0)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_evaluate)

    p = sub.add_parser("verify-reopen",
                       help="reopen PLY/STL fresh; compare counts+sha256")
    p.add_argument("--manifest", required=True)
    p.set_defaults(func=cmd_verify_reopen)

    p = sub.add_parser("benchmark", help="run the fixed-profile benchmark")
    p.add_argument("--out", default="benchmark_out")
    p.add_argument("--repeats", type=int, default=3)
    p.set_defaults(func=cmd_benchmark)

    p = sub.add_parser("ingest-spatial",
                       help="ingest sealed Spatial evidence, carve + export")
    p.add_argument("--dataset", required=True,
                   help="dataset_dir with capture_set.json + packages/ + "
                        "sidecars/ + spatial/")
    p.add_argument("--masks", required=True,
                   help="mask_dir with <image_sha256>.png + .json pairs")
    p.add_argument("--config", required=True,
                   help="JSON grid/carve config (see spatial_adapter)")
    p.add_argument("--out", required=True)
    p.add_argument("--force", action="store_true",
                   help="write revisions (_rN) instead of refusing overwrite")
    p.set_defaults(func=cmd_ingest_spatial)

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns a process exit code."""
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
