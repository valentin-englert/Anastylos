"""Isosurface extraction from carved occupancy volumes.

Runs ``skimage.measure.marching_cubes`` (level 0.5) on the occupancy
uint8 volume, maps verts to board mm, then applies trimesh cleanup
(remove infinite + degenerate faces, merge vertices) and reports
watertightness plus connected-component count.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import trimesh
from skimage.measure import marching_cubes

from anastylos_mesh.carve import CarveResult
from anastylos_mesh.geometry import MeshCoverageError

__all__ = ["SurfaceResult", "extract_surface"]

LEVEL = 0.5


@dataclass(frozen=True)
class SurfaceResult:
    """Extracted mesh plus topology report (all deterministic)."""

    mesh: object  # trimesh.Trimesh, kept untyped to avoid a hard import at type time
    watertight: bool
    components: int
    stats: dict

    @property
    def vertex_count(self) -> int:
        """Number of cleaned vertices."""
        return int(np.asarray(self.mesh.vertices).shape[0])

    @property
    def face_count(self) -> int:
        """Number of cleaned faces."""
        return int(np.asarray(self.mesh.faces).shape[0])


def extract_surface(carve_result: CarveResult) -> SurfaceResult:
    """Extract + clean the level-0.5 isosurface of a carve result.

    Occupancy values are treated as samples at voxel centers: the volume
    is zero-padded by one voxel (so boundary-hugging hulls still close),
    marched at 0.5, and node (i, j, k) maps to
    bounds_min + (index - 0.5) * voxel_size per axis. Raises
    MeshCoverageError on empty occupancy (no surface exists).
    """
    occupancy = np.asarray(carve_result.occupancy, dtype=np.bool_)
    grid = carve_result.grid
    if occupancy.ndim != 3 or occupancy.shape != grid.dims:
        raise MeshCoverageError("SURFACE_GRID_SHAPE_MISMATCH")
    if not occupancy.any():
        raise MeshCoverageError("EMPTY_OCCUPANCY")
    s = float(grid.voxel_size)
    (xmin, _), (ymin, _), (zmin, _) = grid.bounds_mm
    # skimage axis order is (row, col, plane); our volume is (x, y, z),
    # so transpose to (z, y, x) and un-shuffle the verts afterwards.
    padded = np.pad(occupancy.astype(np.uint8), 1, constant_values=0)
    vol = np.ascontiguousarray(padded.transpose(2, 1, 0))
    verts, faces, _, _ = marching_cubes(vol, level=LEVEL, spacing=(s, s, s))
    # marching_cubes already scales indices by spacing, so verts are in
    # padded-index*mm units: node ip sits at bounds_min + (ip - 0.5) * s
    # (values live at voxel centers; padding shifts indices by one).
    verts_mm = np.stack(
        [
            xmin + verts[:, 2] - 0.5 * s,
            ymin + verts[:, 1] - 0.5 * s,
            zmin + verts[:, 0] - 0.5 * s,
        ],
        axis=1,
    )
    mesh = trimesh.Trimesh(vertices=verts_mm, faces=np.asarray(faces), process=False)
    raw_verts = int(np.asarray(mesh.vertices).shape[0])
    raw_faces = int(np.asarray(mesh.faces).shape[0])
    mesh.remove_infinite_values()
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.merge_vertices()
    mesh.remove_unreferenced_vertices()
    watertight = bool(mesh.is_watertight)
    n_faces = int(np.asarray(mesh.faces).shape[0])
    if n_faces == 0:
        raise MeshCoverageError("EMPTY_SURFACE_AFTER_CLEANUP")
    components = len(mesh.split(only_watertight=False))
    stats = {
        "level": LEVEL,
        "voxel_size_mm": s,
        "dims": list(grid.dims),
        "occupied_voxels": int(occupancy.sum()),
        "raw_vertices": raw_verts,
        "raw_faces": raw_faces,
        "vertices": int(np.asarray(mesh.vertices).shape[0]),
        "faces": n_faces,
        "watertight": watertight,
        "components": components,
    }
    return SurfaceResult(mesh=mesh, watertight=watertight,
                         components=components, stats=stats)
