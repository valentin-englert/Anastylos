"""Voice follows the desktop selection: priority of open questions, every status spoken, stale focus, no writes."""

from __future__ import annotations

import json
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

UI_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UI_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import adapter  # noqa: E402
import assembly_help  # noqa: E402
import dialogue_flow  # noqa: E402
import server  # noqa: E402
import voice_focus  # noqa: E402
from adapter import AdapterError  # noqa: E402
from dialogue import settings  # noqa: E402
from dialogue.executor import Executor  # noqa: E402
from test_assembly_help import EVENTS, HelpCase, SESSION, sha, snapshot  # noqa: E402


class DoneApi:
    """No question is left: the coordinator reports the round as finished."""

    def question_next(self, root, sid):
        return {"done": True}

    def question_overview(self, root, sid):
        return {"questions": []}


class OpenApi(DoneApi):
    def question_next(self, root, sid):
        return {"stage": "answer", "question_id": "Q1", "op_key": "Q1", "text": "Where did the part in cell 1 come from?",
                "screen_options": []}

    def question_siblings(self, root, sid, qid):
        return {"cell": "cell-1", "qids": [qid]}


def removal(event_id, label, source, phase="phase-0001"):
    return {"type": "declared_removal", "id": event_id, "session": SESSION, "timestamp": "2026-11-02T10:00:00Z",
            "operator_label": label, "source": source, "phase": phase, "t_s": None, "observation_id": None,
            "declared_by": "operator (test)", "evidence_note": "closing answer", "supersedes": None}


def stored(event_id, target, cell):
    return {"type": "storage_link", "id": event_id, "session": SESSION, "timestamp": "2026-11-02T10:00:01Z",
            "target_id": target, "location": cell, "basis": {"kind": "human", "confirmed_by": "operator (test)"}, "supersedes": None}


class FocusCase(HelpCase):
    def setUp(self):
        super().setUp()
        voice_focus.reset()
        self.addCleanup(voice_focus.reset)
        extra = [removal("dec-0004", "back shell", None), removal("dec-0005", "clip", "back shell"),
                 stored("store-0005", "dec-0005", "cell-3"), removal("dec-0006", "latch", "back shell"),
                 stored("store-0006", "dec-0006", "cell-4"), removal("dec-0007", "foot", "back shell"),
                 stored("store-0007", "dec-0007", "cell-5"), removal("dec-0008", "foot", "back shell"),
                 stored("store-0008", "dec-0008", "cell-5")]
        with self.memory.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("".join(json.dumps(e) + "\n" for e in extra))
        self.build = adapter.guide_build(self.root, "s1")["guide_build"]
        self.job("front-1", "back screw")
        self.link("front-1", "dec-0001")
        self.job("cable-1", "ribbon cable")
        self.link("cable-1", "dec-0002")
        self.job("outline-a", "part 1")
        self.link("outline-a", "dec-0004")
        self.job("loose-1", "something")
        (self.session_dir / "drawings" / "explosion.json").write_text(json.dumps({"layers": [
            {"job": "outline-a", "label": "backshell", "confirmed_by_operator": True},
            {"job": "pcb-1", "label": "pcb", "confirmed_by_operator": True}]}), encoding="utf-8")
        (self.session_dir / "dialogue-closing-done").write_text("done", encoding="utf-8")

    def flow(self, api=None) -> dialogue_flow.DialogueFlow:
        return dialogue_flow.DialogueFlow(self.root, "s1", api or DoneApi(), executor=Executor(None))

    def focus(self, job, age_s: float = 0.0):
        return voice_focus.set_focus(self.root, "s1", job, now=time.time() - age_s)

    def turn(self, text, api=None) -> dict:
        return self.flow(api).turn(None, text)


class PriorityAndStatusTest(FocusCase):
    def test_an_open_question_round_keeps_priority_over_the_selected_part(self):
        self.focus("front-1")
        result = self.turn("where does this part belong", api=OpenApi())
        self.assertEqual(result["speak"]["kind"], "question")
        self.assertIn("Cell 1", result["speak"]["text"])
        self.assertIsNone(voice_focus.view(self.root, "s1")["voice"])

    def test_origin_phrase_is_answered_for_the_focused_part_from_the_checked_text(self):
        self.focus("front-1")
        result = self.turn("Where does this part belong?")
        self.assertTrue(result["handled"])
        speak = result["speak"]
        self.assertEqual(speak["kind"], "guide_verified")
        self.assertEqual(speak["basis_ref"], self.build["package_sha256"])
        self.assertIn("goes back to “the back shell”", speak["text"])
        self.assertNotIn("[", speak["text"])  # the citation tail is not read aloud
        self.assertIn(assembly_help.COARSE_ORIGIN, speak["text"])  # coarse origin: said so, no install point named
        voice = voice_focus.view(self.root, "s1")["voice"]
        self.assertEqual((voice["status"], voice["target_job"], voice["spoken"]), ("coarse_origin", "front-1", speak["text"]))
        self.assertEqual(voice["result"]["package_sha256"], self.build["package_sha256"])

    def test_storage_phrase_says_where_the_part_lies_now(self):
        self.focus("front-1")
        speak = self.turn("where did I put it")["speak"]
        self.assertIn("cell 1", speak["text"])
        self.assertNotIn("back shell", speak["text"])

    def test_a_named_install_point_is_answered_as_stored(self):
        self.focus("cable-1")
        speak = self.turn("where does it go")["speak"]
        self.assertIn("ribbon connector J2", speak["text"])
        self.assertIn("according to the operator", speak["text"])

    def test_an_assembly_speaks_its_parts_and_layer_neighbours(self):
        self.focus("outline-a")
        speak = self.turn("where does this part belong")["speak"]
        self.assertEqual(speak["text"], "Back shell: back screw in cell 1, clip in cell 3, and 3 more. "
                                        "Layer 1 of 2, below it the pcb.")
        self.assertEqual(speak["kind"], "guide_verified")

    def test_other_statuses_speak_their_fixed_message(self):
        self.focus("loose-1")
        self.assertEqual(self.turn("where does this part belong")["speak"]["text"], assembly_help.NEEDS_LINK)
        self.assertEqual(voice_focus.view(self.root, "s1")["voice"]["status"], "needs_link")
        self.focus("front-1")
        with self.memory.open("a", encoding="utf-8", newline="\n") as handle:  # memory moved on, guide not rebuilt
            handle.write(json.dumps(stored("store-0099", "dec-0003", "cell-9")) + "\n")
        speak = self.turn("where did I put it")["speak"]
        self.assertEqual(speak["kind"], "status")
        self.assertIn("guide", speak["text"].lower())
        self.assertEqual(voice_focus.view(self.root, "s1")["voice"]["status"], "guide_unusable")

    def test_a_job_that_vanished_is_a_fixed_apology_not_an_error(self):
        self.focus("loose-1")
        import shutil
        shutil.rmtree(self.session_dir / "drawings" / "jobs" / "loose-1")
        self.assertEqual(self.turn("where does it go")["speak"]["text"], voice_focus.UNAVAILABLE_FOR_PART)

    def test_the_agent_name_before_the_phrase_is_ignored(self):
        settings.save(self.root, None, "Herbert")
        self.focus("front-1")
        self.assertIn("goes back to", self.turn("Herbert, where does this part belong?")["speak"]["text"])
        self.assertIn("goes back to", self.turn("hubert where does this part belong")["speak"]["text"])

    def test_asking_writes_neither_memory_nor_guide_nor_drawings(self):
        self.focus("front-1")
        paths = [self.memory, self.session_dir / "guide" / "session.json"]
        before = {p: sha(p) for p in paths}
        files = {k: v for k, v in snapshot(self.session_dir).items() if not k.startswith("dialogue-")}
        for text in ("where does this part belong", "where did I put it", "where is cell 8"):
            self.turn(text)
        self.assertEqual({p: sha(p) for p in paths}, before)
        after = {k: v for k, v in snapshot(self.session_dir).items() if not k.startswith("dialogue-")}
        self.assertEqual(after, files)


class FocusStateTest(FocusCase):
    def test_no_focus_leaves_everything_as_before(self):
        self.assertEqual(self.turn("where does this part belong"), {"handled": False})
        self.assertEqual(self.flow().turn("next_question", None)["speak"]["text"], dialogue_flow.ALL_DONE)

    def test_focus_without_a_part_asks_to_select_one(self):
        voice_focus.set_focus(self.root, "s1", None)
        result = self.turn("where does this part belong")
        self.assertEqual(result["speak"]["text"], "Please select a part on the screen first.")
        self.assertEqual(voice_focus.view(self.root, "s1")["voice"]["status"], "no_selection")

    def test_phrases_about_other_things_still_go_to_the_guide(self):
        self.focus("front-1")
        for text in ("where is cell 8", "what comes before this", "how heavy is it"):
            self.assertEqual(self.turn(text), {"handled": False}, text)

    def test_a_focus_older_than_thirty_minutes_is_ignored(self):
        self.focus("front-1", age_s=31 * 60)
        self.assertEqual(self.turn("where does this part belong"), {"handled": False})
        self.assertEqual(self.flow().turn("next_question", None)["speak"]["text"], dialogue_flow.ALL_DONE)
        self.assertFalse(voice_focus.view(self.root, "s1")["focused"])
        self.focus("front-1", age_s=29 * 60)
        self.assertTrue(self.turn("where does this part belong")["handled"])

    def test_a_bare_wake_word_names_what_the_desktop_shows(self):
        self.focus("outline-a")
        speak = self.flow().turn("next_question", None)["speak"]
        self.assertEqual(speak["text"], "controller. Selected part: backshell.")
        voice_focus.set_focus(self.root, "s1", None)
        self.assertEqual(self.flow().turn("next_question", None)["speak"]["text"], "controller. No part is selected.")

    def test_a_bare_wake_word_with_an_open_question_still_asks_the_question(self):
        self.focus("front-1")
        speak = self.flow(OpenApi()).turn("next_question", None)["speak"]
        self.assertEqual(speak["kind"], "question")

    def test_focus_is_validated_and_scoped_to_its_session(self):
        with self.assertRaises(AdapterError) as unknown_job:
            voice_focus.set_focus(self.root, "s1", "no-such-job")
        self.assertEqual(unknown_job.exception.status, 404)
        with self.assertRaises(AdapterError):
            voice_focus.set_focus(self.root, "nope", None)
        with self.assertRaises(AdapterError):
            voice_focus.set_focus(self.root, "s1", "../x")
        self.assertFalse(voice_focus.view(self.root, "s1")["focused"])


class FocusRouteTest(FocusCase):
    def setUp(self):
        super().setUp()
        self.httpd = server.serve(self.root, "127.0.0.1", 0)
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(thread.join, 30)
        self.addCleanup(self.httpd.shutdown)
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        dialogue_flow._FLOWS[(str(self.root), "s1")] = self.flow()  # the round is finished; the real adapter has no questions here
        self.addCleanup(dialogue_flow._FLOWS.pop, (str(self.root), "s1"), None)

    def call(self, path, body=None):
        request = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                         method="GET" if body is None else "POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_page_reports_focus_and_polls_the_voice_answer(self):
        self.assertFalse(self.call("/api/focus?session=s1")[1]["focused"])
        status, view = self.call("/api/focus", {"session": "s1", "job": "front-1"})
        self.assertEqual((status, view["focused"], view["job"], view["voice"]), (200, True, "front-1", None))
        status, turn = self.call("/api/dialogue/turn", {"session": "s1", "text": "where did I put it"})
        self.assertEqual((status, turn["handled"]), (200, True))
        voice = self.call("/api/focus?session=s1")[1]["voice"]
        self.assertEqual((voice["status"], voice["spoken"], voice["target_job"]), ("answered", turn["speak"]["text"], "front-1"))
        self.assertEqual(self.call("/api/focus", {"session": "s1", "job": None})[1]["job"], None)
        self.assertEqual(self.call("/api/focus", {"session": "s1", "job": "missing"})[0], 404)
        self.assertEqual(self.call("/api/focus", {"job": None})[0], 400)


if __name__ == "__main__":
    unittest.main()
