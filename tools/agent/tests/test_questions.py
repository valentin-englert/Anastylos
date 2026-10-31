"""Agent M1: the question loop on copied FULL-01 v21 development data, in English, without any paid call.

Acceptance (M1): FULL-01 P1-P3 reproducible from copies; the twelve regular questions kept apart from the
EXTRA declarations; confirmed answers write what ``answers.apply`` writes; no write before "yes", no stale
or duplicate write in the crash cases; English yes / no / skip / unsure / not-a-part / unclear paths;
prefetched L1 wording bound to its revision and facts; journals conform to CONTRACT §3.4 and are readable
by the LIVE-01 scorer; a failed phase keeps the session incomplete.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1]
REPO = AGENT.parents[1]
LIVE = REPO / "tools" / "live"
FIXTURES = LIVE / "tests" / "fixtures"  # lane A's verbatim FULL-01 v21 copies (MANIFEST.json)
for path in (AGENT, LIVE / "integration", LIVE / "eval", LIVE / "providers"):
    sys.path.insert(0, str(path))

import ask_loop  # noqa: E402
import anastylos  # noqa: E402
import english  # noqa: E402
import journal_check  # noqa: E402
import states as st  # noqa: E402
from contracts import answers  # noqa: E402
from fake import FakeProposer  # noqa: E402
from memory_writer import census  # noqa: E402
from state import SessionState  # noqa: E402

OPERATOR = "operator (agent test)"
PHASES = {"P1": "while unscrewing the back cover", "P2": "while opening the housing",
          "P3": "while removing parts from the front shell"}
REGULAR = ["Q-P1-01", "Q-P1-02", "Q-P1-03", "Q-P2-01", "Q-P2-02", "Q-P2-03",
           "Q-P3-01", "Q-P3-02", "Q-P3-03", "Q-P3-04", "Q-P3-05", "Q-P3-06"]
FIELDS = ("question", "choice", "source", "destination", "label", "not_a_part")


class SimulatedCrash(BaseException):
    """Process death at an injection point; no `except Exception` may swallow it."""


def apply_input() -> list[dict]:
    lines = (FIXTURES / "audit" / "apply_input.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def regular_answers() -> dict[str, dict]:
    return {a["question"]: a for a in apply_input() if a["question"] != "EXTRA"}


def make_session(root: Path, phases=("P1", "P2", "P3"), failed=()) -> SessionState:
    """A session as after `phase` for each tag: run folders copied from the fixtures, memory before answers."""
    state = SessionState.create(root, "SNES controller (FULL-01 copy)", OPERATOR)
    shutil.copyfile(FIXTURES / "audit" / "memory.before-answers.jsonl", state.memory_dir / "memory.jsonl")
    for tag in phases:
        state.begin_phase(tag, PHASES[tag], FIXTURES / tag / "parts.json", "0" * 64)
        if tag in failed:
            state.run_dir(tag).mkdir(parents=True)
            state.fail_phase(tag, "custody step failed (simulated)")
            continue
        state.run_dir(tag).mkdir(parents=True)
        for name in ("questions.json", "parts.json"):
            shutil.copyfile(FIXTURES / tag / name, state.run_dir(tag) / name)
        state.finish_phase(tag, {"questions": len(json.loads((FIXTURES / tag / "questions.json").read_text(
            encoding="utf-8"))["questions"])})
    return state


def english_reply(answer: dict, tokens: dict[str, str]) -> str:
    """How an English-speaking operator types an answer.py answer: labels and places stay verbatim."""
    if answer.get("not_a_part"):
        return "not a part"
    if answer.get("choice"):
        token = next(t for t, event in tokens.items() if event == answer["choice"])
        return f"{answer['label']} from {token}" if answer.get("label") else token
    if answer.get("source") is not None:
        return f"{answer['label']} from {answer['source']}"
    return f"{answer['label']} to {answer['destination']}"


def full01_script(runs: list[Path]) -> dict:
    questions, _ = answers.load_runs(runs)
    script = {}
    for qid, a in regular_answers().items():
        if qid not in questions:
            continue
        tokens = {chr(ord("A") + i): c["removal_event"] for i, c in enumerate(questions[qid].get("candidates", []))}
        script[qid] = {"answer": [english_reply(a, tokens)], "confirm": ["yes"]}
    return script


def memory(state: SessionState) -> list[dict]:
    return [json.loads(line) for line in (state.memory_dir / "memory.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]


def content(event: dict) -> dict:
    return {k: v for k, v in event.items() if k not in ("id", "timestamp", "target_id")}


def journal(state: SessionState) -> list[dict]:
    path = state.root / "live" / "journal.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def committed_answers(state: SessionState) -> dict[str, dict]:
    done = {r["op_key"] for r in journal(state) if r["kind"] == "done"}
    return {r["question_id"]: r["answer"] for r in journal(state) if r["kind"] == "intent" and r["op_key"] in done}


def crash_at(point: str, nth: int = 1):
    seen = {"n": 0}

    def fault(at: str) -> None:
        if at == point:
            seen["n"] += 1
            if seen["n"] == nth:
                raise SimulatedCrash(point)
    return fault


def in_process_memory_call(session_dir: Path, argv: list) -> dict:
    """``anastylos_memory.cli.main`` without a subprocess, for the crash matrix (speed only)."""
    from anastylos_memory import cli
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(["--session-dir", str(session_dir), *map(str, argv)])
    if code:
        raise RuntimeError(f"memory cli exit {code}")
    return json.loads(out.getvalue())


class Case(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def session(self, name="s", **kwargs) -> SessionState:
        return make_session(self.tmp / name, **kwargs)

    def reference_memory(self, committed: dict[str, dict]) -> list[dict]:
        """What answers.apply writes for the same answers, in the same order, on a fresh copy."""
        ref = make_session(Path(tempfile.mkdtemp(dir=self.tmp)) / "reference")
        questions, _ = answers.load_runs(ref.done_runs())
        answers.apply(list(committed.values()), questions, ref.memory_dir, OPERATOR)
        return memory(ref)


class Full01Reference(Case):
    def test_the_twelve_regular_questions_reproduce_answers_apply(self):
        state = self.session()
        operator = ask_loop.ScriptedOperator(full01_script(state.done_runs()))
        report = ask_loop.run(state, operator, memory_call=in_process_memory_call)
        self.assertEqual(sorted(report.committed), sorted(REGULAR))
        committed = committed_answers(state)
        for qid, expected in regular_answers().items():
            with self.subTest(question=qid):
                self.assertEqual({k: committed[qid].get(k) for k in FIELDS},
                                 {k: expected.get(k) if k != "not_a_part" else bool(expected.get(k)) for k in FIELDS})
        self.assertEqual([content(e) for e in memory(state)], [content(e) for e in self.reference_memory(committed)])
        c = census(memory(state))
        self.assertEqual((c["orphan_declared_without_storage"], c["duplicate_op_keys"], c["observations_without_tray_link"]),
                         ([], [], []))
        self.assertTrue(anastylos.summary(state)["complete"], anastylos.summary(state)["incomplete"])

    def test_extra_declarations_are_not_questions(self):
        state = self.session()
        c = ask_loop.open_coordinator(state)
        try:
            self.assertEqual(sorted(c.book.questions), REGULAR)
        finally:
            c.close()
        extra = [a for a in apply_input() if a["question"] == "EXTRA"]
        self.assertEqual(len(extra), 3)  # unprompted owner statements: outside the question loop (M2: `declare`)

    def test_every_operator_facing_line_is_english(self):
        state = self.session()
        operator = ask_loop.ScriptedOperator(full01_script(state.done_runs()))
        ask_loop.run(state, operator, memory_call=in_process_memory_call)
        shown = [line for line in operator.transcript if not line.startswith("> ")]
        self.assertGreater(len(shown), 30)
        for line in shown:
            with self.subTest(line=line[:60]):
                self.assertEqual(english.german_words(line), [], line)
        asked = [r for r in journal(state) if r["kind"] == "question_state" and r["state"] == "ASKED"]
        for record in asked:
            self.assertTrue(record["prompt"]["rubric"]["pass"], (record["question_id"], record["prompt"]["rubric"]))


class EnglishPaths(Case):
    def run_one(self, script, **kwargs):
        state = self.session(phases=("P1",))
        operator = ask_loop.ScriptedOperator(script)
        report = ask_loop.run(state, operator, **kwargs)
        return state, operator, report

    def test_no_write_before_yes_and_a_rejected_read_back_is_never_written(self):
        state = self.session(phases=("P1",))
        before = len(memory(state))
        c = ask_loop.open_coordinator(state)
        try:
            prompt = c.ask("Q-P1-01")
            reply = c.receive(ask_loop.operator_input(prompt["op_key"], {"text": "Screw from the left rail"}))
            self.assertEqual(reply["status"], "read_back")
            self.assertTrue(reply["read_back"].startswith("I will record:"))
            self.assertEqual(len(memory(state)), before)
            self.assertEqual(c.receive(ask_loop.operator_input(prompt["op_key"], {"text": "No"}))["status"], "asked_again")
            self.assertEqual(len(memory(state)), before)
            c.receive(ask_loop.operator_input(prompt["op_key"], {"text": "Screw from top-left screw hole H2"}))
            self.assertEqual(c.receive(ask_loop.operator_input(prompt["op_key"], {"text": "maybe"}))["status"], "read_back")
            self.assertEqual(len(memory(state)), before)
            self.assertEqual(c.receive(ask_loop.operator_input(prompt["op_key"], {"text": "Yes."}))["status"], "committed")
        finally:
            c.close()
        sources = [e.get("source") for e in memory(state) if e["type"] == "declared_removal"]
        self.assertEqual(sources, ["top-left screw hole H2"])

    def test_skip_defers_and_the_question_comes_back_once(self):
        script = {"Q-P1-01": {"answer": ["skip", "Screw from top-left screw hole H2"], "confirm": ["yes"]},
                  "Q-P1-02": {"answer": ["Schrauben from a deep hole"], "confirm": ["correct"]},
                  "Q-P1-03": {"answer": ["Schrauben from a deep hole"], "confirm": ["y"]}}
        state, operator, report = self.run_one(script)
        self.assertEqual(report.deferred, ["Q-P1-01"])
        self.assertEqual(sorted(report.committed), ["Q-P1-01", "Q-P1-02", "Q-P1-03"])
        self.assertLess(report.committed.index("Q-P1-02"), report.committed.index("Q-P1-01"))  # re-asked after the open ones
        self.assertIn("OK, I will ask again later.", operator.transcript)

    def test_unsure_twice_leaves_the_question_unresolved_and_the_session_incomplete(self):
        script = {"Q-P1-01": {"answer": ["I don't know", "not sure"]},
                  "Q-P1-02": {"answer": ["not a part"], "confirm": ["yes"]},
                  "Q-P1-03": {"answer": ["skip", "later"]}}
        state, _, report = self.run_one(script)
        self.assertEqual(set(report.unresolved), {"Q-P1-01", "Q-P1-03"})
        self.assertEqual(report.committed, ["Q-P1-02"])
        self.assertFalse(any(e["type"] == "declared_removal" for e in memory(state)))  # not a part: nothing declared
        confirmed = [r for r in journal(state) if r["kind"] == "question_state" and r["state"] == "CONFIRMED"]
        self.assertEqual(confirmed[0]["resolved"]["outcome"], "no_part")
        self.assertEqual(ask_loop.question_states(state)["Q-P1-01"], "LEFT_UNRESOLVED")

    def test_unknown_input_is_never_recorded(self):
        script = {"Q-P1-01": {"answer": ["yes", "banana?", "123456"]}}
        state, operator, report = self.run_one(script)
        self.assertIn("Q-P1-01", report.deferred)
        self.assertTrue(any(line.startswith("I did not understand that.") for line in operator.transcript))
        self.assertFalse(any(e["type"] == "declared_removal" for e in memory(state)))

    def test_screen_choice_and_spoken_option_resolve_to_the_same_candidate(self):
        state = self.session(phases=("P1", "P2"))
        c = ask_loop.open_coordinator(state)
        try:
            prompt = c.ask("Q-P2-01")
            token = next(o["token"] for o in prompt["screen_options"] if o["removal_event"] == "E-0035")
            reply = c.receive(ask_loop.operator_input(prompt["op_key"], {"choice": token}))
            self.assertEqual(reply["answer"]["choice"], "E-0035")
            self.assertIn(f"marked {token} on the screen", reply["read_back"])
        finally:
            c.close()


class Prefetch(Case):
    def wording(self, text):
        return FakeProposer(script={"question_wording": {"mode": "output", "output": {"text": text, "facts_used": []}}})

    def test_accepted_wording_is_used_for_its_own_revision_only(self):
        state = self.session(phases=("P1",))
        text = "While unscrewing the back cover: where did the first part you put in cell 1 come from? (placed at about 0:25)"
        verdicts = ask_loop.prefetch(state, self.wording(text), max_calls=1)
        self.assertEqual(verdicts["Q-P1-01"], "accepted")
        c = ask_loop.open_coordinator(state)
        try:
            prompt = c.ask("Q-P1-01")
            self.assertEqual((prompt["wording"], prompt["text"]), ("L1", text))
        finally:
            c.close()

    def test_a_revision_change_after_prefetch_falls_back_to_l0(self):
        state = self.session(phases=("P1",))
        text = "While unscrewing the back cover: where did the first part you put in cell 1 come from? (placed at about 0:25)"
        ask_loop.prefetch(state, self.wording(text), max_calls=1)
        questions_path = state.run_dir("P1") / "questions.json"
        doc = json.loads(questions_path.read_text(encoding="utf-8"))
        doc["questions"][0]["deposit_window_s"] = [w + 1.0 for w in doc["questions"][0]["deposit_window_s"]]
        questions_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        c = ask_loop.open_coordinator(state)
        try:
            prompt = c.ask("Q-P1-01")
            self.assertEqual(prompt["wording"], "L0")
            self.assertTrue(prompt["op_key"].endswith(":2"))
            self.assertTrue(any(r["kind"] == "fallback" and r["reason"].startswith("stale") for r in c.journal.records))
        finally:
            c.close()

    def test_bad_provider_results_and_german_text_keep_english_l0(self):
        cases = {"invalid_json": {"mode": "invalid_json"}, "schema_violation": {"mode": "schema_violation"},
                 "timeout": {"mode": "timeout"}, "disabled": {"mode": "disabled"},
                 "german": {"mode": "output", "output": {"text": "Beim Abschrauben der Rückseite: Woher kam das Teil in "
                                                                 "Zelle 1? (abgelegt bei etwa 0:25)", "facts_used": []}},
                 "invented cell": {"mode": "output", "output": {"text": "While unscrewing the back cover: where did the "
                                                                        "part in cell 7 come from?", "facts_used": []}}}
        for name, step in cases.items():
            with self.subTest(name):
                state = self.session(name=re.sub(r"\W", "_", name), phases=("P1",))
                verdict = ask_loop.prefetch(state, FakeProposer(script={"question_wording": step}), max_calls=1)["Q-P1-01"]
                self.assertNotEqual(verdict, "accepted")
                c = ask_loop.open_coordinator(state)
                try:
                    prompt = c.ask("Q-P1-01")
                    self.assertEqual(prompt["wording"], "L0")
                    self.assertEqual(english.german_words(prompt["text"]), [])
                finally:
                    c.close()

    def test_the_question_loop_never_calls_a_provider(self):
        class Refusing:
            def propose(self, *args):
                raise AssertionError("no model call may sit between a question and the operator")
        state = self.session(phases=("P1",))
        script = {q: {"answer": ["skip", "later"]} for q in ("Q-P1-01", "Q-P1-02", "Q-P1-03")}
        c = ask_loop.open_coordinator(state)
        self.assertIsNone(c.proposer)
        c.close()
        ask_loop.run(state, ask_loop.ScriptedOperator(script))
        self.assertFalse(any(r["kind"] == "provider_call" for r in journal(state)))
        del Refusing


class Recovery(Case):
    POINTS = ["after_answered", "after_confirmed", "after_intent", "after_memory:declared_removal",
              "after_journal:declared_removal", "after_memory:storage_link", "after_journal:storage_link",
              "after_journal:result"]

    def test_a_crash_at_every_point_then_a_restart_gives_the_reference_memory(self):
        for point in self.POINTS:
            for nth in (1, 6):
                with self.subTest(point=point, nth=nth):
                    state = self.session(name=f"{point.replace(':', '_')}-{nth}")
                    script = full01_script(state.done_runs())
                    operator = ask_loop.ScriptedOperator(script)
                    with self.assertRaises(SimulatedCrash):
                        ask_loop.run(state, operator, fault=crash_at(point, nth), memory_call=in_process_memory_call)
                    # the process died: its lock handle closes; a new process continues with fresh replies
                    ask_loop.run(state, ask_loop.ScriptedOperator(full01_script(state.done_runs())),
                                 memory_call=in_process_memory_call)
                    committed = committed_answers(state)
                    self.assertEqual(sorted(committed), REGULAR)
                    self.assertEqual([content(e) for e in memory(state)],
                                     [content(e) for e in self.reference_memory(committed)])
                    c = census(memory(state))
                    self.assertEqual((c["orphan_declared_without_storage"], c["duplicate_op_keys"]), ([], []))
                    self.assertEqual(journal_check.problems(journal(state)), [])

    def test_a_confirmation_lost_before_its_intent_is_asked_again_not_written(self):
        state = self.session(phases=("P1",))
        script = {"Q-P1-01": {"answer": ["Screw from top-left screw hole H2"], "confirm": ["yes"]}}
        with self.assertRaises(SimulatedCrash):
            ask_loop.run(state, ask_loop.ScriptedOperator(script), fault=crash_at("after_confirmed"))
        self.assertFalse(any(e["type"] == "declared_removal" for e in memory(state)))
        operator = ask_loop.ScriptedOperator({"Q-P1-01": {"confirm": ["yes"]},
                                              "Q-P1-02": {"answer": ["skip", "later"]}, "Q-P1-03": {"answer": ["skip", "later"]}})
        report = ask_loop.run(state, operator)
        self.assertEqual(report.committed, ["Q-P1-01"])
        self.assertTrue(operator.transcript[0].startswith("I will record:"))  # the open read-back, shown again
        self.assertEqual(sum(e["type"] == "declared_removal" for e in memory(state)), 1)


class JournalConformance(Case):
    def test_journal_fields_follow_contract_0_2(self):
        state = self.session()
        ask_loop.run(state, ask_loop.ScriptedOperator(full01_script(state.done_runs())), memory_call=in_process_memory_call)
        records = journal(state)
        self.assertEqual(journal_check.problems(records), [])
        self.assertEqual(sum(r["kind"] == "memory_result" and r["status"] == "ok" for r in records), 12)
        self.assertEqual(records[0]["language"], "en")

    def test_the_live01_scorer_reads_the_journal_and_finds_no_integrity_violation(self):
        import score_live01
        state = self.session()
        ask_loop.run(state, ask_loop.ScriptedOperator(full01_script(state.done_runs())), memory_call=in_process_memory_call,
                     session_fields={"round": "live", "die_sha256": None, "delivery_delay_s": None})
        read = score_live01.read_journal(state.root / "live" / "journal.jsonl")
        questions = score_live01.collect_questions([read])
        self.assertEqual(sorted(questions), REGULAR)
        result = score_live01.integrity([read], questions, memory(state), {})
        for name in ("commits_without_read_back", "stale_commits", "duplicate_commits", "commits_without_intent",
                     "commits_missing_from_memory", "declarations_promoted_to_evidence", "unjournaled_declarations",
                     "duplicate_memory_ids"):
            with self.subTest(counter=name):
                self.assertEqual(result[name]["count"], 0, result[name]["items"])


class FailedPhase(Case):
    def test_a_failed_phase_is_skipped_visibly_and_keeps_the_session_incomplete(self):
        state = self.session(phases=("P1", "P2", "P3"), failed=("P2",))
        script = {q: s for q, s in full01_script(state.done_runs()).items() if not q.startswith("Q-P2")}
        operator = ask_loop.ScriptedOperator(script)
        report = ask_loop.run(state, operator, memory_call=in_process_memory_call)
        self.assertEqual(report.excluded_phases, ["P2"])
        self.assertIn("Warning: phase P2 did not finish processing; its questions are not included.", operator.transcript)
        self.assertEqual(len(report.committed), 9)
        status = anastylos.summary(state)
        self.assertFalse(status["complete"])
        self.assertTrue(any(item.startswith("phase P2 is failed") for item in status["incomplete"]))


class Cli(Case):
    def test_ask_with_a_script_and_status_are_english_and_complete(self):
        root = Path(tempfile.mkdtemp(dir=self.tmp)) / "cli"
        state = make_session(root)
        script = self.tmp / "script.json"
        script.write_text(json.dumps(full01_script(state.done_runs()), ensure_ascii=False), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(anastylos.main(["ask", "--session", str(root), "--script", str(script)]), 0)
        result = json.loads(out.getvalue())
        self.assertTrue(result["complete"], result["incomplete"])
        self.assertEqual(sorted(result["this_run"]["committed"]), REGULAR)
        self.assertEqual(result["questions"], {st.COMMITTED: 12})


if __name__ == "__main__":
    unittest.main()
