import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from answers import apply, plan, remap  # noqa: E402

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
    def test_extra_part_needs_phase_label_and_destination(self):
        questions = {**QUESTIONS, "EXTRA": {"id": "EXTRA", "kind": "extra", "phases": {"P2": "phase-0002"}}}
        ok = plan({"question": "EXTRA", "phase": "P2", "label": "back shell", "source": "device", "destination": "OUT-B"}, questions)
        self.assertEqual((ok["phase"], ok["location"]), ("phase-0002", "OUT-B"))
        self.assertIn("rejected", plan({"question": "EXTRA", "phase": "P9", "label": "x", "source": "y", "destination": "z"}, questions))
        self.assertIn("rejected", plan({"question": "EXTRA", "phase": "P2", "label": "x"}, questions))

    def test_extra_part_without_source_is_kept_with_unknown_source(self):  # FULL-01: the PCB was dropped
        questions = {**QUESTIONS, "EXTRA": {"id": "EXTRA", "kind": "extra", "phases": {"P2": "phase-0002"}}}
        pcb = plan({"question": "EXTRA", "phase": "P2", "label": "Platine mitsamt Kabel", "source": None,
                    "destination": "ausserhalb des Sichtfelds der Kamera", "t_s": 68}, questions)
        self.assertNotIn("rejected", pcb)
        self.assertIsNone(pcb["source"])
        self.assertIn("source not stated", pcb["note"])


class RemapTest(unittest.TestCase):
    """Replay onto a rebuilt run: context decides, ids never do."""

    OLD = {
        "Q-P3-02": {"id": "Q-P3-02", "kind": "source_of_part", "phase_tag": "P3", "cell": "cell-7", "deposit_window_s": [44.2, 47.2],
                    "candidates": [{"removal_event": "E-0071", "window_s": [40.0, 43.8], "sources": [{"site": "SRC-5"}]},
                                   {"removal_event": "E-0070", "window_s": [23.6, 28.2], "sources": [{"site": "SRC-9"}]}]},
        "Q-P1-01": {"id": "Q-P1-01", "kind": "source_of_part", "phase_tag": "P1", "cell": "cell-6", "deposit_window_s": [38.0, 49.4],
                    "candidates": []},
        "Q-P1-02": {"id": "Q-P1-02", "kind": "source_of_part", "phase_tag": "P1", "cell": "cell-1", "deposit_window_s": [38.2, 55.4],
                    "candidates": []},
    }
    NEW = {
        "Q-P3-04": {"id": "Q-P3-04", "kind": "source_of_part", "phase_tag": "P3", "cell": "cell-7", "deposit_window_s": [44.0, 47.2],
                    "candidates": [{"removal_event": "E-0066", "window_s": [40.0, 43.8], "sources": [{"site": "SRC-5"}]}]},
        "Q-P1-02": {"id": "Q-P1-02", "kind": "source_of_part", "phase_tag": "P1", "cell": "cell-1", "deposit_window_s": [25.6, 28.6],
                    "candidates": []},
        "Q-P1-03": {"id": "Q-P1-03", "kind": "source_of_part", "phase_tag": "P1", "cell": "cell-1", "deposit_window_s": [38.2, 55.4],
                    "candidates": []},
    }

    def test_question_and_choice_follow_context_not_ids(self):
        replayed, report = remap([{"question": "Q-P3-02", "choice": "E-0071", "label": "D-pad"},
                                  {"question": "Q-P1-02", "source": "deep holes", "label": "screws"}], self.OLD, self.NEW)
        self.assertEqual([(a["question"], a.get("choice")) for a in replayed], [("Q-P3-04", "E-0066"), ("Q-P1-03", None)])
        self.assertEqual(replayed[1]["replayed_from"], {"question": "Q-P1-02"})
        self.assertEqual([r["outcome"] for r in report], ["mapped", "mapped"])

    def test_dismissed_false_alarm_without_counterpart_is_eliminated_not_replayed(self):
        replayed, report = remap([{"question": "Q-P1-01", "not_a_part": True}], self.OLD, self.NEW)
        self.assertEqual(replayed, [])
        self.assertEqual(report[0]["outcome"], "false_positive_eliminated")

    def test_choice_without_unique_counterpart_stays_a_declaration_without_a_link(self):
        new = {"Q-P3-04": {**self.NEW["Q-P3-04"], "candidates": [{"removal_event": "E-0066", "window_s": [40.0, 43.8],
                                                                  "sources": [{"site": "SRC-5"}, {"site": "SRC-6"}]}]}}
        replayed, report = remap([{"question": "Q-P3-02", "choice": "E-0071", "label": "D-pad"}], self.OLD, new)
        self.assertEqual(report[0]["outcome"], "unresolved_candidate_kept_as_declaration")
        self.assertIsNone(replayed[0]["choice"])
        self.assertIn("E-0071", replayed[0]["source"])
        self.assertNotIn("E-0066", replayed[0]["source"])
        self.assertEqual(plan(replayed[0], {**new, "Q-P3-04": {**new["Q-P3-04"], "memory_phase": "phase-0003", "part": "P3-C07"}})["source"],
                         replayed[0]["source"])

    def test_ambiguous_question_is_not_replayed(self):
        new = {"A": {**self.NEW["Q-P1-03"], "id": "A"}, "B": {**self.NEW["Q-P1-03"], "id": "B"}}
        replayed, report = remap([{"question": "Q-P1-02", "source": "holes"}], self.OLD, new)
        self.assertEqual((replayed, report[0]["outcome"]), ([], "unresolved_ambiguous"))

    def test_replayed_note_names_the_earlier_question(self):
        action = plan({"question": "Q-P3-01", "choice": "E-66", "replayed_from": {"question": "Q-P3-09"},
                       "provenance": "operator_answers.jsonl sha256 abc"}, QUESTIONS)
        self.assertIn("replayed from earlier run's Q-P3-09", action["note"])
        same_id = plan({"question": "Q-P3-01", "choice": "E-66", "replayed_from": {"question": "Q-P3-01"}}, QUESTIONS)
        self.assertIn("replayed from earlier run's Q-P3-01", same_id["note"])
        self.assertIn("sha256 abc", action["note"])


class ApplyTest(unittest.TestCase):
    def test_answers_land_in_memory_with_operator_basis_and_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "memory"
            from answers import _memory
            phase = _memory(session, "record", "phase", "--label", "P3")["id"]
            questions = {k: {**v, "memory_phase": phase} for k, v in QUESTIONS.items()}
            questions["Q-P2-02"] = {**questions["Q-P2-01"], "id": "Q-P2-02", "part": "P2-C02b"}
            results = apply([{"question": "Q-P3-01", "choice": "E-66", "label": "D-pad membrane"},
                             {"question": "Q-P2-01", "source": "aus den Löchern der Außenseite", "label": "weiße Noppe"},
                             {"question": "Q-P2-02", "source": "L side", "label": "nub", "identical_to": ["P2-C02"]},
                             {"question": "Q-P3-06", "choice": "E-66"}], questions, session, "owner")
            events = [json.loads(line) for line in (session / "memory.jsonl").read_text(encoding="utf-8").splitlines()]
        kinds = [e["type"] for e in events]
        self.assertEqual(kinds.count("declared_removal"), 3)
        self.assertEqual(kinds.count("storage_link"), 3)
        self.assertEqual(kinds.count("compatible_group"), 1)
        self.assertIn("rejected", results[3])
        self.assertTrue(all(e["declared_by"] == "owner" for e in events if e["type"] == "declared_removal"))

    def test_extra_part_without_source_lands_in_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "memory"
            from answers import _memory
            phase = _memory(session, "record", "phase", "--label", "P2")["id"]
            questions = {"EXTRA": {"id": "EXTRA", "kind": "extra", "phases": {"P2": phase}}}
            results = apply([{"question": "EXTRA", "phase": "P2", "label": "Platine mitsamt Kabel", "source": None,
                              "destination": "ausserhalb des Sichtfelds der Kamera", "t_s": 68}], questions, session, "owner")
            events = [json.loads(line) for line in (session / "memory.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertIn("declared_removal", results[0])
        removal = next(e for e in events if e["type"] == "declared_removal")
        self.assertIsNone(removal["source"])
        self.assertEqual(removal["t_s"], 68)
        self.assertTrue(any(e["type"] == "storage_link" and e["target_id"] == removal["id"] for e in events))


if __name__ == "__main__":
    unittest.main()
