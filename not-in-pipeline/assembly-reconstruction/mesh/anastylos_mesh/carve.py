"""Voxel-grid visual-hull carving.

Occupancy rule (frozen): a voxel is occupied iff
observed_count >= min_observed AND foreground_count == observed_count.
Any observed background carves the voxel; outside-image and
behind-camera projections are UNOBSERVED and carry no evidence either
way. All coordinates are board mm (x right, y down, z into the board);
projection mirrors spatial ``project_board_point`` via
``anastylos_mesh.geometry.project_points``.

Gates (fail-closed): grid caps (max 256 per dim, max 8M voxels),
minimum view count, and minimum baseline: the maximum angular
separation of camera centers about the ROI center must reach
min_baseline_deg, else DegenerateBaselineError.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from anastylos_mesh.cameras import Camera
from anastylos_mesh.geometry import (
    DegenerateBaselineError,
    MeshCoverageError,
    MeshProvenanceError,
    project_points,
)
from anastylos_mesh.masks import Mask

MAX_GRID_DIM = 256
MAX_VOXELS = 8_000_000


@dataclass(frozen=True)
class VoxelGrid:
    """Regular grid over a board-mm box.

    bounds_mm: ((xmin,xmax),(ymin,ymax),(zmin,zmax)) with min<max.
    occupancy arrays use shape (nx, ny, nz); voxel (i,j,k) center is
    (xmin+(i+0.5)*s, ymin+(j+0.5)*s, zmin+(k+0.5)*s).
    length_unit declares the world unit, mm-only like Camera.
    """

    bounds_mm: tuple[tuple[float, float], tuple[float, float], tuple[float, float]]
    voxel_size: float
    length_unit: str = "mm"

    def __post_init__(self) -> None:
        if self.length_unit != "mm":
            raise MeshCoverageError("BAD_GRID_LENGTH_UNIT")
        if not (isinstance(self.voxel_size, (int, float)) and math.isfinite(self.voxel_size)
                and self.voxel_size > 0):
            raise MeshCoverageError("BAD_VOXEL_SIZE")
        try:
            (xmin, xmax), (ymin, ymax), (zmin, zmax) = self.bounds_mm
        except (TypeError, ValueError):
            raise MeshCoverageError("BAD_GRID_BOUNDS") from None
        for lo, hi in ((xmin, xmax), (ymin, ymax), (zmin, zmax)):
            if not (math.isfinite(lo) and math.isfinite(hi)) or not hi > lo:
                raise MeshCoverageError("BAD_GRID_BOUNDS")
        dims = self.dims
        if any(d < 1 for d in dims):
            raise MeshCoverageError("EMPTY_GRID")
        if any(d > MAX_GRID_DIM for d in dims):
            raise MeshCoverageError("GRID_DIM_CAP_EXCEEDED")
        if dims[0] * dims[1] * dims[2] > MAX_VOXELS:
            raise MeshCoverageError("GRID_VOXEL_CAP_EXCEEDED")

    @property
    def dims(self) -> tuple[int, int, int]:
        """(nx, ny, nz) voxel counts per axis (ceil of extent/size)."""
        (xmin, xmax), (ymin, ymax), (zmin, zmax) = self.bounds_mm
        return (
            int(math.ceil((xmax - xmin) / self.voxel_size)),
            int(math.ceil((ymax - ymin) / self.voxel_size)),
            int(math.ceil((zmax - zmin) / self.voxel_size)),
        )

    @property
    def roi_center(self) -> tuple[float, float, float]:
        """Bounds center in board mm."""
        (xmin, xmax), (ymin, ymax), (zmin, zmax) = self.bounds_mm
        return ((xmin + xmax) / 2.0, (ymin + ymax) / 2.0, (zmin + zmax) / 2.0)

    def centers(self) -> np.ndarray:
        """Voxel centers, (nx*ny*nz, 3) float64 in x-fastest (i,j,k) order."""
        (xmin, _), (ymin, _), (zmin, _) = self.bounds_mm
        nx, ny, nz = self.dims
        s = float(self.voxel_size)
        xs = xmin + (np.arange(nx, dtype=np.float64) + 0.5) * s
        ys = ymin + (np.arange(ny, dtype=np.float64) + 0.5) * s
        zs = zmin + (np.arange(nz, dtype=np.float64) + 0.5) * s
        xx, yy, zz = np.meshgrid(xs, ys, zs, indexing="ij")
        return np.stack([xx.ravel(), yy.ravel(), zz.ravel()], axis=1)


@dataclass(frozen=True)
class CarveResult:
    """Occupancy plus coverage metrics (all deterministic)."""

    occupancy: np.ndarray  # bool (nx, ny, nz)
    grid: VoxelGrid
    observed_counts: np.ndarray  # int16 (nx, ny, nz)
    metrics: dict

    @property
    def occupied_count(self) -> int:
        """Number of occupied voxels."""
        return int(np.asarray(self.occupancy, dtype=np.bool_).sum())


def max_baseline_deg(cameras: list[Camera], roi_center: tuple[float, float, float]) -> float:
    """Max pairwise angular separation (deg) of centers about roi_center."""
    dirs = []
    for cam in cameras:
        cx, cy, cz = cam.center_board()
        v = (cx - roi_center[0], cy - roi_center[1], cz - roi_center[2])
        n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
        if n == 0:
            continue
        dirs.append((v[0] / n, v[1] / n, v[2] / n))
    best = 0.0
    for i in range(len(dirs)):
        for j in range(i + 1, len(dirs)):
            dot = max(-1.0, min(1.0, sum(a * b for a, b in zip(dirs[i], dirs[j]))))
            best = max(best, math.degrees(math.acos(dot)))
    return best


def check_pose_epoch_consistency(cameras: list[Camera]) -> str:
    """Return the single shared pose_epoch; mixed epochs reject fail-closed.

    All views of one carve must observe the same object-pose epoch.
    Mixed epochs mean the object moved mid-capture; no registration
    transform path exists yet, so fusion is refused with
    MeshProvenanceError (never silently averaged).
    """
    if not cameras:
        return ""
    epochs = {cam.pose_epoch for cam in cameras}
    if len(epochs) > 1:
        raise MeshProvenanceError(
            f"MIXED_POSE_EPOCH: {len(epochs)} distinct pose_epoch values "
            "(object moved; no registration path exists yet)"
        )
    return next(iter(epochs))


def carve(
    cameras: list[Camera],
    masks: list[Mask],
    grid: VoxelGrid,
    min_observed: int = 2,
    min_views: int = 2,
    min_baseline_deg: float = 5.0,
) -> CarveResult:
    """Carve the visual hull. Raises typed errors fail-closed.

    Each camera is validated, each mask is validated against its
    camera (same order, same count). Voxel centers project through
    every view with distortion; observed means in front AND inside
    [0,width)x[0,height) with nearest-pixel sampling
    (floor(u), floor(v) under the top-left-center-0,0 convention).
    """
    if len(cameras) != len(masks):
        raise MeshCoverageError("CAMERA_MASK_COUNT_MISMATCH")
    if len(cameras) < min_views:
        raise MeshCoverageError("TOO_FEW_VIEWS")
    if not isinstance(min_observed, int) or min_observed < 1:
        raise MeshCoverageError("BAD_MIN_OBSERVED")
    if min_observed > len(cameras):
        raise MeshCoverageError("MIN_OBSERVED_EXCEEDS_VIEWS")
    for cam in cameras:
        cam.validate()
    for mask, cam in zip(masks, cameras):
        mask.validate(cam)
    baseline = max_baseline_deg(cameras, grid.roi_center)
    if baseline < min_baseline_deg:
        raise DegenerateBaselineError(
            f"DEGENERATE_BASELINE: max separation {baseline:.3f} deg "
            f"< required {min_baseline_deg:.3f} deg"
        )
    # Temporal gate runs after the geometric gates so degenerate-view
    # diagnostics keep priority over epoch diagnostics.
    check_pose_epoch_consistency(cameras)

    nx, ny, nz = grid.dims
    n = nx * ny * nz
    centers = grid.centers()
    observed = np.zeros(n, dtype=np.int64)
    foreground = np.zeros(n, dtype=np.int64)
    per_view_observed: list[int] = []
    for cam, mask in zip(cameras, masks):
        uv, in_front = project_points(
            centers,
            list(cam.T_board_camera),
            list(cam.K),
            list(cam.dist),
            list(cam.reference_to_image),
        )
        px = np.floor(uv[:, 0]).astype(np.int64)
        py = np.floor(uv[:, 1]).astype(np.int64)
        inside = (
            in_front
            & (px >= 0) & (px < cam.width)
            & (py >= 0) & (py < cam.height)
        )
        per_view_observed.append(int(inside.sum()))
        if not inside.any():
            continue
        fg = np.asarray(mask.foreground, dtype=np.bool_)
        hit = fg[py[inside], px[inside]]
        observed[inside] += 1
        foreground[inside] += hit.astype(np.int64)

    observed_grid = observed.reshape((nx, ny, nz))
    occupancy = ((observed >= min_observed) & (foreground == observed)).reshape((nx, ny, nz))
    gated = int((observed >= min_observed).sum())
    metrics = {
        "total_voxels": n,
        "dims": [nx, ny, nz],
        "views": len(cameras),
        "min_observed": min_observed,
        "baseline_deg": baseline,
        "min_baseline_deg": float(min_baseline_deg),
        "voxels_meeting_min_observed": gated,
        "unknown_voxels": n - gated,
        "unknown_fraction": ((n - gated) / n) if n else 0.0,
        "occupied_voxels": int(occupancy.sum()),
        "observation_fraction": (gated / n) if n else 0.0,
        "occupied_fraction": (float(occupancy.sum()) / n) if n else 0.0,
        "mean_observed_per_voxel": float(observed.mean()) if n else 0.0,
        "per_view_observed_voxels": per_view_observed,
    }
    return CarveResult(
        occupancy=occupancy.astype(np.bool_),
        grid=grid,
        observed_counts=observed_grid.astype(np.int16),
        metrics=metrics,
    )
