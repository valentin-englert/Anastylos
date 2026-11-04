"""Calibrated camera views for mesh carving.

Mirrors the calibration profile contract
(``calibration/spatial/anastylos_spatial/profiles.py`` lines 100-116:
brown_conrady_5 model, reference size [w,h] ints 1..32768, K 9 finite
with fx=k[0]>0 and fy=k[4]>0, distortion 5 finite) and the pose
contract (``validator.py`` lines 306-333: T_board_camera mm-only,
16 finite, bottom row [0,0,0,1], rotation det +1, camera center z<0
since the board z axis points into the paper).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from anastylos_mesh.geometry import (
    MAX_IMAGE_DIM,
    MeshCalibrationError,
    camera_center,
    validate_det1,
    validate_reference_to_image,
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
CAMERA_MODEL = "brown_conrady_5"


def _is_hex64(value: object) -> bool:
    return isinstance(value, str) and _HEX64.match(value) is not None


def _finite9(values: object) -> bool:
    return (
        isinstance(values, (list, tuple))
        and len(values) == 9
        and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
            for v in values
        )
    )


def _finite5(values: object) -> bool:
    return (
        isinstance(values, (list, tuple))
        and len(values) == 5
        and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
            for v in values
        )
    )


def _valid_px(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_IMAGE_DIM


@dataclass(frozen=True)
class Camera:
    """One calibrated view.

    K is row-major 9-list in the REFERENCE pixel frame
    (fx=K[0], fy=K[4], cx=K[2], cy=K[5]); distortion is the 5-vector
    [k1,k2,p1,p2,k3] applied before the image transform; width/height
    are the observed image size in px; T_board_camera is row-major
    16-list in mm mapping camera coords into board coords; image_sha256
    pins the exact image bytes; pose_epoch identifies the object-pose
    epoch shared by every view of a static capture (carve rejects mixed
    epochs: the object moved and no registration path exists yet).
    reference_to_image maps reference pixels to image pixels (default
    identity, i.e. reference frame == image frame); length_unit is the
    declared world unit, mm-only (any other value fails closed).
    """

    K: tuple[float, ...]
    dist: tuple[float, ...]
    width: int
    height: int
    T_board_camera: tuple[float, ...]
    image_sha256: str
    pose_epoch: str
    reference_image_size_px: tuple[int, int] = (0, 0)
    reference_to_image: tuple[float, ...] = field(
        default=(1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    )
    length_unit: str = "mm"

    def validate(self) -> "Camera":
        """Fail-closed checks; returns self. Raises MeshCalibrationError."""
        if not _finite9(self.K):
            raise MeshCalibrationError("NONFINITE_K")
        if not (self.K[0] > 0 and self.K[4] > 0):
            raise MeshCalibrationError("NONPOSITIVE_FOCAL_LENGTH")
        if not _finite5(self.dist):
            raise MeshCalibrationError("BAD_DISTORTION")
        if not _valid_px(self.width) or not _valid_px(self.height):
            raise MeshCalibrationError("BAD_IMAGE_SIZE")
        ref_w, ref_h = self.reference_image_size_px
        if not (ref_w == 0 and ref_h == 0) and not (_valid_px(ref_w) and _valid_px(ref_h)):
            raise MeshCalibrationError("BAD_REFERENCE_SIZE")
        validate_reference_to_image(list(self.reference_to_image))
        matrix = validate_det1(list(self.T_board_camera))
        if camera_center(matrix)[2] >= 0:
            raise MeshCalibrationError("INVERTED_BOARD_Z")
        if not _is_hex64(self.image_sha256):
            raise MeshCalibrationError("BAD_IMAGE_SHA256")
        if not isinstance(self.pose_epoch, str) or not self.pose_epoch:
            raise MeshCalibrationError("BAD_POSE_EPOCH")
        if self.length_unit != "mm":
            raise MeshCalibrationError("BAD_LENGTH_UNIT")
        return self

    @property
    def effective_reference_size(self) -> tuple[int, int]:
        """Reference size, defaulting to the image size when unset (0, 0)."""
        if self.reference_image_size_px == (0, 0):
            return (self.width, self.height)
        return self.reference_image_size_px

    def center_board(self) -> tuple[float, float, float]:
        """Camera center in board coords (mm)."""
        return camera_center(list(self.T_board_camera))
