"""Agent M2: declarations (EXTRA, group), the English guide bound to the package hash, guided reassembly.

FULL-01 v21 development data (copies), no paid call: fake proposers only.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_questions as tq  # noqa: E402  (session helpers, FULL-01 script)

import anastylos  # noqa: E402
import assemble  # noqa: E402
import declare  # noqa: E402
import english  # noqa: E402
import guide  # noqa: E402
import guide_render as en  # noqa: E402
from contracts import answers  # noqa: E402
from fake import FakeProposer  # noqa: E402
from lock import LockHeld  # noqa: E402
from memory_writer import census  # noqa: E402
from state import SessionError  # noqa: E402

V21_PACKAGE = tq.REPO / "tools" / "guide" / "guide02" / "fixtures" / "session.v21.json"
GROUP_LABEL = "Schrauben (5, austauschbar)"


def extra_rows() -> list[dict]:
    return [a for a in tq.apply_input() if a["question"] == "EXTRA"]


def reply(text: str, n: int = 0) -> dict:
    return {"source": "typed", "text": text, "choice": None, "received_at": f"2026-10-30T00:00:{n:02d}.000+00:00",
            "transcript": None}


def screws(state) -> list[str]:
    events = tq.memory(state)
    return [e["id"] for e in sorted((e for e in events if e["type"] == "declared_removal"
                                     and e["operator_label"].startswith("Schraube")), key=lambda e: e["t_s"])]


def full_session(root: Path, with_group: bool = True):
    """FULL-01 as the agent records it: 12 questions, 3 EXTRA declarations, the owner's screw group."""
    state = tq.make_session(root)
    tq.ask_loop.run(state, tq.ask_loop.ScriptedOperator(tq.full01_script(state.done_runs())),
                    memory_call=tq.in_process_memory_call)
    book = declare.Declarations(state, call=tq.in_process_memory_call).open()
    try:
        for n, row in enumerate(extra_rows()):
            proposal = book.propose_extra(row["phase"], row["label"], row["destination"], row.get("source"), row.get("t_s"))
            assert book.confirm(proposal["op_key"], reply("yes", n))["status"] == "committed"
        if with_group:
            proposal = book.propose_group(screws(state), GROUP_LABEL)
            assert book.confirm(proposal["op_key"], reply("yes", 9))["status"] == "committed"
    finally:
        book.close()
    return state


def declared(state) -> dict[str, dict]:
    journal = [json.loads(line) for line in (state.root / "declare" / "journal.jsonl").read_text(encoding="utf-8").splitlines()]
    done = {r["op_key"] for r in journal if r["kind"] == "done"}
    return {r["op_key"]: r["answer"] for r in journal if r["kind"] == "intent" and r["op_key"] in done}


class Case(tq.Case):
    pass


class ExtraDeclarations(Case):
    def test_the_three_extra_declarations_reproduce_answers_apply(self):
        state = full_session(self.tmp / "s", with_group=False)
        extras = list(declared(state).values())
        self.assertEqual(len(extras), 3)
        for got, row in zip(extras, extra_rows()):
            self.assertEqual({k: got.get(k) for k in ("phase", "label", "source", "destination", "t_s")},
                             {k: row.get(k) for k in ("phase", "label", "source", "destination", "t_s")})
        committed = list(tq.committed_answers(state).values()) + extras
        reference = tq.make_session(Path(tempfile.mkdtemp(dir=self.tmp)) / "ref")
        answers.apply(committed, answers.load_runs(reference.done_runs())[0], reference.memory_dir, tq.OPERATOR)
        self.assertEqual([tq.content(e) for e in tq.memory(state)], [tq.content(e) for e in tq.memory(reference)])
        c = census(tq.memory(state))
        self.assertEqual((c["orphan_declared_without_storage"], c["duplicate_op_keys"]), ([], []))

    def test_extra_declarations_are_not_questions_of_the_loop(self):
        state = full_session(self.tmp / "s", with_group=False)
        self.assertEqual(set(tq.ask_loop.question_states(state)), set(tq.REGULAR))

    def test_no_write_before_yes_and_no_writes_nothing(self):
        state = tq.make_session(self.tmp / "s")
        before = len(tq.memory(state))
        book = declare.Declarations(state).open()
        try:
            row = extra_rows()[0]
            proposal = book.propose_extra(row["phase"], row["label"], row["destination"], row.get("source"), row.get("t_s"))
            self.assertTrue(proposal["read_back"].startswith("I will record:"))
            self.assertEqual(len(tq.memory(state)), before)
            self.assertEqual(book.confirm(proposal["op_key"], reply("maybe", 1))["status"], "read_back")
            self.assertEqual(book.confirm(proposal["op_key"], reply("no", 2))["status"], "withdrawn")
            self.assertEqual(len(tq.memory(state)), before)
            self.assertEqual(book.confirm(proposal["op_key"], reply("yes", 3))["status"], "stale")
            self.assertEqual(len(tq.memory(state)), before)
        finally:
            book.close()

    def test_stale_duplicate_and_repeated_declarations_never_write_twice(self):
        state = tq.make_session(self.tmp / "s")
        book = declare.Declarations(state).open()
        try:
            a, b = extra_rows()[0], extra_rows()[1]
            first = book.propose_extra(a["phase"], a["label"], a["destination"], a.get("source"), a.get("t_s"))
            second = book.propose_extra(b["phase"], b["label"], b["destination"], b.get("source"), b.get("t_s"))
            self.assertEqual(book.confirm(first["op_key"], reply("yes", 1))["status"], "stale")  # replaced
            self.assertEqual(book.confirm(second["op_key"], reply("yes", 2))["status"], "committed")
            self.assertEqual(book.confirm(second["op_key"], reply("yes", 2))["status"], "duplicate")
            self.assertEqual(book.confirm(second["op_key"], reply("yes", 3))["status"], "stale")
            with self.assertRaisesRegex(SessionError, "already recorded"):
                book.propose_extra(b["phase"], b["label"], b["destination"], b.get("source"), b.get("t_s"))
            with self.assertRaisesRegex(SessionError, "cannot be recorded"):
                book.propose_extra("P9", "x", "cell 3")
        finally:
            book.close()
        self.assertEqual(sum(e["type"] == "declared_removal" for e in tq.memory(state)), 1)

    def test_a_pending_declaration_survives_a_restart(self):
        state = tq.make_session(self.tmp / "s")
        row = extra_rows()[0]
        book = declare.Declarations(state).open()
        proposal = book.propose_extra(row["phase"], row["label"], row["destination"], row.get("source"), row.get("t_s"))
        book.close()
        book = declare.Declarations(state).open()
        try:
            self.assertEqual(book.pending()["op_key"], proposal["op_key"])
            self.assertEqual(book.confirm(proposal["op_key"], reply("yes"))["status"], "committed")
        finally:
            book.close()
        self.assertEqual(anastylos.declare_states(state), {"D-01": "COMMITTED"})

    def test_a_crash_at_every_write_point_recovers_to_exactly_one_declaration(self):
        row = extra_rows()[1]
        for point in ("after_confirmed", "after_intent", "after_memory:declared_removal", "after_journal:declared_removal",
                      "after_memory:storage_link", "after_journal:storage_link", "after_journal:result"):
            with self.subTest(point=point):
                state = tq.make_session(self.tmp / point.replace(":", "_"))
                book = declare.Declarations(state, fault=tq.crash_at(point)).open()
                proposal = book.propose_extra(row["phase"], row["label"], row["destination"], row.get("source"), row.get("t_s"))
                with self.assertRaises(tq.SimulatedCrash):
                    book.confirm(proposal["op_key"], reply("yes"))
                book.close()  # the process died; its lock handle closes
                book = declare.Declarations(state).open()
                try:
                    if point == "after_confirmed":  # not durable: the read-back is open again, nothing written
                        self.assertEqual(sum(e["type"] == "declared_removal" for e in tq.memory(state)), 0)
                        self.assertEqual(book.confirm(proposal["op_key"], reply("yes", 5))["status"], "committed")
                    else:
                        self.assertIsNone(book.pending())
                finally:
                    book.close()
                events = tq.memory(state)
                self.assertEqual(sum(e["type"] == "declared_removal" for e in events), 1)
                self.assertEqual(sum(e["type"] == "storage_link" for e in events), 1)
                c = census(events)
                self.assertEqual((c["orphan_declared_without_storage"], c["duplicate_op_keys"]), ([], []))
                journal = [json.loads(line) for line in book.journal.path.read_text(encoding="utf-8").splitlines()]
                self.assertEqual(sum(r["kind"] == "memory_result" and r.get("status") == "ok" for r in journal), 1)

    def test_a_group_crash_after_the_memory_call_is_found_not_rewritten(self):
        state = tq.make_session(self.tmp / "s")
        tq.ask_loop.run(state, tq.ask_loop.ScriptedOperator(tq.full01_script(state.done_runs())),
                        memory_call=tq.in_process_memory_call)
        book = declare.Declarations(state, fault=tq.crash_at("after_memory:compatible_group")).open()
        proposal = book.propose_group(screws(state), GROUP_LABEL)
        self.assertIn("no part is matched to a particular hole", proposal["read_back"])
        with self.assertRaises(tq.SimulatedCrash):
            book.confirm(proposal["op_key"], reply("yes"))
        book.close()
        declare.Declarations(state).open().close()
        groups = [e for e in tq.memory(state) if e["type"] == "compatible_group"]
        self.assertEqual(len(groups), 1)
        self.assertEqual((groups[0]["members"], groups[0]["label"], groups[0]["basis"]["kind"]),
                         (screws(state), GROUP_LABEL, "operator_statement"))

    def test_declarations_wait_while_the_question_loop_holds_the_session(self):
        state = tq.make_session(self.tmp / "s")
        c = tq.ask_loop.open_coordinator(state)
        try:
            with self.assertRaises(LockHeld):
                declare.Declarations(state).open()
        finally:
            c.close()


class Guide(Case):
    def setUp(self):
        super().setUp()
        self.state = full_session(self.tmp / "s")
        self.build = guide.build(self.state)

    def package_bytes(self) -> bytes:
        return (guide.guide_dir(self.state) / "session.json").read_bytes()

    def test_the_build_hash_is_recorded_and_a_changed_package_is_refused(self):
        self.assertEqual(self.build["package_sha256"], guide.recorded_sha256(self.state))
        first = guide.answer(self.state, "where is cell 8")
        self.assertTrue(first["accepted"])
        self.assertEqual(guide.answer(self.state, "Where is cell 8?")["source"], "cache")
        path = guide.guide_dir(self.state) / "session.json"
        path.write_bytes(self.package_bytes().replace(b"cell-8", b"cell-9"))
        refused = guide.answer(self.state, "where is cell 8")
        self.assertEqual(refused["source"], "refusal")
        self.assertIn("changed after it was built", refused["text"])
        rebuilt = guide.build(self.state)  # rebuilt from the same memory: the same bytes, the same hash
        self.assertEqual(rebuilt["package_sha256"], self.build["package_sha256"])
        self.assertEqual(guide.answer(self.state, "where is cell 8")["source"], "cache")
        book = declare.Declarations(self.state, call=tq.in_process_memory_call).open()
        try:  # the memory changes, so the package changes
            proposal = book.propose_extra("P1", "spring", "cell 12", "battery door", 5.0)
            book.confirm(proposal["op_key"], reply("yes", 7))
        finally:
            book.close()
        changed = guide.build(self.state)
        self.assertNotEqual(changed["package_sha256"], self.build["package_sha256"])
        again = guide.answer(self.state, "where is cell 8")
        self.assertEqual(again["source"], "L0")  # the answer cached for the old hash is not reused
        self.assertEqual(again["package_sha256"], changed["package_sha256"])
        builds = (guide.guide_dir(self.state) / "builds.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(builds), 3)  # append-only build record

    def test_every_rendered_step_and_answer_passes_independent_verification(self):
        plan, package = assemble.steps(self.state)
        self.assertEqual(len(plan), 14)
        documents = [{"schema": "anastylos-guide-claims@1", "claims": [c["claim"] for c in s["claims"]],
                      "insufficient_evidence": False} for s in plan]
        for question in ("where is cell 8", "where does dec-0013 go", "what comes before cell 5", "where is L-Taste",
                         "where are the Schrauben", "where does grp-0001 go"):
            result = guide.answer(self.state, question)
            self.assertTrue(result["accepted"], (question, result))
            documents.append({"schema": "anastylos-guide-claims@1", "claims": [c["claim"] for c in result["claims"]],
                              "insufficient_evidence": False})
        for document in documents:
            verdict = guide.claims.check(self.package_bytes(), guide.recorded_sha256(self.state), document=document)
            self.assertTrue(verdict.accepted, verdict.as_json())
            self.assertFalse(guide.claims.check(self.package_bytes(), "0" * 64, document=document).accepted)

    def test_unsupported_and_tampered_model_claims_are_not_spoken(self):
        bad = {"fabricated id": {"relation": "stored_in", "subject": "dec-0099", "object": "cell-8", "basis_word": None},
               "wrong cell": {"relation": "stored_in", "subject": "dec-0004", "object": "cell-2", "basis_word": None},
               "inverted basis": {"relation": "stored_in", "subject": "dec-0004", "object": "cell-8",
                                  "basis_word": "vom System zugeordnet"},
               "invented dependency": {"relation": "dependency", "subject": "dec-0004", "object": "dec-0005",
                                       "basis_word": None},
               # dec-0001 is the screw the operator said came from H2; another screw of the group never gets H2
               "unique screw hole": {"relation": "removed_from", "subject": "dec-0002", "object": "top-left screw hole H2",
                                     "basis_word": None}}
        for name, claim in bad.items():
            with self.subTest(name):
                output = {"schema": "anastylos-guide-claims@1", "claims": [claim], "insufficient_evidence": False}
                proposer = FakeProposer(script={"guide_claims": {"mode": "output", "output": output}})
                result = guide.answer(self.state, f"tell me something about {name}", proposer=proposer)
                self.assertEqual((result["source"], result["accepted"], result["text"]), ("L1", False, en.NOT_PROVEN))

    def test_a_valid_model_answer_is_rendered_in_english_from_the_verified_claims(self):
        output = {"schema": "anastylos-guide-claims@1", "insufficient_evidence": False, "claims": [
            {"relation": "stored_in", "subject": "dec-0004", "object": "cell-8", "basis_word": "laut Operator"}]}
        proposer = FakeProposer(script={"guide_claims": {"mode": "output", "output": output}})
        result = guide.answer(self.state, "which part sits in the right corner of the tray?", proposer=proposer)
        self.assertTrue(result["accepted"])
        self.assertTrue(result["text"].startswith("The part dec-0004 (operator label “L-Taste”) is in cell 8, "
                                                  "according to the operator"), result["text"])

    def test_a_slow_or_failing_model_falls_back_without_waiting_longer_than_the_limit(self):
        class Slow:
            def propose(self, task, payload, request_id):
                time.sleep(2.0)
                return {"status": "ok"}
        started = time.monotonic()
        result = guide.answer(self.state, "how do I open it", proposer=Slow(), timeout_s=0.2)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(result["source"], "refusal")
        self.assertIn("no answer within", result["findings"][0])
        for mode in ("timeout", "invalid_json", "disabled"):
            failing = guide.answer(self.state, f"how do I open it ({mode})",
                                   proposer=FakeProposer(script={"guide_claims": {"mode": mode}}))
            self.assertEqual(failing["source"], "refusal", mode)
            self.assertTrue(failing["text"].startswith("I can only answer from this session's package."))
        self.assertEqual(threading.active_count() >= 1, True)

    def test_the_package_matches_the_v21_package_up_to_ids(self):
        mine = json.loads(self.package_bytes())
        v21 = json.loads(V21_PACKAGE.read_text(encoding="utf-8"))

        def shape(package):
            parts = sorted((p.get("operator_label") or "", (p.get("stored_in") or {}).get("location"), p["basis"],
                            bool(p.get("group"))) for p in package["parts"])
            labels = {p["id"]: p.get("operator_label") or (p.get("stored_in") or {}).get("location") for p in package["parts"]}
            order = [sorted(labels[i] for i in step["parts"]) for step in package["install_order"]["steps"]]
            return parts, order
        self.assertEqual(shape(mine), shape(v21))

    def test_every_guide_and_step_sentence_is_english(self):
        plan, _ = assemble.steps(self.state)
        texts = [s["text"] for s in plan] + [guide.answer(self.state, q)["text"] for q in
                                            ("where is cell 1", "where does dec-0012 go", "how do I open it", "where is X")]
        texts += [en.NOT_PROVEN, en.UNKNOWN, en.UNCHECKABLE.format(reason="it changed after it was built"), guide.HELP]
        for text in texts:
            with self.subTest(text=text[:50]):
                self.assertEqual(english.german_words(text), [], text)


class Reassembly(Case):
    def setUp(self):
        super().setUp()
        self.state = full_session(self.tmp / "s")
        guide.build(self.state)

    def run_script(self, replies):
        operator = tq.ask_loop.ScriptedOperator({f"step-{n}": {"assemble": []} for n in range(1, 15)})
        queue = list(replies)
        operator.reply = lambda stage, qid, text, prompt: {"text": queue.pop(0) if queue else "stop"}
        return assemble.run(self.state, operator), operator

    def test_interrupt_and_resume_neither_skips_nor_repeats(self):
        now, operator = self.run_script(["done"] * 5 + ["stop"])
        self.assertEqual(now["current"]["step"], 6)
        self.assertIn("Paused at step 6. Run `assemble` again to continue.", operator.transcript)
        now, _ = self.run_script(["skip", "where is cell 1"] + ["done"] * 9)
        self.assertTrue(now["complete"])
        records = assemble._records(self.state, now["package_sha256"])
        self.assertEqual([r["step"] for r in records if r["event"] == "done"], list(range(1, 15)))
        with self.assertRaises(SessionError):
            assemble.mark_done(self.state, 3)

    def test_a_skip_is_refused_and_a_question_is_answered_in_english(self):
        now, operator = self.run_script(["skip", "where is cell 1", "stop"])
        self.assertEqual(now["current"]["step"], 1)
        self.assertIn("Steps cannot be skipped. Say done when the part is installed, or stop to pause.", operator.transcript)
        self.assertTrue(any(line.startswith("The parts of group grp-0001") for line in operator.transcript))

    def test_a_failed_return_check_reopens_its_step_and_a_passed_one_verifies(self):
        self.run_script(["done"] * 6 + ["stop"])
        returns = {"schema": assemble.RETURNS_SCHEMA, "returns": [
            {"id": "R-0001", "status": "candidate_temporal_exclusive", "departure": {"zone": "cell-6"},
             "memory_check": "MISMATCH_with_memory"},
            {"id": "R-0002", "status": "candidate_temporal_exclusive", "departure": {"zone": "cell-3"},
             "memory_check": "consistent_with_memory"},
            {"id": "R-0003", "status": "ambiguous"}]}
        path = self.tmp / "returns.json"
        path.write_text(json.dumps(returns), encoding="utf-8")
        self.assertEqual(assemble.apply_returns(self.state, path), {4: "verification_failed", 5: "verified"})
        now = assemble.status(self.state)
        self.assertEqual(now["current"]["step"], 4)
        _, operator = self.run_script(["stop"])
        self.assertIn("Step 4 needs attention: the return check found the part in a different place.", operator.transcript)

    def test_progress_for_an_older_package_is_not_applied(self):
        self.run_script(["done"] * 3 + ["stop"])
        book = declare.Declarations(self.state, call=tq.in_process_memory_call).open()
        try:
            proposal = book.propose_extra("P1", "spring", "cell 12", "battery door", 5.0)
            book.confirm(proposal["op_key"], reply("yes", 7))
        finally:
            book.close()
        guide.build(self.state)
        now = assemble.status(self.state)
        self.assertEqual(now["current"]["step"], 1)
        self.assertEqual(len(now["progress_for_other_packages"]), 1)


class Cli(Case):
    def test_declare_guide_assemble_and_status_from_the_command_line(self):
        root = Path(tempfile.mkdtemp(dir=self.tmp)) / "cli"
        state = tq.make_session(root)
        tq.ask_loop.run(state, tq.ask_loop.ScriptedOperator(tq.full01_script(state.done_runs())),
                        memory_call=tq.in_process_memory_call)

        def run(*argv):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = anastylos.main(list(argv))
            return code, (json.loads(out.getvalue()) if out.getvalue().strip() else None), err.getvalue()

        row = extra_rows()[2]
        code, result, err = run("declare", "--session", str(root), "--phase", row["phase"], "--label", row["label"],
                                "--to", row["destination"], "--from", row["source"], "--at", str(row["t_s"]),
                                "--confirm", "yes")
        self.assertEqual((code, result["status"]), (0, "committed"))
        self.assertTrue(err.startswith("I will record:"))
        self.assertEqual(run("guide", "build", "--session", str(root))[0], 0)
        code, result, _ = run("guide", "ask", "--session", str(root), "--question", "where is cell 8")
        self.assertTrue(result["accepted"])
        script = self.tmp / "replies.json"
        script.write_text(json.dumps({"step-1": {"assemble": ["done", "stop"]}}), encoding="utf-8")
        code, result, _ = run("assemble", "--session", str(root), "--script", str(script))
        self.assertEqual((code, result["current_step"]), (0, 2))
        code, result, _ = run("status", "--session", str(root))
        self.assertEqual((result["guide"]["steps_done"], result["declarations"]), (1, {"D-01": "COMMITTED"}))
        code, _, err = run("guide", "ask", "--session", str(root))
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("STOPPED: guide ask needs --question."))


if __name__ == "__main__":
    unittest.main()
