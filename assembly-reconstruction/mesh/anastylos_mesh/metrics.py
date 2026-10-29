"""Reconstruction quality metrics against synthetic ground truth.

All metrics are deterministic given (mesh, scene, n_samples, seed):
bbox dimension error (mm), symmetric Chamfer distance (mm) via
``scipy.spatial.cKDTree`` on sampled surface points, completeness
fraction within tau, trimesh volume error, and vertex/face counts.
"""

from __future__ import annotations

import math

import numpy as np
import trimesh
from scipy.spatial import cKDTree
from trimesh.sample import sample_surface

from anastylos_mesh.geometry import MeshCoverageError
from anastylos_mesh.synthetic import Scene

__all__ = ["evaluate"]


def _sample_recon(mesh: trimesh.Trimesh, n: int, seed: int) -> np.ndarray:
    faces = np.asarray(mesh.faces)
    if faces.shape[0] == 0:
        raise MeshCoverageError("EMPTY_RECONSTRUCTION")
    points, _ = sample_surface(mesh, n, seed=seed)
    return np.asarray(points, dtype=np.float64)


def evaluate(
    mesh: trimesh.Trimesh,
    scene: Scene,
    n_samples: int = 20000,
    tau_mm: float = 1.0,
    seed: int = 0,
) -> dict:
    """Compare a reconstructed mesh to a scene's ground truth.

    Returns a JSON-serializable dict. Raises MeshCoverageError on an
    empty reconstruction (distances would be undefined).
    """
    if n_samples < 1:
        raise MeshCoverageError("BAD_METRIC_SAMPLES")
    if not (math.isfinite(tau_mm) and tau_mm > 0):
        raise MeshCoverageError("BAD_METRIC_TAU")
    verts = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces)
    if faces.shape[0] == 0 or verts.shape[0] == 0:
        raise MeshCoverageError("EMPTY_RECONSTRUCTION")

    gt_dims = np.array(scene.gt_bbox_dims, dtype=np.float64)
    lo = verts.min(axis=0)
    hi = verts.max(axis=0)
    rec_dims = hi - lo
    abs_err = np.abs(rec_dims - gt_dims)

    rec_pts = _sample_recon(mesh, n_samples, seed)
    gt_pts = scene.sample_points(n_samples, seed + 1)
    d_rec_to_gt, _ = cKDTree(gt_pts).query(rec_pts)
    d_gt_to_rec, _ = cKDTree(rec_pts).query(gt_pts)
    chamfer_rg = float(np.mean(d_rec_to_gt))
    chamfer_gr = float(np.mean(d_gt_to_rec))

    watertight = bool(mesh.is_watertight)
    rec_vol = float(mesh.volume)
    gt_vol = float(scene.gt_volume)
    vol_abs = abs(rec_vol - gt_vol)

    return {
        "scene": scene.name,
        "scene_params": {str(k): float(v) for k, v in scene.params.items()},
        "n_samples": int(n_samples),
        "tau_mm": float(tau_mm),
        "seed": int(seed),
        "bbox_dims_gt_mm": [float(v) for v in gt_dims],
        "bbox_dims_rec_mm": [float(v) for v in rec_dims],
        "bbox_dims_abs_err_mm": [float(v) for v in abs_err],
        "bbox_dims_max_abs_err_mm": float(np.max(abs_err)),
        "chamfer_rec_to_gt_mm": chamfer_rg,
        "chamfer_gt_to_rec_mm": chamfer_gr,
        "chamfer_symmetric_mm": 0.5 * (chamfer_rg + chamfer_gr),
        "completeness_within_tau": float(np.mean(d_gt_to_rec <= tau_mm)),
        "accuracy_within_tau": float(np.mean(d_rec_to_gt <= tau_mm)),
        "volume_gt_mm3": gt_vol,
        "volume_rec_mm3": rec_vol,
        "volume_abs_err_mm3": vol_abs,
        "volume_rel_err": (vol_abs / gt_vol) if gt_vol > 0 else float("inf"),
        "volume_watertight": watertight,
        "vertices": int(verts.shape[0]),
        "faces": int(faces.shape[0]),
    }
