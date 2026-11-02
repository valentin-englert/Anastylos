"""VOICE-01 voice routes (TL decision R9/R10): auth, binding, limits, rate, logs, forwarding."""

from __future__ import annotations

import io
import json
import sys
import threading
import unittest
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anastylos_voice_routes as voice  # noqa: E402
from test_guided_routes import GuidedCase  # noqa: E402

SECRET_WORDS = "screw from the back shell"


def wav(seconds=1.0, rate=16000, channels=1):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as clip:
        clip.setnchannels(channels)
        clip.setsampwidth(2)
        clip.setframerate(rate)
        clip.writeframes(b"\x01\x00" * int(seconds * rate) * channels)
    return buffer.getvalue()


class FakeSessionUi:
    """Scripted stand-in for tools/session-ui on 127.0.0.1."""

    def __init__(self):
        self.calls = []
        self.replies = {}
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.calls.append((self.path, body))
                data = json.dumps(fake.replies.get(self.path, {})).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class VoiceCase(GuidedCase):
    def setUp(self):
        super().setUp()
        self.ui = FakeSessionUi()
        self.addCleanup(self.ui.close)
        script = Path(self.tmp.name) / "fake_transcribe.py"
        script.write_text(
            "import json, sys\n"
            "audio = sys.stdin.buffer.read()\n"
            f"print(json.dumps({{'status': 'ok', 'transcript': '{SECRET_WORDS}', 'latency_s': 0.1}}))\n",
            encoding="utf-8")
        self.state.voice_config = voice.VoiceConfig(
            transcriber=[sys.executable, "-B", str(script)], session_ui_port=self.ui.port,
            bindings_path=self.data / voice.BINDINGS_FILE, dialogue=False)
        self.source_id, self.token, _legacy = self.enroll_source()
        self.session_id = self.open_session(self.bootstrap(), self.source_id)["session_id"]
        (self.data / voice.BINDINGS_FILE).write_text(json.dumps({self.session_id: "demo-01"}), encoding="utf-8")

    def transcribe(self, raw, token=None, content_type="audio/wav", session_id=None):
        return self.call("POST", f"/v1/guided/sessions/{session_id or self.session_id}/voice/transcribe",
                         token=token or self.token, raw=raw, extra_headers={"Content-Type": content_type})

    def say(self, body, session_id=None):
        return self.json_call("POST", f"/v1/guided/sessions/{session_id or self.session_id}/voice/input",
                              token=self.token, body=body)

    def text(self, words):
        return {"kind": "input", "operator_input": {"source": "speech_transcript", "text": words}}


class TranscribeTests(VoiceCase):
    def test_ok_and_nothing_stored_or_logged(self):
        status, data, _ = self.transcribe(wav())
        self.assertEqual(status, 200, data)
        self.assertEqual(json.loads(data)["transcript"], SECRET_WORDS)
        journal = (self.data / "voice-journal.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(SECRET_WORDS, journal)
        self.assertIn('"status": "ok"', journal)
        stored = [p for p in self.data.rglob("*") if p.is_file() and p.suffix == ".wav"]
        self.assertEqual(stored, [])

    def test_auth_required_and_session_scoped(self):
        status, _, _ = self.call("POST", f"/v1/guided/sessions/{self.session_id}/voice/transcribe", raw=wav(),
                                 extra_headers={"Content-Type": "audio/wav"})
        self.assertEqual(status, 401)
        other_id, other_token, _ = self.enroll_source("other-phone")
        status, _, _ = self.transcribe(wav(), token=other_token)
        self.assertEqual(status, 404)

    def test_type_and_size_limits_never_call_provider(self):
        self.state.voice_config.transcriber = [sys.executable, "-c", "raise SystemExit(9)"]
        self.assertEqual(self.transcribe(wav(), content_type="application/octet-stream")[0], 415)
        self.assertEqual(self.transcribe(wav(rate=44100))[0], 415)
        self.assertEqual(self.transcribe(wav(seconds=0.2))[0], 415)
        self.assertEqual(self.transcribe(wav(seconds=9.0))[0], 413)
        self.assertEqual(self.transcribe(b"not audio")[0], 415)

    def test_rate_limit_per_token_and_route(self):
        for _ in range(voice.CALLS_PER_MINUTE["transcribe"]):
            self.assertEqual(self.transcribe(wav(seconds=0.5))[0], 200)
        self.assertEqual(self.transcribe(wav(seconds=0.5))[0], 429)
        (self.data / voice.BINDINGS_FILE).write_text("{}", encoding="utf-8")
        for _ in range(voice.CALLS_PER_MINUTE["input"]):
            self.assertEqual(self.say({"kind": "command", "command": "repeat"})[0], 409)
        self.assertEqual(self.say({"kind": "command", "command": "repeat"})[0], 429)
        journal = (self.data / "voice-journal.jsonl").read_text(encoding="utf-8")
        self.assertIn('"status": "rate_limited"', journal)


class InputTests(VoiceCase):
    def test_unbound_session_is_409(self):
        (self.data / voice.BINDINGS_FILE).write_text("{}", encoding="utf-8")
        status, payload, _ = self.say({"kind": "command", "command": "next_question"})
        self.assertEqual(status, 409, payload)
        self.assertEqual(self.ui.calls, [])

    def test_bad_bodies_are_400(self):
        self.assertEqual(self.say({"kind": "command", "command": "delete_memory"})[0], 400)
        self.assertEqual(self.say({"kind": "input", "operator_input": {"source": "typed", "text": "x"}})[0], 400)
        self.assertEqual(self.say({"kind": "shout"})[0], 400)

    def test_dialogue_turn_is_relayed_and_unhandled_text_falls_back_to_the_guide(self):
        self.state.voice_config.dialogue = True
        said = {"text": "I see several small parts in cell 1. Are they all the same?", "kind": "question", "basis_ref": "k1"}
        self.ui.replies = {"/api/dialogue/turn": {"handled": True, "speak": said}}
        _, first, _ = self.say({"kind": "command", "command": "next_question"})
        self.assertEqual(first["speak"], said)
        self.assertEqual(self.ui.calls[0], ("/api/dialogue/turn", {"session": "demo-01", "command": "next_question"}))
        _, again, _ = self.say({"kind": "command", "command": "repeat"})
        self.assertEqual(again["speak"], said)
        _, reply, _ = self.say(self.text("yes, all screws"))
        self.assertEqual(self.ui.calls[1], ("/api/dialogue/turn", {"session": "demo-01", "text": "yes, all screws"}))
        self.assertEqual(reply["speak"], said)
        self.ui.replies = {"/api/dialogue/turn": {"handled": False},
                           "/api/guide/ask": {"accepted": True, "text": "It goes into cell 4.", "package_sha256": "p"}}
        _, guide, _ = self.say(self.text("where does it go"))
        self.assertEqual(guide["speak"]["kind"], "guide_verified")
        self.assertEqual([p for p, _ in self.ui.calls][-2:], ["/api/dialogue/turn", "/api/guide/ask"])

    def test_forwarding_table_question_read_back_commit_repeat(self):
        self.ui.replies = {
            "/api/questions/next": {"done": False, "stage": "answer", "question_id": "Q-P1-01", "op_key": "k1",
                                    "text": "What did you take out first?", "screen_options": ["A", "B"]},
            "/api/questions/answer": {"status": "read_back", "stage": "confirm", "question_id": "Q-P1-01",
                                      "op_key": "k1", "read_back": "A screw from the back shell. Is that right?"},
            "/api/questions/confirm": {"status": "committed", "stage": "done", "question_id": "Q-P1-01"},
        }
        _, first, _ = self.say({"kind": "command", "command": "next_question"})
        self.assertEqual(first["speak"], {"text": "What did you take out first?" + voice.SCREEN_HINT,
                                          "kind": "question", "basis_ref": "k1"})
        _, again, _ = self.say({"kind": "command", "command": "repeat"})
        self.assertEqual(again["speak"], first["speak"])
        _, read_back, _ = self.say(self.text(SECRET_WORDS))
        self.assertEqual(read_back["speak"]["kind"], "read_back")
        _, done, _ = self.say(self.text("yes, that is correct"))
        self.assertEqual(done["speak"], {"text": voice.RECORDED, "kind": "status", "basis_ref": None})
        paths = [p for p, _ in self.ui.calls]
        self.assertEqual(paths, ["/api/questions/next", "/api/questions/answer", "/api/questions/confirm"])
        self.assertEqual(self.ui.calls[1][1], {"session": "demo-01", "question_id": "Q-P1-01", "text": SECRET_WORDS})
        journal = (self.data / "voice-journal.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(SECRET_WORDS, journal)

    def test_guide_answers_only_verified_text(self):
        self.ui.replies = {"/api/guide/ask": {"text": "It goes into cell 4.", "accepted": True,
                                              "package_sha256": "p" * 64}}
        _, ok, _ = self.say(self.text("where does the D-pad go"))
        self.assertEqual(ok["speak"], {"text": "It goes into cell 4.", "kind": "guide_verified",
                                       "basis_ref": "p" * 64})
        self.ui.replies = {"/api/guide/ask": {"text": "free model text", "accepted": False,
                                              "package_sha256": "p" * 64}}
        _, refused, _ = self.say(self.text("which screwdriver"))
        self.assertEqual(refused["speak"]["text"], voice.NOT_CONFIRMED)
        self.assertNotIn("free model text", json.dumps(refused))


if __name__ == "__main__":
    unittest.main()
