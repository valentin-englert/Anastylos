import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from answers import apply, plan  # noqa: E402

QUESTIONS = {
    "Q-P3-01": {"id": "Q-P3-01", "kind": "source_of_part", "part": "P3-C10", "cell": "cell-10", "memory_phase": "phase-0001",
                "deposit_window_s": [30.6, 35.4],
                "candidates": [{"removal_event": "E-66", "window_s": [23.6, 28.2], "sources": [{"site": "SRC-10", "centroid_mm": [186.0, 152.5]}]},
                               {"removal_event": "E-46", "window_s": [22.0, 22.2], "sources": [{"site": "SRC-3"}]}]},
    "Q-P3-06": {"id": "Q-P3-06", "kind": "destination_of_removal", "removal_event": "E-104", "memory_phase": "phase-0001", "window_s": [78.2, 81.4],
                "sources": [{"site": "SRC-20", "centroid_mm": [208.5, 142.0]}]},
    "Q-P2-01": {"id": "Q-P2-01", "kind": "source_of_part", "part": "P2-C02", "cell": "cell-2", "memory_phase": "phase-0001",
                "deposit_window_s": [55.0, 60.0],
                "candidates": []},
}


class PlanTest(unittest.TestCase):
    def test_offered_candidate_becomes_a_declared_source(self):
        action = plan({"question": "Q-P3-01", "choice": "E-66", "label": "D-pad membrane"}, QUESTIONS)
        self.assertEqual(action["location"], "cell-10")
        self.assertIn("E-66", action["source"])
        self.assertIn("chosen by the operator", action["source"])

    def test_candidate_that_was_not_offered_is_rejected(self):
        self.assertIn("rejected", plan({"question": "Q-P3-01", "choice": "E-999"}, QUESTIONS))

    def test_free_source_text_is_accepted_when_no_candidate_fits(self):
        action = plan({"question": "Q-P2-01", "source": "between PCB sensor and R button", "label": "rubber nub"}, QUESTIONS)
        self.assertEqual(action["source"], "between PCB sensor and R button")

    def test_destination_question_needs_a_destination(self):
        self.assertIn("rejected", plan({"question": "Q-P3-06"}, QUESTIONS))
        self.assertEqual(plan({"question": "Q-P3-06", "destination": "OUT-E"}, QUESTIONS)["location"], "OUT-E")

    def test_not_a_part_is_dismissed_without_writing(self):
        self.assertIn("dismissed", plan({"question": "Q-P3-01", "not_a_part": True}, QUESTIONS))

    def test_skipped_and_unknown_questions_write_nothing(self):
        self.assertIn("rejected", plan({"question": "Q-P3-01", "skip": True}, QUESTIONS))
        self.assertIn("rejected", plan({"question": "Q-X-99", "choice": "E-66"}, QUESTIONS))


class ExtraPartTest(unittest.TestCase):
    def test_extra_part_needs_phase_source_and_destination(self):
        questions = {**QUESTIONS, "EXTRA": {"id": "EXTRA", "kind": "extra", "phases": {"P2": "phase-0002"}}}
        ok = plan({"question": "EXTRA", "phase": "P2", "label": "back shell", "source": "device", "destination": "OUT-B"}, questions)
        self.assertEqual((ok["phase"], ok["location"]), ("phase-0002", "OUT-B"))
        self.assertIn("rejected", plan({"question": "EXTRA", "phase": "P9", "label": "x", "source": "y", "destination": "z"}, questions))
        self.assertIn("rejected", plan({"question": "EXTRA", "phase": "P2", "label": "x"}, questions))


class ApplyTest(unittest.TestCase):
    def test_answers_land_in_memory_with_operator_basis_and_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "memory"
            from answers import _memory
            phase = _memory(session, "record", "phase", "--label", "P3")["id"]
            questions = {k: {**v, "memory_phase": phase} for k, v in QUESTIONS.items()}
            questions["Q-P2-02"] = {**questions["Q-P2-01"], "id": "Q-P2-02", "part": "P2-C02b"}
            results = apply([{"question": "Q-P3-01", "choice": "E-66", "label": "D-pad membrane"},
                             {"question": "Q-P2-01", "source": "R side", "label": "nub"},
                             {"question": "Q-P2-02", "source": "L side", "label": "nub", "identical_to": ["P2-C02"]},
                             {"question": "Q-P3-06", "choice": "E-66"}], questions, session, "owner")
            events = [json.loads(line) for line in (session / "memory.jsonl").read_text(encoding="utf-8").splitlines()]
        kinds = [e["type"] for e in events]
        self.assertEqual(kinds.count("declared_removal"), 3)
        self.assertEqual(kinds.count("storage_link"), 3)
        self.assertEqual(kinds.count("compatible_group"), 1)
        self.assertIn("rejected", results[3])
        self.assertTrue(all(e["declared_by"] == "owner" for e in events if e["type"] == "declared_removal"))


if __name__ == "__main__":
    unittest.main()
