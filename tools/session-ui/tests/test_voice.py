"""Voice route: a push-to-talk clip goes to the transcriber process and only the transcript comes back (no network)."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import wave
from pathlib import Path

UI_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UI_ROOT))
sys.path.insert(0, str(UI_ROOT / "demo"))

import prepare_demo  # noqa: E402
import server  # noqa: E402

FAKE_OK = [sys.executable, "-c", "import sys, json; n = len(sys.stdin.buffer.read()); "
           "print(json.dumps({'status': 'ok', 'transcript': 'yes', 'latency_s': 0.1, 'bytes': n}))"]
FAKE_BROKEN = [sys.executable, "-c", "import sys; sys.exit(3)"]


def wav_clip() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(16000)
        clip.writeframes(b"\x00\x00" * 8000)
    return buffer.getvalue()


class VoiceRouteTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        workspace = Path(tmp.name) / "workspace"
        workspace.mkdir()
        prepare_demo.make_fresh_session(workspace / "p1", phases=("P1",))
        self.httpd = server.serve(workspace, "127.0.0.1", 0)
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(thread.join, 30)
        self.addCleanup(self.httpd.shutdown)
        self.addCleanup(setattr, server.Handler, "transcriber", server.TRANSCRIBER)

    def post(self, body: bytes, content_type: str):
        request = urllib.request.Request(self.base + "/api/voice/transcribe", data=body, method="POST",
                                         headers={"Content-Type": content_type})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_clip_is_transcribed_and_only_the_transcript_returns(self):
        server.Handler.transcriber = FAKE_OK
        status, payload = self.post(wav_clip(), "audio/wav")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"status": "ok", "transcript": "yes", "latency_s": 0.1, "error": None})

    def test_other_content_types_are_refused(self):
        server.Handler.transcriber = FAKE_OK
        self.assertEqual(self.post(b"{}", "application/json")[0], 415)

    def test_a_failing_transcriber_is_reported(self):
        server.Handler.transcriber = FAKE_BROKEN
        self.assertEqual(self.post(wav_clip(), "audio/wav")[0], 502)


if __name__ == "__main__":
    unittest.main()
