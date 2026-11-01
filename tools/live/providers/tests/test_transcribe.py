"""Speech-to-text provider: local WAV checks, request shape, retries and sanitized failures (no network)."""

from __future__ import annotations

import io
import json
import os
import sys
import unittest
import wave
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import transcribe  # noqa: E402

KEY = "k" * 40


def wav(rate: int = 16000, channels: int = 1, width: int = 2, seconds: float = 0.5) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as clip:
        clip.setnchannels(channels)
        clip.setsampwidth(width)
        clip.setframerate(rate)
        clip.writeframes(b"\x00" * int(rate * seconds) * channels * width)
    return buffer.getvalue()


class FakeTransport:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def post(self, url, headers, body, timeout_s):
        self.calls.append((url, headers, body))
        return self.replies.pop(0)


class TranscribeTest(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.dict(os.environ, {transcribe.KEY_ENV: KEY})
        patch.start()
        self.addCleanup(patch.stop)

    def test_success_sends_multipart_with_request_and_audio(self):
        fake = FakeTransport([(200, json.dumps({"transcript": " Done. ", "audioDurationMs": 500}).encode())])
        result = transcribe.transcribe(wav(), transport=fake)
        self.assertEqual((result["status"], result["transcript"], result["audio_ms"]), ("ok", "Done.", 500))
        url, headers, body = fake.calls[0]
        self.assertEqual(url, transcribe.ENDPOINT)
        self.assertTrue(headers["Content-Type"].startswith("multipart/form-data; boundary="))
        self.assertIn(b'name="request"', body)
        self.assertIn(b'"audioEncoding": "WAV"', body)
        self.assertIn(b'"languageBias": ["English"]', body)
        self.assertIn(b'name="audio"; filename="clip.wav"', body)

    def test_unsupported_audio_is_refused_before_sending(self):
        fake = FakeTransport([])
        for clip in (wav(rate=44100), wav(channels=2), wav(width=1), b"not audio"):
            result = transcribe.transcribe(clip, transport=fake)
            self.assertEqual(result["status"], "invalid_audio")
        self.assertEqual(fake.calls, [])

    def test_retries_only_retryable_statuses(self):
        fake = FakeTransport([(429, b"busy"), (200, b'{"transcript": "yes"}')])
        result = transcribe.transcribe(wav(), transport=fake, sleep=lambda s: None)
        self.assertEqual((result["status"], result["attempts"]), ("ok", 2))
        fake = FakeTransport([(400, b"bad wav")])
        result = transcribe.transcribe(wav(), transport=fake, sleep=lambda s: None)
        self.assertEqual((result["status"], result["attempts"]), ("provider_error", 1))

    def test_missing_key_is_disabled_and_errors_hide_the_key(self):
        with mock.patch.dict(os.environ, {transcribe.KEY_ENV: ""}):
            self.assertEqual(transcribe.transcribe(wav())["status"], "disabled")
        fake = FakeTransport([(401, f"invalid Bearer {KEY}".encode())])
        result = transcribe.transcribe(wav(), transport=fake, sleep=lambda s: None)
        self.assertNotIn(KEY, json.dumps(result))


if __name__ == "__main__":
    unittest.main()
