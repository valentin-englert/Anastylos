import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from returns import check_events_doc, plan, read_memory, verify  # noqa: E402

SITES = [
    {"id": "W-GREEN", "rect_mm": [236.1, 136.5, 13.0, 13.0]},
    {"id": "W-YELLOW", "rect_mm": [249.5, 125.9, 13.0, 13.0]},
    {"id": "W-RED", "rect_mm": [263.1, 136.6, 13.0, 13.0]},
]


def _memory_file(tmp: str) -> Path:
    lines = []
    for index, (centroid, cell) in enumerate((([256.0, 132.7], 6), ([241.7, 144.1], 9), ([268.6, 144.2], 4)), 1):
        lines.append({"type": "observation", "id": f"obs-{index:04d}", "centroid_mm": centroid, "supersedes": None})
        lines.append({"type": "tray_link", "id": f"link-{index:04d}", "observation_id": f"obs-{index:04d}",
                      "tray": {"number": cell}, "supersedes": None})
    path = Path(tmp) / "memory.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


def _event(event_id, zone, kind, t0, t1):
    zone_kind = "tray_cell" if zone.startswith("cell-") else "site"
    return {"id": event_id, "zone": zone, "zone_kind": zone_kind, "kind": kind, "t_before_s": t0, "t_after_s": t1}


def _doc(events, gaps=()):
    return {"events": events, "sampling": {"gaps_over_max": list(gaps)}}


class ReassemblyReturnsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.steps = plan(read_memory(_memory_file(self.tmp.name)), SITES)

    def tearDown(self):
        self.tmp.cleanup()

    def test_plan_is_reverse_removal_order_with_home_wells(self):
        self.assertEqual([(s["cell"], s["home_well"]) for s in self.steps],
                         [("cell-4", "W-RED"), ("cell-9", "W-GREEN"), ("cell-6", "W-YELLOW")])

    def test_correct_returns_are_consistent(self):
        result = verify(_doc([
            _event("E-1", "cell-4", "vacated", 10, 12), _event("E-2", "W-RED", "site_filled", 11, 14),
            _event("E-3", "cell-9", "vacated", 20, 22), _event("E-4", "W-GREEN", "site_filled", 21, 24),
        ]), self.steps)
        self.assertEqual([r["memory_check"] for r in result["returns"]], ["consistent_with_memory"] * 2)
        self.assertEqual(result["wells_without_return"], ["W-YELLOW"])

    def test_wrong_well_is_flagged_then_correction_is_consistent(self):
        result = verify(_doc([
            _event("E-1", "cell-4", "vacated", 10, 12), _event("E-2", "W-GREEN", "site_filled", 11, 14),
            _event("E-3", "W-GREEN", "site_emptied", 20, 22), _event("E-4", "W-RED", "site_filled", 21, 24),
        ]), self.steps)
        self.assertEqual([r["memory_check"] for r in result["returns"]], ["MISMATCH_with_memory", "consistent_with_memory"])

    def test_part_put_back_into_its_cell_is_not_a_mismatch(self):
        result = verify(_doc([
            _event("E-1", "cell-6", "vacated", 10, 12), _event("E-2", "cell-6", "appeared", 13, 15),
        ]), self.steps)
        self.assertEqual(result["returns"][0]["memory_check"], "back_in_a_cell")

    def test_overlapping_movements_stay_ambiguous(self):
        result = verify(_doc([
            _event("E-1", "cell-4", "vacated", 10, 12), _event("E-2", "cell-9", "vacated", 10, 12),
            _event("E-3", "W-RED", "site_filled", 11, 14),
        ]), self.steps)
        self.assertEqual(result["returns"][0]["status"], "ambiguous")

    def test_events_without_v1_1_declared_occupancy_are_refused(self):
        with self.assertRaises(SystemExit):
            check_events_doc({"provenance": {}, "zones": []})
        with self.assertRaises(SystemExit):
            check_events_doc({"provenance": {"observer_version": "1.1.0"}, "zones": [{"zone": "cell-4"}]})
        check_events_doc({"provenance": {"observer_version": "1.1.0"}, "zones": [{"zone": "cell-4", "initially": "occupied"}]})

    def test_arrival_without_departure_is_unattributed(self):
        result = verify(_doc([_event("E-1", "W-RED", "site_filled", 11, 14)]), self.steps)
        self.assertEqual(result["returns"][0]["status"], "unattributed")


if __name__ == "__main__":
    unittest.main()
