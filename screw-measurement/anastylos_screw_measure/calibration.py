"""Spatial calibration for screw measurement.

Accepts validated pixels-per-millimetre metadata or a documented
calibrated reference. Anything invalid degrades to pixel-only output:
millimetre fields stay null and name their reason. Nothing here invents
a scale; without provenance there is no conversion.

File schema (JSON)::

    {"pixels_per_mm": 12.5, "pixels_per_mm_std": 0.05,
     "frame": "surface_a3", "provenance": "rectified 2026-10-26 run 4"}

or, equivalently, a measured reference in the screw plane::

    {"reference": {"length_mm": 20.0, "pixels": 250.0,
                   "label": "check line H, photo 3"},
     "frame": "surface_a3", "provenance": "..."}

Frames reuse the calibration-kit vocabulary (surface_a3, surface_a4,
fixture_front, fixture_back, charuco_board); anything else is recorded
verbatim as "custom:<name>".
"""

from __future__ import annotations

import json
import math
from pathlib import Path

KNOWN_FRAMES = ("surface_a3", "surface_a4", "fixture_front",
                "fixture_back", "charuco_board")
MAX_PPM = 1000.0
CONFLICT_TOLERANCE = 0.01


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def blank(reason: str) -> dict:
    """Pixel-only calibration: millimetre conversion is disabled."""
    return {"valid": False, "pixels_per_mm": None, "pixels_per_mm_std": 0.0,
            "frame": None, "provenance": None, "reasons": [reason]}


def load_calibration(path: str | Path | None) -> dict:
    """Load and validate a calibration file. None means pixel-only."""
    if path is None:
        return blank("no calibration supplied; pixel-only output")
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"calibration file not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return blank(f"calibration file unreadable: {exc}")
    if not isinstance(payload, dict):
        return blank("calibration file must hold a JSON object")
    reasons: list[str] = []
    ppm = _number(payload.get("pixels_per_mm"))
    if "pixels_per_mm" in payload and (ppm is None or ppm <= 0 or ppm > MAX_PPM):
        reasons.append("pixels_per_mm must be a finite number in (0, 1000]")
        ppm = None
    ref_ppm = None
    reference = payload.get("reference")
    if reference is not None:
        if not isinstance(reference, dict):
            reasons.append("reference must be an object with length_mm, pixels, label")
        else:
            length = _number(reference.get("length_mm"))
            pixels = _number(reference.get("pixels"))
            label = reference.get("label")
            if length is None or length <= 0:
                reasons.append("reference.length_mm must be a positive number")
            if pixels is None or pixels <= 0:
                reasons.append("reference.pixels must be a positive number")
            if not isinstance(label, str) or not label.strip():
                reasons.append("reference.label must name the measured object")
            if not reasons and length and pixels:
                ref_ppm = pixels / length
    if ppm is not None and ref_ppm is not None:
        if abs(ppm - ref_ppm) / ppm > CONFLICT_TOLERANCE:
            reasons.append("pixels_per_mm conflicts with the reference length (>1%)")
            ppm = None
    if ppm is None:
        ppm = ref_ppm
    provenance = payload.get("provenance")
    if ppm is not None and (not isinstance(provenance, str) or not provenance.strip()):
        reasons.append("provenance must name the calibration source")
    std = _number(payload.get("pixels_per_mm_std", 0.0))
    if std is None or std < 0:
        reasons.append("pixels_per_mm_std must be a number >= 0")
        std = 0.0
    frame = payload.get("frame", "unspecified")
    if not isinstance(frame, str) or not frame.strip():
        reasons.append("frame must be a non-empty string")
        frame = "unspecified"
    frame = frame.strip()
    if frame not in KNOWN_FRAMES and frame != "unspecified" and not frame.startswith("custom:"):
        frame = f"custom:{frame}"
    if ppm is None or reasons:
        return {"valid": False, "pixels_per_mm": None, "pixels_per_mm_std": float(std),
                "frame": frame, "provenance": None, "reasons": reasons or ["no usable scale"]}
    return {"valid": True, "pixels_per_mm": round(float(ppm), 6),
            "pixels_per_mm_std": float(std), "frame": frame,
            "provenance": provenance.strip(),
            "reference": reference if ref_ppm is not None and "pixels_per_mm" not in payload else None,
            "reasons": []}


def to_millimetres(value_px: float, px_std: float, calibration: dict | None) -> tuple[float | None, list | None, list[str]]:
    """Convert pixels to mm. Returns (value, [lo, hi], reasons); nulls when invalid."""
    calibration = calibration or blank("no calibration supplied; pixel-only output")
    if not calibration.get("valid") or not calibration.get("pixels_per_mm"):
        return None, None, list(calibration.get("reasons") or ["no valid calibration"])
    ppm = float(calibration["pixels_per_mm"])
    ppm_std = float(calibration.get("pixels_per_mm_std") or 0.0)
    value = value_px / ppm
    variance = (px_std / ppm) ** 2 + (value_px * ppm_std / ppm ** 2) ** 2
    half = 2.0 * math.sqrt(variance)
    return (round(value, 3), [round(max(0.0, value - half), 3), round(value + half, 3)], [])
