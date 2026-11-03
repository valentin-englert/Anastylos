"""Spoken question round with bundling, against a scripted stand-in for the coordinator adapter."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

UI_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UI_ROOT))

import dialogue_flow  # noqa: E402
from dialogue.executor import Executor, MuseClient  # noqa: E402
from meta import TransportTimeout  # noqa: E402
import videos  # noqa: E402
from catalog import selector  # noqa: E402
from dialogue import bundle, settings  # noqa: E402
from dialogue import items as cell_items  # noqa: E402

DEFER = "Why don't we resolve this later on the desktop?"


class FakeApi:
    """Questions Q1..Q3 in cell-1 (screws and a button), Q4 alone in cell-2. Mimics the coordinator states."""

    def __init__(self):
        self.cells = {"Q1": "cell-1", "Q2": "cell-1", "Q3": "cell-1", "Q4": "cell-2"}
        self.state = {q: "OPEN" for q in self.cells}
        self.answers, self.log, self.write_origins, self.reasons = {}, [], [], {}

    def _next(self):
        for wanted in (("ASKED", "READ_BACK"), ("OPEN",)):
            for q in self.cells:
                if self.state[q] in wanted:
                    return q
        return None

    def question_next(self, root, sid):
        q = self._next()
        if q is None:
            return {"done": True}
        if self.state[q] == "READ_BACK":
            return {"stage": "confirm", "question_id": q, "op_key": q, "read_back": f"read back {q}"}
        self.state[q] = "ASKED"
        return {"stage": "answer", "question_id": q, "op_key": q, "text": f"Where did the part in {self.cells[q]} come from?",
                "screen_options": []}

    def question_overview(self, root, sid):
        return {"questions": [{"id": q, "kind": "source_of_part", "cell": cell, "state": self.state[q],
                               "reason": self.reasons.get(q)}
                              for q, cell in self.cells.items()]}

    def question_reopen(self, root, sid, qid):
        if self.state[qid] != "LEFT_UNRESOLVED":
            raise AssertionError(f"{qid} is not unresolved")
        self.state[qid] = "ASKED"
        revision = sum(1 for call in self.log if call[0] == "reopen" and call[1] == qid) + 2
        self.log.append(("reopen", qid, revision))
        return {"stage": "answer", "question_id": qid, "op_key": f"{qid}:{revision}",
                "text": f"What is in {self.cells[qid]}?", "screen_options": []}

    def question_siblings(self, root, sid, qid):
        cell = self.cells[qid]
        waiting = [q for q in self.cells if self.cells[q] == cell and self.state[q] in ("OPEN", "ASKED")]
        return {"cell": cell, "qids": [qid] + [q for q in waiting if q != qid]}

    def question_open(self, root, sid, qid):
        if self.state[qid] == "OPEN":
            self.state[qid] = "ASKED"
        return {}

    def question_answer(self, root, sid, qid, text, choice):
        self.log.append(("answer", qid, text))
        if text.strip().lower() in ("mmm", "uhh"):
            return {"status": "unclear", "stage": "answer", "question_id": qid}
        self.answers[qid] = text
        self.state[qid] = "READ_BACK"
        return {"status": "read_back", "stage": "confirm", "question_id": qid, "op_key": qid, "read_back": f"read back {qid}"}

    def question_confirm(self, root, sid, qid, text):
        self.log.append(("confirm", qid, text))
        if text.strip().lower() == "yes":
            self.state[qid] = "COMMITTED"
            return {"status": "committed", "stage": "done", "question_id": qid, "op_key": qid}
        self.state[qid] = "ASKED"
        return {"status": "asked_again", "stage": "answer", "question_id": qid, "op_key": qid, "text": "again?",
                "screen_options": []}

    def closing_write(self, root, sid, note_id, labels, location, said, *, origins=None):
        self.log.append(("closing_write", tuple(labels), location))
        self.write_origins.append(tuple(origins or [None] * len(labels)))
        return {"status": "committed", "results": []}

    def question_defer(self, root, sid, qid, reason):
        self.log.append(("defer", qid, reason))
        self.state[qid] = "LEFT_UNRESOLVED"
        self.reasons[qid] = reason
        return {}


class ScriptedModel:
    """A fake Muse transport: answers each extract_items call from a table keyed by the operator's words."""

    def __init__(self, table, fail=None):
        self.table, self.fail, self.posts = table, fail, 0

    def post(self, url, headers, body, timeout_s):
        self.posts += 1
        if self.fail:
            raise self.fail
        payload = json.loads(json.loads(body)["input"][1]["content"][0]["text"].split("\n", 1)[1])
        reply = self.table.get(payload.get("answer_text"), {"kind": "unclear", "items": []})
        return 200, json.dumps({"status": "completed", "model": "m", "output": [
            {"type": "message", "content": [{"type": "output_text", "text": json.dumps(reply)}]}]}).encode()


def rendered(kind, persona, where="cell 1"):
    return [line.replace("{where}", where).replace("{Where}", where.capitalize()) for line in selector.variants(kind, persona)]


def item(name, count=None, origin=None):
    return {"name": name, "count": count, "origin": origin}


def answer(*items):
    return {"kind": "answer", "items": list(items)}


MODEL_TABLE = {
    "yeah five screws from the back shell.": answer(item("screw", 5, "back shell")),
    "two screws and a left button from the back shell": answer(item("screw", 2, "back shell"), item("left button", None, "back shell")),
    "it came from left and right button": answer(item("left and right button", None, None)),
    "which cell?": {"kind": "meta", "items": []},
    "cell three are the right button of the uh back shell.": answer(item("right button", None, "back shell")),
    "there is only one part,": answer(item("only one part", 1, None)),
    "why": answer(item("why", 2, None)),
    "the right button": answer(item("right button")),
    "two screws from the back shell": answer(item("screw", 2, "back shell")),
    "the start membrane": answer(item("start membrane")),
    "screws": answer(item("screw")),
    "trigger spring": answer(item("trigger spring")),
}


class ItemsTests(unittest.TestCase):
    def test_plain_answers_and_non_answers_are_told_apart(self):
        plain = cell_items.classify_plain
        self.assertEqual([plain(t) for t in ("yes.", "no,", "nothing", "which cell?", "why", "in cell three there is the",
                                             "I don't know", "skip", "")],
                         ["yes", "no", "empty", "meta", "meta", "incomplete", "unsure", "skip", "unclear"])
        self.assertIsNone(plain("two screws and a left button"))

    def test_readback_confirmation_accepts_only_unhedged_agreement(self):
        confirm = cell_items.classify_confirmation
        self.assertEqual(confirm("right, yes,"), "yes")
        self.assertEqual(confirm("that's right"), "yes")
        for text in ("right, no", "yes, maybe", "I think yes", "yes, but no", "no, yes"):
            self.assertIsNone(confirm(text), text)

    def test_names_must_be_real_words_the_operator_said(self):
        check = cell_items.check_items
        self.assertEqual(check([item("screw", 2)], "two screws"), [{"name": "screw", "count": 2, "origin": None}])
        said = "which cell why only one part two whys the"
        for bad in ("why", "which cell", "only one part", "two whys", "the"):
            self.assertEqual(check([item(bad, 1)], said), [], bad)
        self.assertEqual(check([item("washer", 1)], "two screws"), [])
        self.assertEqual(check([item("screw", 5)], "some screws")[0]["count"], None)  # a number must have been said
        self.assertEqual(check([item("screw", None, "the back shell")], "a screw from the back shell")[0]["origin"], "back shell")

    def test_repeated_syllable_noise_is_rejected_without_normalization(self):
        transcript = "front shell Dpadpad"
        said = set(cell_items.words(transcript))
        self.assertTrue(cell_items.has_repeated_cvc_fragment(transcript))
        self.assertIsNone(cell_items.clean_origin("front shell Dpadpad", said))
        self.assertFalse(cell_items.has_repeated_cvc_fragment("front shell D-pad"))
        self.assertEqual(cell_items.clean_origin("front shell D-pad", set(cell_items.words("front shell D-pad"))),
                         "front shell D-pad")
        self.assertEqual(cell_items.check_items([item("Dpadpad", 1)], "Dpadpad"), [])

    def test_rule_fallback_reads_lists_counts_and_origins(self):
        found = cell_items.rule_items("two screws and a left button from the back shell")
        self.assertEqual([(i["name"], i["count"], i["origin"]) for i in found],
                         [("screw", 2, "back shell"), ("left button", 1, "back shell")])
        self.assertEqual(cell_items.rule_items("there is only one part"), [])
        self.assertEqual(cell_items.rule_items("why"), [])

    def test_sentence_is_one_short_claim(self):
        found = [{"name": "screw", "count": 2, "origin": "back shell"}, {"name": "left button", "count": 1, "origin": "back shell"}]
        self.assertEqual(cell_items.sentence("Cell 3", found), "Cell 3: two screws and left button, from back shell. Right?")
        self.assertEqual(cell_items.sentence("Cell 3", [{"name": "right button", "count": 1, "origin": None}]),
                         "Cell 3: right button, origin unknown. Right?")

    def test_origin_answer(self):
        self.assertEqual(bundle.parse_origin("from the left side"), {"kind": "origin", "origin": "left side"})
        self.assertEqual(bundle.parse_origin("they came out of the back shell")["origin"], "back shell")
        self.assertEqual(bundle.parse_origin("skip")["kind"], "skip")


class FlowTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "s1").mkdir()
        self.api = FakeApi()
        self.model = None
        self.flow = dialogue_flow.DialogueFlow(self.root, "s1", self.api, executor=Executor(None))

    def with_model(self, table=None, fail=None):
        self.model = ScriptedModel(MODEL_TABLE if table is None else table, fail)
        muse = MuseClient(transport=self.model, environ={"MODEL_API_KEY": "k"})
        self.flow = dialogue_flow.DialogueFlow(self.root, "s1", self.api, executor=Executor(muse))

    def say(self, text=None, command=None):
        result = self.flow.turn(command, text)
        return result["speak"]["text"] if result.get("speak") else None

    def writes(self):
        return [c for c in self.api.log if c[0] == "closing_write"]

    def coordinator_calls(self):
        return [c for c in self.api.log if c[0] in ("answer", "confirm", "defer")]

    def notes(self):
        path = self.root / "s1" / "dialogue-notes.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def finished(self):
        for q in self.api.state:
            self.api.state[q] = "COMMITTED"

    # -- the one open question

    def test_one_open_question_per_cell_without_count_or_screen(self):
        first = self.say(command="next_question")
        self.assertIn(first, rendered("cell_open", "serious"))
        for persona in ("serious", "playful", "unhinged"):
            for line in selector.variants("cell_open", persona):
                self.assertNotIn("several", line)
                self.assertNotRegex(line, r"\d")

    def test_nothing_is_written_before_the_yes(self):
        self.with_model()
        self.say(command="next_question")
        self.say("two screws and a left button from the back shell")
        self.assertEqual(self.coordinator_calls(), [])
        self.assertEqual(self.writes(), [])

    # -- replay of the owner's real transcripts (dialogue-turns.jsonl of the third voice test)

    def test_replay_which_cell_and_why_are_questions_back_not_answers(self):
        self.with_model()
        first = self.say(command="next_question")
        self.assertEqual(self.say("which cell?"), first)
        self.assertEqual(self.say("why"), first)
        self.assertEqual(self.flow.agent._events.get("cell-Q1", {}).get("reasks", 0), 0)  # no re-ask is used up
        self.assertEqual(self.coordinator_calls(), [])

    def test_replay_an_answer_that_stops_mid_sentence_waits(self):
        self.with_model()
        self.say(command="next_question")
        self.assertIn(self.say("in cell three there is the"), selector.variants("go_on", "serious"))
        self.assertEqual(self.flow.agent._events.get("cell-Q1", {}).get("reasks", 0), 0)

    def test_replay_there_is_only_one_part_is_not_a_name(self):
        self.with_model()
        self.say(command="next_question")
        self.assertIn("could you please repeat it?", self.say("there is only one part,"))
        self.assertEqual(self.coordinator_calls(), [])

    def test_replay_five_screws_from_the_back_shell_then_yes(self):
        self.with_model()
        self.say(command="next_question")
        self.assertEqual(self.say("yeah five screws from the back shell."), "Cell 1: five screws, from back shell. Right?")
        reply = self.say("yes.")
        self.assertEqual([self.api.state[q] for q in ("Q1", "Q2", "Q3")], ["COMMITTED"] * 3)
        self.assertEqual(self.api.answers, {"Q1": "screw from back shell", "Q2": "screw from back shell", "Q3": "screw from back shell"})
        self.assertEqual(self.writes(), [("closing_write", ("screw", "screw"), "cell-1")])
        self.assertIn("cell 2", reply.lower())

    def test_initially_unresolved_cells_are_reoffered_once_at_the_round_tail(self):
        self.with_model()
        self.api.state.update(Q1="LEFT_UNRESOLVED", Q2="LEFT_UNRESOLVED", Q3="LEFT_UNRESOLVED")
        self.api.state["Q4"] = "COMMITTED"
        reply = self.say(command="next_question")
        self.assertIn(reply, rendered("cell_open", "serious"))
        self.assertEqual(self.flow.last_speak["basis_ref"], "Q1:2")
        self.assertEqual(self.flow.current["asked"], reply)
        self.assertEqual(self.say("why"), self.flow.last_speak["text"])
        self.assertEqual([call[1] for call in self.api.log if call[0] == "reopen"], ["Q1", "Q2", "Q3"])
        self.assertIn("work area", self.say("skip"))
        self.assertEqual([call[1] for call in self.api.log if call[0] == "reopen"], ["Q1", "Q2", "Q3"])
        self.assertEqual(self.api.answers, {})
        self.assertEqual(self.writes(), [])

    def test_surplus_questions_from_a_confirmed_cell_are_not_reoffered(self):
        self.api.state.update(Q1="COMMITTED", Q2="LEFT_UNRESOLVED", Q3="LEFT_UNRESOLVED", Q4="COMMITTED")
        self.api.reasons.update(Q2=dialogue_flow.SURPLUS_REASON, Q3=dialogue_flow.SURPLUS_REASON)
        self.assertIn("work area", self.say(command="next_question"))
        self.assertEqual([call for call in self.api.log if call[0] == "reopen"], [])

    # -- mapping onto the detected questions

    def test_fewer_parts_leave_the_surplus_unresolved_and_delete_nothing(self):
        self.with_model()
        self.say(command="next_question")
        self.assertEqual(self.say("two screws from the back shell"), "Cell 1: two screws, from back shell. Right?")
        self.say("yes")
        self.assertEqual([self.api.state[q] for q in ("Q1", "Q2", "Q3")], ["COMMITTED", "COMMITTED", "LEFT_UNRESOLVED"])
        self.assertEqual(self.writes(), [])

    def test_several_different_items_in_one_answer(self):
        self.with_model()
        self.say(command="next_question")
        self.assertEqual(self.say("two screws and a left button from the back shell"),
                         "Cell 1: two screws and left button, from back shell. Right?")
        self.say("yes")
        self.assertEqual(sorted(self.api.answers.values()), ["left button from back shell", "screw from back shell", "screw from back shell"])

    def test_an_empty_cell_stays_unresolved_while_the_next_cell_opens(self):
        self.say(command="next_question")
        reply = self.say("nothing")
        self.assertEqual([self.api.state[q] for q in ("Q1", "Q2", "Q3")], ["LEFT_UNRESOLVED"] * 3)
        self.assertIn("cell 2", reply.lower())
        self.assertEqual(self.coordinator_calls().count(("answer",)), 0)

    # -- origin, correction, no

    def test_origin_is_asked_once_and_dont_know_is_recorded_as_unknown(self):
        self.with_model()
        self.say(command="next_question")
        self.assertIn(self.say("the right button"), selector.variants("cell_origin", "serious"))
        self.assertEqual(self.say("I don't know"), "Cell 1: right button, origin unknown. Right?")
        self.say("yes")
        self.assertEqual(self.api.answers["Q1"], "right button from unknown")

    def test_a_plural_without_a_number_asks_how_many_once(self):
        self.with_model()
        self.say(command="next_question")
        self.assertIn(self.say("screws"), selector.variants("cell_how_many", "serious"))
        self.assertIn(self.say("three"), selector.variants("cell_origin", "serious"))
        self.assertEqual(self.say("from the back shell"), "Cell 1: three screws, from back shell. Right?")

    def test_asr_like_origin_noise_is_clarified_without_domain_normalization(self):
        table = {**MODEL_TABLE, "rubber membrane": answer(item("rubber membrane", 1, None))}
        self.with_model(table)
        self.say(command="next_question")
        self.say("rubber membrane")
        self.say("front shell Dpadpad")
        self.assertIn("could you please repeat it?", self.flow.last_speak["text"])
        self.assertEqual(self.flow.current["stage"], "origin")
        self.assertNotIn("D-pad", self.flow.last_speak["text"])
        self.assertEqual(self.api.answers, {})

    def test_muse_origin_subset_cannot_bypass_raw_asr_uncertainty(self):
        table = {**MODEL_TABLE, "rubber membrane": answer(item("rubber membrane", 1, None))}
        self.with_model(table)
        self.say(command="next_question")
        self.say("rubber membrane")
        calls = []

        def scripted_extract(text, question_type, tag="", need_name=False):
            calls.append((text, question_type))
            # This is what Muse would return if raw uncertainty reached extraction.
            return ({"intent": "answer", "part_name": None, "origin": "front shell", "storage": None, "count": None},
                    "muse", [])

        self.flow.agent.extract = scripted_extract
        reply = self.say("front shell Dpadpad")
        self.assertIn("could you please repeat it?", reply)
        self.assertEqual(calls, [])
        self.assertNotIn("Right?", reply)
        self.assertEqual(self.flow.current["stage"], "origin")
        self.assertEqual(self.api.answers, {})
        self.assertEqual(self.writes(), [])

    def test_muse_item_subset_cannot_bypass_raw_asr_uncertainty(self):
        table = {**MODEL_TABLE, "front shell Dpadpad": answer(item("front shell", 1, None))}
        self.with_model(table)
        self.say(command="next_question")
        reply = self.say("front shell Dpadpad")
        self.assertIn("could you please repeat it?", reply)
        self.assertNotEqual(self.flow.current["stage"], "confirm")
        self.assertEqual(self.model.posts, 0)
        self.assertEqual(self.api.answers, {})
        self.assertEqual(self.writes(), [])

    def test_no_asks_the_cell_again_and_a_correction_in_the_same_sentence_is_taken_directly(self):
        self.with_model({**MODEL_TABLE, "it's a right button from the back shell": answer(item("right button", 1, "back shell"))})
        self.say(command="next_question")
        self.say("two screws and a left button from the back shell")
        self.assertIn(self.say("no"), rendered("cell_again", "serious"))
        self.say("two screws and a left button from the back shell")
        self.assertEqual(self.say("no, it's a right button from the back shell"), "Cell 1: right button, from back shell. Right?")
        self.assertEqual(self.coordinator_calls(), [])

    # -- model failure falls back, deferral advances

    def test_model_timeout_falls_back_to_the_rules_and_the_read_back_still_gates(self):
        self.with_model(fail=TransportTimeout("t"))
        self.say(command="next_question")
        self.assertEqual(self.say("two screws and a left button from the back shell"),
                         "Cell 1: two screws and left button, from back shell. Right?")
        self.assertEqual(self.coordinator_calls(), [])

    def test_not_understood_twice_defers_and_the_same_turn_asks_the_next_cell(self):
        self.say(command="next_question")
        self.assertIn("could you please repeat it?", self.say("there is only one part,"))
        reply = self.say("there is only one one part.")
        self.assertIn("Why don't we resolve this later on the desktop?", reply)
        self.assertIn("cell 2", reply.lower())
        self.assertEqual([self.api.state[q] for q in ("Q1", "Q2", "Q3")], ["LEFT_UNRESOLVED"] * 3)
        self.assertEqual(self.flow.current["qids"], ["Q4"])
        result = self.flow.turn(None, "d-pad from the front shell")  # no command needed: still the question round
        self.assertTrue(result["handled"])

    def test_newly_deferred_cell_returns_once_after_remaining_open_cells(self):
        table = {**MODEL_TABLE, "d-pad": answer(item("D-pad", 1, None))}
        self.with_model(table)
        self.say(command="next_question")
        self.assertIn("cell 2", self.say("skip").lower())
        self.say("d-pad")
        self.say("from the front shell")
        self.say("yes")
        self.assertEqual(self.flow.current["cell"], "cell-1")
        self.assertEqual(self.flow.current["qids"], ["Q1", "Q2", "Q3"])
        self.assertEqual([call[1] for call in self.api.log if call[0] == "reopen"], ["Q1", "Q2", "Q3"])
        self.assertIn("Right?", self.say("two screws and a left button from the back shell"))
        self.assertIn("work area", self.say("yes"))
        self.assertEqual([self.api.state[q] for q in ("Q1", "Q2", "Q3")], ["COMMITTED"] * 3)
        self.assertEqual([call[1] for call in self.api.log if call[0] == "reopen"], ["Q1", "Q2", "Q3"])

    def test_tail_offer_marker_prevents_reopening_the_same_cell_after_restart(self):
        self.api.state.update(Q1="LEFT_UNRESOLVED", Q2="LEFT_UNRESOLVED", Q3="LEFT_UNRESOLVED", Q4="COMMITTED")
        self.say(command="next_question")
        self.assertEqual([call[1] for call in self.api.log if call[0] == "reopen"], ["Q1", "Q2", "Q3"])
        self.api.state.update(Q1="LEFT_UNRESOLVED", Q2="LEFT_UNRESOLVED", Q3="LEFT_UNRESOLVED")
        restarted = dialogue_flow.DialogueFlow(self.root, "s1", self.api, executor=Executor(None))
        restarted.turn("next_question", None)
        self.assertNotEqual(restarted.current.get("cell"), "cell-1")
        self.assertEqual([call[1] for call in self.api.log if call[0] == "reopen"], ["Q1", "Q2", "Q3"])

    def test_model_is_used_first_for_real_answers_and_not_for_plain_yes_no(self):
        self.with_model()
        self.say(command="next_question")
        self.say("two screws and a left button from the back shell")
        self.assertEqual(self.model.posts, 1)
        self.say("yes")
        self.assertEqual(self.model.posts, 1)

    # -- speech never reaches the guide, echo, repeat, log, voice

    def test_speech_never_reaches_the_guide_while_questions_remain_and_nothing_mentions_a_screen(self):
        result = self.flow.turn(None, "where does it go")
        self.assertTrue(result["handled"])
        self.assertNotIn("screen", result["speak"]["text"].lower())
        self.finished()
        (self.root / "s1" / "dialogue-closing-done").write_text("done", encoding="utf-8")
        self.flow.current = None
        self.assertFalse(self.flow.turn(None, "where does it go")["handled"])

    def test_echo_is_ignored_and_a_bare_wake_word_repeats_the_question(self):
        first = self.say(command="next_question")
        self.assertIsNone(self.flow.turn(None, first.rstrip("?."))["speak"])
        self.assertEqual(self.say(command="next_question"), first)

    def test_every_turn_is_logged_locally_with_transcript_parser_source_and_timing(self):
        self.with_model()
        self.say(command="next_question")
        self.say("which cell?")
        lines = [json.loads(line) for line in (self.root / "s1" / "dialogue-turns.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual((lines[1]["transcript"], lines[1]["stage"], lines[1]["parser"], lines[1]["source"]),
                         ("which cell?", "cell/ask", "meta", "rule"))
        self.assertIn("turn_ms", lines[1])

    def test_persona_changes_the_wording_per_workspace(self):
        settings.save(self.root, persona="unhinged")
        first = self.say(command="next_question")  # persona colour on every other line at most: the first one is plain
        self.assertIn(first, rendered("cell_open", "serious"))

    def test_a_read_back_left_open_by_an_older_flow_is_closed_with_no(self):
        self.api.state["Q1"] = "READ_BACK"
        self.say(command="next_question")
        self.assertIn(("confirm", "Q1", "no"), self.api.log)
        self.assertNotIn("COMMITTED", self.api.state.values())

    # -- closing questions

    def test_closing_questions_go_through_read_back_and_land_in_memory_only_after_yes(self):
        self.with_model({"the front shell, the back shell and the PCB": answer(item("front shell"), item("back shell"), item("PCB")),
                         "the start membrane from the front shell": answer(item("start membrane", 1, "front shell"))})
        self.finished()
        self.assertIn("work area", self.say(command="next_question"))
        self.assertEqual(self.say("the front shell, the back shell and the PCB"), "Still in the work area: front shell, back shell, PCB. Right?")
        self.assertEqual(self.writes(), [])
        self.assertIn("miss", self.say("yes"))
        self.assertEqual(self.writes(), [("closing_write", ("front shell", "back shell", "PCB"), "work area")])
        self.assertIn("cell", self.say("yes"))
        self.assertEqual(self.say("in cell six the start membrane from the front shell"),
                         "Missed in cell 6: start membrane, from front shell. Right?")
        self.assertIn("no more questions", self.say("yes"))
        self.assertEqual(self.writes()[1], ("closing_write", ("start membrane",), "cell-6"))
        self.assertIn("no more questions", self.say(command="next_question"))

    def test_closing_nothing_left_and_nothing_missed(self):
        self.finished()
        self.say(command="next_question")
        self.assertIn("miss", self.say("nothing"))
        self.assertIn("no more questions", self.say("no, that's all"))
        self.assertEqual(self.writes(), [])

    def test_closing_missed_negatives_finish_cleanly_in_both_states(self):
        self.finished()
        self.say(command="next_question")
        self.say("nothing")
        reply = self.say("now i don't think so")
        self.assertIn("no more questions", reply.lower())
        self.assertNotIn("resolve this later", reply.lower())
        self.assertEqual(self.writes(), [])

        other = FlowTests()
        other.setUp()
        other.finished()
        other.say(command="next_question")
        other.say("nothing")
        other.say("yes")
        reply = other.say("no,")
        self.assertIn("no more questions", reply.lower())
        self.assertNotIn("resolve this later", reply.lower())
        self.assertEqual(other.writes(), [])

    def test_closing_missed_cell_collects_count_and_origin_then_writes_only_after_yes(self):
        self.with_model({"RGBY membranes": answer(item("RGBY membrane", None, None))})
        self.finished()
        self.say(command="next_question")
        self.say("nothing")
        self.say("yes")
        self.assertIn(self.say("cell six, RGBY membranes"), selector.variants("cell_how_many", "serious"))
        self.assertIn(self.say("four"), selector.variants("cell_origin", "serious"))
        read_back = self.say("from the front shell")
        self.assertIn("Missed in cell 6", read_back)
        self.assertIn("four RGBY membranes", read_back)
        self.assertIn("from front shell", read_back)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.notes(), [])

        reply = self.say("right, yes,")
        self.assertIn("no more questions", reply.lower())
        self.assertEqual(self.writes(), [("closing_write", ("RGBY membrane",) * 4, "cell-6")])
        self.assertEqual(self.api.write_origins[-1], ("front shell",) * 4)
        note = self.notes()[-1]
        self.assertTrue(note["confirmed_by_operator"])
        self.assertEqual(note["operator_said"]["count"], 4)
        self.assertEqual(note["operator_said"]["origin"], "front shell")
        self.assertEqual(note["operator_said"]["location"], "cell-6")
        self.assertIn("Right?", note["operator_said"]["read_back"])

    def test_finished_video_opens_the_dialogue_on_the_first_utterance(self):
        (self.root / "s1" / "dialogue-pending-open").write_text("x", encoding="utf-8")
        result = self.flow.turn(None, "hello")
        self.assertTrue(result["handled"])
        self.assertFalse((self.root / "s1" / "dialogue-pending-open").exists())


FIDGET_TABLE = {
    "screws.": answer(item("screw")),
    "a plastic wheel": answer(item("plastic wheel")),
    "a spring": answer(item("spring")),
    "yes, barrel.": answer(item("barrel")),
    "Robert?": {"kind": "meta", "items": []},
    "a plastic wheel from inside the toy": answer(item("plastic wheel", 1, "inside the toy")),
}
FIDGET_FIELDS = {
    "uh it came out of the outer shell of the whole toy I think somewhere": {
        "intent": "answer", "part_name": None, "origin": "outer shell of the whole toy", "storage": None, "count": None},
}


class FieldsModel(ScriptedModel):
    """Also answers extract_fields (the origin fallback) from a second table."""

    def __init__(self, table, fields, fail=None):
        super().__init__(table, fail)
        self.fields = fields

    def post(self, url, headers, body, timeout_s):
        payload = json.loads(json.loads(body)["input"][1]["content"][0]["text"].split("\n", 1)[1])
        if "question_type" not in payload:
            return super().post(url, headers, body, timeout_s)
        self.posts += 1
        reply = self.fields.get(payload["answer_text"], {"intent": "unclear", "part_name": None, "origin": None,
                                                          "storage": None, "count": None})
        return 200, json.dumps({"status": "completed", "model": "m", "output": [
            {"type": "message", "content": [{"type": "output_text", "text": json.dumps(reply)}]}]}).encode()


class FidgetToyReplayTests(unittest.TestCase):
    """The real transcripts of the owner's fidget-toy run (dialogue-turns.jsonl), replayed against a fake model."""

    say, writes, coordinator_calls, notes, finished = (FlowTests.say, FlowTests.writes, FlowTests.coordinator_calls,
                                                       FlowTests.notes, FlowTests.finished)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "s1").mkdir()
        self.api = FakeApi()
        self.model = FieldsModel({**MODEL_TABLE, **FIDGET_TABLE}, FIDGET_FIELDS)
        muse = MuseClient(transport=self.model, environ={"MODEL_API_KEY": "k"})
        self.flow = dialogue_flow.DialogueFlow(self.root, "s1", self.api, executor=Executor(muse))

    def test_origin_answers_with_stutter_and_filler_are_understood_and_the_read_back_stays_the_gate(self):
        self.say(command="next_question")
        self.assertIn(self.say("screws."), selector.variants("cell_how_many", "serious"))
        self.assertIn(self.say("four."), selector.variants("cell_origin", "serious"))
        self.assertEqual(self.say("From the outer shell of the the toy."), "Cell 1: four screws, from outer shell of the toy. Right?")
        self.assertEqual(self.coordinator_calls(), [])
        self.say("yes.")
        self.assertEqual([self.api.state[q] for q in ("Q1", "Q2", "Q3")], ["COMMITTED"] * 3)
        # the second cell asks for the origin itself: never "same origin as last time"
        self.assertIn(self.say("a plastic wheel"), selector.variants("cell_origin", "serious"))
        self.assertEqual(self.say("in the inside of the toy"), "Cell 2: plastic wheel, from inside of the toy. Right?")

    def test_the_model_reads_an_origin_the_rules_cannot_and_its_words_must_be_in_the_transcript(self):
        self.say(command="next_question")
        self.say("a spring")
        said = "uh it came out of the outer shell of the whole toy I think somewhere"
        self.assertEqual(self.say(said), "Cell 1: spring, from outer shell of the whole toy. Right?")
        self.assertEqual(self.coordinator_calls(), [])
        self.assertGreaterEqual(self.model.posts, 2)

    def test_a_bare_wake_word_with_no_transcript_repeats_the_open_question(self):
        first = self.say(command="next_question")
        self.say("screws.")
        self.assertEqual(self.say(command="next_question"), self.flow.last_speak["text"])
        self.assertNotEqual(first, self.flow.last_speak["text"])

    def test_closing_missed_part_in_the_work_area_asks_only_for_the_missing_name(self):
        self.finished()
        self.say(command="next_question")
        self.assertEqual(self.say("Robert?"), self.flow.last_speak["text"])
        self.assertIn("barrel", self.say("yes, barrel."))
        self.assertIn("miss", self.say("yes."))
        self.assertIn("cell or place", self.say("yes."))
        self.assertIn(self.say("in the working area,"), selector.variants("missed_name", "serious"))
        self.assertIn(self.say("a spring"), selector.variants("cell_origin", "serious"))
        self.assertEqual(self.say("inside the toy"), "Missed in the work area: spring, from inside the toy. Right?")
        self.assertEqual(len(self.writes()), 1)  # only the assemblies so far
        self.say("yes")
        self.assertEqual(self.writes()[1], ("closing_write", ("spring",), "work area"))

    def test_closing_missed_work_area_alone_and_in_one_sentence(self):
        self.finished()
        self.say(command="next_question")
        self.say("yes, barrel.")
        self.say("yes.")
        self.say("yes.")
        self.assertIn(self.say("working area"), selector.variants("missed_name", "serious"))
        self.assertEqual(self.flow.current["location"], "work area")
        # the name first, the place after
        self.setUp()
        self.finished()
        self.say(command="next_question")
        self.say("yes, barrel.")
        self.say("yes.")
        self.say("yes.")
        self.assertEqual(self.say("in the working area, a plastic wheel from inside the toy"),
                         "Missed in the work area: plastic wheel, from inside the toy. Right?")


CELL_ORDER = [1, 8, 3, 2, 10, 4, 5, 7, 6, 9, 11, 12]  # the order of the owner's third fidget-toy run
ONE_SHOT = {  # what the owner said per cell, as one answer to "what's in it, and where did it come from?"
    1: ("five screws from the back shell", answer(item("screw", 5, "back shell"))),
    8: ("green button from the front shell", answer(item("green button", 1, "front shell"))),
    3: ("right button from the back of the controller", answer(item("right button", 1, "back of the controller"))),
    2: ("left button from the back of the controller", answer(item("left button", 1, "back of the controller"))),
    10: ("blue button from the front shell", answer(item("blue button", 1, "front shell"))),
    4: ("two white rubber membrane identical from left and right button",
        answer(item("white rubber membrane identical", 2, "left and right button"))),
    5: ("white rubber membrane from Dpad", answer(item("white rubber membrane", 1, "Dpad"))),
    7: ("start and select button from the front shell", answer(item("start and select button", 1, "front shell"))),
    6: ("wide rubber membrane from the front shell", answer(item("wide rubber membrane", 1, "front shell"))),
    9: ("Dpad from the front shell", answer(item("Dpad", 1, "front shell"))),
    11: ("yellow button", answer(item("yellow button"))),
    12: ("red button from the front shell", answer(item("red button", 1, "front shell"))),
}


class ThirdFidgetRunReplayTests(unittest.TestCase):
    """video-20261103-121526-30832219: shorter dialogue (name and origin asked together), fillers, corrections, cut-offs."""

    say, writes, coordinator_calls = FlowTests.say, FlowTests.writes, FlowTests.coordinator_calls

    def setUp(self, persona="serious"):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "s1").mkdir()
        settings.save(self.root, persona=persona)
        self.api = FakeApi()
        self.api.cells = {f"Q{n}": f"cell-{n}" for n in CELL_ORDER}
        self.api.state = {q: "OPEN" for q in self.api.cells}
        table = {text: reply for text, reply in ONE_SHOT.values()}
        table["yellow button"] = answer(item("yellow button"))
        table["green button"] = answer(item("green button"))
        self.model = FieldsModel(table, {})
        muse = MuseClient(transport=self.model, environ={"MODEL_API_KEY": "k"})
        self.flow = dialogue_flow.DialogueFlow(self.root, "s1", self.api, executor=Executor(muse))

    def run_round(self):
        """Returns the number of operator turns per cell (answers and yes/no, not the opening command)."""
        turns, cell_turns = {}, 0
        self.say(command="next_question")
        for number in CELL_ORDER:
            text, _reply = ONE_SHOT[number]
            self.assertIn(f"cell {number}", self.flow.last_speak["text"].lower())
            cell_turns = 1
            reply = self.say(text)
            if "Right?" not in reply:  # the origin was missing: asked once
                cell_turns += 1
                reply = self.say("from the front shell")
            self.assertIn("Right?", reply)
            cell_turns += 1
            self.say("yes.")
            turns[number] = cell_turns
        return turns

    def test_two_turns_per_cell_and_three_when_the_origin_is_missing(self):
        turns = self.run_round()
        self.assertEqual(sorted(turns.values()), [2] * 11 + [3])
        self.assertEqual(turns[11], 3)
        self.assertEqual(sum(turns.values()) / len(turns), 25 / 12)
        self.assertEqual(self.api.answers["Q4"], "white rubber membrane from left and right button")  # no "identical", count 2
        self.assertEqual(self.api.answers["Q1"], "screw from back shell")

    def test_nothing_proposes_the_last_origin(self):
        self.say(command="next_question")
        self.say(ONE_SHOT[1][0])
        self.say("yes.")
        for number in (8, 3):
            self.assertNotIn("last time", self.flow.last_speak["text"])
            self.say(ONE_SHOT[number][0])
            self.say("yes.")
        self.assertNotIn("last time", self.flow.last_speak["text"])

    def test_oh_is_a_repeat_request_never_an_origin(self):
        asked = self.say(command="next_question")
        self.assertEqual(self.say("oh,"), asked)
        self.assertEqual(self.flow.agent._events.get("cell-Q1", {}).get("reasks", 0), 0)
        self.say("green button")
        self.assertIn(self.flow.last_speak["text"], rendered("cell_origin", "serious"))
        self.assertEqual(self.say("oh,"), self.flow.last_speak["text"])
        self.assertNotIn("from oh", self.flow.last_speak["text"])

    def test_corrections_are_taken_directly(self):
        self.say(command="next_question")
        self.say(ONE_SHOT[1][0])
        self.say("yes.")
        self.model.table["green button from the back shell"] = answer(item("green button", 1, "back shell"))
        self.say(ONE_SHOT[8][0].replace("front", "back"))  # misheard origin
        for correction, expected in (("no front shell", "Cell 8: green button, from front shell. Right?"),
                                     ("no, from Dpad", "Cell 8: green button, from Dpad. Right?")):
            self.assertEqual(self.say(correction), expected, correction)
        self.assertEqual(self.api.answers.get("Q8"), None)  # still nothing written

    def test_an_answer_cut_off_mid_speech_gets_go_on_not_a_re_ask(self):
        self.say(command="next_question")
        self.say(ONE_SHOT[1][0])
        self.assertIn(self.say("no, from from the back of the controller, lef..."), rendered("go_on", "serious"))
        self.assertEqual(self.flow.agent._events.get("cell-Q1", {}).get("reasks", 0), 0)
        self.assertEqual(self.flow.current["stage"], "confirm")

    def test_persona_lines_do_not_repeat_until_the_slot_is_used_up_and_flavour_is_every_other_line(self):
        agent = dialogue_flow.DialogueAgent("unhinged", Executor(None))
        lines = [agent.recorded_line(f"cell-{n}") for n in range(40)]
        flavoured = lines[1::2]  # the even lines carry the persona, the odd ones are plain
        unhinged_lines = set(selector.variants("recorded", "unhinged"))
        self.assertTrue(all(line in unhinged_lines for line in flavoured))
        self.assertTrue(all(line in selector.variants("recorded", "serious") for line in lines[0::2]))
        round_one = flavoured[:len(unhinged_lines)]
        self.assertEqual(len(set(round_one)), len(unhinged_lines))  # every funny line once before any repeats
        self.assertTrue(all(a != b for a, b in zip(lines, lines[1:])))

    def test_catalog_has_many_short_unhinged_lines_and_no_insults(self):
        for kind in ("recorded", "cell_open"):
            for persona in ("serious", "playful", "unhinged"):
                self.assertGreaterEqual(len(selector.variants(kind, persona)), 12, (kind, persona))
        for kind in selector.CATALOG["phrases"]:
            for line in selector.variants(kind, "unhinged"):
                if kind in ("recorded", "cell_open", "cell_again", "go_on", "cell_origin", "cell_how_many"):
                    self.assertLessEqual(len(line.replace("{where}", "cell 8").replace("{Where}", "Cell 8").split()), 10, line)
                self.assertNotRegex(line.lower(), r"stupid|idiot|dumb|moron|shut up|hate")


class AutoBindTests(unittest.TestCase):
    def test_finished_job_binds_the_uploading_guided_session_and_marks_the_dialogue_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, data = Path(tmp) / "ws", Path(tmp) / "receiver"
            video_id = "ab" * 16
            directory = data / "videos" / "guided-1" / video_id
            directory.mkdir(parents=True)
            (directory / "meta.json").write_text(json.dumps({"video_id": video_id, "session_id": "guided-1", "status": "complete"}),
                                                 encoding="utf-8")
            root.mkdir()
            (root / "capture-settings.json").write_text(json.dumps({"receiver_data_dir": str(data)}), encoding="utf-8")
            (data / videos.VOICE_BINDINGS).write_text(json.dumps({"other": "keep"}), encoding="utf-8")
            (root / "video-x").mkdir()
            videos._bind_voice(root, video_id, "video-x")
            self.assertEqual(json.loads((data / videos.VOICE_BINDINGS).read_text(encoding="utf-8")),
                             {"other": "keep", "guided-1": "video-x"})


if __name__ == "__main__":
    unittest.main()
