"""Sequential, deterministic frame sampling from one video file.

Frames are decoded in order (no seeking), so frame indices are exact.
OpenCV applies the container's display rotation; the reported size is
the displayed one.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np


@dataclass(frozen=True)
class Sample:
    frame_index: int
    t_s: float
    image: np.ndarray


def sha256_file(path: Path, chunk_bytes: int = 1 << 24) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_bytes):
            digest.update(chunk)
    return digest.hexdigest()


def describe(path: Path) -> dict:
    capture = _open(path)
    try:
        return {
            "path": str(path),
            "frame_count": int(capture.get(cv2.CAP_PROP_FRAME_COUNT)),
            "fps": round(float(capture.get(cv2.CAP_PROP_FPS)), 5),
            "display_size": [int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))],
            "orientation_meta_deg": float(capture.get(cv2.CAP_PROP_ORIENTATION_META)),
        }
    finally:
        capture.release()


def sample_frames(path: Path, step_frames: int, wanted: set[int] | None = None) -> Iterator[Sample]:
    """Yield every ``step_frames``-th frame, or exactly the ``wanted`` indices."""
    capture = _open(path)
    try:
        index = -1
        last_wanted = max(wanted) if wanted else None
        while capture.grab():
            index += 1
            if last_wanted is not None and index > last_wanted:
                return
            if (wanted is not None and index not in wanted) or (wanted is None and index % step_frames):
                continue
            ok, image = capture.retrieve()
            if not ok:
                raise RuntimeError(f"{path}: frame {index} grabbed but could not be decoded")
            yield Sample(index, capture.get(cv2.CAP_PROP_POS_MSEC) / 1000.0, image)
    finally:
        capture.release()


def _open(path: Path) -> cv2.VideoCapture:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise SystemExit(f"cannot open video {path}")
    capture.set(cv2.CAP_PROP_ORIENTATION_AUTO, 1)
    return capture
