"""Calibrated geometry: pixels to millimetres, only on valid metadata.

Conversions apply exclusively when frame_metadata carries a finite,
positive pixels_per_mm inside a sane range. Anything else yields no
millimetre claims and a calibration_error string; pixel verdicts are
unaffected. Millimetre coordinates share the source-frame origin
(top-left) and axis directions.
"""

from __future__ import annotations

import math

MIN_PPM = 0.05
MAX_PPM = 500.0


def convert_geometry(markers: list[dict], body: dict | None,
                     frame_metadata: dict | None
                     ) -> tuple[dict | None, str | None]:
    """Convert source-frame pixels to mm when metadata is valid."""
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
    for marker in markers:
        ccx, ccy = marker["centroid_xy"]
        converted.append(
            {"marker_ref": marker["marker_id"],
             "centroid_mm": [round(ccx / ppm, 3), round(ccy / ppm, 3)]})
    body_mm = None
    if body is not None:
        bcx, bcy = body["center_xy"]
        body_mm = {"center_mm": [round(bcx / ppm, 3), round(bcy / ppm, 3)]}
    return {"pixels_per_mm": ppm, "markers": converted,
            "body": body_mm,
            "note": "mm share the source-frame origin (top-left) and axes"}, None
