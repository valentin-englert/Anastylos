import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parts import number_parts  # noqa: E402


def removal(event, site, t0, t1, **extra):
    return {"event": event, "site": site, "t_before_s": t0, "t_after_s": t1, "basis": "observed", **extra}


def proposal(pid, cell, dep, t0, t1, status, **extra):
    return {"id": pid, "deposit": {"id": dep, "zone": cell, "t_before_s": t0, "t_after_s": t1}, "status": status, **extra}


CUSTODY = {"proposals": [
    proposal("L-0001", "cell-1", "E-10", 13, 15, "candidate_temporal_exclusive",
             removal=removal("E-1", "SRC-1", 10, 12, sites=["SRC-1", "SRC-2"], events=["E-1", "E-2"]), autonomy="automatic"),
    proposal("L-0002", "cell-1", "E-11", 20, 22, "ambiguous", reasons=["deposit into an already occupied cell"],
             candidates=[removal("E-3", "SRC-3", 16, 18)]),
    proposal("L-0003", "cell-8", "E-12", 30, 32, "unattributed", reasons=["no observed or declared removal precedes this deposit"]),
], "removals_without_linked_deposit": [removal("E-3", "SRC-3", 16, 18), removal("E-5", "SRC-9", 40, 42)]}
REGISTRY = {"SRC-1": {"geometry": {"centroid_mm": [100, 100], "area_mm2": 60}}}


class NumberPartsTest(unittest.TestCase):
    def setUp(self):
        self.parts, self.questions = number_parts(CUSTODY, "P1", REGISTRY)

    def test_every_deposit_is_a_numbered_part(self):
        self.assertEqual([p["part"] for p in self.parts], ["P1-C01", "P1-C01b", "P1-C08"])
        self.assertTrue(self.parts[1]["same_cell_group"])

    def test_linked_part_carries_its_sources(self):
        linked = self.parts[0]
        self.assertEqual(linked["removal_events"], ["E-1", "E-2"])
        self.assertEqual(linked["sources"][0]["centroid_mm"], [100, 100])
        self.assertNotIn("removal_event", self.parts[2])

    def test_open_parts_and_unplaced_removals_become_questions_not_facts(self):
        kinds = [(q["kind"], q.get("part") or q.get("removal_event")) for q in self.questions]
        self.assertEqual(kinds, [("source_of_part", "P1-C01b"), ("source_of_part", "P1-C08"),
                                 ("destination_of_removal", "E-5")])
        self.assertEqual(self.questions[0]["candidates"][0]["removal_event"], "E-3")


if __name__ == "__main__":
    unittest.main()
