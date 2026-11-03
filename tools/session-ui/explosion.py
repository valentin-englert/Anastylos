"""Explosion view for the session UI: list, compose and serve bytes. Composition itself lives in tools/explosion.

Reads are side-effect free (nothing is written for GET requests; the SVG is composed in memory). The only writes are
the operator's layout decisions in ``<session>/drawings/explosion.json``; memory and job folders are never touched.
Ids are plain names resolved inside the workspace root, files come from a fixed whitelist (same rule as drawings.py).

Routes for ``server.py`` (not wired here; the integration owner adds them). Query/body field ``session`` is the
session id as everywhere else; ``Handler.workspace_root`` is the workspace root; AdapterError carries the HTTP status.

    GET  /api/explosion?session=<id>
         self._send_json(explosion.explosion_view(Handler.workspace_root, session_id))
    GET  /api/explosion/file?session=<id>&name=<name>[&job=<job>][&mode=exploded|assembled]
         body, content_type = explosion.explosion_bytes(Handler.workspace_root, session_id, name, job, mode)
         self._send_bytes(body, content_type)
         name=explosion.svg (no job) is the composed drawing; with job: drawing.svg | overlay.jpg | rectified.png
    POST /api/explosion/layout   JSON body {"session": "<id>", "action": "propose" | "confirm" | "reorder", ...}
         self._mutate(session_id, lambda: explosion.layout_action(Handler.workspace_root, session_id, body))
         propose: {}                                                          writes the skeleton, keeps confirmations
         confirm: {"job", "label"?, "view_side"?, "flip_horizontal"?, "confirmed"? (default true)}
         reorder: {"jobs": [<every layer job, top to bottom>]}
    Static (add to ``STATIC`` and to the GET route table): /explosion.html, /explosion.js, /explosion.css

Every POST answers with the refreshed ``explosion_view`` plus ``action`` (and ``added`` for propose). The page for a
session is ``/explosion.html?session=<id>``; it announces the selected part with the window event
``anastylos:part-selected`` (detail ``{job, label}``; both null when the selection is cleared).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import drawings
from adapter import AdapterError, closing_write, resolve_session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "explosion"))
import compose  # noqa: E402  (tools/explosion; stdlib only)
import explosion_jobs  # noqa: E402
import explosion_layout  # noqa: E402

SHEET_OUTLINES = Path(__file__).resolve().parents[1] / "drawing-proof" / "sheet_outlines.py"
OUTLINES_TIMEOUT_S = 300
JOB_FILES = {"drawing.svg": "image/svg+xml", "overlay.jpg": "image/jpeg", "rectified.png": "image/png"}
SVG_TYPE = "image/svg+xml"
ACTIONS = ("propose", "confirm", "reorder", "rotate")


WORK_AREA = "work area"


def _key(label: str) -> str:
    """Labels compare ignoring case, spaces and punctuation: 'Front shell' == 'frontshell'."""
    return "".join(ch for ch in (label or "").lower() if ch.isalnum())


def _memory_parts(session: Path, label: str) -> list[dict]:
    """Operator-declared parts of the session's memory whose label matches ``label`` (superseded events left out)."""
    events = []
    for path in sorted(session.glob("*/memory.jsonl")):
        try:
            events += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except (OSError, ValueError):
            continue
    superseded = {e.get("supersedes") for e in events if e.get("supersedes")}
    return [{"id": e["id"], "label": e["operator_label"]} for e in events
            if e.get("type") == "declared_removal" and e["id"] not in superseded and _key(e.get("operator_label")) == _key(label)]


def _memory_part_for(root: Path, session_id: str, session: Path, job: str, label: str | None) -> dict:
    """Confirming a layer is the operator's approval of its label: a label that matches no current part is recorded
    as an operator-stated part in the work area (idempotent); one clear match is only linked; several matches do nothing."""
    if not label or not _key(label):
        return {"status": "no_label"}
    matches = _memory_parts(session, label)
    if len(matches) > 1:
        return {"status": "ambiguous", "candidates": matches}
    if matches:
        return {"status": "linked", "part": matches[0]["id"]}
    note_id = "EXPL-" + hashlib.sha256(_key(label).encode("utf-8")).hexdigest()[:12]
    said = json.dumps({"source": "explosion view", "layer": job, "label": label,
                       "note": "operator confirmed this layer under this name in the explosion view"})
    try:
        written = closing_write(root, session_id, note_id, [label.strip()], WORK_AREA, said)
    except AdapterError as exc:
        return {"status": "failed", "message": str(exc)}
    if written.get("status") != "committed":
        return {"status": "failed", "message": "the part could not be recorded"}
    matches = _memory_parts(session, label)
    return {"status": "recorded", "part": matches[0]["id"]} if len(matches) == 1 else {"status": "recorded"}


def _session(root: Path, session_id: str) -> Path:
    return resolve_session(root, session_id)


def _refused(exc: Exception, status: int = 409) -> AdapterError:
    return AdapterError(str(exc), status)


def explosion_view(root: Path, session_id: str) -> dict:
    """Layout spec, composed manifest and per-layer file links for the page; ``error`` explains an unusable spec."""
    session = _session(root, session_id)
    view = {"session": session_id, "layout": None, "manifest": None, "error": None, "files": {},
            "proposable_jobs": [e.job for e in _drawn_jobs(session)]}
    try:
        view["layout"] = explosion_layout.load(session)
        if view["layout"] is not None:
            view["manifest"] = compose.compose(session, view["layout"]).manifest
    except (explosion_layout.LayoutError, explosion_jobs.JobError) as exc:
        view["error"] = str(exc)
        return view
    if view["manifest"]:
        view["files"] = {layer["job"]: [name for name in JOB_FILES if layer["inputs"].get(name)] for layer in view["manifest"]["layers"]}
    return view


def _drawn_jobs(session: Path) -> list:
    found = (explosion_jobs.read_job(session, name) for name in explosion_jobs.list_job_names(session))
    return [e for e in found if e.state == "done" and e.outline is not None]


def explosion_bytes(root: Path, session_id: str, name: str, job: str = "", mode: str = "exploded") -> tuple[bytes, str]:
    session = _session(root, session_id)
    if not job:
        if name != "explosion.svg":
            raise AdapterError(f"Unknown explosion file {name!r}.", 404)
        try:
            return compose.compose(session, mode=mode).svg.encode("utf-8"), SVG_TYPE
        except explosion_layout.LayoutError as exc:
            raise _refused(exc) from exc
    if name not in JOB_FILES:
        raise AdapterError(f"Unknown explosion file {name!r}.", 404)
    try:
        job_path = explosion_jobs.jobs_root(session) / explosion_jobs.plain_job(job)
    except explosion_jobs.JobError as exc:
        raise _refused(exc, 404) from exc
    path = explosion_jobs.find_rectified(job_path) if name == "rectified.png" else job_path / name
    if path is None or not (job_path / "progress.json").is_file() or not path.is_file() or path.resolve().parent != job_path.resolve():
        raise AdapterError(f"{name} is not available for drawing job {job!r}.", 404)
    return path.read_bytes(), JOB_FILES[name]


def _not_drawn(stdout: str | None) -> list:
    """Parts the tool saw but would not draw; the older result is a plain list of jobs."""
    try:
        result = json.loads(stdout or "")
    except ValueError:
        return []
    return result.get("not_drawn", []) if isinstance(result, dict) else []


def find_outlines(root: Path, session_id: str, run=subprocess.run) -> dict:
    """The operator's click: outlines of the large parts in the session's video become unconfirmed drawings in the layout."""
    session = _session(root, session_id)
    if any(name.startswith("outline-") for name in explosion_jobs.list_job_names(session)):
        raise AdapterError("This session already has outlines from the sheet.", 409)
    try:
        video = json.loads((session / "agent-session.json").read_text(encoding="utf-8"))["phases"][-1]["video"]
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        raise AdapterError("This session has no processed video.", 409) from None
    done = run([sys.executable, "-B", str(SHEET_OUTLINES), "--session-dir", str(session), "--video", str(video), "--frame", "auto"],
               capture_output=True, text=True, encoding="utf-8", timeout=OUTLINES_TIMEOUT_S)
    if done.returncode:
        reason = (done.stderr or "").strip().splitlines()
        raise AdapterError(f"Could not find outlines: {reason[-1] if reason else 'the tool reported an error'}", 502)
    return {**layout_action(root, session_id, {"action": "propose"}), "not_drawn": _not_drawn(done.stdout)}


def layout_action(root: Path, session_id: str, body: dict) -> dict:
    """Apply one operator decision to the layout spec and return the refreshed view."""
    session = _session(root, session_id)
    action = body.get("action")
    if action not in ACTIONS:
        raise AdapterError(f"Action must be one of {', '.join(ACTIONS)}.", 400)
    extra: dict = {}
    try:
        if action == "propose":
            extra["added"] = explosion_layout.propose(session)["added"]
        elif action == "confirm":
            explosion_layout.confirm_layer(session, _text(body, "job", required=True), label=_text(body, "label"),
                                           view_side=_text(body, "view_side"), flip_horizontal=_flag(body, "flip_horizontal"),
                                           confirmed=_flag(body, "confirmed", default=True))
            if _flag(body, "confirmed", default=True):  # the panel shows outline and source, so one click decides both questions
                layer = next(l for l in explosion_layout.load(session)["layers"] if l["job"] == body["job"])
                memory = _memory_part_for(root, session_id, session, body["job"], layer["label"])
                extra["memory"] = memory
                part = f"; memory part {memory['part']}" if memory.get("part") else ""
                drawings.bind(root, session_id, body["job"], "confirm", f"confirmed in the explosion view as {layer['label']}{part}")
        elif action == "rotate":
            explosion_layout.rotate_layer(session, _text(body, "job", required=True))
        else:
            jobs = body.get("jobs")
            if not isinstance(jobs, list) or not all(isinstance(j, str) for j in jobs):
                raise AdapterError("jobs must be a list of drawing job names.", 400)
            explosion_layout.reorder_layers(session, jobs)
    except (explosion_layout.LayoutError, explosion_jobs.JobError) as exc:
        raise _refused(exc) from exc
    return {"action": action, **extra, **explosion_view(root, session_id)}


def _text(body: dict, key: str, required: bool = False) -> str | None:
    value = body.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise AdapterError(f"{key} must be text.", 400)
    return value


def _flag(body: dict, key: str, default: bool | None = None) -> bool | None:
    value = body.get(key, default)
    if value is not None and not isinstance(value, bool):
        raise AdapterError(f"{key} must be true or false.", 400)
    return value
