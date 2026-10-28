"""Watched zones: tray cells from the calibration kit, device sites from an inventory."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path

INVENTORY_SCHEMA = "custody-site-inventory@1"


@dataclass(frozen=True)
class Zone:
    """A rectangle on the sheet plane (frame mm) that is watched for content changes.

    ``kind`` is ``tray_cell`` (fixed on the sheet) or ``site`` (a position on
    a resting device, valid only for the pose it had at ``valid_from_s``).
    """

    id: str
    kind: str
    rect_mm: tuple[float, float, float, float]
    valid_from_s: float = 0.0
    cell_number: int | None = None
    excluded_from_anchor: tuple[tuple[float, float, float, float], ...] = field(default=())
    # Declared occupancy at the zone's first reference; None keeps the v1.0
    # convention (tray cells start empty, inventoried sites start occupied).
    initially_occupied: bool | None = None


def grow(rect_mm, by_mm: float) -> list[float]:
    x, y, w, h = rect_mm
    return [x - by_mm, y - by_mm, w + 2 * by_mm, h + 2 * by_mm]


def tray_cells(kit_zones: list[dict]) -> list[Zone]:
    cells = [zone for zone in kit_zones if zone["type"] == "tray_cell"]
    return [Zone(f"cell-{zone['number']}", "tray_cell", tuple(zone["rect_frame_mm"]), cell_number=int(zone["number"])) for zone in cells]


def load_inventory(path: Path, view: str) -> tuple[list[Zone], dict]:
    inventory = json.loads(Path(path).read_text(encoding="utf-8"))
    if inventory.get("schema") != INVENTORY_SCHEMA:
        raise SystemExit(f"{path}: not a {INVENTORY_SCHEMA} file")
    if inventory.get("view") != view:
        raise SystemExit(f"{path}: inventory view {inventory.get('view')!r} does not match {view!r}")
    sites = inventory.get("sites") or []
    ids = [site["id"] for site in sites]
    if len(set(ids)) != len(ids):
        raise SystemExit(f"{path}: duplicate site ids")
    start = float(inventory["inventory_t_s"])
    zones = []
    for site in sites:
        others = tuple(tuple(other["rect_mm"]) for other in sites if other["id"] != site["id"])
        zones.append(Zone(site["id"], "site", tuple(site["rect_mm"]), valid_from_s=start, excluded_from_anchor=others,
                          initially_occupied=_declared_occupancy(path, site["id"], site.get("initially"))))
    return zones, inventory


def apply_tray_occupancy(cells: list[Zone], inventory: dict | None, path: Path | None) -> list[Zone]:
    """Set the declared start occupancy of tray cells from the inventory's optional ``tray_initially`` map."""
    declared = (inventory or {}).get("tray_initially") or {}
    unknown = sorted(set(declared) - {cell.id for cell in cells})
    if unknown:
        raise SystemExit(f"{path}: tray_initially names unknown cells {unknown}")
    return [replace(cell, initially_occupied=_declared_occupancy(path, cell.id, declared.get(cell.id))) for cell in cells]


def _declared_occupancy(path, zone_id: str, value: str | None) -> bool | None:
    if value is None:
        return None
    if value not in ("occupied", "empty"):
        raise SystemExit(f"{path}: {zone_id}: initially must be 'occupied' or 'empty', not {value!r}")
    return value == "occupied"
