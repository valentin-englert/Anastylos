"""Part drawings for the session UI: start background jobs, show progress and results, record the operator's binding.

Layout inside a session directory (all names plain; the browser never sends paths):
    drawings/specs/<name>.json   job specs placed by the operator or agent (tools/drawing-proof/drawing_job.py)
    drawings/jobs/<job>/         one background job: progress.json, drawing.svg, overlay.jpg, contact_sheet.jpg
    drawings/jobs/<job>/binding.json   the operator's explicit decision; memory is never written
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

from adapter import AdapterError, resolve_session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "drawing-proof"))
import job_control  # noqa: E402  (stdlib only: no image or network code in the UI process)

DRAWINGS = "drawings"
FILES = {"drawing.svg": "image/svg+xml", "overlay.jpg": "image/jpeg", "contact_sheet.jpg": "image/jpeg"}
BINDABLE = {"drawn_unconfirmed", "NEEDS_CONFIRMATION"}


def _plain(name: str, what: str) -> str:
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise AdapterError(f"Unknown {what} {name!r}.", 404)
    return name


def _job_dir(root: Path, session_id: str, job: str) -> Path:
    path = resolve_session(root, session_id) / DRAWINGS / "jobs" / _plain(job, "drawing job")
    if not (path / "progress.json").is_file():
        raise AdapterError(f"Unknown drawing job {job!r}.", 404)
    return path


def _summary(job_dir: Path) -> dict:
    progress = job_control.status(job_dir)
    result = progress.get("result") or {}
    binding = job_dir / "binding.json"
    capture = job_dir / "capture-request.json"
    return {"job": job_dir.name, "state": progress.get("state"), "stage": progress.get("stage"), "done": progress.get("done"),
            "total": progress.get("total"), "elapsed_s": progress.get("elapsed_s"), "stage_s": progress.get("stage_s"),
            "error": progress.get("error"), "status": result.get("status"), "reasons": result.get("reasons", []),
            "part": (result.get("spec") or {}).get("part"), "scope": (result.get("spec") or {}).get("scope"),
            "choice": result.get("choice"), "frame": result.get("frame"), "opening_candidates": result.get("opening_candidates"),
            "files": [name for name in FILES if (job_dir / name).is_file()],
            "binding": json.loads(binding.read_text(encoding="utf-8")) if binding.is_file() else None,
            "capture": _capture_view(capture), "still": bool((result.get("spec") or {}).get("still"))}


def _capture_view(path: Path) -> dict | None:
    """The job's phone capture request as stored (read only; captures.py owns the record)."""
    if not path.is_file():
        return None
    doc = json.loads(path.read_text(encoding="utf-8"))
    return {k: doc.get(k) for k in ("state", "phone_status", "label", "follow_up_job", "failure", "requested_at", "delivered_at")}


def list_drawings(root: Path, session_id: str) -> dict:
    base = resolve_session(root, session_id) / DRAWINGS
    specs = sorted(p.stem for p in (base / "specs").glob("*.json")) if (base / "specs").is_dir() else []
    jobs = sorted((p for p in (base / "jobs").iterdir() if (p / "progress.json").is_file()), key=lambda p: p.stat().st_mtime,
                  reverse=True) if (base / "jobs").is_dir() else []
    return {"specs": specs, "jobs": [_summary(j) for j in jobs]}


def start_drawing(root: Path, session_id: str, spec_name: str) -> dict:
    base = resolve_session(root, session_id) / DRAWINGS
    spec = base / "specs" / f"{_plain(spec_name, 'drawing spec')}.json"
    if not spec.is_file():
        raise AdapterError(f"Unknown drawing spec {spec_name!r}.", 404)
    running = [j for j in list_drawings(root, session_id)["jobs"] if j["state"] == "running" and j["job"].startswith(spec_name + "-")]
    if running:  # a double click must not start a second job for the same spec
        return {"job": running[0]["job"], "state": "running", "already_running": True}
    job = base / "jobs" / f"{spec_name}-{time.strftime('%Y%m%d-%H%M%S')}"
    job_control.start(spec, job)
    return {"job": job.name, "state": "started"}


def resume_drawing(root: Path, session_id: str, job: str) -> dict:
    """Continue a paused or failed job from its checkpoints (no repeated Muse or SAM call for finished stages)."""
    job_dir = _job_dir(root, session_id, job)
    state = _summary(job_dir)["state"]
    if state not in ("paused", "failed"):
        raise AdapterError(f"Only a paused or failed job can be resumed (state {state}).", 409)
    job_control.start(job_dir / "spec.json", job_dir)
    return {"job": job, "state": "resumed"}


def drawing_status(root: Path, session_id: str, job: str) -> dict:
    return _summary(_job_dir(root, session_id, job))


def drawing_bytes(root: Path, session_id: str, job: str, name: str) -> tuple[bytes, str]:
    if name not in FILES:
        raise AdapterError(f"Unknown drawing file {name!r}.", 404)
    path = _job_dir(root, session_id, job) / name
    if not path.is_file():
        raise AdapterError(f"{name} is not available for this job.", 404)
    return path.read_bytes(), FILES[name]


def bind(root: Path, session_id: str, job: str, decision: str, note: str) -> dict:
    """Record the operator's explicit decision on 'this drawing shows this part'. Idempotent; never touches memory."""
    if decision not in ("confirm", "reject"):
        raise AdapterError("Decision must be confirm or reject.", 400)
    job_dir = _job_dir(root, session_id, job)
    existing = job_dir / "binding.json"
    if existing.is_file():
        return {**json.loads(existing.read_text(encoding="utf-8")), "already_recorded": True}
    summary = _summary(job_dir)
    if summary["state"] != "done" or summary["status"] not in BINDABLE:
        raise AdapterError(f"This drawing cannot be bound (state {summary['state']}, status {summary['status']}).", 409)
    svg = job_dir / "drawing.svg"
    record = {"schema": "anastylos-drawing-binding@1", "decision": decision, "note": note[:300], "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "job_status": summary["status"], "reasons_shown": summary["reasons"], "part": summary["part"], "scope": summary["scope"],
              "frame": summary["frame"], "drawing_sha256": hashlib.sha256(svg.read_bytes()).hexdigest() if svg.is_file() else None,
              "memory_written": False}
    existing.write_text(json.dumps(record, indent=1), encoding="utf-8")
    return record
