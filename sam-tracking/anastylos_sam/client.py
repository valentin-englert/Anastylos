"""Meta Model API transport. THE ONLY network module in this package.

Stdlib ``urllib`` only. The API key lives in memory for the
Authorization header and is NEVER printed, logged, or written to any
file. Ledger entries are redacted by construction (no key, no prompt
text, no media bytes).
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
import typing
import urllib.error
import urllib.request
from pathlib import Path

from . import BASE_URL

_JSON = "application/json"
_RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class ApiError(Exception):
    def __init__(self, status: int | None, detail: Any, latency_s: float):
        super().__init__(f"HTTP {status}: {json.dumps(detail)[:500]}")
        self.status = status
        self.detail = detail
        self.latency_s = latency_s


class BudgetExceeded(Exception):
    pass


def load_key(api_key_stdin: bool = False) -> str:
    if api_key_stdin:
        data = sys.stdin.read().strip()
        if not data:
            raise SystemExit("gate2a_client: ERROR: no key received on stdin")
        return data
    key = os.environ.get("MODEL_API_KEY", "").strip()
    if not key:
        raise SystemExit("set MODEL_API_KEY or use --api-key-stdin")
    return key


def check_budget(spent_usd: float, next_usd: float, ceiling_usd: float) -> float:
    if spent_usd + next_usd > ceiling_usd + 1e-9:
        raise BudgetExceeded(
            f"refusing request: ${spent_usd:.6f} spent + ${next_usd:.6f} "
            f"estimate exceeds ${ceiling_usd:.6f} ceiling"
        )
    return spent_usd + next_usd


def append_ledger(path: Path, entry: dict) -> None:
    entry = {"ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             **entry}
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def _backoff(attempt: int) -> float:
    return min(30.0, 0.5 * (2 ** attempt)) + random.uniform(0, 0.25)


def _post_json(url: str, key: str, payload: dict,
               timeout_s: float) -> tuple[int, dict, float]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": _JSON, "Accept": _JSON})
    begun = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            raw = response.read().decode("utf-8", "replace")
            return response.status, json.loads(raw), time.monotonic() - begun
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")[:2000]
        try:
            detail = json.loads(raw)
        except ValueError:
            detail = {"raw": raw}
        raise ApiError(exc.code, detail, time.monotonic() - begun)


def get_models(key: str, timeout_s: float = 30.0) -> tuple[int, dict]:
    request = urllib.request.Request(
        BASE_URL + "/models", method="GET",
        headers={"Authorization": "Bearer " + key, "Accept": _JSON})
    begun = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")[:500]
        raise ApiError(exc.code, {"raw": raw}, time.monotonic() - begun)


def upload_file(path: Path, key: str, purpose: str = "user_data",
                timeout_s: float = 300.0, max_attempts: int = 3) -> dict:
    """Multipart upload per POST /v1/files. Returns the file object."""
    boundary = f"----anastylos-{random.randrange(1 << 63):016x}"
    data = path.read_bytes()
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"purpose\"\r\n\r\n"
        f"{purpose}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
        f"filename=\"{path.name}\"\r\nContent-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")
    last: ApiError | None = None
    for attempt in range(max_attempts):
        request = urllib.request.Request(
            BASE_URL + "/files", data=body, method="POST",
            headers={"Authorization": "Bearer " + key,
                     "Content-Type": f"multipart/form-data; boundary={boundary}",
                     "Accept": _JSON})
        begun = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                return {"http": response.status,
                        "object": json.loads(response.read().decode("utf-8")),
                        "latency_s": round(time.monotonic() - begun, 2)}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")[:2000]
            try:
                detail: Any = json.loads(raw)
            except ValueError:
                detail = {"raw": raw}
            last = ApiError(exc.code, detail, time.monotonic() - begun)
            if exc.code not in _RETRY_STATUS or attempt == max_attempts - 1:
                raise last
        except (OSError, TimeoutError) as exc:
            last = ApiError(None, {"error": type(exc).__name__, "message": str(exc)[:300]},
                             time.monotonic() - begun)
            if attempt == max_attempts - 1:
                raise last
        time.sleep(_backoff(attempt))
    raise typing.cast(ApiError, last)


def _iter_sse_events(data: bytes) -> typing.Iterator[dict]:
    """Parse SSE bytes into event dicts. Defensive: skips comments,
    blank noise, and a trailing [DONE] marker."""
    buf: list[str] = []

    def flush() -> dict | None:
        payload = "".join(part[5:] if part.startswith("data:") else ""
                          for part in buf).strip()
        del buf[:]
        if not payload or payload == "[DONE]":
            return None
        try:
            event = json.loads(payload)
        except ValueError:
            return {"type": "anastylos.unparsable",
                    "raw_head": payload[:300]}
        return event if isinstance(event, dict) else {"type": "anastylos.non_object"}

    text = data.decode("utf-8", "replace")
    for line in text.splitlines():
        if line == "":
            event = flush()
            if event is not None:
                yield event
        elif line.startswith(":"):
            continue
        else:
            buf.append(line + "\n")
    event = flush()
    if event is not None:
        yield event


def post_responses_stream(
    payload: dict, key: str, timeout_s: float = 600.0,
    max_attempts: int = 3, max_events: int = 200000,
) -> tuple[list[dict], dict]:
    """POST /v1/responses with stream=true. Returns (events, meta).

    Retries only pre-body failures and retryable HTTP statuses; a
    started stream is never retried (billing already incurred).
    """
    payload = {**payload, "stream": True}
    body = json.dumps(payload).encode("utf-8")
    last: ApiError | None = None
    for attempt in range(max_attempts):
        request = urllib.request.Request(
            BASE_URL + "/responses", data=body, method="POST",
            headers={"Authorization": "Bearer " + key,
                     "Content-Type": _JSON, "Accept": "text/event-stream"})
        begun = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail_raw = exc.read().decode("utf-8", "replace")[:2000]
            try:
                detail: Any = json.loads(detail_raw)
            except ValueError:
                detail = {"raw": detail_raw}
            last = ApiError(exc.code, detail, time.monotonic() - begun)
            if exc.code not in _RETRY_STATUS or attempt == max_attempts - 1:
                raise last
            time.sleep(_backoff(attempt))
            continue
        events = list(_iter_sse_events(raw))
        if len(events) > max_events:
            raise ApiError(None, {"error": "event flood",
                                  "events": len(events)}, time.monotonic() - begun)
        terminal = next((event.get("type") for event in reversed(events)
                         if isinstance(event, dict) and event.get("type",
                            "").startswith("response.") and
                         event.get("type") in ("response.completed",
                            "response.failed", "response.incomplete")), None)
        meta = {"http": 200, "latency_s": round(time.monotonic() - begun, 2),
                "events": len(events), "terminal": terminal,
                "bytes": len(raw)}
        return events, meta
    raise typing.cast(ApiError, last)


def post_responses_once(payload: dict, key: str,
                        timeout_s: float = 600.0) -> tuple[dict, dict]:
    payload = {**payload, "stream": False}
    status, data, latency = _post_json(BASE_URL + "/responses", key, payload,
                                       timeout_s)
    return data, {"http": status, "latency_s": round(latency, 2)}
