"""Meta Model API proposer (CONTRACT §3.1, §4). The only network module under `tools/live/`.

Stdlib `urllib` only. The key is read from `os.environ["MODEL_API_KEY"]` per call, held only for the
Authorization header, and never printed, logged, cached or written. Per run dir it keeps:
`cache/<request_sha256>.json` (recorded fixtures; dedup) and `traces.jsonl` (sanitized traces, also the
ledger of HTTP attempts counted against the caller's call cap).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Mapping, Protocol

from provider_core import (api_error_from_body, build_request, encode_request, interpret_response, make_error,
                           make_fixture, make_result, make_trace, read_fixture, sha256_hex)

ENDPOINT = "https://api.meta.ai/v1/responses"
KEY_ENV = "MODEL_API_KEY"
TIMEOUT_S = 120.0
MAX_RETRIES = 2
BACKOFF_S = (2.0, 5.0)
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class TransportTimeout(Exception):
    pass


class TransportFailure(Exception):
    """No HTTP response at all (DNS, refused connection, TLS). Message carries no request data."""


class Transport(Protocol):
    def post(self, url: str, headers: dict, body: bytes, timeout_s: float) -> tuple[int, bytes]: ...


class UrllibTransport:
    def post(self, url: str, headers: dict, body: bytes, timeout_s: float) -> tuple[int, bytes]:
        request = urllib.request.Request(url, data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                return response.status, response.read(MAX_RESPONSE_BYTES)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(MAX_RESPONSE_BYTES)
        except TimeoutError as exc:
            raise TransportTimeout(type(exc).__name__) from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise TransportTimeout(type(exc.reason).__name__) from None
            raise TransportFailure(type(exc.reason).__name__ if exc.reason else "URLError") from None
        except OSError as exc:
            raise TransportFailure(type(exc).__name__) from None


def _retryable(status: int) -> bool:
    return status in (408, 429) or 500 <= status <= 599


class MetaProposer:
    def __init__(self, run_dir: Path, max_calls: int, *, transport: Transport | None = None,
                 environ: Mapping[str, str] | None = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        if max_calls < 0:
            raise ValueError("max_calls must be >= 0")
        self._run_dir = Path(run_dir)
        self._cache_dir = self._run_dir / "cache"
        self._trace_path = self._run_dir / "traces.jsonl"
        self._max_calls = max_calls
        self._transport = transport or UrllibTransport()
        self._environ = os.environ if environ is None else environ
        self._sleep = sleep
        self._clock = clock
        self._calls_used = self._attempts_already_traced()

    @property
    def calls_used(self) -> int:
        return self._calls_used

    def _attempts_already_traced(self) -> int:
        if not self._trace_path.is_file():
            return 0
        with open(self._trace_path, encoding="utf-8") as handle:
            return sum(json.loads(line)["attempts"] for line in handle if line.strip())

    def propose(self, task: str, payload: dict, request_id: str) -> dict:
        body = build_request(task, payload)
        raw = encode_request(body)
        digest = sha256_hex(raw)
        result, attempts, http_status = self._propose(task, body, raw, digest, request_id)
        self._append_trace(make_trace(result, attempts=attempts, http_status=http_status))
        return result

    def _propose(self, task, body, raw, digest, request_id) -> tuple[dict, int, int | None]:
        common = {"request_id": request_id, "task": task, "request_sha256": digest}
        cached = read_fixture(self._cache_dir / f"{digest}.json")
        if cached is not None:
            seen = interpret_response(task, cached["response"])
            return make_result(**common, cache_hit=True, latency_s=cached["latency_s"], **seen), 0, 200

        key = self._environ.get(KEY_ENV, "").strip()
        if not key:
            return make_result(**common, status="disabled", error=make_error("missing_key", KEY_ENV)), 0, None

        headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json",
                   "Accept": "application/json"}
        secrets = (key,)
        attempts, http_status, last_error = 0, None, None
        begun = self._clock()
        while True:
            if self._calls_used >= self._max_calls:
                error = last_error or make_error("call_cap", f"{self._max_calls} calls used")
                return make_result(**common, status="budget_exhausted", latency_s=self._elapsed(begun, attempts),
                                   error=error), attempts, http_status
            self._calls_used += 1
            attempts += 1
            try:
                http_status, response_raw = self._transport.post(ENDPOINT, headers, raw, TIMEOUT_S)
            except TransportTimeout as exc:
                return make_result(**common, status="timeout", latency_s=self._clock() - begun,
                                   error=make_error("timeout", str(exc), secrets=secrets)), attempts, None
            except TransportFailure as exc:
                return make_result(**common, status="provider_error", latency_s=self._clock() - begun,
                                   error=make_error("transport", str(exc), secrets=secrets)), attempts, None
            if http_status == 200:
                break
            last_error = api_error_from_body(http_status, response_raw, secrets)
            if not _retryable(http_status) or attempts > MAX_RETRIES:
                return make_result(**common, status="provider_error", latency_s=self._clock() - begun,
                                   error=last_error), attempts, http_status
            self._sleep(BACKOFF_S[attempts - 1])

        latency = self._clock() - begun
        try:
            response = json.loads(response_raw.decode("utf-8"))
        except ValueError as exc:
            return make_result(**common, status="provider_error", latency_s=latency,
                               error=make_error("unparsable_response", str(exc), 200, secrets)), attempts, 200
        self._write_fixture(make_fixture(task=task, body=body, request_sha256=digest, response=response,
                                         latency_s=latency))
        seen = interpret_response(task, response)
        return make_result(**common, latency_s=latency, **seen), attempts, 200

    def _elapsed(self, begun: float, attempts: int) -> float | None:
        return self._clock() - begun if attempts else None

    def _write_fixture(self, fixture: dict) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        target = self._cache_dir / f"{fixture['request_sha256']}.json"
        partial = target.with_suffix(".partial")
        partial.write_text(json.dumps(fixture, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
        os.replace(partial, target)

    def _append_trace(self, trace: dict) -> None:
        self._run_dir.mkdir(parents=True, exist_ok=True)
        with open(self._trace_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(trace, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
