import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fake import VALID_OUTPUTS, synthetic_response  # noqa: E402
from meta import ENDPOINT, MetaProposer, TransportFailure, TransportTimeout  # noqa: E402
from provider_core import MODEL, RESULT_SCHEMA, SCHEMAS_DIR, TRACE_SCHEMA  # noqa: E402
from jsonschema_subset import validate  # noqa: E402
from smoke import SYNTHETIC_PAYLOADS  # noqa: E402

KEY = "sk-test-SECRETKEYVALUE-0123456789abcdefghij"
RESULT_JSON_SCHEMA = json.loads((SCHEMAS_DIR / "provider-result.json").read_text(encoding="utf-8"))


def _ok_body(task, output=None, **overrides):
    return json.dumps({**synthetic_response(json.dumps(output or VALID_OUTPUTS[task])), **overrides}).encode()


class ScriptedTransport:
    """Returns (status, body) tuples or raises the scripted exceptions, in order; records each request."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.requests = []

    def post(self, url, headers, body, timeout_s):
        self.requests.append({"url": url, "headers": headers, "body": body, "timeout_s": timeout_s})
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


class MetaProposerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name) / "run"
        self.sleeps = []

    def tearDown(self):
        self.tmp.cleanup()

    def proposer(self, transport, max_calls=8, environ=None):
        return MetaProposer(self.run_dir, max_calls, transport=transport,
                            environ={"MODEL_API_KEY": KEY} if environ is None else environ,
                            sleep=self.sleeps.append)

    def traces(self):
        return [json.loads(line) for line in (self.run_dir / "traces.jsonl").read_text(encoding="utf-8").splitlines()]

    def assert_result_shape(self, result):
        self.assertEqual(validate(result, RESULT_JSON_SCHEMA), [])
        self.assertEqual(result["schema"], RESULT_SCHEMA)

    def test_success_for_every_task_sends_contract_payload(self):
        for task, payload in SYNTHETIC_PAYLOADS.items():
            transport = ScriptedTransport((200, _ok_body(task)))
            result = self.proposer(transport).propose(task, payload, f"r-{task}")
            self.assert_result_shape(result)
            self.assertEqual(result["status"], "ok", result["error"])
            self.assertEqual(result["output"], VALID_OUTPUTS[task])
            self.assertEqual((result["model"], result["reasoning_effort_returned"]), (MODEL, "max"))
            sent = json.loads(transport.requests[0]["body"])
            self.assertEqual(transport.requests[0]["url"], ENDPOINT)
            self.assertEqual(transport.requests[0]["timeout_s"], 120.0)
            self.assertEqual(sent["model"], "muse-spark-1.3-contributor")
            self.assertEqual(sent["reasoning"], {"effort": "max"})
            self.assertEqual(sent["text"]["format"]["type"], "json_schema")
            self.assertTrue(sent["text"]["format"]["strict"])
            self.assertIn("propose", sent["input"][0]["content"][0]["text"])

    def test_invalid_outputs_are_rejected_in_code(self):
        cases = {
            "not json": synthetic_response("{nope"),
            "extra field": synthetic_response(json.dumps({**VALID_OUTPUTS["next_action"], "x": 1})),
            "wrong enum": synthetic_response(json.dumps({**VALID_OUTPUTS["next_action"], "action": "request_view"})),
            "no message": {**synthetic_response("{}"), "output": [{"type": "reasoning"}]},
        }
        for name, body in cases.items():
            with self.subTest(name):
                transport = ScriptedTransport((200, json.dumps(body).encode()))
                result = self.proposer(transport).propose("next_action", SYNTHETIC_PAYLOADS["next_action"], name)
                self.assertEqual(result["status"], "invalid_output")
                self.assertIsNone(result["output"])
                self.assert_result_shape(result)

    def test_model_or_effort_substitution_is_a_provider_error(self):
        for override in ({"model": "muse-spark-1.3"}, {"reasoning": {"effort": "low"}}):
            transport = ScriptedTransport((200, _ok_body("next_action", **override)))
            result = self.proposer(transport).propose("next_action", SYNTHETIC_PAYLOADS["next_action"], "r")
            self.assertEqual(result["status"], "provider_error")
            self.assertIsNone(result["output"])

    def test_timeout_is_not_retried(self):
        transport = ScriptedTransport(TransportTimeout("TimeoutError"))
        result = self.proposer(transport).propose("next_action", SYNTHETIC_PAYLOADS["next_action"], "r")
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(len(transport.requests), 1)

    def test_transport_failure_is_provider_error(self):
        transport = ScriptedTransport(TransportFailure("ConnectionRefusedError"))
        result = self.proposer(transport).propose("next_action", SYNTHETIC_PAYLOADS["next_action"], "r")
        self.assertEqual(result["status"], "provider_error")

    def test_retries_retryable_status_then_succeeds(self):
        transport = ScriptedTransport((429, b'{"error":{"type":"rate_limit","message":"slow down"}}'),
                                      (503, b"busy"), (200, _ok_body("next_action")))
        result = self.proposer(transport).propose("next_action", SYNTHETIC_PAYLOADS["next_action"], "r")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(transport.requests), 3)
        self.assertEqual(len(self.sleeps), 2)
        self.assertEqual(self.traces()[0]["attempts"], 3)

    def test_retry_limit_is_two(self):
        transport = ScriptedTransport(*[(500, b"{}")] * 4)
        result = self.proposer(transport).propose("next_action", SYNTHETIC_PAYLOADS["next_action"], "r")
        self.assertEqual(result["status"], "provider_error")
        self.assertEqual(result["error"]["http_status"], 500)
        self.assertEqual(len(transport.requests), 3)

    def test_non_retryable_status_fails_at_once(self):
        transport = ScriptedTransport((400, b'{"error":{"type":"invalid_request","message":"bad schema"}}'))
        result = self.proposer(transport).propose("next_action", SYNTHETIC_PAYLOADS["next_action"], "r")
        self.assertEqual((result["status"], result["error"]["type"]), ("provider_error", "invalid_request"))
        self.assertEqual(len(transport.requests), 1)

    def test_identical_request_is_served_from_cache(self):
        transport = ScriptedTransport((200, _ok_body("next_action")))
        first = self.proposer(transport).propose("next_action", SYNTHETIC_PAYLOADS["next_action"], "r1")
        second = self.proposer(ScriptedTransport()).propose("next_action", SYNTHETIC_PAYLOADS["next_action"], "r2")
        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["cache_hit"])
        self.assertEqual(second["output"], first["output"])
        self.assertEqual(second["request_sha256"], first["request_sha256"])
        self.assertEqual(len(transport.requests), 1)

    def test_call_cap_counts_attempts_and_survives_new_instance(self):
        transport = ScriptedTransport((503, b"{}"), (200, _ok_body("question_wording")))
        result = self.proposer(transport, max_calls=1).propose(
            "question_wording", SYNTHETIC_PAYLOADS["question_wording"], "r")
        self.assertEqual(result["status"], "budget_exhausted")
        self.assertEqual(len(transport.requests), 1)
        again = self.proposer(ScriptedTransport(), max_calls=1).propose(
            "answer_interpretation", SYNTHETIC_PAYLOADS["answer_interpretation"], "r2")
        self.assertEqual(again["status"], "budget_exhausted")

    def test_zero_cap_sends_nothing(self):
        transport = ScriptedTransport()
        result = self.proposer(transport, max_calls=0).propose("next_action", SYNTHETIC_PAYLOADS["next_action"], "r")
        self.assertEqual(result["status"], "budget_exhausted")
        self.assertEqual(transport.requests, [])

    def test_missing_key_is_disabled_without_network(self):
        transport = ScriptedTransport()
        result = self.proposer(transport, environ={}).propose("next_action", SYNTHETIC_PAYLOADS["next_action"], "r")
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(transport.requests, [])
        self.assert_result_shape(result)

    def test_invalid_payload_is_a_caller_error(self):
        with self.assertRaises(ValueError):
            self.proposer(ScriptedTransport()).propose("next_action", {"state": {}}, "r")

    def test_key_never_reaches_traces_cache_or_errors(self):
        echo = json.dumps({"error": {"type": "auth", "message": f"bad header Bearer {KEY} for key {KEY}"}}).encode()
        proposer = self.proposer(ScriptedTransport((401, echo), (200, _ok_body("next_action"))))
        failed = proposer.propose("question_wording", SYNTHETIC_PAYLOADS["question_wording"], "r1")
        proposer.propose("next_action", SYNTHETIC_PAYLOADS["next_action"], "r2")
        self.assertEqual(failed["status"], "provider_error")
        written = "".join(path.read_text(encoding="utf-8") for path in self.run_dir.rglob("*") if path.is_file())
        for text in (written, json.dumps(failed)):
            self.assertNotIn(KEY, text)
            self.assertNotIn("SECRETKEYVALUE", text)
            self.assertIsNone(re.search(r"(?i)authorization|bearer\s+(?!\[redacted\])", text))
        for trace in self.traces():
            self.assertEqual(trace["schema"], TRACE_SCHEMA)
            self.assertNotIn("headers", trace)
            self.assertNotIn("output", trace)
            self.assertLessEqual(len((trace["error"] or {}).get("message", "")), 300)


class DurableBudgetTest(unittest.TestCase):
    setUp = MetaProposerTest.setUp
    tearDown = MetaProposerTest.tearDown
    proposer = MetaProposerTest.proposer
    def test_reservation_precedes_transport_and_survives_interruption(self):
        outer = self
        class Crash(BaseException):
            pass
        class Interrupted:
            def post(self, *args):
                rows = (outer.run_dir / "attempts.jsonl").read_text().splitlines()
                outer.assertEqual(sum(json.loads(r)["attempts"] for r in rows), 1)
                raise Crash()
        with self.assertRaises(Crash):
            self.proposer(Interrupted(), max_calls=1).propose("next_action", SYNTHETIC_PAYLOADS["next_action"], "crash")
        transport = ScriptedTransport()
        restarted = self.proposer(transport, max_calls=1)
        self.assertEqual(restarted.calls_used, 1)
        self.assertEqual(restarted.propose("next_action", SYNTHETIC_PAYLOADS["next_action"], "restart")["status"],
                         "budget_exhausted")
        self.assertEqual(transport.requests, [])

    def test_completed_reservations_not_counted_twice_and_legacy_counts_remain(self):
        self.run_dir.mkdir()
        (self.run_dir / "traces.jsonl").write_text(json.dumps({"attempts": 3}) + "\n")
        transport = ScriptedTransport((429, b"{}"), (200, _ok_body("next_action")))
        p = self.proposer(transport, max_calls=5)
        p.propose("next_action", SYNTHETIC_PAYLOADS["next_action"], "new")
        restarted = self.proposer(ScriptedTransport(), max_calls=5)
        self.assertEqual(restarted.calls_used, 5)
        cached = restarted.propose("next_action", SYNTHETIC_PAYLOADS["next_action"], "cache")
        self.assertTrue(cached["cache_hit"])
        self.assertEqual(self.proposer(ScriptedTransport(), max_calls=5).calls_used, 5)

    def test_competing_process_is_excluded(self):
        import subprocess
        from lock import LockHeld
        self.run_dir.mkdir()
        code = ("import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
                "from lock import SessionLock; l=SessionLock(Path(sys.argv[2])); "
                "l.acquire({'operation':'test'}); print('ready',flush=True); sys.stdin.readline()")
        child = subprocess.Popen([sys.executable, "-c", code, str(Path(__file__).resolve().parents[2]),
                                  str(self.run_dir)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "ready")
            with self.assertRaises(LockHeld):
                self.proposer(ScriptedTransport()).propose("next_action", SYNTHETIC_PAYLOADS["next_action"], "competing")
        finally:
            child.communicate("done\n", timeout=5)
        self.assertEqual(self.proposer(ScriptedTransport()).calls_used, 0)

    def test_real_transport_process_deadline_without_network(self):
        import subprocess
        import time
        from unittest.mock import patch
        from meta import UrllibTransport
        real_run = subprocess.run
        def slow_worker(*args, **kwargs):
            return real_run([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        started = time.monotonic()
        with patch("meta.subprocess.run", side_effect=slow_worker):
            with self.assertRaises(TransportTimeout):
                UrllibTransport().post("unused", {}, b"{}", 0.1)
        self.assertLess(time.monotonic() - started, 5)

    def test_hard_process_exit_preserves_reserved_attempt(self):
        import subprocess
        code = """
import sys, os
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from meta import MetaProposer
from smoke import SYNTHETIC_PAYLOADS
class ExitTransport:
    def post(self, *args):
        os._exit(23)
MetaProposer(Path(sys.argv[2]), 1, transport=ExitTransport(), environ={'MODEL_API_KEY':'fake'}).propose(
    'next_action', SYNTHETIC_PAYLOADS['next_action'], 'hard-exit')
"""
        done = subprocess.run([sys.executable, "-c", code, str(Path(__file__).resolve().parents[1]),
                               str(self.run_dir)], capture_output=True, timeout=10)
        self.assertEqual(done.returncode, 23, done.stderr)
        p = self.proposer(ScriptedTransport(), max_calls=1)
        self.assertEqual(p.calls_used, 1)
        self.assertEqual(p.propose("next_action", SYNTHETIC_PAYLOADS["next_action"], "after-exit")["status"],
                         "budget_exhausted")


if __name__ == "__main__":
    unittest.main()
