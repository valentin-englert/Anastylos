"""Foreground masks for mesh carving.

A Mask pins opaque PNG evidence bytes (sha256-verified, magic-checked)
to the exact calibrated image (image_sha256 link, verified against the
Camera) and carries the decoded foreground grid the carver consumes.
PNG decoding itself is out of scope for the stdlib+numpy-only core:
the caller supplies the (height, width) foreground array alongside the
PNG bytes, and validate() checks shape against the declared dims and
the camera. Provenance is one of synthetic|manual|auto, mirroring the
calibration evidence-class vocabulary.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from anastylos_mesh.geometry import MeshCalibrationError, MeshProvenanceError

if TYPE_CHECKING:
    from anastylos_mesh.cameras import Camera

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PROVENANCE_VALUES = ("synthetic", "manual", "auto")
THRESHOLD = 127


def png_dimensions(png: bytes) -> tuple[int, int]:
    """(width, height) read from a PNG IHDR (stdlib only, deterministic).

    Raises MeshProvenanceError when the bytes are not a readable PNG.
    """
    data = bytes(png)
    if len(data) < 24 or not data.startswith(PNG_MAGIC):
        raise MeshProvenanceError("MASK_PNG_UNREADABLE")
    (length,) = struct.unpack(">I", data[8:12])
    if data[12:16] != b"IHDR" or length < 8:
        raise MeshProvenanceError("MASK_PNG_UNREADABLE")
    width, height = struct.unpack(">II", data[16:24])
    if width < 1 or height < 1:
        raise MeshProvenanceError("MASK_PNG_UNREADABLE")
    return (width, height)


@dataclass(frozen=True)
class Mask:
    """Foreground evidence for one view.

    png_bytes: opaque PNG evidence bytes (hash-pinned; the IHDR
      resolution must match the declared width/height). foreground:
      (height, width) bool array, True inside the object silhouette.
      image_sha256 must equal the Camera's image_sha256; sha256 must
      equal sha256(png_bytes). pose_epoch, when set, must equal the
      Camera's pose_epoch (source-epoch link).
    """

    png_bytes: bytes
    sha256: str
    image_sha256: str
    provenance: str
    width: int
    height: int
    foreground: np.ndarray
    pose_epoch: str | None = None

    def validate(self, camera: "Camera") -> "Mask":
        """Fail-closed checks against a Camera; returns self.

        Raises MeshProvenanceError for hash/link/provenance/epoch/byte
        failures and MeshCalibrationError for declared-dimension
        mismatches (declared dims vs camera/array are a calibration
        inconsistency; evidence bytes disagreeing with the declaration
        are a provenance failure).
        """
        if not isinstance(self.png_bytes, (bytes, bytearray)) or not self.png_bytes.startswith(PNG_MAGIC):
            raise MeshProvenanceError("BAD_PNG_MAGIC")
        if hashlib.sha256(bytes(self.png_bytes)).hexdigest() != self.sha256:
            raise MeshProvenanceError("MASK_SHA256_MISMATCH")
        if self.image_sha256 != camera.image_sha256:
            raise MeshProvenanceError("MASK_IMAGE_LINK_MISMATCH")
        if self.pose_epoch is not None and self.pose_epoch != camera.pose_epoch:
            raise MeshProvenanceError("MASK_POSE_EPOCH_MISMATCH")
        if self.provenance not in PROVENANCE_VALUES:
            raise MeshProvenanceError("BAD_MASK_PROVENANCE")
        if self.width != camera.width or self.height != camera.height:
            raise MeshCalibrationError("MASK_CAMERA_SIZE_MISMATCH")
        fg = np.asarray(self.foreground)
        if fg.shape != (self.height, self.width) or fg.dtype != np.bool_:
            raise MeshCalibrationError("MASK_FOREGROUND_SHAPE_MISMATCH")
        iw, ih = png_dimensions(bytes(self.png_bytes))
        if (iw, ih) != (self.width, self.height):
            raise MeshProvenanceError("MASK_PNG_RESOLUTION_MISMATCH")
        return self

    def diagnostics(self) -> dict:
        """Coverage fraction plus empty/full flags (deterministic)."""
        fg = np.asarray(self.foreground, dtype=np.bool_)
        total = fg.size
        count = int(fg.sum())
        return {
            "foreground_fraction": (count / total) if total else 0.0,
            "foreground_px": count,
            "total_px": int(total),
            "is_empty": bool(count == 0),
            "is_full": bool(total > 0 and count == total),
        }


def preprocess(gray: np.ndarray) -> np.ndarray:
    """Deterministic binarization: foreground iff gray >= 127.

    Accepts any array-like of uint8 grayscale (H, W); returns a bool
    (H, W) array. No morphology, no randomness.
    """
    arr = np.asarray(gray)
    if arr.ndim != 2:
        raise MeshCalibrationError("MASK_PREPROCESS_NOT_GRAYSCALE")
    return (arr >= THRESHOLD).astype(np.bool_)
