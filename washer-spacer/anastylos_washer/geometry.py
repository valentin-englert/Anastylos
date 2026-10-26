"""Calibrated dimensions: pixels to millimetres, only on valid metadata.

Same convention as the sibling recognizers: conversions apply
exclusively when frame_metadata carries a finite, positive
pixels_per_mm inside a sane range. Anything else yields no
millimetre claims and a calibration_error string; pixel verdicts
are unaffected. Millimetre coordinates share the source-frame
origin (top-left) and axis directions.
"""

from __future__ import annotations

import math

MIN_PPM = 0.05
MAX_PPM = 500.0

DIM_KEYS = ("outer_d", "inner_d", "length_px", "thickness_px",
            "across_corners", "across_flats")


def convert_observations(observations: list[dict],
                         frame_metadata: dict | None
                         ) -> tuple[dict | None, str | None]:
    """Convert observation dimensions to mm when metadata is valid."""
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
    ppm = float(ppm)
    converted = []
    for obs in observations:
        dims = {}
        for key in DIM_KEYS:
            entry = obs["geometry"].get(key)
            if isinstance(entry, dict) and "value_px" in entry:
                dims[key] = {
                    "value_mm": round(entry["value_px"] / ppm, 3),
                    "uncertainty_mm": round(
                        entry["uncertainty_px"] / ppm, 3)}
        converted.append({"observation_ref": obs["observation_id"],
                          "dimensions_mm": dims})
    return {"pixels_per_mm": ppm, "observations": converted,
            "note": "mm share the source-frame origin and axes; sizes "
                    "are measured, never mapped to standards"}, None
