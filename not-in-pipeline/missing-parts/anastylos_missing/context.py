"""Comparison context: original versus current, declared up front.

A classified comparison needs an explicit context file naming both roles
(original/reference state, current/reassembly state) and their frames::

    {"original": {"role": "original_assembly", "frame": "fixture_back"},
     "current": {"role": "reassembly_state", "frame": "fixture_back"},
     "surface": "light", "transform": null, "params": {}}

Without a context file the pipeline stays neutral: regions are reported
as observed differences only, never as absent or displaced parts.
Frames reuse the calibration-kit vocabulary; anything else is kept as
``custom:<name>``. Frames that differ need a verified transform, else
the comparison is unavailable.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

KNOWN_FRAMES = ("surface_a3", "surface_a4", "fixture_front",
                "fixture_back", "charuco_board")
TRANSFORM_MAX_RMS_MM = 1.0

DEFAULT_PARAMS = {
    "max_shift_px": 25,
    "link_distance_px": 120,
    "min_area_px": 25,
    "diff_threshold": 30.0,
    "ncc_present": 0.85,
    "ncc_contradict": 0.90,
    "search_radius_px": 12,
    "max_present_offset_px": 8,
}


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _frame_name(value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    name = value.strip()
    if name in KNOWN_FRAMES or name.startswith("custom:"):
        return name
    return f"custom:{name}"


def _side(payload, key: str, reasons: list[str]) -> dict:
    side = (payload.get(key) or {}) if isinstance(payload, dict) else {}
    role = side.get("role") if isinstance(side, dict) else None
    if not isinstance(role, str) or not role.strip():
        reasons.append(f"{key}.role must be a non-empty string")
        role = None
    frame = _frame_name(side.get("frame")) if isinstance(side, dict) else None
    if isinstance(side, dict) and "frame" in side and side.get("frame") is not None \
            and _frame_name(side.get("frame")) is None:
        reasons.append(f"{key}.frame must be a non-empty string or null")
    return {"role": role.strip() if role else None, "frame": frame}


def _transform(payload, reasons: list[str]):
    transform = payload.get("transform")
    if transform is None:
        return None
    if not isinstance(transform, dict):
        reasons.append("transform must be an object or null")
        return None
    matrix = transform.get("homography")
    ok = (isinstance(matrix, list) and len(matrix) == 3
          and all(isinstance(row, list) and len(row) == 3
                  and all(_number(v) is not None for v in row) for row in matrix))
    if not ok:
        reasons.append("transform.homography must be a 3x3 finite matrix")
    provenance = transform.get("provenance")
    if not isinstance(provenance, str) or not provenance.strip():
        reasons.append("transform.provenance must name the verification source")
    rms = _number(transform.get("reprojection_rms_mm"))
    if rms is None or rms < 0:
        reasons.append("transform.reprojection_rms_mm must be a number >= 0")
    elif rms > TRANSFORM_MAX_RMS_MM:
        reasons.append(f"transform rms {rms} mm exceeds {TRANSFORM_MAX_RMS_MM} mm")
    for key in ("from_frame", "to_frame"):
        if _frame_name(transform.get(key)) is None:
            reasons.append(f"transform.{key} must name a frame")
    if reasons:
        return None
    return {"homography": [[float(v) for v in row] for row in matrix],
            "from_frame": _frame_name(transform.get("from_frame")),
            "to_frame": _frame_name(transform.get("to_frame")),
            "provenance": provenance.strip(), "reprojection_rms_mm": rms}


def load_context(path: str | Path | None) -> dict:
    """Load and validate a comparison context. None means neutral mode."""
    if path is None:
        return {"declared": False, "original": {"role": None, "frame": None},
                "current": {"role": None, "frame": None}, "surface": "light",
                "transform": None, "params": dict(DEFAULT_PARAMS),
                "reasons": ["no comparison context declared; neutral differences only"]}
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"context file not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read context file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"context file {path} must hold a JSON object")
    reasons: list[str] = []
    original = _side(payload, "original", reasons)
    current = _side(payload, "current", reasons)
    surface = payload.get("surface", "light")
    if surface not in ("light", "dark"):
        reasons.append("surface must be 'light' or 'dark'")
        surface = "light"
    transform = _transform(payload, reasons)
    params = dict(DEFAULT_PARAMS)
    raw_params = payload.get("params", {})
    if not isinstance(raw_params, dict):
        reasons.append("params must be an object")
    else:
        for key, default in DEFAULT_PARAMS.items():
            if key in raw_params:
                value = _number(raw_params[key])
                if value is None or value <= 0:
                    reasons.append(f"params.{key} must be a positive number")
                else:
                    params[key] = value
    if reasons:
        raise ValueError("invalid comparison context: " + "; ".join(reasons))
    frames = {original["frame"], current["frame"]} - {None}
    needs_transform = len(frames) == 2
    if needs_transform and transform is None:
        raise ValueError("invalid comparison context: original and current frames differ "
                         f"({original['frame']} vs {current['frame']}) without a verified transform")
    if transform is not None and not needs_transform:
        raise ValueError("invalid comparison context: transform supplied for a single-frame comparison")
    return {"declared": True, "original": original, "current": current,
            "surface": surface, "transform": transform, "params": params, "reasons": []}
