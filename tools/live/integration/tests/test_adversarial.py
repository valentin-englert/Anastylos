"""Cross-lane adversarial set (TL): lane A's coordinator wired to lane C's real proposers (fake transport or
FakeProposer), lane B's verifier and lane D's validator. No network, no key: every case is deterministic.

Classes: crash, retry, corruption, grounding, stale revision, invalid tool output. The lane suites prove each
part alone; these cases prove the seams between them fail closed.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import slices  # noqa: E402  (sets the live, guide and providers paths)
from coordinator import Coordinator  # noqa: E402
from fake import FakeProposer, synthetic_response  # noqa: E402
from meta import MetaProposer  # noqa: E402

import claims  # noqa: E402

ANSWER = "Screw from the top-left hole of the back plate"
FAKE_KEY = {"MODEL_API_KEY": "test-key-not-real"}


class SimulatedCrash(BaseException):
    """Not an Exception: the coordinator's provider guard must not swallow it."""


class ScriptedTransport:
    """Meta transport double: returns the scripted (status, body) pairs in order."""

    def __init__(self, replies):
        self.replies, self.posts = list(replies), 0

    def post(self, url, headers, body, timeout_s):
        self.posts += 1
        return self.replies.pop(0)


def ok_body(output: dict) -> tuple[int, bytes]:
    return 200, json.dumps(synthetic_response(json.dumps(output, ensure_ascii=False))).encode("utf-8")


def busy_body(status: int) -> tuple[int, bytes]:
    return status, json.dumps({"error": {"type": "overloaded", "message": "busy"}}).encode("utf-8")


class Workspace:
    def __init__(self, test: unittest.TestCase):
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.runs, self.memory = slices.memory_copy(self.root / "run")
        self.phase_words = json.loads(slices.PHASE_WORDS.read_text(encoding="utf-8"))["phases"]

    def open(self, proposer=None, fault=None) -> Coordinator:
        return Coordinator(self.root / "run", self.memory, self.runs, self.phase_words, declared_by="adversarial",
                           proposer=proposer, provider_call_cap=6, fault=fault).open()

    def memory_lines(self) -> list[str]:
        return [l for l in (self.memory / "memory.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


def ask_answer_confirm(c: Coordinator) -> tuple[dict, dict, dict | None]:
    qid = c.book.live_order()[0]
    prompt = c.ask(qid)
    reply = c.receive(slices.operator_input(prompt["op_key"], ANSWER))
    confirm = c.receive(slices.operator_input(prompt["op_key"], "yes")) if reply["status"] == "read_back" else None
    return prompt, reply, confirm


def fallbacks(c: Coordinator) -> list[str]:
    return [r["reason"] for r in c.journal.of_kind("fallback")]


class InvalidToolOutput(unittest.TestCase):
    def test_every_bad_provider_result_falls_back_to_l0_and_still_needs_the_read_back(self):
        l0 = Workspace(self)
        c = l0.open()
        _, _, l0_confirm = ask_answer_confirm(c)
        c.close()
        for mode in ("invalid_json", "schema_violation", "wrong_enum", "timeout", "provider_error", "disabled",
                     "budget_exhausted"):
            with self.subTest(mode=mode):
                ws = Workspace(self)
                script = {"answer_interpretation": {"mode": mode},
                          "question_wording": {"mode": "invalid_json" if mode == "wrong_enum" else mode}}
                c = ws.open(FakeProposer(script=script))
                try:
                    prompt, reply, confirm = ask_answer_confirm(c)
                    self.assertEqual(prompt["wording"], "L0")
                    self.assertEqual(reply["status"], "read_back")
                    self.assertEqual(confirm["status"], "committed")
                    self.assertEqual(sorted(confirm["events"]), sorted(l0_confirm["events"]))
                    self.assertEqual(len(fallbacks(c)), 2, fallbacks(c))
                finally:
                    c.close()


class Grounding(unittest.TestCase):
    def test_wording_that_adds_a_cell_or_number_is_rejected(self):
        ws = Workspace(self)
        invented = {"text": "Where did the part you put in cell 9 twelve seconds ago, at 0:12, come from?", "facts_used": []}
        c = ws.open(FakeProposer(script={"question_wording": {"mode": "output", "output": invented}}))
        try:
            prompt = c.ask(c.book.live_order()[0])
            self.assertEqual(prompt["wording"], "L0")
            self.assertTrue(any(r.startswith("gate:") for r in fallbacks(c)), fallbacks(c))
        finally:
            c.close()

    def test_interpretation_that_invents_a_place_is_not_used(self):
        ws = Workspace(self)
        invented = {"intent": "describe_place", "choice": None, "place_text": "the battery compartment bottom right",
                    "label_text": "Screw"}
        c = ws.open(FakeProposer(script={"question_wording": {"mode": "disabled"},
                                         "answer_interpretation": {"mode": "output", "output": invented}}))
        try:
            _, reply, _ = ask_answer_confirm(c)
            self.assertNotIn("battery", reply["read_back"])
            self.assertTrue(any(r.startswith("gate:") for r in fallbacks(c)), fallbacks(c))
        finally:
            c.close()

    def test_guide_claims_about_absent_ids_or_wrong_cells_are_not_spoken(self):
        raw = slices.PACKAGE.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        cases = {
            "fabricated id": [{"relation": "stored_in", "subject": "dec-9999", "object": "cell-1", "basis_word": None}],
            "wrong cell": [{"relation": "stored_in", "subject": "dec-0012", "object": "cell-5", "basis_word": None}],
            "place for id": [{"relation": "stored_in", "subject": "dec-0012", "object": "Zelle 2", "basis_word": None}],
            "raw basis token": [{"relation": "stored_in", "subject": "dec-0012", "object": "cell-1",
                                 "basis_word": "operator_declared"}],
            "inverted basis": [{"relation": "stored_in", "subject": "dec-0012", "object": "cell-1",
                                "basis_word": claims.SYSTEM_ASSIGNED}],
            "swapped order": [{"relation": "install_before", "subject": "dec-0007", "object": "dec-0008",
                               "basis_word": None}],
        }
        for name, claim_list in cases.items():
            with self.subTest(name):
                document = {"schema": "anastylos-guide-claims@1", "claims": claim_list, "insufficient_evidence": False}
                verdict = claims.check(raw, sha, document=document)
                self.assertFalse(verdict.accepted)
                self.assertEqual(verdict.speech, claims.NOT_PROVEN)
        control = {"schema": "anastylos-guide-claims@1", "insufficient_evidence": False, "claims": [
            {"relation": "stored_in", "subject": "dec-0012", "object": "cell-1", "basis_word": None}]}
        self.assertTrue(claims.check(raw, sha, document=control).accepted)
        self.assertFalse(claims.check(raw, "0" * 64, document=control).accepted)


class StaleRevision(unittest.TestCase):
    def test_answer_and_confirmation_for_a_superseded_revision_never_reach_memory(self):
        ws = Workspace(self)
        c = ws.open(FakeProposer(script={"question_wording": {"mode": "disabled"},
                                         "answer_interpretation": {"mode": "disabled"}}))
        try:
            qid = c.book.live_order()[0]
            prompt = c.ask(qid)
            reply = c.receive(slices.operator_input(prompt["op_key"], ANSWER))
            self.assertEqual(reply["status"], "read_back")
            before = ws.memory_lines()
            questions_path = next(r for r in ws.runs if r.name == qid.split("-")[1]) / "questions.json"
            doc = json.loads(questions_path.read_text(encoding="utf-8"))
            target = next(q for q in doc["questions"] if q["id"] == qid)
            target["deposit_window_s"] = [target["deposit_window_s"][0] + 1.0, target["deposit_window_s"][1] + 1.0]
            questions_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(c.reload_questions(), [qid])
            stale = c.receive(slices.operator_input(prompt["op_key"], "yes"))
            self.assertEqual(stale["status"], "stale")
            self.assertEqual(ws.memory_lines(), before)
        finally:
            c.close()

    def test_l2_stale_and_forbidden_proposals_are_rejected_and_p0_executes(self):
        # own process: l2sim's `session` module shadows tools/session/session.py used by the coordinator
        done = subprocess.run([sys.executable, str(HERE / "l2_probe.py")], capture_output=True, text=True,
                              encoding="utf-8")
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        report = json.loads(done.stdout)
        for mode in ("stale_revision", "wrong_enum"):
            with self.subTest(mode=mode):
                r = report[mode]
                self.assertGreater(r["rejected"], 0)
                self.assertEqual(r["executed_by_policy"], 0)
                self.assertTrue(r["integrity_ok"], r["integrity"])
                self.assertEqual(r["stale_actions_executed"], 0)
        self.assertTrue(report["wrong_enum"]["reasons"], report["wrong_enum"])


class Retry(unittest.TestCase):
    def test_two_busy_replies_then_ok_is_one_result_with_three_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            transport = ScriptedTransport([busy_body(429), busy_body(503),
                                           ok_body({"text": "Where did the part come from?", "facts_used": []})])
            p = MetaProposer(Path(tmp), max_calls=5, transport=transport, environ=FAKE_KEY, sleep=lambda s: None)
            payload = {"op_key": "S:Q:1", "kind": "source_of_part", "mode": "open", "facts": {}, "l0_text": "x?"}
            result = p.propose("question_wording", payload, "r1")
            self.assertEqual(result["status"], "ok")
            trace = json.loads((Path(tmp) / "traces.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(trace["attempts"], 3)
            self.assertEqual(p.calls_used, 3)
            again = p.propose("question_wording", payload, "r2")
            self.assertTrue(again["cache_hit"])
            self.assertEqual(transport.posts, 3)

    def test_persistent_overload_falls_back_to_l0_and_the_write_still_waits_for_yes(self):
        ws = Workspace(self)
        transport = ScriptedTransport([busy_body(429)] * 6)
        p = MetaProposer(ws.root / "provider", max_calls=6, transport=transport, environ=FAKE_KEY, sleep=lambda s: None)
        c = ws.open(p)
        try:
            before = len(ws.memory_lines())
            qid = c.book.live_order()[0]
            prompt = c.ask(qid)
            self.assertEqual(prompt["wording"], "L0")
            reply = c.receive(slices.operator_input(prompt["op_key"], ANSWER))
            self.assertEqual(len(ws.memory_lines()), before)
            confirm = c.receive(slices.operator_input(prompt["op_key"], "yes"))
            self.assertEqual(confirm["status"], "committed")
            self.assertEqual(transport.posts, 6)  # 3 attempts per call, cap 6 respected
            self.assertTrue(all("provider_error" in r or "budget_exhausted" in r for r in fallbacks(c)), fallbacks(c))
            self.assertEqual(reply["status"], "read_back")
        finally:
            c.close()


class Crash(unittest.TestCase):
    def test_crash_after_intent_with_a_provider_wired_recovers_to_exactly_one_write(self):
        ws = Workspace(self)

        def crash(point):
            if point == "after_intent":
                raise SimulatedCrash(point)

        c = ws.open(FakeProposer(script={"question_wording": {"mode": "timeout"},
                                         "answer_interpretation": {"mode": "timeout"}}), fault=crash)
        qid = c.book.live_order()[0]
        prompt = c.ask(qid)
        c.receive(slices.operator_input(prompt["op_key"], ANSWER))
        with self.assertRaises(SimulatedCrash):
            c.receive(slices.operator_input(prompt["op_key"], "yes"))
        c.close()
        reopened = ws.open(FakeProposer(script={"question_wording": {"mode": "timeout"}}))
        try:
            self.assertIsNone(reopened.stopped)
            census = reopened.summary()["census"]
            committed = reopened.committed_answers()
            self.assertEqual([a["question_id"] for a in committed], [qid])
            again = reopened.receive(slices.operator_input(prompt["op_key"], "yes"))
            self.assertIn(again["status"], ("duplicate", "ignored"))
            self.assertEqual(census["observations_without_tray_link"], [])
        finally:
            reopened.close()

    def test_a_provider_that_raises_does_not_take_the_session_down(self):
        class Exploding:
            def propose(self, task, payload, request_id):
                raise RuntimeError("provider bug")

        ws = Workspace(self)
        c = ws.open(Exploding())
        try:
            _, _, confirm = ask_answer_confirm(c)
            self.assertEqual(confirm["status"], "committed")
            self.assertTrue(all(r.startswith("proposer raised RuntimeError") for r in fallbacks(c)), fallbacks(c))
        finally:
            c.close()


class Corruption(unittest.TestCase):
    def test_torn_memory_line_stops_before_any_question(self):
        ws = Workspace(self)
        with open(ws.memory / "memory.jsonl", "a", encoding="utf-8") as handle:
            handle.write('{"schema": "anastylos-memory-event@1", "torn')
        c = ws.open(FakeProposer())
        try:
            self.assertIsNotNone(c.stopped)
        finally:
            c.close()

    def test_tampered_provider_cache_is_refused_and_the_coordinator_falls_back(self):
        ws = Workspace(self)
        transport = ScriptedTransport([ok_body({"text": "Where did the part come from?", "facts_used": []})])
        cache = ws.root / "provider"
        p = MetaProposer(cache, max_calls=1, transport=transport, environ=FAKE_KEY, sleep=lambda s: None)
        payload = {"op_key": "S:Q:1", "kind": "source_of_part", "mode": "open", "facts": {}, "l0_text": "x?"}
        self.assertEqual(p.propose("question_wording", payload, "r1")["status"], "ok")
        for fixture in (cache / "cache").glob("*.json"):
            doc = json.loads(fixture.read_text(encoding="utf-8"))
            doc["request"]["model"] = "another-model"
            fixture.write_text(json.dumps(doc), encoding="utf-8")
        with self.assertRaises(ValueError):  # neither replayed nor silently paid for again
            p.propose("question_wording", payload, "r2")
        self.assertEqual(transport.posts, 1)

        class SameRequest:  # the coordinator's view: the provider raises on this cache entry
            def propose(self, task, _payload, request_id):
                return p.propose("question_wording", payload, request_id)

        c = ws.open(SameRequest())
        try:
            prompt = c.ask(c.book.live_order()[0])
            self.assertEqual(prompt["wording"], "L0")
            self.assertTrue(any("proposer raised ValueError" in r for r in fallbacks(c)), fallbacks(c))
        finally:
            c.close()


if __name__ == "__main__":
    unittest.main()
