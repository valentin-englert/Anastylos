import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fake import VALID_OUTPUTS, FakeProposer, synthetic_response  # noqa: E402
from provider_core import build_request, encode_request, make_fixture, sha256_hex  # noqa: E402
from smoke import SYNTHETIC_PAYLOADS  # noqa: E402

RECORDED = Path(__file__).resolve().parent / "fixtures" / "recorded"


def _write_fixture(directory, task, output):
    body = build_request(task, SYNTHETIC_PAYLOADS[task])
    digest = sha256_hex(encode_request(body))
    fixture = make_fixture(task=task, body=body, request_sha256=digest,
                           response=synthetic_response(json.dumps(output)), latency_s=1.5)
    (Path(directory) / f"{digest}.json").write_text(json.dumps(fixture), encoding="utf-8")
    return digest


class FakeProposerTest(unittest.TestCase):
    def test_replays_fixture_by_request_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            digest = _write_fixture(tmp, "next_action", VALID_OUTPUTS["next_action"])
            result = FakeProposer(Path(tmp)).propose("next_action", SYNTHETIC_PAYLOADS["next_action"], "r")
        self.assertEqual((result["status"], result["request_sha256"]), ("ok", digest))
        self.assertTrue(result["cache_hit"])
        self.assertEqual(result["output"], VALID_OUTPUTS["next_action"])

    def test_missing_fixture_is_provider_error(self):
        result = FakeProposer().propose("next_action", SYNTHETIC_PAYLOADS["next_action"], "r")
        self.assertEqual((result["status"], result["error"]["type"]), ("provider_error", "fixture_missing"))

    def test_tampered_fixture_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            digest = _write_fixture(tmp, "next_action", VALID_OUTPUTS["next_action"])
            path = Path(tmp) / f"{digest}.json"
            fixture = json.loads(path.read_text(encoding="utf-8"))
            fixture["request"]["model"] = "other"
            path.write_text(json.dumps(fixture), encoding="utf-8")
            with self.assertRaises(ValueError):
                FakeProposer(Path(tmp)).propose("next_action", SYNTHETIC_PAYLOADS["next_action"], "r")

    def test_failure_modes(self):
        expected = {"invalid_json": "invalid_output", "schema_violation": "invalid_output",
                    "wrong_enum": "invalid_output", "timeout": "timeout", "provider_error": "provider_error",
                    "disabled": "disabled", "budget_exhausted": "budget_exhausted"}
        for mode, status in expected.items():
            for task in ("answer_interpretation", "next_action", "guide_claims"):
                with self.subTest(mode=mode, task=task):
                    result = FakeProposer(script={task: {"mode": mode}}).propose(task, SYNTHETIC_PAYLOADS[task], "r")
                    self.assertEqual(result["status"], status)
                    self.assertIsNone(result["output"])

    def test_scripted_output_by_request_id_wins_over_task(self):
        chosen = {**VALID_OUTPUTS["answer_interpretation"], "intent": "choose", "choice": "cand-2"}
        fake = FakeProposer(script={"r1": {"mode": "output", "output": chosen}, "answer_interpretation": {"mode": "timeout"}})
        payload = SYNTHETIC_PAYLOADS["answer_interpretation"]
        self.assertEqual(fake.propose("answer_interpretation", payload, "r1")["output"], chosen)
        self.assertEqual(fake.propose("answer_interpretation", payload, "r2")["status"], "timeout")
        self.assertEqual([call["mode"] for call in fake.calls], ["output", "timeout"])

    def test_stale_revision_is_valid_but_one_revision_behind(self):
        current = {**VALID_OUTPUTS["next_action"], "question_id": "Q-3", "question_revision": 2}
        fake = FakeProposer(script={"next_action": {"mode": "stale_revision", "output": current}})
        result = fake.propose("next_action", SYNTHETIC_PAYLOADS["next_action"], "r")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["output"]["question_revision"], 1)

    def test_unknown_mode_rejected(self):
        with self.assertRaises(ValueError):
            FakeProposer(script={"r": {"mode": "flaky"}})


class RecordedFixturesTest(unittest.TestCase):
    """The real-API smoke fixtures replay to `ok` and match their manifest."""

    def test_recorded_fixtures_replay(self):
        manifest = json.loads((RECORDED / "MANIFEST.json").read_text(encoding="utf-8"))
        for entry in manifest["files"]:
            self.assertEqual(hashlib.sha256((RECORDED / entry["file"]).read_bytes()).hexdigest(), entry["sha256"])
        fake = FakeProposer(RECORDED)
        replayed = {task: fake.propose(task, payload, f"replay-{task}") for task, payload in SYNTHETIC_PAYLOADS.items()}
        recorded = {json.loads((RECORDED / e["file"]).read_text(encoding="utf-8"))["task"] for e in manifest["files"]}
        for task in recorded:
            self.assertEqual(replayed[task]["status"], "ok", replayed[task]["error"])


if __name__ == "__main__":
    unittest.main()
