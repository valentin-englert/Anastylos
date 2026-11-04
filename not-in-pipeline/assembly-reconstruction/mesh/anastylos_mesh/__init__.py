"""Mesh visual-hull core: typed errors and package version.

Conventions are mirrored EXACTLY from the calibration / spatial sources
(see anastylos_mesh.geometry docstring for the source inventory):
no axis flips, no silent inverses. T_board_camera maps camera coords
into board coords (mm); board is x right, y down, z into the board.
"""

from __future__ import annotations

ALGORITHM_VERSION = "mesh-vh-0.1.0"

from anastylos_mesh.geometry import (
    DegenerateBaselineError,
    MeshCalibrationError,
    MeshCoverageError,
    MeshError,
    MeshProvenanceError,
)

__all__ = [
    "ALGORITHM_VERSION",
    "DegenerateBaselineError",
    "MeshCalibrationError",
    "MeshCoverageError",
    "MeshError",
    "MeshProvenanceError",
]
