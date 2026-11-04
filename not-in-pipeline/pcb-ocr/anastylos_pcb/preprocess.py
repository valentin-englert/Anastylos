"""Deterministic preprocessing for small silkscreen text."""

from __future__ import annotations

import cv2
import numpy as np

UPSCALE = 2.5


def prepare(crop: np.ndarray) -> dict[str, np.ndarray]:
    """Return normal and inverted grayscale variants, upscaled and equalized."""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    wider = cv2.resize(gray, None, fx=UPSCALE, fy=UPSCALE, interpolation=cv2.INTER_CUBIC)
    flat = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(wider)
    return {"normal": flat, "inverted": 255 - flat}
