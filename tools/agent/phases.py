"""One disassembly phase: hash the video, run the frozen session runner on it, record the outcome.

`tools/session/session.py` is an E3-B frozen file: it is only ever called as a subprocess, never edited or
imported. It cannot resume a half-finished run (B10), so a failed phase keeps its folder for inspection and the
operator records the phase again under the next tag.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from state import SessionError, SessionState

REPO = Path(__file__).resolve().parents[2]
SESSION_RUNNER = REPO / "tools" / "session" / "session.py"
CUSTODY_DIR = REPO / "custody-ledger"
CUSTODY_CONFIG = "custody-config.v15.json"


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[list[str]], Completed]


def vision_python(explicit: Path | None = None) -> Path:
    """The pinned OpenCV environment; the custody pipeline does not run on a bare interpreter."""
    venv = Path(explicit) if explicit else REPO / ".venv" / "Scripts" / "python.exe"
    if not venv.is_file():
        raise SessionError(f"{venv} is missing; create it per calibration/README.md or pass --python")
    return venv


def subprocess_runner(command: list[str]) -> Completed:
    done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return Completed(done.returncode, done.stdout, done.stderr)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def session_command(python: Path, video: Path, tag: str, label: str, memory_dir: Path, run_dir: Path) -> list[str]:
    return [str(python), str(SESSION_RUNNER), "--video", str(video), "--phase", tag, "--phase-label", label,
            "--custody-dir", str(CUSTODY_DIR), "--config", CUSTODY_CONFIG, "--memory", str(memory_dir),
            "--out", str(run_dir)]


def run_phase(state: SessionState, tag: str, label: str, video: Path, runner: Runner, python: Path) -> dict:
    """Run one phase and return its counts; a runner failure is recorded on the phase and raised."""
    video = Path(video).resolve()
    if not video.is_file():
        raise SessionError(f"video {video} not found")
    state.begin_phase(tag, label, video, sha256_file(video))
    done = runner(session_command(python, video, tag, label, state.memory_dir, state.run_dir(tag)))
    report_path = state.run_dir(tag) / "parts.json"
    if done.returncode != 0 or not report_path.is_file():
        error = (done.stderr or done.stdout or "session runner wrote no parts.json").strip()
        state.fail_phase(tag, error)
        raise SessionError(f"phase {tag} failed; its folder is kept: {state.run_dir(tag)}\n{error[-800:]}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("video_sha256") not in (None, state.phase(tag)["video_sha256"]):
        state.fail_phase(tag, "the run's video hash differs from the recorded video")
        raise SessionError(f"phase {tag}: the session runner processed a different video")
    state.finish_phase(tag, report["counts"])
    return report["counts"]
