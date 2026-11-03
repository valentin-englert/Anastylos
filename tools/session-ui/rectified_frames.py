"""Sheet-registered, rectified frames of one video, decoded in order (OpenCV; used by the worker subprocesses only).

``source.rectify`` opens the video and seeks for every frame; on a phone video a seek costs about 0.16 s while registration
costs 0.02 s, so a clip of 30 frames took 6 s. Here one capture stays open: frames close to the last one are reached by
``grab()`` (1.5 ms per skipped frame) and only a far or backward jump seeks. The registration and the warp are the ones of
``source.rectify`` (same custody sheet reader and configuration), so the images are the same; a frame whose registration
fails, or has rms above ``MAX_RMS_MM``, is reported with its reason and never repaired.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "drawing-proof"))
import source  # noqa: E402

MAX_RMS_MM = 0.5
GRAB_LIMIT = 60  # skipping this many frames with grab() is cheaper than one seek


class SheetFrames:
    def __init__(self, video: Path, px_per_mm: float, region_mm: tuple[float, float, float, float]):
        from anastylos_custody.config import load_custody_config
        from anastylos_custody.sheet import SheetReader
        custody, harness, kit, _ = load_custody_config(source.CUSTODY_CONFIG)
        self.reader = SheetReader(harness, kit, custody["view"])
        self.capture = cv2.VideoCapture(str(video))
        self.fps = self.capture.get(cv2.CAP_PROP_FPS)
        self.total = int(self.capture.get(cv2.CAP_PROP_FRAME_COUNT))
        self.px_per_mm, self.region_mm = px_per_mm, region_mm
        self.position: int | None = None  # index of the next frame the capture would deliver

    def close(self) -> None:
        self.capture.release()

    def __enter__(self) -> "SheetFrames":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _frame(self, index: int):
        ahead = None if self.position is None else index - self.position
        if ahead is None or ahead < 0 or ahead > GRAB_LIMIT:
            self.capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        else:
            for _ in range(ahead):
                self.capture.grab()
        ok, frame = self.capture.read()
        self.position = index + 1 if ok else None
        return frame if ok else None

    def rectified(self, index: int) -> tuple[np.ndarray | None, str | None]:
        """``(image, None)`` for a registered frame, else ``(None, reason)``."""
        frame = self._frame(index)
        if frame is None:
            return None, f"cannot read frame {index}"
        report, homography, _corners = self.reader.register(frame)
        if homography is None:
            return None, f"sheet registration failed: {report.get('reasons')}"[:200]
        rms = report.get("rms_mm")
        if isinstance(rms, (int, float)) and rms > MAX_RMS_MM:
            return None, f"registration rms {rms:.2f} mm is above {MAX_RMS_MM} mm"
        x, y, w, h = self.region_mm
        ppm = self.px_per_mm
        to_px = np.array([[ppm, 0, -x * ppm], [0, ppm, -y * ppm], [0, 0, 1.0]])
        size = (int(round(w * ppm)), int(round(h * ppm)))
        return cv2.warpPerspective(frame, to_px @ homography, size, flags=cv2.INTER_CUBIC), None
