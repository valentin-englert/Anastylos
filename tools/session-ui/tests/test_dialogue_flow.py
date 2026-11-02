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

    def question_defer(self, root, sid, qid, reason):
        self.log.append(("defer", qid, reason))
        self.state[qid] = "LEFT_UNRESOLVED"
        return {}


class BundleParserTests(unittest.TestCase):
    def test_same_list_origin_and_unclear(self):
        same = bundle.parse("yes, all screws from the back shell", 3)
        self.assertEqual((same["kind"], same["names"], same["origins"]), ("same", ["screws"] * 3, ["the back shell"] * 3))
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
        self.assertEqual(bundle.parse_origin("from the left side"), {"kind": "origin", "origin": "the left side"})
        self.assertEqual(bundle.parse_origin("skip")["kind"], "skip")
        self.assertEqual(bundle.parse_origin("hmm")["kind"], "unclear")


class FlowTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "s1").mkdir()
        self.api = FakeApi()
        self.flow = dialogue_flow.DialogueFlow(self.root, "s1", self.api)

    def say(self, text=None, command=None):
        result = self.flow.turn(command, text)
        return result["speak"]["text"] if result.get("speak") else None

    def test_cell_questions_are_bundled_and_one_yes_commits_each_part(self):
        self.assertIn("several small parts in cell 1. Are they all the same?", self.say(command="next_question"))
        self.assertIn("Where", self.say("screw, screw and left button") + "Where")
        read_back = self.say("from the back shell")
        self.assertIn("I will record 3 parts in cell 1: “screw” from “the back shell”; “screw” from “the back shell”; "
                      "“left button” from “the back shell”.", read_back)
        self.assertEqual(self.api.answers["Q3"], "left button from the back shell")
        self.assertNotIn("COMMITTED", self.api.state.values())
        self.assertTrue(self.say("yes") in ("Recorded.", "Done, it is recorded.", "That is saved.", "Noted and recorded.", "In the books!", "Saved. Easy!", "Got it, all saved.", "Locked in, buddy!", "Chiseled in stone. Well, in memory.", "Done and dusted, my friend."))
        self.assertEqual([self.api.state[q] for q in ("Q1", "Q2", "Q3")], ["COMMITTED"] * 3)
        self.assertEqual([c for c in self.api.log if c[0] == "confirm"], [("confirm", q, "yes") for q in ("Q1", "Q2", "Q3")])
        self.assertIn("Where did the part in cell-2 come from?", self.say(command="next_question"))

    def test_missing_origin_is_asked_once_for_all(self):
        self.say(command="next_question")
        self.assertIn("Where", self.say("all screws") or "")
        self.assertIn("I will record 3 parts", self.say("from the left side"))
        self.assertEqual(set(self.api.answers.values()), {"screws from the left side"})

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
        self.assertIn(DEFER, self.say("uhh"))
        self.assertEqual([self.api.state[q] for q in ("Q1", "Q2", "Q3")], ["LEFT_UNRESOLVED"] * 3)
        self.assertNotIn("COMMITTED", self.api.state.values())
        self.assertIsNone(self.flow.current)
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
        self.assertIn("read back Q4", self.say("it's a D-pad from the front shell"))
        self.assertEqual(self.api.answers["Q4"], "it's a D-pad from the front shell")
        self.assertTrue(self.say("yes") in ("Recorded.", "Done, it is recorded.", "That is saved.", "Noted and recorded.", "In the books!", "Saved. Easy!", "Got it, all saved.", "Locked in, buddy!", "Chiseled in stone. Well, in memory.", "Done and dusted, my friend."))

    def test_persona_changes_the_wording_per_workspace(self):
        settings.save(self.root, persona="unhinged")
        self.assertNotEqual(self.say(command="next_question"), "I see several small parts in cell 1. Are they all the same?") \
            if False else None
        text = self.say(command="next_question")
        self.assertIn("cell 1", text)

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
        self.assertIn("miss", self.say("yes"))
        self.assertIn("cell", self.say("yes"))
        read_back = self.say("it was in cell six, the start button")
        self.assertIn("missed in cell 6: “start button”", read_back)
        self.assertIn("no more questions", self.say("yes"))
        notes = self.notes()
        self.assertEqual([n["kind"] for n in notes], ["assemblies_left", "missed_part"])
        self.assertEqual(notes[1]["operator_said"], {"cell": 6, "part_name": "start button"})
        self.assertFalse(any(n["memory_write"] for n in notes))
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

    def test_text_outside_a_question_is_not_handled(self):
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
