"""Versioned calibration adapter (anastylos-microscopy-calibration/1).

Rules (see documentation/CALIBRATION.md):

* Millimetres only with explicit, operator-supplied calibration evidence.
  No default scale or field of view is assumed.
* A calibration is bound to an image resolution; a mismatch rejects it.
* Both reader spellings carry one physical meaning (image pixels per mm)
  but are NOT interchangeable inside the readers, so this adapter emits the
  exact per-module form: ``pixels_per_mm`` (polarity-reader, washer-spacer,
  screw-measurement file form) and ``px_per_mm`` (pcb-ocr,
  component-fingerprints, connector-matching).
* Anything invalid degrades to "no calibration" with recorded reasons;
  readers then report pixel-only results.
"""

from __future__ import annotations

import math

from anastylos_microscopy import CALIBRATION_SCHEMA

ADAPTER_VERSION = "calibration-adapter/1"
MAX_SCALE = 1000.0
CONFLICT_TOLERANCE = 0.01  # same 1% rule as screw-measurement


def _finite(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def absent(reason: str = "no calibration supplied") -> dict:
    return {"status": "absent", "valid": False, "pixels_per_mm": None,
            "reasons": [reason], "source": None, "adapter_version": ADAPTER_VERSION}


def resolve(document: dict | None, image_resolution: dict, origin: str | None = None) -> dict:
    """Validate a calibration document against the image it is applied to."""
    if document is None:
        return absent()
    reasons: list[str] = []
    if not isinstance(document, dict):
        return {**absent("calibration must be a JSON object"), "status": "invalid"}
    if document.get("schema") not in (None, CALIBRATION_SCHEMA):
        reasons.append(f"unsupported calibration schema {document.get('schema')!r}")
    ppm_a = _finite(document.get("pixels_per_mm"))
    ppm_b = _finite(document.get("px_per_mm"))
    if document.get("pixels_per_mm") is not None and ppm_a is None:
        reasons.append("pixels_per_mm must be a finite number")
    if document.get("px_per_mm") is not None and ppm_b is None:
        reasons.append("px_per_mm must be a finite number")
    reference = document.get("reference")
    ppm_ref = None
    if reference is not None:
        if not isinstance(reference, dict):
            reasons.append("reference must be an object")
        else:
            length = _finite(reference.get("length_mm"))
            pixels = _finite(reference.get("pixels"))
            if not length or not pixels or length <= 0 or pixels <= 0:
                reasons.append("reference needs positive length_mm and pixels")
            else:
                ppm_ref = pixels / length
    values = [v for v in (ppm_a, ppm_b, ppm_ref) if v is not None]
    if not values and not reasons:
        reasons.append("no scale given (pixels_per_mm, px_per_mm or reference)")
    scale = values[0] if values else None
    for other in values[1:]:
        if abs(other - scale) > CONFLICT_TOLERANCE * scale:
            reasons.append(f"scale forms disagree ({scale:.6g} vs {other:.6g} px/mm, >1%)")
            break
    if scale is not None and not (0 < scale <= MAX_SCALE):
        reasons.append(f"scale {scale} outside (0, {MAX_SCALE}] px/mm")
    provenance = document.get("provenance")
    if not isinstance(provenance, str) or not provenance.strip():
        reasons.append("provenance must name the measurement source (e.g. stage micrometer photo)")
    declared = document.get("image_resolution")
    if not (isinstance(declared, list) and len(declared) == 2
            and all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in declared)):
        reasons.append("image_resolution [width, height] is required: a scale is only valid "
                       "for the capture resolution it was measured at")
    elif declared != [image_resolution["width"], image_resolution["height"]]:
        reasons.append(f"calibration measured at {declared[0]}x{declared[1]} but image is "
                       f"{image_resolution['width']}x{image_resolution['height']}")
    std = _finite(document.get("pixels_per_mm_std"))
    if document.get("pixels_per_mm_std") is not None and (std is None or std < 0):
        reasons.append("pixels_per_mm_std must be a finite non-negative number")
    origin_mm = document.get("origin_frame_mm")
    if origin_mm is not None and not (isinstance(origin_mm, list) and len(origin_mm) == 2
                                      and all(_finite(v) is not None for v in origin_mm)):
        reasons.append("origin_frame_mm must be [x0, y0]")
    summary = {
        "schema": CALIBRATION_SCHEMA,
        "adapter_version": ADAPTER_VERSION,
        "source": origin,
        "provenance": provenance if isinstance(provenance, str) else None,
        "optical_profile": document.get("optical_profile"),
        "frame": document.get("frame"),
        "view": document.get("view"),
        "measured_utc": document.get("measured_utc"),
        "image_resolution": declared,
        "document": document,
    }
    if reasons:
        return {**summary, "status": "invalid", "valid": False, "pixels_per_mm": None,
                "reasons": reasons}
    return {**summary, "status": "valid", "valid": True, "pixels_per_mm": round(scale, 9),
            "pixels_per_mm_std": std if std is not None else 0.0,
            "origin_frame_mm": [float(v) for v in origin_mm] if origin_mm else None,
            "reasons": []}


def for_reader(calibration: dict, spelling: str) -> tuple[dict | None, dict]:
    """Return (reader-native calibration object or None, adaptation note)."""
    if not calibration.get("valid"):
        return None, {"applied": False, "spelling": spelling,
                      "reason": "; ".join(calibration.get("reasons") or ["no valid calibration"])}
    ppm = calibration["pixels_per_mm"]
    note = {"applied": True, "spelling": spelling, "value": ppm,
            "adapter_version": ADAPTER_VERSION}
    if spelling == "pixels_per_mm":
        return {"pixels_per_mm": ppm}, note
    if spelling == "px_per_mm":
        origin = calibration.get("origin_frame_mm")
        view = calibration.get("view") or calibration.get("frame") or "microscope_image"
        if origin is None:
            note["origin_defaulted"] = "origin_frame_mm defaulted to [0, 0] (image top-left)"
            origin = [0.0, 0.0]
        return {"px_per_mm": ppm, "view": str(view), "origin_frame_mm": origin}, note
    if spelling == "screw_measurement_file":
        frame = calibration.get("frame") or "custom:microscope"
        return {"pixels_per_mm": ppm, "pixels_per_mm_std": calibration.get("pixels_per_mm_std", 0.0),
                "frame": frame, "provenance": calibration["provenance"]}, note
    raise ValueError(f"unknown calibration spelling {spelling!r}")
