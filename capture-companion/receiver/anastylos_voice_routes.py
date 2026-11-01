#!/usr/bin/env python3
"""Voice routes for the capture receiver (VOICE-01, TL decision R9/R10). Stdlib only.

R9  POST /v1/guided/sessions/{id}/voice/transcribe  audio/wav → {status, transcript, latency_s}
R10 POST /v1/guided/sessions/{id}/voice/input       {kind: input|command} → {status, speak}

Same TLS, guided-token and session checks as the other guided routes
(role ``source``, session bound to that source via the control journal).
The receiver only runs ``transcribe.py --stdin`` as a subprocess (the key
lives only in this process environment) and forwards operator input to
the running session UI on 127.0.0.1; the session UI stays the single
writer. Neither audio nor transcript is stored or logged: the voice
journal records route, status and timing only.
"""

from __future__ import annotations

import io
import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
import wave
from collections import deque
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path

import anastylos_guided_routes as guided

PREFIX = "/v1/guided/sessions/"
WAV_MAX_BYTES = 260 * 1024
MIN_SECONDS, MAX_SECONDS, RATE = 0.4, 8.0, 16000
TRANSCRIBE_DEADLINE_S = 60
CALLS_PER_MINUTE = 20
INPUT_BODY_MAX = 16 * 1024
SESSION_UI_TIMEOUT_S = 120
BINDINGS_FILE = "guided-voice-bindings.json"

# Fixed sentences the receiver may choose (TL table, last row and refusal).
RECORDED = "Recorded."
NOT_CONFIRMED = "I cannot confirm that from the evidence."
SCREEN_HINT = " Please look at the screen for the options."
ALL_DONE = "There are no more questions."
COME_BACK = "I will come back to this question later."
NOT_UNDERSTOOD = "Sorry, I did not get that. Please say it again."
NOTHING_TO_REPEAT = "There is nothing to repeat yet."
UNAVAILABLE = "The workstation could not handle that. Please use the screen."


@dataclass
class VoiceConfig:
    """Workstation-side voice settings; set on the receiver state at serve time."""
    transcriber: list[str] | None = None
    session_ui_port: int | None = None
    bindings_path: Path | None = None


@dataclass
class _SessionVoice:
    lock: threading.Lock = field(default_factory=threading.Lock)
    question_id: str | None = None
    stage: str | None = None
    last_speak: dict | None = None


def get_voice(state):
    """Per-data-dir voice runtime: config, per-session state, rate windows, journal lock."""
    cached = vars(state).get("_voice_runtime")
    if cached is None:
        with state.lock:
            cached = vars(state).get("_voice_runtime")
            if cached is None:
                cached = {"sessions": {}, "rates": {}, "lock": threading.Lock()}
                vars(state)["_voice_runtime"] = cached
    return cached


def is_voice_path(path):
    return path.startswith(PREFIX) and (path.endswith("/voice/transcribe") or path.endswith("/voice/input"))


def check_wav(audio):
    """Mono 16-bit PCM at 16 kHz, 0.4–8 s; returns seconds or raises ValueError."""
    try:
        with wave.open(io.BytesIO(audio)) as clip:
            channels, width, rate, frames = (clip.getnchannels(), clip.getsampwidth(),
                                             clip.getframerate(), clip.getnframes())
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"not a PCM WAV file ({exc})") from None
    if channels != 1 or width != 2 or rate != RATE:
        raise ValueError("need mono 16-bit PCM at 16 kHz")
    seconds = frames / rate
    if not MIN_SECONDS <= seconds <= MAX_SECONDS:
        raise ValueError("clip must be 0.4 to 8 seconds")
    return seconds


def speak_for(route, result):
    """Map one session-UI response to (speak, question_id, stage) per the TL table."""
    def question(r):
        text = r.get("text") or ""
        if r.get("screen_options"):
            text += SCREEN_HINT
        return {"text": text, "kind": "question", "basis_ref": r.get("op_key")}

    if route == "guide":
        if result.get("accepted"):
            return {"text": result.get("text") or "", "kind": "guide_verified",
                    "basis_ref": result.get("package_sha256")}, None, None
        return {"text": NOT_CONFIRMED, "kind": "status", "basis_ref": result.get("package_sha256")}, None, None
    if result.get("done") is True:
        return {"text": ALL_DONE, "kind": "status", "basis_ref": None}, None, None
    status, stage, qid = result.get("status"), result.get("stage"), result.get("question_id")
    if status == "committed" or status == "already_committed":
        return {"text": RECORDED, "kind": "status", "basis_ref": result.get("op_key")}, None, None
    if stage == "confirm" and result.get("read_back"):
        return {"text": result["read_back"], "kind": "read_back", "basis_ref": result.get("op_key")}, qid, "confirm"
    if stage == "answer" and result.get("text"):
        return question(result), qid, "answer"
    if stage == "answer":
        return {"text": NOT_UNDERSTOOD, "kind": "status", "basis_ref": None}, qid, "answer"
    if stage == "deferred":
        return {"text": COME_BACK, "kind": "status", "basis_ref": None}, None, None
    return {"text": UNAVAILABLE, "kind": "status", "basis_ref": None}, qid, stage


class VoiceRoutes:
    """Route handlers bound to one live request handler."""

    def __init__(self, handler):
        self.h = handler
        self.state = handler.state
        self.guided = guided.GuidedRoutes(handler)
        self.config = vars(self.state).get("voice_config") or VoiceConfig()
        self.runtime = get_voice(self.state)

    def dispatch_post(self, parsed):
        path = parsed.path
        suffix = "/voice/transcribe" if path.endswith("/voice/transcribe") else "/voice/input"
        session_id = self.guided._session_id_from(path, suffix)
        if session_id is None:
            return False
        record = self.guided._require_guided("source")
        if record is None:
            return True
        _snapshot, exc = self.guided._journal_errors(self.guided.journal.get_session,
                                                     session_id, record.get("source_id"))
        if exc is not None:
            return True
        if not self._within_rate(record.get("source_id") or ""):
            return True
        if suffix == "/voice/transcribe":
            self._transcribe(session_id)
        else:
            self._input(session_id)
        return True

    # -- R9 ----------------------------------------------------------------

    def _transcribe(self, session_id):
        if self.h.headers.get("Content-Type", "").split(";")[0].strip() != "audio/wav":
            self._discard()
            return self._fail(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "unsupported_media", "Send the clip as audio/wav.")
        try:
            length = int(self.h.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if not 0 < length <= WAV_MAX_BYTES:
            self.h.close_connection = True
            return self._fail(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "too_large", "The clip is empty or too large.")
        audio = self.h.rfile.read(length)
        try:
            seconds = check_wav(audio)
        except ValueError as exc:
            return self._fail(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "invalid_audio", str(exc))
        if not self.config.transcriber:
            return self._fail(HTTPStatus.SERVICE_UNAVAILABLE, "voice_unavailable", "Transcription is not configured.")
        voice = self._session(session_id)
        if not voice.lock.acquire(blocking=False):
            return self._fail(HTTPStatus.TOO_MANY_REQUESTS, "busy", "One voice call at a time.", {"Retry-After": "1"})
        try:
            began = time.monotonic()
            try:
                done = subprocess.run(self.config.transcriber, input=audio, capture_output=True,
                                      timeout=TRANSCRIBE_DEADLINE_S)
            except subprocess.TimeoutExpired:
                self._journal(session_id, "transcribe", "timeout", seconds, time.monotonic() - began)
                return self._fail(HTTPStatus.GATEWAY_TIMEOUT, "timeout", "Transcription did not finish in time.")
            if done.returncode != 0:
                self._journal(session_id, "transcribe", "provider_error", seconds, time.monotonic() - began)
                return self._fail(HTTPStatus.BAD_GATEWAY, "provider_error", "Transcription failed.")
            try:
                result = json.loads(done.stdout)
            except ValueError:
                self._journal(session_id, "transcribe", "provider_error", seconds, time.monotonic() - began)
                return self._fail(HTTPStatus.BAD_GATEWAY, "provider_error", "Transcription failed.")
            self._journal(session_id, "transcribe", result.get("status"), seconds, time.monotonic() - began)
            self.h._send_json(HTTPStatus.OK, {k: result.get(k) for k in ("status", "transcript", "latency_s")})
        finally:
            voice.lock.release()

    # -- R10 ---------------------------------------------------------------

    def _input(self, session_id):
        body = self.guided._read_json(INPUT_BODY_MAX)
        if body is None:
            return
        kind = body.get("kind")
        if kind == "command":
            command = body.get("command")
            if command not in ("next_question", "repeat"):
                return self._fail(HTTPStatus.BAD_REQUEST, "invalid_input", "Unknown command.")
            text = None
        elif kind == "input":
            operator_input = body.get("operator_input")
            if not isinstance(operator_input, dict) or operator_input.get("source") != "speech_transcript" \
                    or not isinstance(operator_input.get("text"), str) or not operator_input["text"].strip():
                return self._fail(HTTPStatus.BAD_REQUEST, "invalid_input",
                                  "operator_input needs source speech_transcript and text.")
            command, text = None, operator_input["text"].strip()
        else:
            return self._fail(HTTPStatus.BAD_REQUEST, "invalid_input", "kind must be input or command.")
        agent_session = self._binding(session_id)
        if agent_session is None:
            return self._fail(HTTPStatus.CONFLICT, "not_bound", "voice not bound to an agent session")
        voice = self._session(session_id)
        if not voice.lock.acquire(blocking=False):
            return self._fail(HTTPStatus.TOO_MANY_REQUESTS, "busy", "One voice call at a time.", {"Retry-After": "1"})
        try:
            began = time.monotonic()
            if command == "repeat":
                speak = voice.last_speak or {"text": NOTHING_TO_REPEAT, "kind": "status", "basis_ref": None}
                self._journal(session_id, "repeat", "ok", None, time.monotonic() - began)
                return self.h._send_json(HTTPStatus.OK, {"status": "ok", "speak": speak})
            if command == "next_question":
                route, path, payload = "question", "/api/questions/next", {"session": agent_session}
            elif voice.stage == "answer" and voice.question_id:
                route, path, payload = "question", "/api/questions/answer", {
                    "session": agent_session, "question_id": voice.question_id, "text": text}
            elif voice.stage == "confirm" and voice.question_id:
                route, path, payload = "question", "/api/questions/confirm", {
                    "session": agent_session, "question_id": voice.question_id, "text": text}
            else:
                route, path, payload = "guide", "/api/guide/ask", {"session": agent_session, "question": text}
            status, result = self._session_ui(path, payload)
            if status != 200 or not isinstance(result, dict):
                speak = {"text": UNAVAILABLE, "kind": "status", "basis_ref": None}
                self._journal(session_id, path, f"session_ui_{status}", None, time.monotonic() - began)
                return self.h._send_json(HTTPStatus.OK, {"status": "session_ui_error", "speak": speak})
            speak, qid, stage = speak_for(route, result)
            if route == "question":
                voice.question_id, voice.stage = qid, stage
            voice.last_speak = speak
            self._journal(session_id, path, "ok", None, time.monotonic() - began)
            self.h._send_json(HTTPStatus.OK, {"status": "ok", "speak": speak})
        finally:
            voice.lock.release()

    # -- plumbing ----------------------------------------------------------

    def _session_ui(self, path, payload):
        port = self.config.session_ui_port
        if not port:
            return 503, None
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=SESSION_UI_TIMEOUT_S) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, None
        except (urllib.error.URLError, OSError, ValueError):
            return 502, None

    def _binding(self, session_id):
        path = self.config.bindings_path or (self.state.data_dir / BINDINGS_FILE)
        try:
            bindings = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        name = bindings.get(session_id) if isinstance(bindings, dict) else None
        if not isinstance(name, str) or not name or "/" in name or "\\" in name or name in (".", ".."):
            return None
        return name

    def _session(self, session_id):
        with self.runtime["lock"]:
            return self.runtime["sessions"].setdefault(session_id, _SessionVoice())

    def _within_rate(self, token_key):
        now = time.monotonic()
        with self.runtime["lock"]:
            window = self.runtime["rates"].setdefault(token_key, deque())
            while window and now - window[0] > 60:
                window.popleft()
            if len(window) >= CALLS_PER_MINUTE:
                limited = True
            else:
                window.append(now)
                limited = False
        if limited:
            self._fail(HTTPStatus.TOO_MANY_REQUESTS, "rate_limited", "At most 20 voice calls per minute.",
                       {"Retry-After": "5"})
        return not limited

    def _journal(self, session_id, route, status, audio_s, elapsed_s):
        line = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "session_id": session_id,
                "route": route, "status": status, "audio_s": None if audio_s is None else round(audio_s, 2),
                "elapsed_s": round(elapsed_s, 3)}
        with self.runtime["lock"]:
            with open(self.state.data_dir / "voice-journal.jsonl", "a", encoding="utf-8") as handle:
                handle.write(json.dumps(line) + "\n")

    def _discard(self):
        try:
            length = int(self.h.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if 0 < length <= WAV_MAX_BYTES:
            self.h.rfile.read(length)
        else:
            self.h.close_connection = True

    def _fail(self, status, code, message, extra=None):
        self.h._send_json(status, {"error": {"code": code, "message": message}}, extra)
