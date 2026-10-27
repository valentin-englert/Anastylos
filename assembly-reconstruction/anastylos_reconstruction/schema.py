"""Assembly State v1: version constants and output validation. Stdlib only."""

from __future__ import annotations

SCHEMA_VERSION = "assembly-state-v1"
PROJECTOR_NAME = "anastylos-assembly-reconstruction"
PROJECTOR_VERSION = "1.0.0"

STATUSES = ("ok", "ambiguous", "unknown", "unavailable")

# Deterministic exploded-layout constants (layout_px; no physical meaning).
LAYOUT = {
    "scale": 4.0,          # layout px per physical mm of symbol size
    "column_rows": 10,     # stack height before wrapping to a new column
    "margin_x": 24.0,
    "margin_y": 24.0,
    "column_width": 200.0,
    "column_gap": 48.0,
    "row_gap": 28.0,
    "label_band": 64.0,    # vertical space reserved for card labels
}

# Physical-map rendering scale (SVG px per frame mm; stated in the SVG).
PHYSICAL_SCALE = 2.0


class StateError(ValueError):
    """An Assembly State document fails v1 validation."""


def _need(mapping: dict, key: str, what: str):
    if not isinstance(mapping, dict) or key not in mapping:
        raise StateError(f"{what} is missing {key!r}")
    return mapping[key]


def validate_state(state: dict) -> None:
    """Check the structural v1 contract. Raises StateError on violation."""
    if not isinstance(state, dict):
        raise StateError("state must be a JSON object")
    if state.get("schema_version") != SCHEMA_VERSION:
        raise StateError(f"schema_version must be {SCHEMA_VERSION!r}")
    if state.get("status") not in STATUSES:
        raise StateError(f"status must be one of {STATUSES}")
    _need(state, "provenance", "state")
    _need(state, "coordinate_systems", "state")
    components = _need(state, "components", "state")
    if not isinstance(components, list):
        raise StateError("components must be a list")
    for component in components:
        _need(component, "id", "component")
        identity = _need(component, "identity", f"component {component.get('id')}")
        if identity.get("status") != "unknown" or identity.get("label") is not None:
            raise StateError(
                f"component {component.get('id')}: v1 identity must stay unknown with null label")
        physical = _need(component, "physical", f"component {component.get('id')}")
        _need(physical, "source_view", "physical")
        _need(physical, "bbox_mm", "physical")
        _need(physical, "centroid_mm", "physical")
        _need(physical, "evidence", "physical")
        exploded = _need(component, "exploded", f"component {component.get('id')}")
        for key in ("x", "y", "w", "h", "column", "row"):
            _need(exploded, key, "exploded")
        if exploded.get("units") != "layout_px":
            raise StateError("exploded.units must be 'layout_px'")
    _need(state, "relationships", "state")
    _need(state, "missing_evidence", "state")
    _need(state, "warnings", "state")
