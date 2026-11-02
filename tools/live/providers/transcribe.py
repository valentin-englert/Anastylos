"""One-shot speech-to-text with muse-voice-transcribe-1.0 (Meta Model API), push-to-talk mode.

    python tools/live/providers/transcribe.py --stdin < clip.wav      # prints anastylos-live-transcript@1

Contract (dev.meta.ai/docs/api-reference/voice/transcribe, read 2026-11-01): POST /v1/asr/transcribe,
multipart with a JSON part ``request`` and a part ``audio``; WAV only, mono 16-bit PCM at 16 or 24 kHz, at most
10 minutes and 32 MB; the transcript is ``transcript``; no confidence is returned. The clip is checked locally
before anything is sent. The transcript is operator input like typed text: the coordinator still reads it back.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import uuid
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from meta import BACKOFF_S, KEY_ENV, MAX_RETRIES, TransportFailure, TransportTimeout, UrllibTransport, _retryable  # noqa: E402
from provider_core import sanitize_message  # noqa: E402

MODEL = "muse-voice-transcribe-1.0"
ENDPOINT = "https://api.meta.ai/v1/asr/transcribe"
SCHEMA = "anastylos-live-transcript@1"
TIMEOUT_S = 60.0
MAX_BYTES, MAX_SECONDS, RATES = 32 * 1024 * 1024, 600, (16000, 24000)
KEYWORDS = ("Muse", "back shell", "front shell", "PCB", "D-pad", "membrane", "rubber pad", "screw", "cell")


def check_wav(audio: bytes) -> dict:
    """Mono 16-bit PCM WAV at 16 or 24 kHz within the size and duration limits; raises ValueError otherwise."""
    if len(audio) > MAX_BYTES:
        raise ValueError("audio larger than 32 MB")
    try:
        with wave.open(io.BytesIO(audio)) as clip:
            channels, width, rate, frames = clip.getnchannels(), clip.getsampwidth(), clip.getframerate(), clip.getnframes()
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"not a PCM WAV file ({exc})") from None
    if channels != 1 or width != 2 or rate not in RATES:
        raise ValueError(f"need mono 16-bit PCM at 16 or 24 kHz, got {channels} ch, {8 * width} bit, {rate} Hz")
    seconds = frames / rate
    if seconds > MAX_SECONDS:
        raise ValueError("audio longer than 10 minutes")
    return {"seconds": round(seconds, 2), "rate": rate}


def multipart(request: dict, audio: bytes) -> tuple[bytes, str]:
    boundary = f"anastylos-{uuid.uuid4().hex}"
    head = (f'--{boundary}\r\nContent-Disposition: form-data; name="request"\r\nContent-Type: application/json\r\n\r\n'
            f'{json.dumps(request)}\r\n--{boundary}\r\nContent-Disposition: form-data; name="audio"; filename="clip.wav"\r\n'
            "Content-Type: audio/wav\r\n\r\n").encode("utf-8")
    return head + audio + f"\r\n--{boundary}--\r\n".encode("utf-8"), f"multipart/form-data; boundary={boundary}"


def transcribe(audio: bytes, transport=None, keywords=KEYWORDS, language: str = "English", sleep=time.sleep) -> dict:
    result = {"schema": SCHEMA, "model": MODEL, "status": None, "transcript": None, "audio_ms": None, "latency_s": None,
              "http": None, "attempts": 0, "error": None}
    try:
        result["clip"] = check_wav(audio)
    except ValueError as exc:
        return {**result, "status": "invalid_audio", "error": str(exc)}
    key = os.environ.get(KEY_ENV, "").strip()
    if not key:
        return {**result, "status": "disabled", "error": f"{KEY_ENV} not set in this process"}
    body, content_type = multipart({"model": MODEL, "audioEncoding": "WAV", "mode": "PUSH_TO_TALK",
                                    "languageBias": [language], "keywords": list(keywords)}, audio)
    headers = {"Authorization": "Bearer " + key, "Content-Type": content_type, "Accept": "application/json"}
    transport = transport or UrllibTransport()
    began = time.monotonic()
    for attempt in range(MAX_RETRIES + 1):
        result["attempts"] = attempt + 1
        try:
            status, raw = transport.post(ENDPOINT, headers, body, TIMEOUT_S)
        except TransportTimeout:
            result.update(status="timeout", error="no answer within the timeout")
            break
        except TransportFailure as exc:
            result.update(status="provider_error", error=sanitize_message(str(exc), (key,)))
            break
        result["http"] = status
        if status == 200:
            data = json.loads(raw)
            result.update(status="ok", transcript=(data.get("transcript") or "").strip(), audio_ms=data.get("audioDurationMs"))
            break
        result.update(status="provider_error", error=sanitize_message(raw.decode("utf-8", "replace"), (key,)))
        if not _retryable(status) or attempt == MAX_RETRIES:
            break
        sleep(BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)])
    result["latency_s"] = round(time.monotonic() - began, 2)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--keyword", action="append", default=[],
                        help="agent name(s) for the ASR keyword list; replaces the default \"Muse\" keyword")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--stdin", action="store_true", help="read the WAV clip from standard input")
    source.add_argument("--audio", type=Path)
    args = parser.parse_args()
    audio = sys.stdin.buffer.read() if args.stdin else args.audio.read_bytes()
    keywords = tuple(args.keyword) + tuple(k for k in KEYWORDS if k != "Muse") if args.keyword else KEYWORDS
    print(json.dumps(transcribe(audio, keywords=keywords)))


if __name__ == "__main__":
    main()
