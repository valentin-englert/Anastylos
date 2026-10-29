"""Custody-linker rules: exclusivity is a hypothesis, anything hidden makes it ambiguous. Needs the calibration virtualenv.

Run from the custody-ledger directory:

    ..\.venv\Scripts\python -m unittest discover -s tests -v
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import unittest

from anastylos_custody.linker import link


def events_doc(events, zones=(), gaps=()):
    return {"events": events, "zones": list(zones), "sampling": {"gaps_over_max": list(gaps)}}


def removal(event_id, site, before, after):
    return {"id": event_id, "zone": site, "zone_kind": "site", "kind": "site_changed", "t_before_s": before, "t_after_s": after}


def deposit(event_id, cell, before, after, kind="appeared"):
    return {"id": event_id, "zone": cell, "zone_kind": "tray_cell", "kind": kind, "t_before_s": before, "t_after_s": after}


class LinkerTests(unittest.TestCase):
    def test_single_pending_removal_is_only_a_candidate_hypothesis(self):
        doc = link(events_doc([removal("E-1", "S-a", 10, 12), deposit("E-2", "cell-3", 12, 15)]))
        proposal = doc["proposals"][0]
        self.assertEqual(proposal["status"], "candidate_temporal_exclusive")
        self.assertTrue(proposal["requires_operator_confirmation"])
        self.assertEqual(proposal["removal"]["event"], "E-1")

    def test_two_pending_removals_are_ambiguous(self):
        doc = link(events_doc([removal("E-1", "S-a", 10, 12), removal("E-2", "S-b", 11, 13), deposit("E-3", "cell-3", 13, 16)]))
        self.assertEqual(doc["proposals"][0]["status"], "ambiguous")
        self.assertEqual(len(doc["proposals"][0]["candidates"]), 2)

    def test_overlapping_deposit_makes_custody_ambiguous(self):
        doc = link(events_doc([removal("E-1", "S-a", 10, 12), deposit("E-2", "cell-3", 12, 15), deposit("E-3", "cell-4", 11, 14)]))
        self.assertEqual({proposal["status"] for proposal in doc["proposals"]}, {"ambiguous"})

    def test_deposit_without_removal_is_unattributed(self):
        doc = link(events_doc([deposit("E-1", "cell-1", 5, 7)]))
        self.assertEqual(doc["proposals"][0]["status"], "unattributed")

    def test_blind_source_site_prevents_exclusivity(self):
        doc = link(events_doc([removal("E-1", "S-a", 10, 12), deposit("E-2", "cell-3", 12, 15)],
                              zones=[{"zone": "S-b", "kind": "site", "status": "inventory_not_observable"}]))
        self.assertEqual(doc["proposals"][0]["status"], "ambiguous")

    def test_sampling_gap_prevents_exclusivity(self):
        doc = link(events_doc([removal("E-1", "S-a", 10, 12), deposit("E-2", "cell-3", 12, 15)], gaps=[[13.0, 14.0]]))
        self.assertEqual(doc["proposals"][0]["status"], "ambiguous")

    def test_occupied_cell_violates_one_part_per_cell(self):
        doc = link(events_doc([removal("E-1", "S-a", 10, 12), deposit("E-2", "cell-3", 12, 15, kind="changed_while_occupied")]))
        self.assertEqual(doc["proposals"][0]["status"], "ambiguous")

    def test_declared_removal_needs_a_declarer(self):
        declared = {"schema": "custody-declared-removals@1", "removals": [{"id": "D-1", "site": "S-a", "t_before_s": 1, "t_after_s": 2, "declared_by": ""}]}
        with self.assertRaises(SystemExit):
            link(events_doc([]), declared)

    def test_declared_removal_is_marked_human_basis(self):
        declared = {"schema": "custody-declared-removals@1", "removals": [{"id": "D-1", "site": "S-a", "t_before_s": 10, "t_after_s": 12, "declared_by": "operator"}]}
        doc = link(events_doc([deposit("E-2", "cell-3", 12, 15)]), declared)
        self.assertEqual(doc["proposals"][0]["removal"]["basis"], "operator_declared")


class AmbiguityLocalityTests(unittest.TestCase):
    """Linker 1.1: ambiguity stays local, competing sources stay ambiguous, gaps never resolve."""

    def test_initial_ambiguity_does_not_poison_an_independent_later_link(self):
        doc = link(events_doc([
            removal("E-1", "S-a", 10, 12), removal("E-2", "S-b", 10, 12),
            deposit("E-3", "cell-1", 13, 15), deposit("E-4", "cell-2", 16, 18),
            removal("E-5", "S-c", 20, 22), deposit("E-6", "cell-3", 23, 25)]))
        statuses = [p["status"] for p in doc["proposals"]]
        self.assertEqual(statuses[:2], ["ambiguous", "ambiguous"], "which of S-a/S-b went where stays open")
        self.assertEqual(statuses[2], "candidate_by_elimination")
        self.assertEqual(doc["proposals"][2]["removal"]["site"], "S-c")
        self.assertEqual(sorted(doc["proposals"][2]["eliminated"]), ["E-1", "E-2"])

    def test_two_genuinely_competing_sources_stay_ambiguous(self):
        doc = link(events_doc([
            removal("E-1", "S-a", 10, 12), removal("E-2", "S-b", 10, 12), deposit("E-3", "cell-1", 13, 15),
            removal("E-4", "S-c", 20, 22), deposit("E-5", "cell-2", 23, 25)]))
        self.assertEqual([p["status"] for p in doc["proposals"]], ["ambiguous", "ambiguous"],
                         "the leftover of S-a/S-b may be the part in cell-2 as well as S-c")

    def test_occluded_gap_is_never_resolved_by_elimination(self):
        doc = link(events_doc([
            removal("E-1", "S-a", 10, 12), removal("E-2", "S-b", 10, 12),
            deposit("E-3", "cell-1", 13, 15), deposit("E-4", "cell-2", 16, 18),
            removal("E-5", "S-c", 20, 22), deposit("E-6", "cell-3", 23, 25)], gaps=[[21.0, 23.5]]))
        self.assertEqual(doc["proposals"][2]["status"], "ambiguous")
        self.assertTrue(any("sampling gaps" in r for r in doc["proposals"][2]["reasons"]))

    def test_unobservable_source_blocks_elimination(self):
        doc = link(events_doc(
            [removal("E-1", "S-a", 10, 12), removal("E-2", "S-b", 10, 12),
             deposit("E-3", "cell-1", 13, 15), deposit("E-4", "cell-2", 16, 18),
             removal("E-5", "S-c", 20, 22), deposit("E-6", "cell-3", 23, 25)],
            zones=[{"zone": "S-d", "kind": "site", "status": "inventory_not_observable"}]))
        self.assertEqual(doc["proposals"][2]["status"], "ambiguous")

    def test_elimination_never_invents_a_link_without_a_removal(self):
        doc = link(events_doc([removal("E-1", "S-a", 10, 12), deposit("E-2", "cell-1", 13, 15),
                               deposit("E-3", "cell-2", 16, 18)]))
        self.assertEqual([p["status"] for p in doc["proposals"]], ["candidate_temporal_exclusive", "unattributed"])


if __name__ == "__main__":
    unittest.main()
