"""Session-UI adapter tests: offline, on temp FULL-01 copies (P1 fixtures), no network."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

UI_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UI_ROOT))
sys.path.insert(0, str(UI_ROOT / "demo"))

import adapter  # noqa: E402
import prepare_demo  # noqa: E402
from adapter import AdapterError  # noqa: E402


def journal_lines(root: Path) -> list[dict]:
    path = root / "live" / "journal.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class Workspace(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "workspace"
        self.root.mkdir()
        self.session_id = "p1"
        prepare_demo.make_fresh_session(self.root / self.session_id, phases=("P1",))
        self.script = prepare_demo.full01_script()

    def answer_current(self, reply: str, confirm: str = "yes") -> dict:
        asked = adapter.question_next(self.root, self.session_id)
        self.assertFalse(asked.get("done"), asked)
        answered = adapter.question_answer(self.root, self.session_id, asked["question_id"], reply, None)
        if answered["stage"] != "confirm":
            return answered
        return adapter.question_confirm(self.root, self.session_id, asked["question_id"], confirm)

    def answer_all_p1(self) -> None:
        for _ in range(3):
            asked = adapter.question_next(self.root, self.session_id)
            qid = asked["question_id"]
            answered = adapter.question_answer(self.root, self.session_id, qid, self.script[qid]["answer"][0], None)
            self.assertEqual(answered["stage"], "confirm", answered)
            done = adapter.question_confirm(self.root, self.session_id, qid, "yes")
            self.assertEqual(done["status"], "committed", done)


class StatusTest(Workspace):
    def test_fresh_session_is_incomplete_with_open_questions(self):
        summary = adapter.status(self.root, self.session_id)
        self.assertFalse(summary["complete"])
        self.assertTrue(any("still open" in item for item in summary["incomplete"]))
        overview = adapter.question_overview(self.root, self.session_id)
        self.assertEqual(len(overview["questions"]), 3)
        self.assertTrue(all(q["state"] == "OPEN" for q in overview["questions"]))
        self.assertIn("P1", [q["phase"] for q in overview["questions"]])

    def test_answered_session_is_complete(self):
        self.answer_all_p1()
        summary = adapter.status(self.root, self.session_id)
        self.assertTrue(summary["complete"], summary["incomplete"])

    def test_ids_reject_traversal(self):
        for bad in ("..", "../x", "a/b", "a\\b", ""):
            with self.subTest(bad=bad), self.assertRaises(AdapterError):
                adapter.resolve_session(self.root, bad)
        with self.assertRaises(AdapterError):
            adapter.resolve_named(self.root, self.session_id, "selections", "../x.json")

    def test_workspace_root_inside_repo_is_refused(self):
        with self.assertRaises(AdapterError):
            adapter.check_workspace_root(adapter.REPO)


class QuestionRoundTest(Workspace):
    def test_overview_keeps_surplus_and_operator_deferral_reasons_distinct(self):
        qid = adapter.question_next(self.root, self.session_id)["question_id"]
        reason = "dialogue: left unresolved after the cell was described"
        adapter.question_defer(self.root, self.session_id, qid, reason)
        before = journal_lines(self.root / self.session_id)
        overview = adapter.question_overview(self.root, self.session_id)
        question = next(q for q in overview["questions"] if q["id"] == qid)
        self.assertEqual((question["state"], question["reason"]), ("LEFT_UNRESOLVED", reason))
        self.assertEqual(journal_lines(self.root / self.session_id), before)

    def test_reconsider_unresolved_uses_new_revision_without_memory_write(self):
        asked = adapter.question_next(self.root, self.session_id)
        qid = asked["question_id"]
        memory = self.root / self.session_id / "memory" / "memory.jsonl"
        before = memory.read_bytes()
        adapter.question_defer(self.root, self.session_id, qid, "dialogue: operator skip")
        retried = adapter.question_reopen(self.root, self.session_id, qid)
        self.assertNotEqual(retried["op_key"], asked["op_key"])
        self.assertTrue(retried["op_key"].endswith(":2"))
        self.assertEqual(memory.read_bytes(), before)
        self.assertEqual(adapter.question_open(self.root, self.session_id, qid)["op_key"], retried["op_key"])
        coordinator = adapter.ask_loop.open_coordinator(adapter.SessionState.load(self.root / self.session_id))
        try:
            stale = coordinator.receive(adapter.ask_loop.operator_input(asked["op_key"], {"text": "yes"}))
            self.assertEqual(stale["status"], "stale")
        finally:
            coordinator.close()
        with self.assertRaises(AdapterError):
            adapter.question_reopen(self.root, self.session_id, qid)
        self.assertEqual(memory.read_bytes(), before)

    def test_reconsider_refuses_committed_question(self):
        asked = adapter.question_next(self.root, self.session_id)
        qid = asked["question_id"]
        self.answer_current(self.script[qid]["answer"][0])
        memory = self.root / self.session_id / "memory" / "memory.jsonl"
        before = memory.read_bytes()
        with self.assertRaises(AdapterError):
            adapter.question_reopen(self.root, self.session_id, qid)
        self.assertEqual(memory.read_bytes(), before)

    def test_answer_and_confirm_commits(self):
        asked = adapter.question_next(self.root, self.session_id)
        qid = asked["question_id"]
        self.assertIn("text", asked)
        answered = adapter.question_answer(self.root, self.session_id, qid, self.script[qid]["answer"][0], None)
        self.assertEqual(answered["status"], "read_back")
        self.assertIn("read_back", answered)
        done = adapter.question_confirm(self.root, self.session_id, qid, "yes")
        self.assertEqual(done["status"], "committed")
        states = adapter.question_overview(self.root, self.session_id)["states"]
        self.assertEqual(states[qid], "COMMITTED")

    def test_repeated_answer_and_confirm_are_no_duplicates(self):
        asked = adapter.question_next(self.root, self.session_id)
        qid = asked["question_id"]
        first = adapter.question_answer(self.root, self.session_id, qid, self.script[qid]["answer"][0], None)
        before = journal_lines(self.root / self.session_id)
        second = adapter.question_answer(self.root, self.session_id, qid, self.script[qid]["answer"][0], None)
        self.assertEqual(second["status"], "already_answered")
        self.assertEqual(second["read_back"], first["read_back"])
        self.assertEqual(journal_lines(self.root / self.session_id), before)
        committed = adapter.question_confirm(self.root, self.session_id, qid, "yes")
        self.assertEqual(committed["status"], "committed")
        before = journal_lines(self.root / self.session_id)
        repeated = adapter.question_confirm(self.root, self.session_id, qid, "yes")
        self.assertEqual(repeated["status"], "already_committed")
        self.assertEqual(journal_lines(self.root / self.session_id), before)

    def test_no_means_asked_again_and_nothing_recorded(self):
        asked = adapter.question_next(self.root, self.session_id)
        qid = asked["question_id"]
        answered = adapter.question_answer(self.root, self.session_id, qid, self.script[qid]["answer"][0], None)
        self.assertEqual(answered["status"], "read_back")
        again = adapter.question_confirm(self.root, self.session_id, qid, "no")
        self.assertEqual(again["status"], "asked_again")
        states = adapter.question_overview(self.root, self.session_id)["states"]
        self.assertEqual(states[qid], "ASKED")

    def test_unresolved_questions_keep_the_session_incomplete(self):
        committed = []
        for _ in range(2):
            asked = adapter.question_next(self.root, self.session_id)
            committed.append(asked["question_id"])
            answered = adapter.question_answer(self.root, self.session_id, asked["question_id"],
                                               self.script[asked["question_id"]]["answer"][0], None)
            self.assertEqual(answered["stage"], "confirm", answered)
            done = adapter.question_confirm(self.root, self.session_id, asked["question_id"], "yes")
            self.assertEqual(done["status"], "committed", done)
        # Only one question is left: skip once, then "I don't know" exhausts the two asks.
        remaining = adapter.question_next(self.root, self.session_id)["question_id"]
        self.assertNotIn(remaining, committed)
        skipped = adapter.question_answer(self.root, self.session_id, remaining, "skip", None)
        self.assertEqual(skipped["status"], "deferred")
        self.assertEqual(adapter.question_next(self.root, self.session_id)["question_id"], remaining)
        unsure = adapter.question_answer(self.root, self.session_id, remaining, "I don't know", None)
        self.assertEqual(unsure["status"], "deferred")
        finished = adapter.question_next(self.root, self.session_id)
        self.assertTrue(finished["done"])
        self.assertIn(remaining, finished["left_unresolved"])
        summary = adapter.status(self.root, self.session_id)
        self.assertFalse(summary["complete"])
        self.assertTrue(any("left unresolved" in item for item in summary["incomplete"]))


class GuideTest(Workspace):
    def test_build_ask_cache_and_stale(self):
        self.answer_all_p1()
        built = adapter.guide_build(self.root, self.session_id)
        self.assertIn("package_sha256", built["guide_build"])
        view = adapter.guide_view(self.root, self.session_id)
        self.assertTrue(view["usable"])
        self.assertGreater(len(view["parts"]), 0)
        part_id = view["parts"][0]["id"]
        first = adapter.guide_ask(self.root, self.session_id, f"where is {part_id}")
        self.assertEqual(first["source"], "L0")
        self.assertTrue(first["accepted"])
        second = adapter.guide_ask(self.root, self.session_id, f"where is {part_id}")
        self.assertEqual(second["source"], "cache")
        self.assertEqual(second["text"], first["text"])
        # A memory change after the build makes the guide stale; nothing is auto-rebuilt.
        import declare  # product module, used as the CLI uses it

        from state import SessionState
        book = declare.Declarations(SessionState.load(self.root / self.session_id)).open()
        try:
            proposal = book.propose_extra("P1", "stale probe part", "cell 9")
            result = book.confirm(proposal["op_key"], {"source": "typed", "text": "yes", "choice": None,
                                                       "received_at": "2026-10-31T00:00:00.000+00:00",
                                                       "transcript": None})
            self.assertEqual(result["status"], "committed")
        finally:
            book.close()
        stale = adapter.guide_view(self.root, self.session_id)
        self.assertFalse(stale["usable"])
        self.assertIn("stale", stale["reason"])
        refused = adapter.guide_ask(self.root, self.session_id, f"where is {part_id}")
        self.assertEqual(refused["source"], "refusal")
        rebuilt = adapter.guide_build(self.root, self.session_id)
        self.assertNotEqual(rebuilt["guide_build"]["package_sha256"], built["guide_build"]["package_sha256"])
        self.assertTrue(adapter.guide_view(self.root, self.session_id)["usable"])


class SelectionTest(Workspace):
    def setUp(self):
        super().setUp()
        self.answer_all_p1()
        adapter.guide_build(self.root, self.session_id)
        from state import SessionState
        self.bindings = prepare_demo.write_bindings(SessionState.load(self.root / self.session_id))

    def test_select_resolves_the_bound_part_only(self):
        listed = adapter.list_selections(self.root, self.session_id)
        self.assertEqual(len(listed["bindings"]), 2)
        resolved = adapter.select(self.root, self.session_id, listed["bindings"][0]["file"])
        self.assertEqual(resolved["event_id"], listed["bindings"][0]["event_id"])
        self.assertIn("original_location", resolved)
        self.assertIn("stored_location", resolved)

    def test_unknown_event_binding_is_refused(self):
        binding = json.loads(self.bindings[0].read_text(encoding="utf-8"))
        binding["event_id"] = "dec-9999"
        forged = self.root / self.session_id / "selections" / "forged.json"
        forged.write_text(json.dumps(binding), encoding="utf-8")
        with self.assertRaises(AdapterError):
            adapter.select(self.root, self.session_id, "forged.json")

    def test_changed_memory_refuses_without_rebinding(self):
        memory = self.root / self.session_id / "memory" / "memory.jsonl"
        with open(memory, "a", encoding="utf-8", newline="\n") as handle:
            handle.write("\n")
        with self.assertRaisesRegex(AdapterError, "SHA-256"):
            adapter.select(self.root, self.session_id, self.bindings[0].name)

    def test_selected_ask_l0_stored_claims_and_cache(self):
        from state import SessionState
        prepare_demo.write_stored_claims(SessionState.load(self.root / self.session_id), self.bindings)
        first, second = self.bindings[0].name, self.bindings[1].name
        event_id = first.removesuffix(".json")
        local = adapter.selected_ask(self.root, self.session_id, first, "origin")
        self.assertEqual(local["source"], "L0")
        stored = adapter.selected_ask(self.root, self.session_id, first, "origin",
                                      f"{event_id}-origin-positive.json")
        self.assertEqual(stored["source"], "offline_claims")
        self.assertTrue(stored["accepted"])
        replayed = adapter.selected_ask(self.root, self.session_id, first, "origin",
                                        f"{event_id}-origin-positive.json")
        self.assertEqual(replayed["source"], "cache")
        self.assertEqual(replayed["original_source"], "offline_claims")
        # The abstention replays on the second binding: the selected cache is keyed
        # by (binding, intent), so one key holds exactly one accepted document.
        abstention = adapter.selected_ask(self.root, self.session_id, second, "origin",
                                          f"{second.removesuffix('.json')}-origin-abstention.json")
        self.assertEqual(abstention["outcome"], "abstention")
        with self.assertRaises(AdapterError):
            adapter.selected_ask(self.root, self.session_id, first, "destination")


class AssemblyTest(Workspace):
    def test_progress_advances_only_through_the_current_step(self):
        self.answer_all_p1()
        adapter.guide_build(self.root, self.session_id)
        view = adapter.assembly_view(self.root, self.session_id)
        self.assertTrue(view["usable"])
        self.assertGreater(len(view["steps"]), 0)
        self.assertEqual(view["current_step"], 1)
        after = adapter.assembly_done(self.root, self.session_id, 1)
        self.assertEqual(after["steps_done"], 1)
        with self.assertRaisesRegex(AdapterError, "not the current step"):
            adapter.assembly_done(self.root, self.session_id, 1)
        for step in range(2, len(view["steps"]) + 1):
            after = adapter.assembly_done(self.root, self.session_id, step)
        self.assertTrue(after["complete"])


if __name__ == "__main__":
    unittest.main()
