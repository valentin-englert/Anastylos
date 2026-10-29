"""Question state machine and L0 templates (port of seed/h3r0.py)."""

import importlib.util
import tempfile
import unittest
from pathlib import Path

from support import LIVE, PHASE_WORDS, memory_lines, open_coordinator, screen, typed, workspace

import states as st  # noqa: E402
import templates  # noqa: E402
from questions import QuestionBook  # noqa: E402

SEED = LIVE / "seed" / "h3r0.py"


def load_seed():
    spec = importlib.util.spec_from_file_location("h3r0_seed", SEED)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StateMachineTest(unittest.TestCase):
    def test_happy_path_and_side_states(self):
        s = st.QuestionState(st.make_op_key("live-x", "Q-P1-01", 1))
        for target in (st.ASKED, st.ANSWERED, st.READ_BACK, st.CONFIRMED, st.COMMITTED):
            s.move(target)
        self.assertEqual(s.state, st.COMMITTED)
        for target in (st.ASKED, st.SUPERSEDED, st.FAILED):
            self.assertFalse(s.can(target))

    def test_only_a_read_back_leads_to_confirmed(self):
        for state in (st.OPEN, st.ASKED, st.ANSWERED, st.DEFERRED, st.FAILED):
            self.assertNotIn(st.CONFIRMED, st.TRANSITIONS[state])
        self.assertEqual({k for k, v in st.TRANSITIONS.items() if st.CONFIRMED in v}, {st.READ_BACK})

    def test_confirmed_cannot_be_superseded_or_deferred(self):
        s = st.QuestionState("live-x:Q:1", st.CONFIRMED)
        with self.assertRaises(st.TransitionError):
            s.move(st.SUPERSEDED)
        self.assertEqual(s.state, st.CONFIRMED)

    def test_op_key_round_trip(self):
        key = st.make_op_key("live-ab12", "Q-P2-02", 3)
        self.assertEqual(key, "live-ab12:Q-P2-02:3")
        self.assertEqual(st.parse_op_key(key), ("live-ab12", "Q-P2-02", 3))
        with self.assertRaises(ValueError):
            st.make_op_key("live:x", "Q", 1)


class CoordinatorFlowTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = workspace(Path(self._tmp.name))
        self.c = open_coordinator(self.ws)

    def tearDown(self):
        self.c.close()
        self._tmp.cleanup()

    def test_reject_read_back_asks_again_and_writes_nothing(self):
        op_key = self.c.ask("Q-P1-01")["op_key"]
        self.c.receive(typed(op_key, "Schraube aus dem Loch oben links"))
        reply = self.c.receive(typed(op_key, "nein"))
        self.assertEqual(reply["status"], "asked_again")
        self.assertEqual(self.c.states[op_key].state, st.ASKED)
        self.assertEqual(len(memory_lines(self.ws)), 7)

    def test_unclear_read_back_reply_repeats_it(self):
        op_key = self.c.ask("Q-P1-01")["op_key"]
        self.c.receive(typed(op_key, "Schraube aus dem Loch oben links"))
        reply = self.c.receive(typed(op_key, "vielleicht"))
        self.assertEqual(reply["status"], "read_back")
        self.assertEqual(self.c.states[op_key].state, st.READ_BACK)

    def test_unsure_defers_and_the_question_can_be_asked_again(self):
        op_key = self.c.ask("Q-P1-01")["op_key"]
        self.assertEqual(self.c.receive(typed(op_key, "weiß nicht"))["status"], "deferred")
        self.assertEqual(self.c.states[op_key].state, st.DEFERRED)
        self.assertEqual(self.c.ask("Q-P1-01")["op_key"], op_key)

    def test_read_back_state_survives_a_restart_and_commits_after_it(self):
        op_key = self.c.ask("Q-P1-01")["op_key"]
        self.c.receive(typed(op_key, "Schraube aus dem Loch oben links"))
        self.c.close()
        self.c = open_coordinator(self.ws)
        self.assertEqual(self.c.states[op_key].state, st.READ_BACK)
        self.assertEqual(self.c.receive(typed(op_key, "ja"))["status"], "committed")
        dec = memory_lines(self.ws)[7]
        self.assertEqual((dec["operator_label"], dec["source"]), ("Schraube", "dem Loch oben links"))
        self.assertEqual(memory_lines(self.ws)[8]["location"], "cell-1")

    def test_not_a_part_is_committed_without_memory_events(self):
        op_key = self.c.ask("Q-P3-03")["op_key"]
        self.assertEqual(self.c.receive(typed(op_key, "das war kein Teil"))["status"], "read_back")
        self.assertEqual(self.c.receive(typed(op_key, "ja"))["status"], "committed")
        self.assertEqual(len(memory_lines(self.ws)), 7)

    def test_input_for_another_session_is_rejected(self):
        self.c.ask("Q-P1-01")
        self.assertEqual(self.c.receive(typed("live-other:Q-P1-01:1", "ja"))["status"], "rejected")


class TemplateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = workspace(Path(self._tmp.name))
        self.c = open_coordinator(self.ws)

    def tearDown(self):
        self.c.close()
        self._tmp.cleanup()

    def test_every_l0_prompt_passes_the_rubric(self):
        for qid in self.c.book.live_order():
            prompt = self.c.ask(qid)
            self.assertTrue(prompt["rubric"]["pass"], (qid, prompt["text"], prompt["rubric"]))
            self.assertEqual(prompt["wording"], "L0")

    def test_anchors_come_only_from_the_past(self):
        # obs-0001 (t 51.2 s) and obs-0002 (t 58.8 s) were written by session.py for P3
        self.assertIsNone(self.c.ask("Q-P3-01")["anchor"])  # deposit at 30.6 s: nothing earlier
        self.assertEqual(self.c.ask("Q-P3-04")["anchor"], "obs-0002")  # deposit at 66 s: the latest earlier part
        self.assertIn("nach dem Teil aus Zelle 6", self.c.ask("Q-P3-04")["text"])

    def test_committed_answer_becomes_an_anchor_and_its_candidate_is_claimed(self):
        """Q-P2-02 finding: the candidate Q-P2-01 already used must not be offered as if it were free."""
        op_key = self.c.ask("Q-P2-01")["op_key"]
        self.c.receive(typed(op_key, "L-Taste aus A"))
        self.assertEqual(self.c.receive(typed(op_key, "ja"))["status"], "committed")
        prompt = self.c.ask("Q-P2-02")
        claimed = {o["removal_event"]: o["claimed_by"] for o in prompt["screen_options"]}
        self.assertEqual(claimed, {"E-0035": "L-Taste", "E-0049": None})
        self.assertIn("„L-Taste“", prompt["text"])
        self.assertTrue(prompt["rubric"]["pass"])
        self.assertIsNone(next(o for o in prompt["screen_options"] if o["removal_event"] == "E-0035")["spoken_number"])
        reply = self.c.receive(screen(prompt["op_key"], "A"))
        self.assertIn("schon für „L-Taste“ genannt", reply["read_back"])

    def test_claims_do_not_cross_phases(self):
        # E-0049 exists in P1 and in P2; they are different removals of different custody runs
        op_key = self.c.ask("Q-P2-01")["op_key"]
        self.c.receive(typed(op_key, "R-Taste aus B"))
        self.c.receive(typed(op_key, "ja"))
        p1 = self.c.ask("Q-P1-02")
        self.assertEqual([o["claimed_by"] for o in p1["screen_options"]], [None])

    def test_missing_phase_words_fail_closed(self):
        question = self.c.book.questions["Q-P1-01"]
        with self.assertRaises(templates.MissingPhaseWords):
            templates.build_prompt(question, self.c.book.parts_reports["P1"], {"P2": "x"}, [], {})

    def test_rubric_and_geometry_match_the_seed(self):
        seed = load_seed()
        texts = [self.c.ask(qid)["text"] for qid in self.c.book.live_order()]
        texts += ["Bei 78.2-81.4 s wurde etwas entnommen. Kam es aus SRC-10 bei 186/152 mm?", "Woher kam das Teil in cell-8?"]
        for text in texts:
            for possible, has in ((True, True), (True, False), (False, False)):
                self.assertEqual(templates.rubric(text, possible, has), seed.rubric(text, possible, has))
        anchors = [{"basis": "automatic", "cell": "3", "xy": [242.28, 143.58]}, {"basis": "operator_declared", "label": "X", "xy": [190, 150]}]
        for q in self.c.book.questions.values():
            for cand in q.get("candidates", []):
                self.assertEqual(templates.describe_candidate(cand, anchors), seed.describe_candidate(cand, anchors))
        self.assertEqual(PHASE_WORDS, seed.PHASE_WORDS)


class RevisionTest(unittest.TestCase):
    def test_unchanged_reload_keeps_revisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = workspace(Path(tmp))
            book = QuestionBook(ws["runs"])
            self.assertEqual(len(book.sync()), 12)
            self.assertEqual(book.reload(), [])
            self.assertEqual({book.revision(q) for q in book.questions}, {1})


if __name__ == "__main__":
    unittest.main()
