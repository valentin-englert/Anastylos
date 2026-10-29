"""Per-frame image signals on the rectified sheet: normalized gray, motion, colour-free hand mask."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from anastylos_custody.hands import HandTracker


@dataclass
class FrameSignals:
    t_s: float
    gray: np.ndarray          # illumination-normalized, float32
    motion: np.ndarray        # bool: changed since the previous sample
    hand: np.ndarray          # bool: arms and hands reaching in across the sheet border (hands.py, never colour)
    saturation: np.ndarray | None = None  # float32 HSV saturation 0-255 (v1.4, colour cue for tray cells)
    edges: np.ndarray | None = None       # bool Canny edges of the raw grey image (v1.4, structure cue)


class SignalTracker:
    """Turns consecutive rectified colour frames into ``FrameSignals``.

    Hands are found without colour (requirement H1): the hand mask comes from
    ``HandTracker`` over the whole rectified sheet, so skin tone, gloves and
    skin-coloured parts make no difference.
    """

    def __init__(self, signal: dict, hand: dict, px_per_mm: float):
        self.normalize_sigma = float(signal["normalize_sigma_mm"]) * px_per_mm
        self.blur_sigma = float(signal["blur_sigma_mm"]) * px_per_mm
        self.motion_threshold = float(signal["motion_threshold"])
        self.hand_params = {"change_threshold": float(signal["change_threshold"]), **hand.get("tracker", {})}
        # A forearm can fill any normalization window and is flattened to paper grey, cutting the hand off the border;
        # 0 compares raw channels instead.
        self.hand_normalize_sigma = float(hand.get("normalize_sigma_mm", signal["normalize_sigma_mm"])) * px_per_mm
        self.hands: HandTracker | None = None
        self._previous: np.ndarray | None = None

    def update(self, t_s: float, rectified_bgr: np.ndarray, visible: np.ndarray | None = None) -> FrameSignals:
        gray = normalized_gray(rectified_bgr, self.normalize_sigma, self.blur_sigma)
        if self._previous is None:
            motion = np.zeros(gray.shape, bool)
            height, width = gray.shape
            self.hands = HandTracker(gray.shape, (0, 0, width, height), self.hand_params)
        else:
            motion = np.abs(gray - self._previous) > self.motion_threshold
        self._previous = gray
        hand = self.hands.update(t_s, normalized_channels(rectified_bgr, self.hand_normalize_sigma, self.blur_sigma), motion, visible)
        saturation = cv2.cvtColor(rectified_bgr, cv2.COLOR_BGR2HSV)[..., 1].astype(np.float32)
        edges = cv2.Canny(cv2.cvtColor(rectified_bgr, cv2.COLOR_BGR2GRAY), 40, 100) > 0
        return FrameSignals(t_s, gray, motion, hand, saturation, edges)


def normalized_channels(bgr: np.ndarray, normalize_sigma_px: float, blur_sigma_px: float) -> np.ndarray:
    """``normalized_gray`` per colour channel, for appearance changes that grey alone misses.

    The local mean is taken on a shrunken image so that wide windows (whole forearms) stay fast.
    """
    pixels = bgr.astype(np.float32)
    if normalize_sigma_px <= 0:   # raw channels: the capture standard fixes exposure and white balance
        return cv2.GaussianBlur(pixels, (0, 0), blur_sigma_px) if blur_sigma_px > 0 else pixels
    height, width = pixels.shape[:2]
    shrink = max(1, int(normalize_sigma_px // 4))
    small = cv2.resize(pixels, (max(1, width // shrink), max(1, height // shrink)), interpolation=cv2.INTER_AREA)
    local = cv2.resize(cv2.GaussianBlur(small, (0, 0), normalize_sigma_px / shrink), (width, height), interpolation=cv2.INTER_LINEAR)
    out = np.clip(pixels / (local + 1.0) * 128.0, 0, 255).astype(np.float32)
    return cv2.GaussianBlur(out, (0, 0), blur_sigma_px) if blur_sigma_px > 0 else out


def normalized_gray(bgr: np.ndarray, normalize_sigma_px: float, blur_sigma_px: float) -> np.ndarray:
    """Local-mean division, as change-detection's ``local_mean_division``, then a light blur."""
    pixels = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    local = cv2.GaussianBlur(pixels, (0, 0), normalize_sigma_px)
    out = np.clip(pixels / (local + 1.0) * 128.0, 0, 255).astype(np.float32)
    return cv2.GaussianBlur(out, (0, 0), blur_sigma_px) if blur_sigma_px > 0 else out
