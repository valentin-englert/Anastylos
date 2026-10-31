"""Frame source: one exact video frame, registered to the calibration sheet, rectified to sheet millimetres.

Reuses the unchanged custody sheet reader (same harness, kit and view as the custody pipeline). The
rectified image is a top-down view of the sheet plane; part edges that stand above the sheet are shifted by
parallax, so millimetres taken from it are sheet-plane millimetres, not part dimensions.
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "custody-ledger"))
sys.path.insert(0, str(REPO / "change-detection"))

from anastylos_custody.config import load_custody_config  # noqa: E402
from anastylos_custody.sheet import SheetReader  # noqa: E402

CUSTODY_CONFIG = REPO / "custody-ledger" / "custody-config.v15.json"


@dataclass
class RectifiedFrame:
    image: np.ndarray            # BGR, sheet-plane top view
    px_per_mm: float
    origin_mm: tuple[float, float]  # sheet mm of pixel (0, 0)
    homography_image_to_mm: np.ndarray
    provenance: dict

    def mm_to_px(self, points_mm: np.ndarray) -> np.ndarray:
        return (np.asarray(points_mm, float) - self.origin_mm) * self.px_per_mm

    def px_to_mm(self, points_px: np.ndarray) -> np.ndarray:
        return np.asarray(points_px, float) / self.px_per_mm + self.origin_mm


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_frame(video: Path, frame_index: int) -> tuple[np.ndarray, float, float]:
    capture = cv2.VideoCapture(str(video))
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = capture.read()
    finally:
        capture.release()
    if not ok:
        raise SystemExit(f"cannot read frame {frame_index} of {video}")
    return frame, fps, frame_index / fps


def rectify(video: Path, frame_index: int, region_mm: tuple[float, float, float, float], px_per_mm: float,
            video_sha256: str | None = None) -> tuple[np.ndarray, RectifiedFrame]:
    """Return the raw frame and the rectified sheet region ``(x, y, w, h)`` in sheet mm."""
    custody, harness, kit, _ = load_custody_config(CUSTODY_CONFIG)
    reader = SheetReader(harness, kit, custody["view"])
    frame, fps, t_s = read_frame(video, frame_index)
    report, homography, _corners = reader.register(frame)
    if homography is None:
        raise SystemExit(f"sheet registration failed: {report.get('reasons')}")
    x, y, w, h = region_mm
    to_px = np.array([[px_per_mm, 0, -x * px_per_mm], [0, px_per_mm, -y * px_per_mm], [0, 0, 1.0]])
    size = (int(round(w * px_per_mm)), int(round(h * px_per_mm)))
    image = cv2.warpPerspective(frame, to_px @ homography, size, flags=cv2.INTER_CUBIC)
    provenance = {
        "video": str(video), "video_sha256": video_sha256, "frame_index": frame_index, "fps": fps,
        "t_s": round(t_s, 3), "frame_size_px": [frame.shape[1], frame.shape[0]],
        "sheet_view": custody["view"], "custody_config": CUSTODY_CONFIG.name,
        "registration": {key: report.get(key) for key in ("ok", "reasons", "rms_mm", "max_mm", "inliers") if key in report},
        "region_mm": list(region_mm), "px_per_mm": px_per_mm,
    }
    return frame, RectifiedFrame(image, px_per_mm, (x, y), homography, provenance)
