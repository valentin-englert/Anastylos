"""Start and watch drawing jobs without importing image or network code (safe for the offline session UI).

A job is a separate worker process (drawing_job.py run). Its progress.json carries pid, heartbeat and stage start;
``status`` turns a dead worker, a stale heartbeat or an overrun stage into a persisted ``failed`` state.

Concurrency contract (several processes touch one progress.json: the UI poller, ``start`` and the worker):
- every write is atomic (temp file + os.replace) and happens under an exclusive per-job lock (``progress.lock``);
- each run has a ``generation`` and a ``token`` written by ``start`` BEFORE the worker is spawned; the worker adopts
  that state, so its first write cannot clobber anything and ``start`` cannot clobber the worker;
- a terminal state (done, failed, paused) is never overwritten, only replaced by a new generation (``start``);
  a poller re-reads under the lock and writes ``failed`` only if the job is still the same running generation;
- a worker whose generation or state no longer matches the file is stale: its write is refused (``Superseded``).
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

WORKER = Path(__file__).resolve().parent / "drawing_job.py"
STAGE_TIMEOUT_S = {"sample_frames": 900, "select_candidates": 30, "rectify_candidates": 180, "muse_choose_frame": 360,
                   "muse_box": 360, "sam": 300, "checks_and_svg": 60}
STAGES = list(STAGE_TIMEOUT_S)
HEARTBEAT_S, HEARTBEAT_STALE_S = 2.0, 30.0
SPAWN_GRACE_S = 60.0  # a started worker must claim its generation within this time
DEAD_WAIT_S = 10.0
LOCK_TIMEOUT_S = 5.0
TERMINAL_STATES = ("done", "failed", "paused")
TOKEN_ENV = "ANASTYLOS_JOB_TOKEN"


class JobError(Exception):
    """A job could not be started safely (the previous worker would not die, the job is still running)."""


class Superseded(Exception):
    """A worker tried to write progress for a run that is no longer the current running generation."""


class LockTimeout(Exception):
    pass


# ---------------------------------------------------------------- seams (replaced by fakes in tests)

def _now() -> float:
    return time.time()


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def process_alive(pid: int) -> bool:
    if os.name == "nt":
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def kill_process(pid: int) -> None:
    """Kill a worker and its children (a venv python.exe on Windows is a launcher in front of the real interpreter)."""
    command = ["taskkill", "/F", "/T", "/PID", str(pid)] if os.name == "nt" else ["kill", "-9", str(pid)]
    subprocess.run(command, capture_output=True)


def _spawn(spec: Path, job: Path, token: str) -> int:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    env = {**os.environ, TOKEN_ENV: token}
    with open(job / "job.log", "a") as log:
        process = subprocess.Popen([sys.executable, "-B", str(WORKER), "run", "--spec", str(spec), "--job", str(job)],
                                   stdout=log, stderr=subprocess.STDOUT, creationflags=flags, env=env)
    return process.pid


# ---------------------------------------------------------------- atomic file access under a per-job lock

def atomic_write_json(path: Path, doc: dict) -> None:
    """Write ``doc`` so that a reader sees the old or the new file, never a torn one."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
        for attempt in range(20):  # Windows refuses to replace a file another process has open for reading right now
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                _sleep(0.025)
    finally:
        tmp.unlink(missing_ok=True)


def _try_lock(handle) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def locked(job: Path, timeout_s: float = LOCK_TIMEOUT_S):
    """Exclusive lock for read-modify-write of one job's progress.json; the OS drops it if its holder dies."""
    deadline = time.monotonic() + timeout_s
    with open(job / "progress.lock", "a+b") as handle:
        while True:
            try:
                _try_lock(handle)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise LockTimeout(f"progress of {job.name} is locked by another process")
                time.sleep(0.02)
        try:
            yield
        finally:
            _unlock(handle)


def read_progress(job: Path) -> dict | None:
    path = job / "progress.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- judging a running job

def _failure_reason(doc: dict, now: float) -> tuple[str, bool] | None:
    """Why a job in state running must be declared failed, and whether its worker still has to be killed."""
    pid = doc.get("pid")
    if pid is None:  # started, worker not yet spawned or not yet reporting
        started = doc.get("started_at", now)
        return ("worker never reported a pid", False) if now - started > SPAWN_GRACE_S else None
    if not process_alive(int(pid)):
        return "worker process ended without a final state", False
    if now - doc.get("heartbeat_at", 0) > HEARTBEAT_STALE_S:
        return f"no heartbeat for {now - doc['heartbeat_at']:.0f} s", False
    stage = doc.get("stage")
    if stage in STAGE_TIMEOUT_S and now - (doc.get("stage_started_at") or now) > STAGE_TIMEOUT_S[stage]:
        return f"stage {stage} exceeded {STAGE_TIMEOUT_S[stage]} s", True
    return None


def _same_run(a: dict, b: dict) -> bool:
    return a.get("token") == b.get("token") and a.get("generation") == b.get("generation")


def status(job: Path) -> dict:
    """Read progress; a dead worker, a stale heartbeat or an overrun stage becomes ``failed`` (and is persisted).

    The verdict is re-judged on a fresh read under the job lock, so a worker that finished (or was restarted) between
    the first read and the write is never overwritten.
    """
    try:
        doc = read_progress(job)
    except (OSError, ValueError) as exc:
        return {"state": "unknown", "error": f"progress file unreadable: {exc}"}
    if doc is None:
        return {"state": "unknown", "error": "no progress file"}
    if doc.get("state") != "running" or _failure_reason(doc, _now()) is None:
        return doc
    try:
        with locked(job):
            fresh = read_progress(job)
            if fresh is None or fresh.get("state") != "running" or not _same_run(fresh, doc):
                return fresh if fresh is not None else doc
            verdict = _failure_reason(fresh, _now())
            if verdict is None:
                return fresh
            reason, kill = verdict
            fresh.update(state="failed", error=reason, detected_at=_now(), detected_by="status_poll")
            atomic_write_json(job / "progress.json", fresh)
    except LockTimeout:
        return doc  # somebody else is writing right now; the next poll decides
    if kill and fresh.get("pid") is not None and int(fresh["pid"]) != os.getpid():
        kill_process(int(fresh["pid"]))
    return fresh


# ---------------------------------------------------------------- the owning worker

def claim_run(job: Path, token: str | None = None) -> dict:
    """The worker adopts the run ``start`` prepared (``token``), or opens a new generation when run by hand."""
    with locked(job):
        disk = read_progress(job)
        if token is not None:
            if disk is None or disk.get("token") != token or disk.get("state") != "running":
                raise Superseded("this worker's run is no longer the current running generation")
        else:
            if disk is not None and disk.get("state") == "running" and _failure_reason(disk, _now()) is None \
                    and disk.get("pid") not in (None, os.getpid()):
                raise JobError(f"job {job.name} is already running (pid {disk.get('pid')})")
            disk = _initial_doc(job, int((disk or {}).get("generation", 0)) + 1, uuid.uuid4().hex)
        disk.update(pid=os.getpid(), heartbeat_at=_now())
        atomic_write_json(job / "progress.json", disk)
        return disk


def write_if_owner(job: Path, doc: dict) -> None:
    """Persist ``doc`` for its own run only; refuse when the file moved on (stale generation, or already terminal)."""
    with locked(job):
        disk = read_progress(job)
        if disk is None or not _same_run(disk, doc) or disk.get("state") != "running":
            raise Superseded(f"progress of {job.name} belongs to another run or was already ended "
                             f"({(disk or {}).get('state')}, generation {(disk or {}).get('generation')})")
        atomic_write_json(job / "progress.json", doc)


# ---------------------------------------------------------------- starting / resuming

def _initial_doc(job: Path, generation: int, token: str) -> dict:
    now = _now()
    return {"state": "running", "stage": "starting", "pid": None, "generation": generation, "token": token, "started_at": now,
            "heartbeat_at": now, "stage_started_at": now, "done": 0, "total": len(STAGES), "stage_s": {}, "timeouts_s": STAGE_TIMEOUT_S}


def _ensure_dead(previous: dict) -> None:
    pids = {int(p) for p in (previous.get("pid"), previous.get("spawn_pid")) if p is not None} - {os.getpid()}
    for pid in pids:
        if process_alive(pid):
            kill_process(pid)
    deadline = _now() + DEAD_WAIT_S
    while any(process_alive(pid) for pid in pids):
        if _now() > deadline:
            raise JobError(f"previous worker of this job (pid {sorted(pids)}) is still alive; not starting a second one")
        _sleep(0.1)


def start(spec_path: Path, job: Path) -> dict:
    """Start a worker; started again on the same job directory it resumes from that job's checkpoints.

    The previous worker of that job is verified dead first and the generation is bumped. The new run's state is on disk
    before the worker exists. A job that is still running (live worker, fresh heartbeat) is not started a second time.
    """
    job.mkdir(parents=True, exist_ok=True)
    kept = job / "spec.json"
    if spec_path.resolve() != kept.resolve():
        kept.write_text(spec_path.read_text(encoding="utf-8"), encoding="utf-8")
    with locked(job):
        previous = read_progress(job)
        if previous is not None:
            if previous.get("state") == "running" and _failure_reason(previous, _now()) is None:
                return {"job": str(job), "state": "already_running", "pid": previous.get("pid")}
            _ensure_dead(previous)
        doc = _initial_doc(job, int((previous or {}).get("generation", 0)) + 1, uuid.uuid4().hex)
        atomic_write_json(job / "progress.json", doc)
        try:
            pid = _spawn(kept, job, doc["token"])
        except Exception as exc:
            doc.update(state="failed", error=f"worker could not be spawned: {type(exc).__name__}: {exc}", detected_by="start")
            atomic_write_json(job / "progress.json", doc)
            raise
        doc.update(pid=pid, spawn_pid=pid)  # worker adopts this under the same lock and replaces pid with its own
        atomic_write_json(job / "progress.json", doc)
    return {"job": str(job), "state": "started", "pid": pid, "generation": doc["generation"]}
