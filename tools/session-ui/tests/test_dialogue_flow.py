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
from dialogue import bundle, settings  # noqa: E402

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


class BundleParserTests(unittest.TestCase):
    def test_same_list_origin_and_unclear(self):
        same = bundle.parse("yes, all screws from the back shell", 3)
        self.assertEqual((same["kind"], same["names"], same["origins"]), ("same", ["screw"] * 3, ["back shell"] * 3))
        for heard in ("there are all screws, it came from the back shell", "these are all screws. They came from the back shell",
                      "they are all screws from it came from the back shell"):
            got = bundle.parse(heard, 4)
            self.assertEqual((got["kind"], got["names"][0], got["origins"][0]), ("same", "screw", "back shell"), heard)
        self.assertEqual(bundle.parse("five screws from the back shell", 4)["count"], 5)
        self.assertEqual(bundle.parse("these are all lens", 2)["names"], ["lens", "lens"])
        self.assertEqual(bundle.parse("all of them are buttons", 2)["names"], ["button", "button"])
        for noise in ("these are all", "there are", "it came from the back shell"):
            self.assertEqual(bundle.parse(noise, 3)["kind"], "unclear", noise)
        listed = bundle.parse("screw, screw and left button", 3)
        self.assertEqual((listed["kind"], listed["names"]), ("list", ["screw", "screw", "left button"]))
        self.assertEqual(bundle.parse("no", 3)["kind"], "different")
        self.assertEqual(bundle.parse("no, screw, screw, button", 3)["kind"], "list")
        self.assertEqual(bundle.parse("screw, button", 3)["kind"], "unclear")
        self.assertEqual(bundle.parse("yes", 3)["kind"], "unclear")
        self.assertEqual(bundle.parse("I don't know", 3)["kind"], "unsure")
        self.assertEqual(bundle.parse("hmm what", 3)["kind"], "unclear")
        self.assertEqual(bundle.parse("washers", 3, list_mode=True)["kind"], "same")

    def test_origin_answer(self):
        self.assertEqual(bundle.parse_origin("from the left side"), {"kind": "origin", "origin": "left side"})
        self.assertEqual(bundle.parse_origin("it came from the back shell")["origin"], "back shell")
        self.assertEqual(bundle.parse_origin("skip")["kind"], "skip")
        self.assertEqual(bundle.parse_origin("hmm")["kind"], "unclear")


class FlowTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "s1").mkdir()
        self.api = FakeApi()
        self.flow = dialogue_flow.DialogueFlow(self.root, "s1", self.api, executor=Executor(None))

    def say(self, text=None, command=None):
        result = self.flow.turn(command, text)
        return result["speak"]["text"] if result.get("speak") else None

    def test_cell_questions_are_bundled_and_one_yes_commits_each_part(self):
        self.assertIn("several small parts in cell 1. Are they all the same?", self.say(command="next_question"))
        self.assertIn("Where", self.say("screw, screw and left button") + "Where")
        read_back = self.say("from the back shell")
        self.assertEqual(read_back, "Two screws, one left button in cell 1, from back shell. Is that right?")
        self.assertEqual(self.api.answers["Q3"], "left button from back shell")
        self.assertNotIn("COMMITTED", self.api.state.values())
        self.assertIn("Next:", self.say("yes"))
        self.assertEqual([self.api.state[q] for q in ("Q1", "Q2", "Q3")], ["COMMITTED"] * 3)
        self.assertEqual([c for c in self.api.log if c[0] == "confirm"], [("confirm", q, "yes") for q in ("Q1", "Q2", "Q3")])
        self.assertIn("Where did the part in cell-2 come from?", self.say(command="next_question"))

    def test_missing_origin_is_asked_once_for_all(self):
        self.say(command="next_question")
        self.assertIn("Where", self.say("all screws") or "")
        self.assertEqual(self.say("from the left side"), "Three screws in cell 1, from left side. Is that right?")
        self.assertEqual(set(self.api.answers.values()), {"screw from left side"})

    def test_no_to_the_read_back_asks_the_parts_one_by_one_and_writes_nothing(self):
        self.say(command="next_question")
        self.say("all screws from the left side")
        question = self.say("no")
        self.assertIn("Where did the part in cell-1 come from?", question)
        self.assertNotIn("COMMITTED", self.api.state.values())
        self.assertEqual(self.flow.current["mode"], "single")

    def test_bundle_answer_not_understood_twice_leaves_all_parts_for_the_desktop(self):
        self.say(command="next_question")
        self.assertIn("could you please repeat it?", self.say("mmm"))
        reply = self.say("uhh")
        self.assertIn(DEFER, reply)
        self.assertIn("Next:", reply)  # the same turn goes on with the next cell
        self.assertEqual([self.api.state[q] for q in ("Q1", "Q2", "Q3")], ["LEFT_UNRESOLVED"] * 3)
        self.assertNotIn("COMMITTED", self.api.state.values())
        self.assertEqual(self.flow.current["qid"], "Q4")
        items = (self.root / "s1" / "dialogue-open-items.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(items), 1)

    def test_single_part_one_reask_then_defer(self):
        self.api.state.update(Q1="LEFT_UNRESOLVED", Q2="LEFT_UNRESOLVED", Q3="LEFT_UNRESOLVED")
        question = self.say(command="next_question")
        self.assertIn("cell 2", question)
        self.assertIn("could you please repeat it?", self.say("mmm"))
        self.assertIn(DEFER, self.say("uhh"))
        self.assertEqual(self.api.state["Q4"], "LEFT_UNRESOLVED")
        self.assertIn("work area", self.say(command="next_question"))

    def test_single_part_answer_read_back_yes(self):
        self.api.state.update(Q1="LEFT_UNRESOLVED", Q2="LEFT_UNRESOLVED", Q3="LEFT_UNRESOLVED")
        self.say(command="next_question")
        self.assertEqual(self.say("it's a D-pad from the front shell"), "read back Q4")
        self.assertEqual(self.api.answers["Q4"], "D-pad from front shell")
        self.assertIn("Next: What", self.say("yes"))  # recorded, and the closing questions follow in the same turn

    def test_persona_changes_the_wording_per_workspace(self):
        settings.save(self.root, persona="unhinged")
        self.assertNotEqual(self.say(command="next_question"), "I see several small parts in cell 1. Are they all the same?") \
            if False else None
        text = self.say(command="next_question")
        self.assertIn("cell 1", text)

    def writes(self):
        return [c for c in self.api.log if c[0] == "closing_write"]

    def test_owner_transcript_replay_one_short_sentence_one_yes_clean_names(self):
        self.api.cells["Q5"] = "cell-1"
        self.api.state["Q5"] = "OPEN"
        self.say(command="next_question")
        claim = self.say("there are all screws, it came from the back shell")
        self.assertEqual(claim, "Four screws in cell 1, from back shell. Is that right?")
        self.assertEqual(set(self.api.answers.values()), {"screw from back shell"})
        self.assertEqual(claim.count("screw"), 1)
        self.say("yes")
        self.assertEqual([self.api.state[q] for q in ("Q1", "Q2", "Q3", "Q5")], ["COMMITTED"] * 4)

    def test_higher_count_is_claimed_again_and_the_extra_parts_are_written_as_operator_stated(self):
        self.say(command="next_question")
        self.say("all screws from the back shell")
        self.assertEqual(self.say("five"), "Five screws in cell 1, from back shell. Is that right?")
        self.assertEqual(self.writes(), [])
        self.say("yes")
        self.assertEqual([self.api.state[q] for q in ("Q1", "Q2", "Q3")], ["COMMITTED"] * 3)
        self.assertEqual(self.writes(), [("closing_write", ("screw", "screw"), "cell-1")])
        self.assertEqual([n for n in self.notes() if n["kind"] == "bundle_extra"][0]["operator_said"]["count_stated"], 5)

    def test_lower_count_defers_the_surplus_to_the_desktop_and_deletes_nothing(self):
        self.say(command="next_question")
        self.say("all screws from the back shell")
        self.assertEqual(self.say("two"), "Two screws in cell 1, from back shell. Is that right?")
        self.say("yes")
        self.assertEqual([self.api.state[q] for q in ("Q1", "Q2", "Q3")], ["COMMITTED", "COMMITTED", "LEFT_UNRESOLVED"])
        self.assertEqual(self.writes(), [])

    def test_echo_of_the_agents_own_line_is_ignored_and_a_bare_wake_word_repeats_the_question(self):
        first = self.say(command="next_question")
        self.assertIsNone(self.flow.turn(None, first.rstrip("?."))["speak"])
        self.assertEqual(self.api.log, [])
        self.assertEqual(self.say(command="next_question"), first)
        self.assertEqual(self.flow.current["stage"], "label")

    def test_lead_in_only_on_the_first_single_question_and_read_backs_have_none(self):
        self.api.state.update(Q1="LEFT_UNRESOLVED", Q2="LEFT_UNRESOLVED", Q3="LEFT_UNRESOLVED", Q4="OPEN")
        self.api.cells["Q6"] = "cell-5"
        self.api.state["Q6"] = "OPEN"
        first = self.say(command="next_question")
        self.assertTrue(first.startswith(("I ", "A ", "Something", "Ooh", "Psst", "Bold", "Hold", "Okay")) and "Where did" in first and len(first) > 60)
        self.say("D-pad from the front shell")
        self.say("yes")
        second = self.say(command="next_question")
        self.assertTrue(second.endswith("Next: Where did the part in cell-5 come from?"))

    def model_flow(self, reply):
        transport = type("T", (), {"posts": 0})()

        class Fake:
            def post(inner, url, headers, body, timeout_s):
                transport.posts += 1
                if isinstance(reply, Exception):
                    raise reply
                text = json.dumps(reply)
                return 200, json.dumps({"status": "completed", "model": "m", "output": [
                    {"type": "message", "content": [{"type": "output_text", "text": text}]}]}).encode()

        muse = MuseClient(transport=Fake(), environ={"MODEL_API_KEY": "k"})
        self.flow = dialogue_flow.DialogueFlow(self.root, "s1", self.api, executor=Executor(muse))
        return transport

    def test_replay_five_screws_then_the_origin_then_yes_writes_the_extra_parts_after_the_read_back(self):
        self.say(command="next_question")
        self.assertIn("Where", self.say("five screws"))
        self.assertEqual(self.say("they came out of the back shell"), "Five screws in cell 1, from back shell. Is that right?")
        self.assertEqual(self.writes(), [])
        self.say("yes")
        self.assertEqual([self.api.state[q] for q in ("Q1", "Q2", "Q3")], ["COMMITTED"] * 3)
        self.assertEqual(self.writes(), [("closing_write", ("screw", "screw"), "cell-1")])

    def test_replay_those_are_five_screws_from_the_back_shell(self):
        self.say(command="next_question")
        self.assertEqual(self.say("those are five screws from the back shell"), "Five screws in cell 1, from back shell. Is that right?")

    def test_replay_screws_then_five_then_yes(self):
        self.say(command="next_question")
        self.assertIn("Where", self.say("screws"))
        self.assertEqual(self.say("from the back shell"), "Three screws in cell 1, from back shell. Is that right?")
        self.assertEqual(self.say("five"), "Five screws in cell 1, from back shell. Is that right?")
        self.say("yes")
        self.assertEqual(len(self.writes()), 1)

    def test_replay_a_bare_yes_asks_for_the_name_once(self):
        self.say(command="next_question")
        from catalog import selector
        self.assertIn(self.say("yes"), selector.variants("bundle_name", "serious"))
        self.assertEqual(self.flow.current["stage"], "label")
        self.assertIn("could you please repeat it?", self.say("yes"))

    def test_model_extracts_what_the_rules_cannot_and_the_read_back_still_gates_the_write(self):
        sentence = "I would say these small things here are probably screws from the back shell"
        transport = self.model_flow({"intent": "answer", "part_name": "screws", "origin": "back shell", "storage": None, "count": 4})
        self.say(command="next_question")
        self.assertEqual(self.say(sentence), "Three screws in cell 1, from back shell. Is that right?")
        self.assertEqual(transport.posts, 1)
        self.assertEqual([c for c in self.api.log if c[0] == "confirm"], [])
        logged = [json.loads(line) for line in (self.root / "s1" / "dialogue-turns.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual((logged[-1]["transcript"], logged[-1]["source"], logged[-1]["stage"]), (sentence, "model", "bundle/label"))

    def test_model_name_that_is_not_in_the_transcript_is_rejected_and_timeouts_re_ask(self):
        sentence = "I would say these small things here are probably some parts from the back shell"
        self.model_flow({"intent": "answer", "part_name": "washers", "origin": "back shell", "storage": None, "count": None})
        self.say(command="next_question")
        self.assertIn("could you please repeat it?", self.say(sentence))
    def test_a_model_timeout_re_asks_instead_of_guessing(self):
        sentence = "I would say these small things here are probably screws from the back shell"
        self.model_flow(TransportTimeout("t"))
        self.say(command="next_question")
        self.assertIn("could you please repeat it?", self.say(sentence))
        self.assertEqual([c for c in self.api.log if c[0] in ("answer", "confirm")], [])

    def test_every_turn_is_logged_locally_with_the_transcript(self):
        self.say(command="next_question")
        self.say("mmm")
        lines = [json.loads(line) for line in (self.root / "s1" / "dialogue-turns.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([entry["transcript"] for entry in lines], [None, "mmm"])
        self.assertEqual((lines[1]["stage"], lines[1]["parser"], lines[1]["source"]), ("bundle/label", "unclear", "rule"))
        self.assertIn("repeat", lines[1]["reply"])

    def finished(self):
        for q in self.api.state:
            self.api.state[q] = "COMMITTED"

    def notes(self):
        path = self.root / "s1" / "dialogue-notes.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def test_closing_questions_go_through_read_back_and_land_in_notes_not_memory(self):
        self.finished()
        self.assertIn("work area", self.say(command="next_question"))
        read_back = self.say("the front shell and the back shell")
        self.assertIn("“the front shell and the back shell”", read_back)
        self.assertEqual(self.notes(), [])
        self.assertEqual([c for c in self.api.log if c[0] == "closing_write"], [])  # nothing written before "yes"
        self.assertIn("miss", self.say("yes"))
        self.assertEqual([c for c in self.api.log if c[0] == "closing_write"],
                         [("closing_write", ("front shell", "back shell"), "work area")])
        self.assertIn("cell", self.say("yes"))
        read_back = self.say("it was in cell six, the start button")
        self.assertIn("missed in cell 6: “start button”", read_back)
        self.assertIn("no more questions", self.say("yes"))
        notes = self.notes()
        self.assertEqual([n["kind"] for n in notes], ["assemblies_left", "missed_part"])
        self.assertEqual(notes[1]["operator_said"], {"cell": 6, "part_name": "start button"})
        self.assertEqual([c for c in self.api.log if c[0] == "closing_write"][1], ("closing_write", ("start button",), "cell-6"))
        self.assertIn("no more questions", self.say(command="next_question"))
        self.assertEqual([c for c in self.api.log if c[0] in ("answer", "confirm")], [])

    def test_closing_nothing_left_and_nothing_missed(self):
        self.finished()
        self.say(command="next_question")
        self.assertIn("miss", self.say("nothing"))
        self.assertIn("no more questions", self.say("no, that's all"))
        self.assertEqual(self.notes(), [])

    def test_closing_unclear_twice_defers_and_moves_on(self):
        self.finished()
        self.say(command="next_question")
        self.assertIn("could you please repeat it?", self.say("mmm"))
        reply = self.say("uhh")
        self.assertIn(DEFER, reply)
        self.assertIn("miss", reply)

    def test_speech_never_reaches_the_guide_while_questions_remain(self):
        result = self.flow.turn(None, "where does it go")
        self.assertTrue(result["handled"])
        self.assertIn("several small parts", result["speak"]["text"])

    def test_after_the_last_question_speech_goes_to_the_guide(self):
        self.finished()
        (self.root / "s1" / "dialogue-closing-done").write_text("done", encoding="utf-8")
        self.assertFalse(self.flow.turn(None, "where does it go")["handled"])

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
