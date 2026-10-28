"""Per-frame image signals on the rectified sheet: normalized gray, motion, fresh skin."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class FrameSignals:
    t_s: float
    gray: np.ndarray          # illumination-normalized, float32
    motion: np.ndarray        # bool: changed since the previous sample
    fresh_skin: np.ndarray    # bool: skin-coloured and not static for long


class SignalTracker:
    """Turns consecutive rectified colour frames into ``FrameSignals``.

    Skin-coloured pixels that stay still for ``static_after_s`` (a brown PCB,
    a red button) stop counting as skin, so only hands that move or just
    arrived register as intrusions.
    """

    def __init__(self, signal: dict, skin: dict, px_per_mm: float):
        self.normalize_sigma = float(signal["normalize_sigma_mm"]) * px_per_mm
        self.blur_sigma = float(signal["blur_sigma_mm"]) * px_per_mm
        self.motion_threshold = float(signal["motion_threshold"])
        self.skin_low = np.array(skin["ycrcb_min"], np.uint8)
        self.skin_high = np.array(skin["ycrcb_max"], np.uint8)
        self.static_after_s = float(skin["static_after_s"])
        self._previous: np.ndarray | None = None
        self._previous_t: float | None = None
        self._still_skin_s: np.ndarray | None = None

    def update(self, t_s: float, rectified_bgr: np.ndarray) -> FrameSignals:
        gray = normalized_gray(rectified_bgr, self.normalize_sigma, self.blur_sigma)
        if self._previous is None:
            motion = np.zeros(gray.shape, bool)
            dt = 0.0
        else:
            motion = np.abs(gray - self._previous) > self.motion_threshold
            dt = t_s - self._previous_t
        skin = cv2.inRange(cv2.cvtColor(rectified_bgr, cv2.COLOR_BGR2YCrCb), self.skin_low, self.skin_high) > 0
        moving_near = cv2.dilate(motion.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        if self._still_skin_s is None:
            self._still_skin_s = np.zeros(gray.shape, np.float32)
        still = skin & ~moving_near
        self._still_skin_s = np.where(still, self._still_skin_s + dt, 0.0).astype(np.float32)
        self._previous, self._previous_t = gray, t_s
        return FrameSignals(t_s, gray, motion, skin & (self._still_skin_s < self.static_after_s))


def normalized_gray(bgr: np.ndarray, normalize_sigma_px: float, blur_sigma_px: float) -> np.ndarray:
    """Local-mean division, as change-detection's ``local_mean_division``, then a light blur."""
    pixels = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    local = cv2.GaussianBlur(pixels, (0, 0), normalize_sigma_px)
    out = np.clip(pixels / (local + 1.0) * 128.0, 0, 255).astype(np.float32)
    return cv2.GaussianBlur(out, (0, 0), blur_sigma_px) if blur_sigma_px > 0 else out
