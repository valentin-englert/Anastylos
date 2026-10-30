"""Shared test fixtures: temp copies of the FULL-01 v21 fixtures, crash injection, a fake Proposer."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

LIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIVE))

from coordinator import Coordinator  # noqa: E402
from journal import utc_now  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PHASE_WORDS = json.loads((FIXTURES / "phase-words.json").read_text(encoding="utf-8"))["phases"]
OPERATOR = "test operator"


class SimulatedCrash(BaseException):
    """Process death at an injection point (BaseException: no `except Exception` may swallow it)."""


def workspace(tmp: Path) -> dict:
    runs = []
    for tag in ("P1", "P2", "P3"):
        run = tmp / "runs" / tag
        run.mkdir(parents=True)
        for name in ("questions.json", "parts.json"):
            shutil.copyfile(FIXTURES / tag / name, run / name)
        runs.append(run)
    memory = tmp / "memory"  # folder name = memory session name, as in FULL-01
    memory.mkdir()
    shutil.copyfile(FIXTURES / "audit" / "memory.before-answers.jsonl", memory / "memory.jsonl")
    return {"run_dir": tmp / "run", "memory_dir": memory, "runs": runs}


def open_coordinator(ws: dict, **kwargs) -> Coordinator:
    # the historical FULL-01 regression runs in German, as recorded (seed/h3r0.py); English has its own tests
    return Coordinator(ws["run_dir"], ws["memory_dir"], ws["runs"], PHASE_WORDS, OPERATOR, **{"language": "de", **kwargs}).open()


def crash_at(point: str, nth: int = 1):
    seen = {"n": 0}

    def fault(at: str) -> None:
        if at == point:
            seen["n"] += 1
            if seen["n"] == nth:
                raise SimulatedCrash(point)
    return fault


def die(coordinator: Coordinator) -> None:
    """What the OS does when the coordinator process dies: its lock handle closes."""
    coordinator.lock.drop_handle_like_a_crash()


def typed(op_key: str, text: str) -> dict:
    return {"schema": "anastylos-live-operator-input@1", "op_key": op_key, "source": "typed", "text": text,
            "choice": None, "received_at": utc_now(), "transcript": None}


def spoken(op_key: str, text: str) -> dict:
    return {**typed(op_key, text), "source": "speech_transcript",
            "transcript": {"provider": "test", "model": "none", "language": "de"}}


def screen(op_key: str, choice: str) -> dict:
    return {**typed(op_key, ""), "source": "screen_choice", "text": None, "choice": choice}


def memory_lines(ws: dict) -> list[dict]:
    text = (ws["memory_dir"] / "memory.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def content(event: dict) -> dict:
    """An event without what differs between two equal writes (id, time, the id it points at)."""
    return {k: v for k, v in event.items() if k not in ("id", "timestamp", "target_id")}


def provider_result(task: str, request_id: str, status: str = "ok", output=None) -> dict:
    return {"schema": "anastylos-live-provider-result@1", "request_id": request_id, "task": task, "status": status,
            "output": output if status == "ok" else None, "model": "fake", "reasoning_effort_requested": None,
            "reasoning_effort_returned": None, "latency_s": 0.0, "usage": None, "request_sha256": "0" * 64,
            "cache_hit": False, "error": None}


class FakeProposer:
    """Follows the Proposer protocol (CONTRACT §3.1); answers come from a per-task function."""

    def __init__(self, **by_task):
        self.by_task = by_task
        self.calls = []

    def propose(self, task: str, payload: dict, request_id: str) -> dict:
        self.calls.append((task, payload, request_id))
        make = self.by_task.get(task)
        if make is None:
            return provider_result(task, request_id, status="disabled")
        return make(payload, request_id)


def in_process_memory_call(session_dir: Path, argv: list) -> dict:
    """The same ``anastylos_memory.cli.main`` that memory.py runs, without a subprocess (speed only).
    Used where a test makes hundreds of calls; the subprocess path is covered by the other tests."""
    import contextlib
    import io

    from anastylos_memory import cli
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(["--session-dir", str(session_dir), *map(str, argv)])
    if code:
        raise RuntimeError(f"memory cli exit {code}")
    return json.loads(out.getvalue())


def without_session_id(events: list[dict], live_session_id: str) -> list[dict]:
    return [json.loads(json.dumps(content(e), ensure_ascii=False).replace(live_session_id, "LIVE")) for e in events]
