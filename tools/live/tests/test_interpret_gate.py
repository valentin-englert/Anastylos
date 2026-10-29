"""L0 interpretation of operator input, read-back text, and the L1 gates with an in-test fake Proposer."""

import tempfile
import unittest
from pathlib import Path

from support import FakeProposer, memory_lines, open_coordinator, provider_result, screen, spoken, typed, workspace

import interpret  # noqa: E402
from gate import interpretation_gate, schema_errors, task_schema  # noqa: E402
from templates import MACHINE_ID  # noqa: E402

PROMPT = {"screen_options": [
    {"token": "A", "removal_event": "E-0035", "description": "direkt an der Stelle von „L-Taste“", "spoken_number": "eins", "claimed_by": None},
    {"token": "B", "removal_event": "E-0049", "description": "rechts von der Stelle von „L-Taste“, im Kamerabild", "spoken_number": "zwei", "claimed_by": None}]}
SOURCE_Q = {"id": "Q-P2-01", "kind": "source_of_part", "cell": "cell-8"}
DEST_Q = {"id": "Q-P3-06", "kind": "destination_of_removal"}


def interp(text, kind="source_of_part", source="typed"):
    item = typed("live-x:Q:1", text) if source == "typed" else spoken("live-x:Q:1", text)
    return interpret.interpret_answer(item, kind, PROMPT)


class InterpretTest(unittest.TestCase):
    def test_spoken_numbers_digits_and_letters_choose_offered_options(self):
        for text in ("zwei", "2", "Nummer zwei.", "Möglichkeit 2", "b", "Option B"):
            self.assertEqual((interp(text)["intent"], interp(text)["choice"]), ("choose", "B"), text)
        self.assertEqual(interp("drei")["intent"], "unclear")  # not offered
        self.assertEqual(interp("c")["intent"], "unclear")

    def test_label_with_option(self):
        result = interp("R-Taste von zwei")
        self.assertEqual((result["intent"], result["choice"], result["label_text"]), ("choose", "B", "R-Taste"))

    def test_label_and_place_split_only_for_a_short_label(self):
        result = interp("Schraube aus dem Loch oben links")
        self.assertEqual((result["label_text"], result["place_text"]), ("Schraube", "dem Loch oben links"))
        long = "aus den versenkten tiefen Löchern der Außenseite der Rückplatte"
        self.assertEqual((interp(long)["label_text"], interp(long)["place_text"]), (None, long))

    def test_speech_is_handled_like_typing(self):
        for text in ("zwei", "Schraube aus dem Loch oben links", "weiß nicht"):
            self.assertEqual(interp(text, source="speech"), interp(text))

    def test_skip_unsure_not_a_part_and_bare_yes(self):
        self.assertEqual(interp("später")["intent"], "skip")
        self.assertEqual(interp("Keine Ahnung")["intent"], "unsure")
        self.assertTrue(interp("Das ist kein Teil")["not_a_part"])
        self.assertEqual(interp("ja")["intent"], "unclear")
        self.assertEqual(interp("   ")["intent"], "unclear")

    def test_screen_choice(self):
        self.assertEqual(interpret.interpret_answer(screen("k", "A"), "source_of_part", PROMPT)["choice"], "A")
        self.assertEqual(interpret.interpret_answer(screen("k", "Z"), "source_of_part", PROMPT)["intent"], "unclear")
        self.assertTrue(interpret.interpret_answer(screen("k", "not_a_part"), "source_of_part", PROMPT)["not_a_part"])

    def test_destination(self):
        result = interp("Start/Select-Tasten in Zelle 4", kind="destination_of_removal")
        answer = interpret.to_answer(DEST_Q, result, PROMPT, "live-x:Q-P3-06:1", "typed")
        self.assertEqual((answer["label"], answer["destination"]), ("Start/Select-Tasten", "cell-4"))
        outside = interpret.to_answer(DEST_Q, interp("ausserhalb des Bildes", kind="destination_of_removal"), PROMPT, "k", "typed")
        self.assertEqual(outside["destination"], "ausserhalb des Bildes")

    def test_read_back_replies(self):
        for text, intent in (("Ja", "confirm"), ("ja, stimmt!", "confirm"), ("nein", "reject"), ("ja nein", "unclear"),
                             ("Zelle 4", "unclear")):
            self.assertEqual(interpret.interpret_read_back(typed("k", text))["intent"], intent, text)
        self.assertEqual(interpret.interpret_read_back(screen("k", "yes"))["intent"], "confirm")

    def test_read_back_names_no_machine_ids(self):
        answer = interpret.to_answer(SOURCE_Q, interp("R-Taste von zwei"), PROMPT, "live-x:Q-P2-01:1", "typed")
        self.assertEqual(answer["choice"], "E-0049")
        text = interpret.read_back(SOURCE_Q, answer, PROMPT)
        self.assertIn("„R-Taste“", text)
        self.assertIn("Zelle 8", text)
        self.assertIsNone(MACHINE_ID.search(text))

    def test_input_schema_is_enforced(self):
        bad = [{**typed("k", "x"), "schema": "other"}, {**typed("k", "x"), "source": "gesture"},
               {**typed("k", "x"), "extra": 1}, {**spoken("k", "x"), "transcript": None}, {**screen("k", "A"), "choice": None}]
        for item in bad:
            with self.assertRaises(interpret.InputError):
                interpret.check_input(item)


class GateUnitTest(unittest.TestCase):
    def test_schema_subset_validator(self):
        output = task_schema("answer_interpretation")["output"]
        ok = {"intent": "choose", "choice": "A", "place_text": None, "label_text": None}
        self.assertEqual(schema_errors(ok, output), [])
        self.assertTrue(schema_errors({**ok, "intent": "guess"}, output))
        self.assertTrue(schema_errors({**ok, "extra": 1}, output))
        self.assertTrue(schema_errors({k: v for k, v in ok.items() if k != "choice"}, output))

    def test_interpretation_gate_rejects_invention(self):
        said = "die R-Taste kam von rechts"
        accept = {"intent": "describe_place", "choice": None, "place_text": "von rechts", "label_text": "R-Taste"}
        self.assertIsNotNone(interpretation_gate(accept, ["A", "B"], said, "source_of_part")[0])
        cases = [{**accept, "place_text": "rechts oben am Gehäuse"},  # not said
                 {**accept, "intent": "choose", "choice": "C"},  # not offered
                 {**accept, "intent": "choose", "choice": None},
                 {**accept, "intent": "confirm"}]
        for case in cases:
            self.assertIsNone(interpretation_gate(case, ["A", "B"], said, "source_of_part")[0], case)


class L1HookTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = workspace(Path(self._tmp.name))
        self.c = None

    def tearDown(self):
        if self.c:
            self.c.close()
        self._tmp.cleanup()

    def open(self, proposer, **kwargs):
        self.c = open_coordinator(self.ws, proposer=proposer, **kwargs)
        return self.c

    @staticmethod
    def wording(text, facts_used=("phase_words",)):
        return lambda payload, rid: provider_result("question_wording", rid, output={"text": text, "facts_used": list(facts_used)})

    def test_accepted_wording_is_used_and_journaled(self):
        text = "Beim Abschrauben der Rückseite: Woher kam das erste Teil in Zelle 1? (abgelegt bei etwa 0:25)"
        c = self.open(FakeProposer(question_wording=self.wording(text)))
        prompt = c.ask("Q-P1-01")
        self.assertEqual((prompt["wording"], prompt["text"]), ("L1", text))
        self.assertEqual(len(c.journal.of_kind("provider_call")), 1)
        self.assertEqual(c.journal.of_kind("fallback"), [])

    def test_rejected_wording_falls_back_to_l0(self):
        bad = {"new number": "Beim Abschrauben der Rückseite: Woher kam das Teil in Zelle 4? (0:25)",
               "mm": "Beim Abschrauben der Rückseite: Kam es aus 12 mm Tiefe in Zelle 1? (0:25)",
               "machine id": "Beim Abschrauben der Rückseite: Woher kam das Teil in cell-1?",
               "new name": "Beim Abschrauben der Rückseite: Woher kam die „Schraube“ in Zelle 1? (0:25)",
               "no phase": "Woher kam das Teil in Zelle 1? (0:25)"}
        for why, text in bad.items():
            with self.subTest(why=why):
                ws = workspace(Path(self._tmp.name) / why.replace(" ", "_"))
                c = open_coordinator(ws, proposer=FakeProposer(question_wording=self.wording(text)))
                prompt = c.ask("Q-P1-01")
                self.assertEqual(prompt["wording"], "L0")
                self.assertTrue(c.journal.of_kind("fallback")[0]["reason"].startswith("gate"))
                c.close()

    def test_provider_failures_fall_back(self):
        def raises(payload, rid):
            raise ConnectionError("down")
        for name, make in (("timeout", lambda p, rid: provider_result("question_wording", rid, status="timeout")),
                           ("raises", raises),
                           ("wrong task", lambda p, rid: provider_result("next_action", rid, output={}))):
            with self.subTest(name):
                ws = workspace(Path(self._tmp.name) / name.replace(" ", "_"))
                c = open_coordinator(ws, proposer=FakeProposer(question_wording=make))
                self.assertEqual(c.ask("Q-P1-01")["wording"], "L0")
                self.assertEqual(len(c.journal.of_kind("fallback")), 1)
                c.close()

    def test_call_cap(self):
        c = self.open(FakeProposer(), provider_call_cap=1)
        c.ask("Q-P1-01")
        c.ask("Q-P1-02")
        reasons = [r["reason"] for r in c.journal.of_kind("fallback")]
        self.assertEqual(reasons[-1], "provider call cap reached")

    def test_stale_model_result_is_not_used(self):
        holder = {}

        def late(payload, rid):  # the question is re-linked while the model is still thinking
            holder["c"].book.revisions["Q-P1-01"] = (2, "changed")
            return provider_result("question_wording", rid, output={"text": "Beim Abschrauben: Woher? (0:25)", "facts_used": []})
        c = self.open(None)
        c.proposer = FakeProposer(question_wording=late)
        holder["c"] = c
        state = c.state_of("Q-P1-01")
        payload = {"op_key": state.op_key, "kind": "source_of_part", "mode": "open", "facts": {}, "l0_text": "x"}
        self.assertIsNone(c._propose("question_wording", payload, lambda out: (out, "accepted")))
        self.assertIn("stale", c.journal.of_kind("fallback")[-1]["reason"])

    def test_interpretation_proposal_is_gated_and_still_read_back(self):
        def model(payload, rid):
            return provider_result("answer_interpretation", rid, output={
                "intent": "describe_place", "choice": None, "place_text": "oben links aus dem Loch", "label_text": "Schraube"})
        c = self.open(FakeProposer(answer_interpretation=model))
        op_key = c.ask("Q-P1-01")["op_key"]
        reply = c.receive(typed(op_key, "das war eine Schraube, oben links aus dem Loch"))
        self.assertEqual(reply["status"], "read_back")
        self.assertEqual((reply["answer"]["label"], reply["answer"]["source"]), ("Schraube", "oben links aus dem Loch"))
        self.assertEqual(len(memory_lines(self.ws)), 7)  # nothing before the confirmation
        self.assertEqual(c.receive(typed(op_key, "passt so"))["status"], "read_back")  # only an explicit yes confirms
        interpretations = [call for call in c.proposer.calls if call[0] == "answer_interpretation"]
        self.assertEqual(len(interpretations), 1)  # the read-back reply never goes to the model
        self.assertEqual(c.receive(typed(op_key, "ja"))["status"], "committed")

    def test_invented_interpretation_falls_back_to_l0(self):
        invent = lambda p, rid: provider_result("answer_interpretation", rid, output={
            "intent": "describe_place", "choice": None, "place_text": "Batteriefach", "label_text": "Schraube"})
        c = self.open(FakeProposer(answer_interpretation=invent))
        op_key = c.ask("Q-P1-01")["op_key"]
        reply = c.receive(typed(op_key, "Schraube aus dem Loch oben links"))
        self.assertEqual(reply["answer"]["source"], "dem Loch oben links")
        self.assertIn("verbatim", c.journal.of_kind("fallback")[-1]["reason"])


if __name__ == "__main__":
    unittest.main()
