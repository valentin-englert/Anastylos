"""Tray-counter configuration: named millimetre regions plus thresholds."""

from __future__ import annotations

import json
import math
from pathlib import Path


class ConfigError(ValueError):
    """The tray configuration is explicit, so mistakes fail loudly."""


ZONE_KEYS = ("id", "frame", "rect_frame_mm")
PARAM_DEFAULTS = {"min_overlap": 0.5, "delta_threshold": 8.0, "surface": "light"}


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _rect(rect, what: str) -> list[float]:
    if not isinstance(rect, list) or len(rect) != 4 or not all(_is_number(v) for v in rect):
        raise ConfigError(f"{what} needs 'rect_frame_mm' as [x, y, w, h] in millimetres")
    if rect[2] <= 0 or rect[3] <= 0:
        raise ConfigError(f"{what} needs positive width and height, got {rect[2:]}")
    return [float(v) for v in rect]


def _zone(raw: dict, what: str, need_id: bool) -> dict:
    if not isinstance(raw, dict):
        raise ConfigError(f"{what} must be an object")
    unknown = set(raw) - set(ZONE_KEYS)
    if unknown:
        raise ConfigError(f"{what} has unknown keys: {sorted(unknown)}")
    zone = {"frame": raw.get("frame"), "rect_frame_mm": _rect(raw.get("rect_frame_mm"), what)}
    if not isinstance(zone["frame"], str) or not zone["frame"].strip():
        raise ConfigError(f"{what} needs a non-empty 'frame'")
    if need_id:
        if not isinstance(raw.get("id"), str) or not raw["id"].strip():
            raise ConfigError(f"{what} needs a non-empty string 'id'")
        zone["id"] = raw["id"]
    return zone


def load_config(path: str | Path) -> dict:
    """Load and validate a tray-counter config file."""
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read config {path}: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("trays"), list) or not raw["trays"]:
        raise ConfigError("config needs a non-empty 'trays' list")
    unknown = set(raw) - {"trays", "device_area", "params"}
    if unknown:
        raise ConfigError(f"config has unknown keys: {sorted(unknown)}")
    trays = [_zone(item, f"trays[{index}]", True) for index, item in enumerate(raw["trays"])]
    ids = [tray["id"] for tray in trays]
    if len(set(ids)) != len(ids):
        raise ConfigError(f"tray ids must be unique, got {ids}")
    config: dict = {"trays": trays, "device_area": None, "params": dict(PARAM_DEFAULTS)}
    if raw.get("device_area") is not None:
        config["device_area"] = _zone(raw["device_area"], "device_area", False)
    params = raw.get("params") or {}
    if not isinstance(params, dict):
        raise ConfigError("'params' must be an object")
    unknown_params = set(params) - set(PARAM_DEFAULTS)
    if unknown_params:
        raise ConfigError(f"unknown params: {sorted(unknown_params)}")
    config["params"].update(params)
    overlap, threshold, surface = (config["params"][key] for key in ("min_overlap", "delta_threshold", "surface"))
    if not _is_number(overlap) or not 0.0 < overlap <= 1.0:
        raise ConfigError("'min_overlap' must be in (0, 1]")
    if not _is_number(threshold) or threshold <= 0:
        raise ConfigError("'delta_threshold' must be positive")
    if surface not in ("light", "dark"):
        raise ConfigError("'surface' must be 'light' or 'dark'")
    return config
