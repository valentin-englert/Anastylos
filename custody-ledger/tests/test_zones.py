"""Inventory occupancy declarations (observer v1.1).

Run from the custody-ledger directory:

    ..\.venv\Scripts\python -m unittest discover -s tests -v
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from anastylos_custody.zones import Zone, apply_tray_occupancy, load_inventory  # noqa: E402

CELLS = [Zone("cell-1", "tray_cell", (318, 50, 26, 26), cell_number=1), Zone("cell-4", "tray_cell", (318, 137, 26, 26), cell_number=4)]


def _inventory(tmp, **extra):
    doc = {"schema": "custody-site-inventory@1", "view": "surface_a3", "inventory_t_s": 0.0,
           "sites": [{"id": "W-RED", "rect_mm": [263.1, 136.6, 13, 13], "initially": "empty"},
                     {"id": "W-BLUE", "rect_mm": [249.6, 147.1, 13, 13]}], **extra}
    path = Path(tmp) / "inventory.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


class InventoryOccupancyTests(unittest.TestCase):
    def test_site_declarations_are_read_and_default_to_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            sites, _ = load_inventory(_inventory(tmp), "surface_a3")
        self.assertEqual({site.id: site.initially_occupied for site in sites}, {"W-RED": False, "W-BLUE": None})

    def test_tray_declarations_apply_only_to_named_cells(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _inventory(tmp, tray_initially={"cell-4": "occupied"})
            _, doc = load_inventory(path, "surface_a3")
            cells = apply_tray_occupancy(CELLS, doc, path)
        self.assertEqual({cell.id: cell.initially_occupied for cell in cells}, {"cell-1": None, "cell-4": True})

    def test_site_may_carry_its_own_start_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inventory.json"
            path.write_text(json.dumps({"schema": "custody-site-inventory@1", "view": "surface_a3", "inventory_t_s": 0.0,
                                        "sites": [{"id": "S1", "rect_mm": [1, 1, 5, 5]},
                                                  {"id": "S2", "rect_mm": [9, 1, 5, 5], "valid_from_s": 20.5}]}), encoding="utf-8")
            sites, _ = load_inventory(path, "surface_a3")
        self.assertEqual({s.id: s.valid_from_s for s in sites}, {"S1": 0.0, "S2": 20.5})

    def test_unknown_cell_or_value_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _inventory(tmp, tray_initially={"cell-99": "occupied"})
            _, doc = load_inventory(path, "surface_a3")
            with self.assertRaises(SystemExit):
                apply_tray_occupancy(CELLS, doc, path)
            with self.assertRaises(SystemExit):
                apply_tray_occupancy(CELLS, {"tray_initially": {"cell-1": "full"}}, path)


if __name__ == "__main__":
    unittest.main()
