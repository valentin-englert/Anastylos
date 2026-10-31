"""M0-M2 regressions: copied development data and fake HTTP transport only."""
import contextlib
import io
import json
from unittest.mock import patch

import test_questions as tq
import test_m2 as m2
import anastylos
import assemble
import declare
import guide
from meta import MetaProposer
from fake import synthetic_response, VALID_OUTPUTS


class Stabilization(tq.Case):
    def extra(self, state):
        book = declare.Declarations(state, call=tq.in_process_memory_call).open()
        try:
            proposal = book.propose_extra("P1", "spring", "cell 12", "battery door", 5.0)
            self.assertEqual(book.confirm(proposal["op_key"], m2.reply("yes"))["status"], "committed")
        finally:
            book.close()

    def test_summary_reports_two_unresolved_after_round_ends(self):
        state = self.session(phases=("P1",))
        script = {"Q-P1-01": {"answer": ["Screw from top-left screw hole H2"], "confirm": ["yes"]},
                  "Q-P1-02": {"answer": ["skip", "later"]},
                  "Q-P1-03": {"answer": ["skip", "later"]}}
        tq.ask_loop.run(state, tq.ask_loop.ScriptedOperator(script), memory_call=tq.in_process_memory_call)
        result = anastylos.summary(state)
        self.assertEqual(result["questions"], {"COMMITTED": 1, "LEFT_UNRESOLVED": 2})
        self.assertFalse(result["complete"])
        self.assertIn("2 question(s) left unresolved; the question round ended without confirmed answers", result["incomplete"])

    def test_superseded_revision_is_not_an_unanswered_current_question(self):
        state = self.session(phases=("P1",))
        tq.ask_loop.run(state, tq.ask_loop.ScriptedOperator(tq.full01_script(state.done_runs())),
                        memory_call=tq.in_process_memory_call)
        self.assertTrue(anastylos.summary(state)["complete"])
        with patch.object(tq.ask_loop, "question_states", return_value={"old": "SUPERSEDED", "current": "COMMITTED"}):
            state.doc["phases"][0]["counts"]["questions"] = 1
            self.assertTrue(anastylos.summary(state)["complete"])

    def test_stale_guide_refuses_cache_and_assembly_until_explicit_rebuild(self):
        state = m2.full_session(self.tmp / "s")
        first = guide.build(state)
        self.assertTrue(guide.answer(state, "where is cell 8")["accepted"])
        self.assertEqual(guide.answer(state, "where is cell 8")["source"], "cache")
        assemble.mark_done(state, 1)
        answers_before = (guide.guide_dir(state) / "answers.jsonl").read_bytes()
        progress_before = assemble.progress_path(state).read_bytes()
        self.assertEqual(assemble.status(tq.SessionState.load(state.root))["current"]["step"], 2)
        self.extra(state)
        result = guide.answer(state, "where is cell 8")
        self.assertFalse(result["accepted"])
        self.assertEqual(result["source"], "refusal")
        self.assertIn("guide build", result["text"])
        status = anastylos.summary(state)["guide"]
        self.assertFalse(status["usable"])
        self.assertIn("memory", status["reason"])
        with self.assertRaisesRegex(guide.GuideError, "guide build"):
            assemble.run(state, tq.ask_loop.ScriptedOperator({}))
        self.assertEqual((guide.guide_dir(state) / "answers.jsonl").read_bytes(), answers_before)
        self.assertEqual(assemble.progress_path(state).read_bytes(), progress_before)
        rebuilt = guide.build(state)
        self.assertNotEqual(rebuilt["package_sha256"], first["package_sha256"])
        self.assertTrue(anastylos.summary(state)["guide"]["usable"])
        self.assertNotEqual(guide.answer(state, "where is cell 8")["source"], "cache")
        self.assertTrue(guide.answer(state, "where is spring")["accepted"])
        now = assemble.status(state)
        self.assertEqual(now["current"]["step"], 1)
        self.assertEqual(now["latest"], {})
        self.assertEqual(now["progress_for_other_packages"], [first["package_sha256"][:12]])
        self.assertEqual(assemble.progress_path(state).read_bytes(), progress_before)


class Transport:
    def __init__(self, statuses=()):
        self.statuses = list(statuses)
        self.requests = []

    def post(self, url, headers, body, timeout_s):
        self.requests.append(json.loads(body))
        status = self.statuses.pop(0) if self.statuses else 200
        if status != 200:
            return status, b'{}'
        task = json.loads(body)["text"]["format"]["name"].removeprefix("anastylos_")
        return 200, json.dumps(synthetic_response(json.dumps(VALID_OUTPUTS[task]))).encode()


class Budget(tq.Case):
    def command(self, state, *argv):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(anastylos.main([*argv, "--session", str(state.root)]), 0)
        return json.loads(output.getvalue())

    def wiring(self, transport):
        def create(folder, max_calls):
            return MetaProposer(folder, max_calls, transport=transport,
                                environ={"MODEL_API_KEY": "fake-key"}, sleep=lambda _: None)
        return patch("meta.MetaProposer", side_effect=create)

    def test_prepare_then_default_guide_shares_absolute_budget_across_restarts(self):
        state = self.session(phases=("P1",))
        transport = Transport()
        with self.wiring(transport):
            prepared = self.command(state, "prepare", "--muse")
        self.assertEqual(len(transport.requests), 3)
        self.assertEqual(prepared["muse_budget"], {"limit": 12, "used": 3, "remaining": 9})
        tq.ask_loop.run(state, tq.ask_loop.ScriptedOperator(tq.full01_script(state.done_runs())),
                        memory_call=tq.in_process_memory_call)
        guide.build(state)
        with self.wiring(transport):
            result = self.command(state, "guide", "ask", "--muse", "--question", "explain this assembly")
        self.assertEqual(len(transport.requests), 4)
        self.assertEqual(result["muse_budget"], {"limit": 12, "used": 4, "remaining": 8})

    def test_explicit_total_limit_persists_and_repeated_defaults_do_not_refill(self):
        state = m2.full_session(self.tmp / "s")
        guide.build(state)
        transport = Transport((429, 503, 200))
        with self.wiring(transport):
            first = self.command(state, "guide", "ask", "--muse", "--max-calls", "2", "--question", "explain one")
            second = self.command(state, "guide", "ask", "--muse", "--question", "explain two")
            third = self.command(state, "prepare", "--muse")
        self.assertEqual(len(transport.requests), 2)
        for result in (first, second, third):
            self.assertEqual(result["muse_budget"], {"limit": 2, "used": 2, "remaining": 0})

    def test_provider_cache_hit_does_not_consume_budget_after_restart(self):
        state = m2.full_session(self.tmp / "s")
        guide.build(state)
        transport = Transport()
        with self.wiring(transport):
            first = self.command(state, "guide", "ask", "--muse", "--max-calls", "1", "--question", "explain one")
            # Invalid factual output is not in the answer cache, but the provider request is cached.
            second = self.command(state, "guide", "ask", "--muse", "--question", "explain one")
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(first["muse_budget"], second["muse_budget"])
        self.assertEqual(second["muse_budget"], {"limit": 1, "used": 1, "remaining": 0})

    def test_legacy_traces_count_without_a_stored_limit_and_explicit_raise_is_absolute(self):
        state = m2.full_session(self.tmp / "s")
        guide.build(state)
        folder = state.root / "provider"
        folder.mkdir()
        (folder / "traces.jsonl").write_text(json.dumps({"attempts": 12}) + "\n")
        transport = Transport()
        with self.wiring(transport):
            blocked = self.command(state, "guide", "ask", "--muse", "--question", "explain old")
            raised = self.command(state, "guide", "ask", "--muse", "--max-calls", "13", "--question", "explain new")
            again = self.command(state, "guide", "ask", "--muse", "--question", "explain another")
        self.assertEqual(blocked["muse_budget"], {"limit": 12, "used": 12, "remaining": 0})
        self.assertEqual(raised["muse_budget"], {"limit": 13, "used": 13, "remaining": 0})
        self.assertEqual(again["muse_budget"], raised["muse_budget"])
        self.assertEqual(len(transport.requests), 1)


class ProviderLifecycle(tq.Case):
    def test_cli_waits_for_late_provider_and_keeps_timeout_answer(self):
        import threading
        import time
        state = m2.full_session(self.tmp / "s")
        guide.build(state)
        entered, finished = threading.Event(), threading.Event()
        class Slow:
            calls_used = 1
            def propose(self, *args):
                entered.set()
                time.sleep(0.15)
                finished.set()
                return {"status": "ok", "output": {"schema": "anastylos-guide-claims@1", "claims": [],
                                                      "insufficient_evidence": True}}
        out, err = io.StringIO(), io.StringIO()
        with patch("anastylos.session_proposer", return_value=(Slow(), 2)):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = anastylos.main(["guide", "ask", "--session", str(state.root), "--muse",
                                       "--question", "explain this assembly", "--timeout", "0.01"])
        self.assertEqual(code, 0)
        self.assertTrue(entered.is_set() and finished.is_set())
        result = json.loads(out.getvalue())
        self.assertEqual(result["source"], "refusal")
        self.assertFalse(result["accepted"])
        self.assertIn("Waiting for provider work", err.getvalue())
        self.assertIn("no answer within", " ".join(result["findings"]))
        self.assertFalse(guide._pending_workers)

    def test_normal_cli_process_drains_non_daemon_work(self):
        import subprocess
        import sys
        import os
        code = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import test_m2 as m2
import anastylos, guide
state = m2.full_session(Path(sys.argv[2]))
guide.build(state)
class Slow:
    calls_used = 1
    def propose(self, *args):
        time.sleep(0.15)
        print('fake provider completed', file=sys.stderr, flush=True)
        return {'status':'ok','output':{'schema':'anastylos-guide-claims@1','claims':[], 'insufficient_evidence':True}}
anastylos.session_proposer = lambda state, cap: (Slow(), 2)
raise SystemExit(anastylos.main(['guide','ask','--session',str(state.root),'--muse',
                              '--question','explain the assembly','--timeout','0.01']))
"""
        done = subprocess.run([sys.executable, "-c", code, str(tq.AGENT / "tests"), str(self.tmp / "process")],
                              capture_output=True, text=True, encoding="utf-8", timeout=20,
                              env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Waiting for provider work", done.stderr)
        self.assertIn("fake provider completed", done.stderr)
        self.assertEqual(json.loads(done.stdout)["source"], "refusal")

    def test_cli_rejects_concurrent_model_command(self):
        from lock import SessionLock
        state = self.session()
        held = SessionLock(state.root)
        held.acquire({"operation": "test concurrent CLI"})
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err), patch("anastylos.session_proposer") as factory:
                code = anastylos.main(["prepare", "--session", str(state.root), "--muse"])
            self.assertEqual(code, 1)
            self.assertIn("Another model command", err.getvalue())
            factory.assert_not_called()
        finally:
            held.release()
