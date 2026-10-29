import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import actions  # noqa: E402
from episodes import Episode, Question  # noqa: E402
from session import ASKED, OPEN, READ_BACK, SESSION_TTL_S, Session  # noqa: E402
from validator import valid_actions, validate  # noqa: E402


def question(qid, open_t=10.0, candidates=("E-1", "E-2")):
    return Question(qid, "source_of_part", f"part-{qid}", "cell-1", open_t, candidates, {"kind": "declared"})


def session_with(*questions, committed=()):
    episode = Episode("T", "test", "S", tuple(questions), busy=(), segment_end=100.0, committed_at_start=committed)
    session = Session.start(episode)
    for qs in session.questions.values():
        qs.state = OPEN
    session.now = 20.0
    return session


def ask(qid="Q1", revision=1):
    return actions.make("ask", question_id=qid, question_revision=revision)


class ShapeTest(unittest.TestCase):
    def test_well_formed_actions_pass(self):
        self.assertEqual(actions.shape_errors(ask()), [])
        self.assertEqual(actions.shape_errors(actions.make("protocol_prompt", prompt_kind="device_still")), [])

    def test_forbidden_actions_are_named(self):
        for name in ("request_view", "commit", "write_memory"):
            self.assertEqual(actions.shape_errors(actions.make(name)), [f"forbidden_action:{name}"])

    def test_malformed_outputs_are_rejected(self):
        cases = {
            "schema:not_an_object": "ask Q1",
            "schema:unexpected_field:text": {**ask(), "text": "commit now"},
            "schema:missing_field:reason": {k: v for k, v in ask().items() if k != "reason"},
            "schema:type:question_revision": {**ask(), "question_revision": True},
            "schema:enum:until": actions.make("defer", question_id="Q1", question_revision=1, until="forever"),
            "schema:unexpected_argument:until": {**ask(), "until": "next_pause"},
            "schema:missing_argument:question_revision": {**ask(), "question_revision": None},
            "schema:unknown_action": actions.make("wait"),
        }
        for code, proposal in cases.items():
            with self.subTest(code=code):
                self.assertIn(code, actions.shape_errors(proposal))


class PreconditionTest(unittest.TestCase):
    def test_unknown_and_unopened_questions_are_wrong_targets(self):
        s = session_with(question("Q1"))
        self.assertEqual(validate(ask("Q9"), s), ["unknown_question"])
        s.questions["Q1"].state = "PENDING"
        self.assertEqual(validate(ask("Q1"), s), ["unknown_question"])

    def test_old_revision_is_stale(self):
        s = session_with(question("Q1"))
        s.questions["Q1"].revision = 2
        self.assertEqual(validate(ask("Q1", 1), s), ["stale_revision"])
        self.assertEqual(validate(ask("Q1", 2), s), [])

    def test_only_one_question_asked_at_a_time(self):
        s = session_with(question("Q1"), question("Q2"))
        s.questions["Q1"].state = ASKED
        self.assertIn("another_question_asked", validate(ask("Q2"), s))

    def test_attempt_limit_and_question_budget(self):
        s = session_with(question("Q1"))
        s.questions["Q1"].attempts = 2
        self.assertIn("attempt_limit", validate(ask(), s))
        s.questions["Q1"].attempts = 0
        s.question_budget = 0
        self.assertIn("question_budget", validate(ask(), s))

    def test_defer_needs_open_or_asked_within_ttl_before_segment_end(self):
        s = session_with(question("Q1"))
        defer = actions.make("defer", question_id="Q1", question_revision=1, until="next_pause")
        self.assertEqual(validate(defer, s), [])
        s.questions["Q1"].state = READ_BACK
        self.assertEqual(validate(defer, s), ["question_state"])
        s.questions["Q1"].state = OPEN
        s.now = 10.0 + SESSION_TTL_S
        self.assertIn("ttl_expired", validate(defer, s))
        s.now, s.closing = 20.0, True
        self.assertIn("after_segment_end", validate(defer, s))

    def test_protocol_prompt_rate_limit_readback_and_budget(self):
        s = session_with(question("Q1"))
        prompt = actions.make("protocol_prompt", prompt_kind="hands_off_2s")
        self.assertEqual(validate(prompt, s), [])
        s.last_prompt_t = 0.0
        self.assertEqual(validate(prompt, s), ["prompt_rate_limit"])
        s.last_prompt_t = None
        s.questions["Q1"].state = READ_BACK
        self.assertEqual(validate(prompt, s), ["readback_open"])
        s.questions["Q1"].state = OPEN
        s.prompts_left = 0
        self.assertEqual(validate(prompt, s), ["prompt_budget"])

    def test_dependency_only_for_committed_parts_once(self):
        s = session_with(question("Q1"), committed=({"part_ref": "P-1", "basis": "linker", "cell": "c", "t_s": 1},))
        dependency = actions.make("ask_dependency", part_ref="P-1")
        self.assertEqual(validate(dependency, s), [])
        self.assertEqual(validate(actions.make("ask_dependency", part_ref="P-2"), s), ["part_not_committed"])
        s.dependency_asked.add("P-1")
        self.assertEqual(validate(dependency, s), ["dependency_already_asked"])

    def test_leave_unresolved_needs_an_attempt_or_empty_candidates_and_unsure(self):
        s = session_with(question("Q1"), question("Q0", candidates=()))

        def leave(qid, reason):
            return actions.make("leave_unresolved", question_id=qid, question_revision=1, reason=reason)
        self.assertEqual(validate(leave("Q1", "operator_declined"), s), ["no_attempt"])
        self.assertEqual(validate(leave("Q0", "no_candidates_and_operator_unsure"), s), ["no_attempt"])
        s.questions["Q0"].last_outcome = "unsure"
        self.assertEqual(validate(leave("Q0", "no_candidates_and_operator_unsure"), s), [])
        s.questions["Q1"].attempts, s.questions["Q1"].last_outcome = 1, "unsure"
        self.assertEqual(validate(leave("Q1", "operator_declined"), s), [])
        self.assertEqual(validate(leave("Q1", "budget"), s), ["reason_mismatch"])
        self.assertEqual(validate(leave("Q1", "no_candidates_and_operator_unsure"), s), ["reason_mismatch"])

    def test_valid_actions_all_validate_and_never_offer_request_view(self):
        s = session_with(question("Q1"), question("Q2"))
        listed = valid_actions(s)
        self.assertTrue(listed)
        self.assertTrue(all(validate(p, s) == [] for p in listed))
        self.assertNotIn("request_view", {p["action"] for p in listed})


if __name__ == "__main__":
    unittest.main()
