"""Calibrated cable geometry: pixels to millimetres, only on valid metadata.

Same convention as the sibling recognizers: conversions apply
exclusively when frame_metadata carries a finite, positive
pixels_per_mm inside a sane range. Anything else yields no
millimetre claims and a calibration_error string; pixel verdicts
are unaffected.
"""

from __future__ import annotations

import math

MIN_PPM = 0.05
MAX_PPM = 500.0


def check_metadata(frame_metadata: dict | None) -> tuple[float | None, str | None]:
    """Validate frame metadata. Returns (pixels_per_mm, error)."""
    if frame_metadata is None:
        return None, None
    if not isinstance(frame_metadata, dict):
        return None, "frame_metadata must be an object"
    ppm = frame_metadata.get("pixels_per_mm")
    if isinstance(ppm, bool) or not isinstance(ppm, (int, float)) \
            or not math.isfinite(ppm):
        return None, "pixels_per_mm must be a finite number"
    if not MIN_PPM <= ppm <= MAX_PPM:
        return None, (f"pixels_per_mm {ppm} outside "
                      f"[{MIN_PPM}, {MAX_PPM}]")
    return float(ppm), None


def convert_cables(cables: list[dict], frame_metadata: dict | None
                   ) -> tuple[dict | None, str | None]:
    """Convert cable polylines and widths to mm when metadata is valid."""
    ppm, error = check_metadata(frame_metadata)
    if error is not None or ppm is None:
        return None, error
    converted = []
    for cable in cables:
        branches = []
        for branch in cable["branches"]:
            branches.append(
                {"polyline_mm": [[round(px / ppm, 3), round(py / ppm, 3)]
                                 for px, py in branch["polyline_xy"]],
                 "width_mm": round(branch["width_px"] / ppm, 3)})
        converted.append({"cable_ref": cable["cable_id"],
                          "branches_mm": branches})
    return {"pixels_per_mm": ppm, "cables": converted,
            "note": "mm share the source-frame origin and axes"}, None
