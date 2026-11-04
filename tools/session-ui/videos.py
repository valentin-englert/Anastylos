"""Phone videos on the session page: list what the receiver holds, and process one into a new session folder.

The receiver stores finished uploads as ``<receiver data dir>/videos/<guided session>/<video id>/{meta.json,video.mp4}``
(see capture-companion/receiver/README.md). This module only reads that folder; it never writes into it. The data dir
comes from ``<root>/capture-settings.json`` (``receiver_data_dir``), the same one-time setup the capture loop uses.

``Process`` creates a new session folder in the workspace and runs the existing, frozen ``tools/session/session.py``
through ``tools/agent/phases.py`` on the stored file, after re-checking its SHA-256 against the receiver's record. The
run is a background thread; its state is a small file the page polls:

    <root>/video-jobs/<video_id>.json   state (queued, running, done, failed), session folder, stage, counts, error

A failure keeps the pipeline's own reason verbatim (a handheld or new-object video the pipeline rejects shows up here
exactly as the runner reported it); nothing is worked around.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Callable

import ui_meta
from adapter import AdapterError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
import phases  # noqa: E402  (tools/agent: runs the frozen session runner as a subprocess)
from state import SessionError, SessionState  # noqa: E402

SETTINGS = "capture-settings.json"
JOBS = "video-jobs"
VIDEO_ID = re.compile(r"^[0-9a-f]{32}$")
PHASE_TAG = "P1"
PHASE_LABEL = "recorded on the phone"
_guard = threading.Lock()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _videos_dir(root: Path) -> Path | None:
    try:
        settings = json.loads((root / SETTINGS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    data_dir = settings.get("receiver_data_dir")
    return Path(data_dir) / "videos" if isinstance(data_dir, str) and data_dir else None


REPLACE_ATTEMPTS = 20


def _write_atomic(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(document, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(partial, path)
            return
        except PermissionError:  # Windows refuses the replace while a poll has the job file open for a moment
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(0.05)


def _job_path(root: Path, video_id: str) -> Path:
    return root / JOBS / f"{video_id}.json"


def _job(root: Path, video_id: str) -> dict | None:
    """The job file, read through a replace in progress (Windows briefly refuses a read while the file is swapped)."""
    for attempt in range(5):
        try:
            return json.loads(_job_path(root, video_id).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            time.sleep(0.02 * (attempt + 1))
    return None


def _stored(root: Path) -> list[tuple[dict, Path]]:
    base = _videos_dir(root)
    found = []
    if base is None or not base.is_dir():
        return found
    for meta_path in sorted(base.glob("*/*/meta.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if VIDEO_ID.match(str(meta.get("video_id", ""))):
            found.append((meta, meta_path.parent))
    return found


def _view(root: Path, meta: dict) -> dict:
    job = _job(root, meta["video_id"])
    return {"video": meta["video_id"], "guided_session": meta.get("session_id"), "size": meta.get("size"),
            "status": meta.get("status"), "received_chunks": None, "declared_at": meta.get("declared_at"),
            "completed_at": meta.get("completed_at"),
            "job": None if job is None else {k: job.get(k) for k in
                                             ("state", "session", "stage", "error", "counts", "started_at", "finished_at", "parts", "parts_not_drawn", "parts_error", "cells", "cells_error")}}


def list_videos(root: Path) -> dict:
    """Everything the receiver holds, newest first, with the processing state of each."""
    configured = _videos_dir(root) is not None
    videos = [_view(root, meta) for meta, _dir in _stored(root)]
    videos.sort(key=lambda v: v.get("declared_at") or "", reverse=True)
    return {"configured": configured, "videos": videos}


def _find(root: Path, video_id: str) -> tuple[dict, Path]:
    if not VIDEO_ID.match(video_id or ""):
        raise AdapterError(f"Unknown video {video_id!r}.", 404)
    for meta, directory in _stored(root):
        if meta["video_id"] == video_id:
            return meta, directory
    raise AdapterError(f"Unknown video {video_id!r}.", 404)


def start_processing(root: Path, video_id: str, runner: Callable | None = None, python: Path | None = None,
                     background: bool = True, find_parts: Callable | None = None, measure_cells: Callable | None = None) -> dict:
    """Start the pipeline on one complete video; a double click returns the job that is already there."""
    meta, directory = _find(root, video_id)
    if meta.get("status") != "complete":
        raise AdapterError("This video has not finished uploading.", 409)
    video = directory / "video.mp4"
    if not video.is_file():
        raise AdapterError("The stored video file is missing.", 404)
    with _guard:
        existing = _job(root, video_id)
        if existing and existing.get("state") in ("queued", "running", "done"):
            return {**existing, "already_started": True}
        if phases.sha256_file(video) != meta.get("sha256"):
            raise AdapterError("The stored video no longer matches its SHA-256; it was not processed.", 409)
        folder = f"video-{time.strftime('%Y%m%d-%H%M%S')}-{video_id[:8]}"
        job = {"schema": "anastylos-video-job@1", "video": video_id, "state": "queued", "session": folder,
               "stage": "starting", "error": None, "name": ui_meta.take_next_session_name(root), "counts": None, "started_at": _now(), "finished_at": None}
        _write_atomic(_job_path(root, video_id), job)
    args = (root, video_id, video, folder, runner or phases.subprocess_runner, python, find_parts, measure_cells)
    if background:
        threading.Thread(target=_run, args=args, daemon=True, name=f"video-{video_id[:8]}").start()
    else:
        _run(*args)
    return _job(root, video_id)


def _update(root: Path, video_id: str, **changes) -> None:
    job = _job(root, video_id) or {}
    job.update(changes)
    _write_atomic(_job_path(root, video_id), job)


VOICE_BINDINGS = "guided-voice-bindings.json"
OPEN_MARKER = "dialogue-pending-open"  # read and removed by dialogue_flow when the dialogue opens


def _bind_voice(root: Path, video_id: str, folder: str) -> None:
    """Point phone voice at the session built from this video: the guided session that uploaded it -> the new folder.

    This is the one file the module writes into the receiver's data dir; it is the same file the receiver reads
    to route voice input (``anastylos_voice_routes.BINDINGS_FILE``). A failure here never fails the processing job.
    """
    try:
        meta, _directory = _find(root, video_id)
        guided = meta.get("session_id")
        data_dir = _videos_dir(root).parent
        path = data_dir / VOICE_BINDINGS
        try:
            bindings = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            bindings = {}
        if isinstance(guided, str) and guided and isinstance(bindings, dict):
            bindings[guided] = folder
            _write_atomic(path, bindings)
    except (AdapterError, OSError, AttributeError):
        pass


def _find_parts(root: Path, video_id: str, folder: str, find_parts: Callable) -> None:
    """The drawings follow by themselves; a failure here is shown and leaves the processed session and the page's button intact."""
    _update(root, video_id, parts="finding")
    try:
        found = find_parts(root, folder)
        _update(root, video_id, parts="found", parts_not_drawn=len(found.get("not_drawn", [])))
    except Exception as exc:  # noqa: BLE001 - a background job must always end in a visible state
        _update(root, video_id, parts="failed", parts_error=str(exc)[:300])


def _measure_cells(root: Path, video_id: str, folder: str, measure_cells: Callable) -> None:
    """The storage cells are measured once per video so every clip later shows the checked time; a failure is shown only."""
    _update(root, video_id, cells="measuring")
    try:
        measure_cells(root, folder)
        _update(root, video_id, cells="measured")
    except Exception as exc:  # noqa: BLE001 - a background job must always end in a visible state
        _update(root, video_id, cells="failed", cells_error=str(exc)[:300])


def _run(root: Path, video_id: str, video: Path, folder: str, runner: Callable, python: Path | None, find_parts: Callable | None = None,
         measure_cells: Callable | None = None) -> None:
    try:
        _update(root, video_id, state="running", stage="creating the session folder")
        state = SessionState.create(root / folder, "Phone video", "workstation")
        job_name = (_job(root, video_id) or {}).get("name")
        if job_name:
            ui_meta.set_session_name(root / folder, job_name)
        _update(root, video_id, stage="running the session pipeline on the video")
        counts = phases.run_phase(state, PHASE_TAG, PHASE_LABEL, video, runner, phases.vision_python(python or Path(sys.executable)))
        _update(root, video_id, state="done", stage="finished", counts=counts, finished_at=_now())
        _bind_voice(root, video_id, folder)
        (root / folder / OPEN_MARKER).write_text("opens on the first voice turn", encoding="utf-8")
        if find_parts is not None:
            _find_parts(root, video_id, folder, find_parts)
        if measure_cells is not None:
            _measure_cells(root, video_id, folder, measure_cells)
    except SessionError as exc:
        _update(root, video_id, state="failed", stage="failed", error=str(exc), finished_at=_now())
    except Exception as exc:  # noqa: BLE001 - a background job must always end in a visible state
        _update(root, video_id, state="failed", stage="failed", error=f"{type(exc).__name__}: {exc}", finished_at=_now())
