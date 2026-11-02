"""Operator-stated notes (end-of-video questions) through the writer path: one write per op key, recoverable."""

import tempfile
import unittest
from pathlib import Path

from support import SimulatedCrash, crash_at, die, in_process_memory_call, memory_lines, open_coordinator, workspace

from memory_writer import census  # noqa: E402

LABELS = ["front shell", "back shell"]


class OperatorNotes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = workspace(Path(self._tmp.name))
        self.open = []

    def tearDown(self):
        for c in self.open:
            c.close()
        self._tmp.cleanup()

    def coordinator(self, **kwargs):
        c = open_coordinator(self.ws, memory_call=in_process_memory_call, **kwargs)
        self.open.append(c)
        return c

    def declared(self):
        return [e for e in memory_lines(self.ws) if e["type"] == "declared_removal" and "operator-stated" in (e.get("evidence_note") or "")]

    def test_nothing_is_written_until_the_notes_are_written(self):
        before = len(memory_lines(self.ws))
        self.coordinator()
        self.assertEqual(len(memory_lines(self.ws)), before)

    def test_notes_become_named_operator_stated_parts_with_a_location(self):
        c = self.coordinator()
        result = c.write_operator_notes("CLOSE-a", LABELS, "work area", "0" * 64, "front shell and back shell")
        self.assertEqual([r["status"] for r in result], ["committed", "committed"])
        events = memory_lines(self.ws)
        declared = self.declared()
        self.assertEqual([e["operator_label"] for e in declared], LABELS)
        links = [e for e in events if e["type"] == "storage_link" and e["target_id"] in {d["id"] for d in declared}]
        self.assertEqual({e["location"] for e in links}, {"work area"})
        self.assertIn("not observed", declared[0]["evidence_note"])
        self.assertEqual(census(events)["duplicate_op_keys"], [])

    def test_repeating_the_same_notes_writes_nothing_twice_even_after_a_reopen(self):
        c = self.coordinator()
        c.write_operator_notes("CLOSE-a", LABELS, "work area", "0" * 64, "x")
        again = c.write_operator_notes("CLOSE-a", LABELS, "work area", "0" * 64, "x")
        self.assertEqual({r["status"] for r in again}, {"already_committed"})
        c.close()
        reopened = self.coordinator()
        reopened.write_operator_notes("CLOSE-a", LABELS, "work area", "0" * 64, "x")
        self.assertEqual(len(self.declared()), 2)

    def test_stated_origin_is_stored_separately_from_destination_and_recovers_once(self):
        c = self.coordinator(fault=crash_at("after_memory:declared_removal"))
        with self.assertRaises(SimulatedCrash):
            c.write_operator_notes("CLOSE-origin", ["rubber membrane"], "cell-6", "0" * 64,
                                   "rubber membrane from front shell in cell 6", origins=["front shell"])
        die(c)
        recovered = self.coordinator()
        result = recovered.write_operator_notes("CLOSE-origin", ["rubber membrane"], "cell-6", "0" * 64,
                                                "rubber membrane from front shell in cell 6", origins=["front shell"])
        self.assertEqual(result[0]["status"], "already_committed")
        self.assertEqual([(e["operator_label"], e["source"]) for e in self.declared()], [("rubber membrane", "front shell")])
        links = [e for e in memory_lines(self.ws) if e["type"] == "storage_link" and e["target_id"] == self.declared()[0]["id"]]
        self.assertEqual([e["location"] for e in links], ["cell-6"])

    def test_invalid_origin_list_writes_nothing(self):
        c = self.coordinator()
        before = memory_lines(self.ws)
        for origins in ([], [""], [4], ["source", None]):
            with self.subTest(origins=origins), self.assertRaises(ValueError):
                c.write_operator_notes("CLOSE-invalid", ["part"], "cell-6", "0" * 64, "part", origins=origins)
        self.assertEqual(memory_lines(self.ws), before)

    def test_a_crash_at_any_step_is_completed_once_by_recovery(self):
        for point in ("after_intent", "after_memory:declared_removal", "after_journal:declared_removal", "after_memory:storage_link"):
            with self.subTest(point=point):
                self.tearDown()
                self.setUp()
                c = self.coordinator(fault=crash_at(point))
                with self.assertRaises(SimulatedCrash):
                    c.write_operator_notes("CLOSE-b", ["start button"], "cell-6", "0" * 64, "cell 6 start button")
                die(c)
                recovered = self.coordinator()
                self.assertIsNone(recovered.stopped)
                recovered.write_operator_notes("CLOSE-b", ["start button"], "cell-6", "0" * 64, "cell 6 start button")
                self.assertEqual([e["operator_label"] for e in self.declared()], ["start button"])
                self.assertEqual(census(memory_lines(self.ws))["orphan_declared_without_storage"], [])


if __name__ == "__main__":
    unittest.main()
