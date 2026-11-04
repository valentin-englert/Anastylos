"""Replay clips are made before anyone asks for them: one low-priority background worker, one clip at a time.

``kick(root, session)`` queues a scan of a session; the scan measures the storage cells of its videos if that has not been
done (``replay.measure_cells``) and queues a clip for every part whose recorded moment is known and not yet prepared.
It is called when a dialogue answer reaches memory and when the dialogue ends (``dialogue_flow``), when the explosion
page opens a session (``POST /api/focus``), and by the server for the video job. ``open`` is the click on a part:
a ready clip answers at once; otherwise the clip moves to the front of the queue and the answer says ``preparing`` so the
page can show "Almost ready" and poll ``/api/replay`` until it is ready. Nothing blocks a voice turn: a kick only queues.

The extraction itself is ``replay.replay_prepare`` (a subprocess per clip, run at below-normal priority). A clip folder
appears only when complete (``replay_worker`` renames ``<key>.partial``), so a restart leaves nothing half-served; a leftover
``.partial`` folder is replaced by the next run. Without ``enable`` (tests, command-line use) nothing runs in the
background and ``open`` prepares synchronously.
"""

from __future__ import annotations

import collections
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import adapter
import assembly_help
import replay
from adapter import AdapterError

BELOW_NORMAL = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
_runtimes: dict[str, "_Runtime"] = {}
_guard = threading.Lock()


def low_priority_run(command, **kwargs):
    """``subprocess.run`` for a worker that must not compete with the voice turn and the page."""
    if sys.platform == "win32":
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | BELOW_NORMAL
    elif hasattr(os, "nice"):
        kwargs["preexec_fn"] = lambda: os.nice(10)
    return subprocess.run(command, **kwargs)


class _Runtime:
    def __init__(self, root: Path, run):
        self.root, self.run = Path(root), run
        self.cond = threading.Condition()
        self.jobs: collections.deque = collections.deque()
        self.pending: set = set()
        self.current: tuple | None = None
        self.state: dict[tuple, dict] = {}  # (session, part) -> {"state": queued | preparing | failed, "error"}
        self.thread = threading.Thread(target=self._loop, daemon=True, name="replay-prep")
        self.thread.start()

    # -- queue ---------------------------------------------------------------

    def put(self, job: tuple, front: bool = False) -> None:
        with self.cond:
            if job == self.current:
                return
            if job in self.pending:  # already waiting: a click only moves it up
                if front:
                    self.jobs.remove(job)
                    self.jobs.appendleft(job)
                return
            self.pending.add(job)
            (self.jobs.appendleft if front else self.jobs.append)(job)
            if job[0] == "clip":
                self.state[(job[1], job[2])] = {"state": "queued", "error": None}
            self.cond.notify()

    def idle(self) -> bool:
        with self.cond:
            return not self.jobs and self.current is None

    def _loop(self) -> None:
        while True:
            with self.cond:
                while not self.jobs:
                    self.cond.wait()
                job = self.jobs.popleft()
                self.pending.discard(job)
                self.current = job
                if job[0] == "clip":
                    self.state[(job[1], job[2])] = {"state": "preparing", "error": None}
            try:
                self._run(job)
            except Exception as exc:  # noqa: BLE001 - one failed clip must not stop the queue
                if job[0] == "clip":
                    self.state[(job[1], job[2])] = {"state": "failed", "error": str(exc)[:300]}
            else:
                if job[0] == "clip":
                    self.state.pop((job[1], job[2]), None)
            finally:
                with self.cond:
                    self.current = None
                    self.cond.notify_all()

    def _run(self, job: tuple) -> None:
        if job[0] == "scan":
            self._scan(job[1])
        else:
            replay.replay_prepare(self.root, job[1], job[2], run=self.run)

    def _scan(self, session_id: str) -> None:
        try:
            replay.measure_cells(self.root, session_id, run=self.run)
        except AdapterError:
            return  # a clip made without the measurement could show the wrong time; a click will try again and say why
        state = adapter._load_state(self.root, session_id)
        memory = assembly_help.read_memory(state)
        for part in memory["parts"]:
            if replay._moment(state, memory, part)["status"] == "not_prepared":
                self.put(("clip", session_id, part["id"]))


# ------------------------------------------------------------------ public

def enable(root: Path, run=low_priority_run) -> None:
    """Start the background worker for this workspace (the server does this once)."""
    with _guard:
        _runtimes.setdefault(str(root), _Runtime(root, run))


def disable(root: Path) -> None:
    """Forget the worker of this workspace (tests); a running clip finishes, the thread idles."""
    with _guard:
        _runtimes.pop(str(root), None)


def _runtime(root: Path) -> "_Runtime | None":
    return _runtimes.get(str(root))


def kick(root: Path, session_id: str) -> None:
    """Queue a scan of the session; returns at once and never raises."""
    runtime = _runtime(root)
    if runtime is not None:
        runtime.put(("scan", session_id))


def wait_idle(root: Path, timeout_s: float = 30.0) -> bool:
    """Block until the queue is empty (tests)."""
    runtime = _runtime(root)
    deadline = time.monotonic() + timeout_s
    while runtime is not None and not runtime.idle():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


def prep_state(root: Path, session_id: str, moment: dict) -> dict:
    """``{"state": ready | queued | preparing | failed | none, "error"}`` for one moment of ``replay_moments``."""
    if moment["status"] in ("ready", "unregistered"):
        return {"state": "ready", "error": None}
    runtime = _runtime(root)
    entry = runtime.state.get((session_id, moment["part_id"])) if runtime else None
    return entry or {"state": "none", "error": None}


def annotate(root: Path, session_id: str, moments: dict) -> dict:
    """``replay_moments`` result with ``prep`` on every moment (how far its clip is)."""
    for moment in moments["moments"]:
        moment["prep"] = prep_state(root, session_id, moment)
    return moments


def open_clip(root: Path, session_id: str, part_id: str) -> dict:
    """A click on a part: the player data when the clip is ready, else the moment with ``prep`` (the clip is moved up)."""
    runtime = _runtime(root)
    if runtime is None:
        return replay.replay_prepare(root, session_id, part_id)
    moment = replay.replay_moments(Path(root), session_id, [part_id])["moments"][0]
    if moment["status"] in ("not_prepared",):
        runtime.put(("clip", session_id, part_id), front=True)
        return {**moment, "prep": prep_state(root, session_id, moment)}
    return {**replay.replay_prepare(root, session_id, part_id), "prep": {"state": "ready", "error": None}}


def measure_job(root: Path, folder: str) -> dict:
    """The video job's cell measurement (same worker, normal priority is fine: nobody waits on the voice turn here)."""
    return replay.measure_cells(root, folder, run=low_priority_run)
