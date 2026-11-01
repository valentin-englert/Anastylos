"""Start and watch drawing jobs without importing image or network code (safe for the offline session UI).

A job is a separate worker process (drawing_job.py run). Its progress.json carries pid, heartbeat and stage start;
``status`` turns a dead worker, a stale heartbeat or an overrun stage into a persisted ``failed`` state.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

WORKER = Path(__file__).resolve().parent / "drawing_job.py"
STAGE_TIMEOUT_S = {"sample_frames": 900, "select_candidates": 30, "rectify_candidates": 180, "muse_choose_frame": 360,
                   "muse_box": 360, "sam": 300, "checks_and_svg": 60}
STAGES = list(STAGE_TIMEOUT_S)
HEARTBEAT_S, HEARTBEAT_STALE_S = 2.0, 30.0


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


def status(job: Path) -> dict:
    """Read progress; a dead worker, a stale heartbeat or an overrun stage becomes ``failed`` (and is persisted)."""
    path = job / "progress.json"
    if not path.exists():
        return {"state": "unknown", "error": "no progress file"}
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("state") != "running":
        return doc
    now, reason = time.time(), None
    if not process_alive(int(doc["pid"])):
        reason = "worker process ended without a final state"
    elif now - doc.get("heartbeat_at", 0) > HEARTBEAT_STALE_S:
        reason = f"no heartbeat for {now - doc['heartbeat_at']:.0f} s"
    elif doc.get("stage") in STAGE_TIMEOUT_S and now - (doc.get("stage_started_at") or now) > STAGE_TIMEOUT_S[doc["stage"]]:
        reason = f"stage {doc['stage']} exceeded {STAGE_TIMEOUT_S[doc['stage']]} s"
        subprocess.run(["taskkill", "/F", "/PID", str(doc["pid"])] if os.name == "nt" else ["kill", "-9", str(doc["pid"])],
                       capture_output=True)
    if reason:
        doc.update(state="failed", error=reason, detected_at=now)
        path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return doc


def start(spec_path: Path, job: Path) -> dict:
    job.mkdir(parents=True, exist_ok=True)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    with open(job / "job.log", "w") as log:
        process = subprocess.Popen([sys.executable, "-B", str(WORKER), "run", "--spec", str(spec_path), "--job", str(job)],
                                   stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
    # A worker that dies before writing its own progress must still end visibly: status() checks this pid.
    (job / "progress.json").write_text(json.dumps({"state": "running", "stage": "starting", "pid": process.pid, "started_at": time.time(),
                                                   "heartbeat_at": time.time(), "stage_started_at": time.time(), "done": 0,
                                                   "total": len(STAGES), "stage_s": {}}), encoding="utf-8")
    return {"job": str(job), "state": "started", "pid": process.pid}
