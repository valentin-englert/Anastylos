"""Shared, network-free core of the live providers: request body, request hash, response interpretation,
result and trace records, error sanitizing, and the recorded-fixture format (`meta.py` writes it, `fake.py`
replays it). Contract: `tools/live/CONTRACT.md` §3.1 and §4.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from functools import lru_cache
from pathlib import Path

from jsonschema_subset import validate
from task_prompts import PROMPT_VERSION, instructions

MODEL = "muse-spark-1.3-contributor"
EFFORT = "max"
TASKS = ("question_wording", "answer_interpretation", "next_action", "guide_claims")
RESULT_SCHEMA = "anastylos-live-provider-result@1"
TRACE_SCHEMA = "anastylos-live-provider-trace@1"
FIXTURE_SCHEMA = "anastylos-live-provider-fixture@1"
SCHEMAS_DIR = Path(__file__).resolve().parents[1] / "schemas"
ERROR_MESSAGE_MAX = 300

_BEARER = re.compile(r"(?i)bearer\s+\S+")
_TOKEN_LIKE = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")


@lru_cache(maxsize=None)
def task_schema(task: str) -> dict:
    if task not in TASKS:
        raise ValueError(f"unknown task {task!r}")
    return json.loads((SCHEMAS_DIR / f"task.{task}.json").read_text(encoding="utf-8"))


def build_request(task: str, payload: dict) -> dict:
    """The exact request body sent to the Meta Model API. Raises ValueError for an invalid payload (caller bug)."""
    spec = task_schema(task)
    problems = validate(payload, spec["input"])
    if problems:
        raise ValueError(f"{task} payload invalid: {problems[:3]}")
    return {
        "model": MODEL,
        "reasoning": {"effort": EFFORT},
        "input": [
            {"type": "message", "role": "system", "content": [{"type": "input_text", "text": instructions(task)}]},
            {"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "INPUT JSON:\n" + json.dumps(payload, ensure_ascii=False, sort_keys=True)}]},
        ],
        "text": {"format": {"type": "json_schema", "name": f"anastylos_{task}", "strict": True,
                            "schema": spec["output"]}},
    }


def encode_request(body: dict) -> bytes:
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sanitize_message(text: str, secrets: tuple[str, ...] = ()) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    text = _BEARER.sub("Bearer [redacted]", text)
    text = _TOKEN_LIKE.sub("[redacted]", text)
    return text[:ERROR_MESSAGE_MAX]


def make_error(error_type: str, message: str = "", http_status: int | None = None,
               secrets: tuple[str, ...] = ()) -> dict:
    return {"type": error_type, "http_status": http_status, "message": sanitize_message(message, secrets)}


def api_error_from_body(http_status: int, raw: bytes, secrets: tuple[str, ...] = ()) -> dict:
    text = raw.decode("utf-8", "replace")
    try:
        detail = json.loads(text)
    except ValueError:
        return make_error("http_error", text, http_status, secrets)
    error = detail.get("error") if isinstance(detail, dict) else None
    if isinstance(error, dict):
        kind = str(error.get("type") or error.get("code") or "http_error")
        return make_error(kind, str(error.get("message") or ""), http_status, secrets)
    return make_error("http_error", json.dumps(detail, ensure_ascii=False), http_status, secrets)


def _message_texts(response: dict) -> tuple[list[str], bool]:
    texts, refused = [], False
    for item in response.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
            elif isinstance(part, dict) and part.get("type") == "refusal":
                refused = True
    return texts, refused


def interpret_response(task: str, response: dict) -> dict:
    """Map an HTTP-200 response body to status/output/error plus returned model, effort and usage."""
    reasoning = response.get("reasoning") if isinstance(response.get("reasoning"), dict) else {}
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else None
    seen = {"model": response.get("model"), "reasoning_effort_returned": reasoning.get("effort"), "usage": usage}

    def outcome(status: str, output=None, error=None) -> dict:
        return {"status": status, "output": output, "error": error, **seen}

    if response.get("status") != "completed":
        return outcome("provider_error", error=make_error(f"response_{response.get('status')}"))
    if seen["model"] != MODEL:
        return outcome("provider_error", error=make_error("model_mismatch", f"returned {seen['model']}"))
    if seen["reasoning_effort_returned"] != EFFORT:
        return outcome("provider_error", error=make_error("effort_mismatch",
                                                          f"returned {seen['reasoning_effort_returned']}"))
    texts, refused = _message_texts(response)
    if refused:
        return outcome("invalid_output", error=make_error("refusal"))
    if len(texts) != 1:
        return outcome("invalid_output", error=make_error("no_single_message_text", f"{len(texts)} texts"))
    try:
        output = json.loads(texts[0])
    except ValueError as exc:
        return outcome("invalid_output", error=make_error("invalid_json", str(exc)))
    problems = validate(output, task_schema(task)["output"])
    if problems:
        return outcome("invalid_output", error=make_error("schema_violation", "; ".join(problems)))
    return outcome("ok", output=output)


def make_result(*, request_id: str, task: str, status: str, request_sha256: str, cache_hit: bool = False,
                output: dict | None = None, model: str | None = MODEL,
                reasoning_effort_returned: str | None = None, latency_s: float | None = None,
                usage: dict | None = None, error: dict | None = None) -> dict:
    return {
        "schema": RESULT_SCHEMA,
        "request_id": request_id,
        "task": task,
        "status": status,
        "output": output if status == "ok" else None,
        "model": model,
        "reasoning_effort_requested": EFFORT,
        "reasoning_effort_returned": reasoning_effort_returned,
        "latency_s": None if latency_s is None else round(latency_s, 3),
        "usage": usage,
        "request_sha256": request_sha256,
        "cache_hit": cache_hit,
        "error": error,
    }


def make_trace(result: dict, *, attempts: int, http_status: int | None) -> dict:
    """Sanitized per-call trace: no key, no headers, no prompt or output text."""
    return {
        "schema": TRACE_SCHEMA,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "request_id": result["request_id"],
        "task": result["task"],
        "model": result["model"],
        "reasoning_effort_requested": result["reasoning_effort_requested"],
        "reasoning_effort_returned": result["reasoning_effort_returned"],
        "prompt_version": PROMPT_VERSION,
        "latency_s": result["latency_s"],
        "usage": result["usage"],
        "status": result["status"],
        "error": result["error"],
        "request_sha256": result["request_sha256"],
        "cache_hit": result["cache_hit"],
        "attempts": attempts,
        "http_status": http_status,
    }


def make_fixture(*, task: str, body: dict, request_sha256: str, response: dict, latency_s: float) -> dict:
    return {
        "schema": FIXTURE_SCHEMA,
        "task": task,
        "request_sha256": request_sha256,
        "request": body,
        "http_status": 200,
        "response": response,
        "latency_s": round(latency_s, 3),
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def read_fixture(path: Path) -> dict | None:
    """The recorded fixture at `path`, or None when absent. A present but malformed or tampered file raises
    ValueError: replaying it, or silently paying for the call again, would both hide the problem."""
    if not path.is_file():
        return None
    fixture = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(fixture, dict) or fixture.get("schema") != FIXTURE_SCHEMA:
        raise ValueError(f"{path.name}: not a {FIXTURE_SCHEMA} file")
    if sha256_hex(encode_request(fixture["request"])) != fixture.get("request_sha256"):
        raise ValueError(f"{path.name}: request_sha256 does not match the recorded request")
    return fixture
