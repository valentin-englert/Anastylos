"""Linker 1.2: operator-declared external deposits close only what they explicitly name.

Run from the custody-ledger directory:

    ..\\.venv\\Scripts\\python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from anastylos_custody.linker import link  # noqa: E402


def events_doc(events):
    return {"events": events, "zones": [], "sampling": {"gaps_over_max": []}}


def removal(event_id, site, before, after):
    return {"id": event_id, "zone": site, "zone_kind": "site", "kind": "site_changed", "t_before_s": before, "t_after_s": after}


def deposit(event_id, cell, before, after):
    return {"id": event_id, "zone": cell, "zone_kind": "tray_cell", "kind": "appeared", "t_before_s": before, "t_after_s": after}


def declared(*items):
    return {"schema": "custody-declared-deposits@1", "deposits": list(items)}


def outside(item_id, t_s, source=None, evidence="seen on the witness video", by="owner"):
    item = {"id": item_id, "location": "outside the sheet, label A", "t_s": t_s, "declared_by": by, "evidence": evidence}
    if source is not None:
        item["source"] = source
    return item


# A large part (S-big) leaves for outside, then a small part (S-small) goes into cell-1.
SCENE = [removal("E-1", "S-big", 10, 12), removal("E-2", "S-small", 20, 22), deposit("E-3", "cell-1", 23, 25)]


class DeclaredDepositTests(unittest.TestCase):
    def test_missing_declaration_keeps_the_ambiguity(self):
        doc = link(events_doc(SCENE))
        self.assertEqual(doc["proposals"][0]["status"], "ambiguous")
        self.assertEqual(doc["counts"]["automatic_links"], 0)

    def test_named_declaration_closes_its_removal_and_marks_the_link_assisted(self):
        doc = link(events_doc(SCENE), declared_deposits=declared(outside("D-1", 15, {"site": "S-big"})))
        proposal = doc["proposals"][0]
        self.assertEqual(proposal["status"], "candidate_temporal_exclusive")
        self.assertEqual(proposal["removal"]["site"], "S-small")
        self.assertEqual(proposal["autonomy"], "operator_assisted")
        self.assertEqual(doc["counts"]["automatic_links"], 0, "declarations never inflate autonomous links")
        self.assertEqual(doc["counts"]["operator_assisted_links"], 1)

    def test_declaration_without_named_source_closes_nothing(self):
        doc = link(events_doc(SCENE), declared_deposits=declared(outside("D-1", 15)))
        self.assertEqual(doc["proposals"][0]["status"], "ambiguous")
        self.assertEqual(doc["declared_external_deposits"][0]["status"], "no_named_source")

    def test_named_source_without_evidence_is_rejected(self):
        doc = link(events_doc(SCENE), declared_deposits=declared(outside("D-1", 15, {"site": "S-big"}, evidence="")))
        self.assertEqual(doc["declared_external_deposits"][0]["status"], "rejected")
        self.assertEqual(doc["proposals"][0]["status"], "ambiguous")

    def test_incorrect_assertion_about_a_site_without_removal_is_rejected(self):
        doc = link(events_doc(SCENE), declared_deposits=declared(outside("D-1", 15, {"site": "S-never"})))
        self.assertEqual(doc["declared_external_deposits"][0]["status"], "rejected")
        self.assertEqual(doc["proposals"][0]["status"], "ambiguous")

    def test_declaration_before_the_removal_is_rejected(self):
        doc = link(events_doc(SCENE), declared_deposits=declared(outside("D-1", 5, {"site": "S-big"})))
        self.assertEqual(doc["declared_external_deposits"][0]["status"], "rejected")

    def test_duplicate_declarations_close_a_removal_only_once(self):
        doc = link(events_doc(SCENE), declared_deposits=declared(outside("D-1", 15, {"site": "S-big"}),
                                                                 outside("D-2", 16, {"removal_event": "E-1"})))
        self.assertEqual([d["status"] for d in doc["declared_external_deposits"]], ["closes_removal", "rejected"])

    def test_overlapping_removals_stay_ambiguous_when_only_one_is_declared(self):
        scene = [removal("E-1", "S-big", 10, 12), removal("E-2", "S-a", 20, 22), removal("E-3", "S-b", 20, 22),
                 deposit("E-4", "cell-1", 23, 25)]
        doc = link(events_doc(scene), declared_deposits=declared(outside("D-1", 15, {"site": "S-big"})))
        self.assertEqual(doc["proposals"][0]["status"], "ambiguous")

    def test_declaration_contradicting_an_automatic_link_is_a_conflict(self):
        scene = [removal("E-1", "S-a", 10, 12), deposit("E-2", "cell-1", 13, 15)]
        doc = link(events_doc(scene), declared_deposits=declared(outside("D-1", 30, {"site": "S-a"})))
        self.assertEqual(doc["declared_external_deposits"][0]["status"], "conflict")
        self.assertEqual(doc["proposals"][0]["status"], "ambiguous")
        self.assertEqual(doc["counts"]["candidate_links"], 0)

    def test_declaration_after_the_deposit_does_not_shape_it(self):
        doc = link(events_doc(SCENE), declared_deposits=declared(outside("D-1", 40, {"site": "S-big"})))
        self.assertEqual(doc["proposals"][0]["status"], "ambiguous", "S-big was still open when cell-1 was filled")


if __name__ == "__main__":
    unittest.main()
