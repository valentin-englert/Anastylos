"""Linker 1.3: one removal event for a large part that uncovers several close sites at once.

Run from the custody-ledger directory:

    ..\\.venv\\Scripts\\python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from anastylos_custody.linker import group_removals, link  # noqa: E402


def events_doc(events):
    return {"events": events, "zones": [], "sampling": {"gaps_over_max": []}}


def removal(event_id, site, before, after):
    return {"id": event_id, "zone": site, "zone_kind": "site", "kind": "site_changed", "t_before_s": before, "t_after_s": after}


def deposit(event_id, cell, before, after):
    return {"id": event_id, "zone": cell, "zone_kind": "tray_cell", "kind": "appeared", "t_before_s": before, "t_after_s": after}


RECTS = {
    "F1": [100, 100, 10, 10], "F2": [111, 100, 10, 10], "F3": [100, 111, 10, 10],  # fragments of one large part
    "FAR": [200, 100, 10, 10],                                                      # a distant site
}


class RemovalGroupingTests(unittest.TestCase):
    def test_fragments_of_one_large_removal_become_one_event_and_link(self):
        events = [removal("E-1", "F1", 10, 12), removal("E-2", "F2", 10, 12.1), removal("E-3", "F3", 10.1, 12),
                  deposit("E-4", "cell-1", 13, 15)]
        doc = link(events_doc(events), site_rects=RECTS)
        proposal = doc["proposals"][0]
        self.assertEqual(proposal["status"], "candidate_temporal_exclusive")
        self.assertEqual(proposal["removal"]["sites"], ["F1", "F2", "F3"])
        self.assertEqual(doc["counts"]["automatic_links"], 1)

    def test_without_geometry_nothing_is_grouped(self):
        events = [removal("E-1", "F1", 10, 12), removal("E-2", "F2", 10, 12), deposit("E-4", "cell-1", 13, 15)]
        self.assertEqual(link(events_doc(events))["proposals"][0]["status"], "ambiguous")

    def test_same_time_alone_never_groups_distant_sites(self):
        events = [removal("E-1", "F1", 10, 12), removal("E-2", "FAR", 10, 12), deposit("E-4", "cell-1", 13, 15)]
        doc = link(events_doc(events), site_rects=RECTS)
        self.assertEqual(doc["proposals"][0]["status"], "ambiguous")

    def test_close_sites_in_different_intervals_stay_separate(self):
        grouped = group_removals([{"event": "E-1", "site": "F1", "t_before_s": 10, "t_after_s": 12, "basis": "observed"},
                                  {"event": "E-2", "site": "F2", "t_before_s": 20, "t_after_s": 22, "basis": "observed"}], RECTS)
        self.assertEqual(len(grouped), 2)

    def test_a_site_that_changes_twice_gives_two_removals(self):
        events = [removal("E-1", "F1", 10, 12), deposit("E-2", "cell-1", 13, 15),
                  removal("E-3", "F1", 20, 22), deposit("E-4", "cell-2", 23, 25)]
        doc = link(events_doc(events), site_rects=RECTS)
        self.assertEqual([p["status"] for p in doc["proposals"]], ["candidate_temporal_exclusive"] * 2)
        self.assertEqual([p["removal"]["event"] for p in doc["proposals"]], ["E-1", "E-3"])

    def test_declared_removals_are_never_grouped(self):
        grouped = group_removals([{"event": "E-1", "site": "F1", "t_before_s": 10, "t_after_s": 12, "basis": "observed"},
                                  {"event": "X-1", "site": "F2", "t_before_s": 10, "t_after_s": 12, "basis": "operator_declared"}], RECTS)
        self.assertEqual(len(grouped), 2)

    def test_declared_outside_deposit_naming_one_fragment_closes_the_whole_event(self):
        events = [removal("E-1", "F1", 10, 12), removal("E-2", "F2", 10, 12),
                  removal("E-5", "FAR", 20, 22), deposit("E-6", "cell-1", 23, 25)]
        externals = {"schema": "custody-declared-deposits@1", "deposits": [
            {"id": "D-1", "location": "outside", "t_s": 15, "declared_by": "owner", "evidence": "witness video",
             "source": {"site": "F2"}}]}
        doc = link(events_doc(events), declared_deposits=externals, site_rects=RECTS)
        self.assertEqual(doc["declared_external_deposits"][0]["status"], "closes_removal")
        self.assertEqual(doc["proposals"][0]["status"], "candidate_temporal_exclusive")
        self.assertEqual(doc["proposals"][0]["autonomy"], "operator_assisted")


if __name__ == "__main__":
    unittest.main()
