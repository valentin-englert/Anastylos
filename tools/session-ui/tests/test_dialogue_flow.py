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
        self.answers, self.log = {}, []

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

    def closing_write(self, root, sid, note_id, labels, location, said):
        self.log.append(("closing_write", tuple(labels), location))
        return {"status": "committed", "results": []}

    def question_defer(self, root, sid, qid, reason):
        self.log.append(("defer", qid, reason))
        self.state[qid] = "LEFT_UNRESOLVED"
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
    return [line.replace("{where}", where) for line in selector.variants(kind, persona)]


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
    "in the start membrane": answer(item("start membrane")),
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

    def test_names_must_be_real_words_the_operator_said(self):
        check = cell_items.check_items
        self.assertEqual(check([item("screw", 2)], "two screws"), [{"name": "screw", "count": 2, "origin": None}])
        said = "which cell why only one part two whys the"
        for bad in ("why", "which cell", "only one part", "two whys", "the"):
            self.assertEqual(check([item(bad, 1)], said), [], bad)
        self.assertEqual(check([item("washer", 1)], "two screws"), [])
        self.assertEqual(check([item("screw", 5)], "some screws")[0]["count"], None)  # a number must have been said
        self.assertEqual(check([item("screw", None, "the back shell")], "a screw from the back shell")[0]["origin"], "back shell")

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
        self.assertTrue(reply.endswith(tuple(selector.variants("cell_open", "serious")[0:1] + ["What's in cell 2?"])) or "cell 2" in reply)

    def test_replay_the_whys_never_reach_memory(self):
        self.with_model()
        self.api.state.update(Q1="LEFT_UNRESOLVED", Q2="LEFT_UNRESOLVED", Q3="LEFT_UNRESOLVED")
        self.say(command="next_question")
        self.assertEqual(self.say("why"), self.flow.last_speak["text"])
        self.assertIn("could you please repeat it?", self.say("it came from left and right button"))
        self.assertEqual(self.coordinator_calls(), [])

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

    def test_an_empty_cell_leaves_everything_unresolved_without_asking_again(self):
        self.say(command="next_question")
        reply = self.say("nothing")
        self.assertEqual([self.api.state[q] for q in ("Q1", "Q2", "Q3")], ["LEFT_UNRESOLVED"] * 3)
        self.assertIn("cell 2", reply)
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
        self.assertIn("could you please repeat it?", self.say("mmm"))
        reply = self.say("uhh")
        self.assertIn("Why don't we resolve this later on the desktop?", reply)
        self.assertIn("cell 2", reply)
        self.assertEqual([self.api.state[q] for q in ("Q1", "Q2", "Q3")], ["LEFT_UNRESOLVED"] * 3)
        self.assertEqual(self.flow.current["qids"], ["Q4"])
        result = self.flow.turn(None, "d-pad from the front shell")  # no command needed: still the question round
        self.assertTrue(result["handled"])

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
        self.assertIn(self.say(command="next_question"), rendered("cell_open", "unhinged"))

    def test_a_read_back_left_open_by_an_older_flow_is_closed_with_no(self):
        self.api.state["Q1"] = "READ_BACK"
        self.say(command="next_question")
        self.assertIn(("confirm", "Q1", "no"), self.api.log)
        self.assertNotIn("COMMITTED", self.api.state.values())

    # -- closing questions

    def test_closing_questions_go_through_read_back_and_land_in_memory_only_after_yes(self):
        self.with_model({"the front shell, the back shell and the PCB": answer(item("front shell"), item("back shell"), item("PCB")),
                         "in the start membrane": answer(item("start membrane"))})
        self.finished()
        self.assertIn("work area", self.say(command="next_question"))
        self.assertEqual(self.say("the front shell, the back shell and the PCB"), "Still in the work area: front shell, back shell, PCB. Right?")
        self.assertEqual(self.writes(), [])
        self.assertIn("miss", self.say("yes"))
        self.assertEqual(self.writes(), [("closing_write", ("front shell", "back shell", "PCB"), "work area")])
        self.assertIn("cell", self.say("yes"))
        self.assertEqual(self.say("in cell six the start membrane"), "Missed in cell 6: start membrane. Right?")
        self.assertIn("no more questions", self.say("yes"))
        self.assertEqual(self.writes()[1], ("closing_write", ("start membrane",), "cell-6"))
        self.assertIn("no more questions", self.say(command="next_question"))

    def test_closing_nothing_left_and_nothing_missed(self):
        self.finished()
        self.say(command="next_question")
        self.assertIn("miss", self.say("nothing"))
        self.assertIn("no more questions", self.say("no, that's all"))
        self.assertEqual(self.writes(), [])

    def test_finished_video_opens_the_dialogue_on_the_first_utterance(self):
        (self.root / "s1" / "dialogue-pending-open").write_text("x", encoding="utf-8")
        result = self.flow.turn(None, "hello")
        self.assertTrue(result["handled"])
        self.assertFalse((self.root / "s1" / "dialogue-pending-open").exists())


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
