"""Evidence replay: the recorded video moment of a part, from stored video only.

A ``declared_removal`` may carry ``t_s``, a time in its phase video. The pipeline sets it as the START of a window,
and what the window is depends on the question the operator answered (``tools/session/answers.py``):

- ``source_of_part`` answered with typed words: ``t_s`` = start of the DEPOSIT window (when the part was put down);
- ``source_of_part`` answered by choosing an offered candidate, and ``destination_of_removal``: start of the REMOVAL window;
- an operator-added extra part: a time the operator stated;
- anything else: a time whose meaning the record does not say.

The panel therefore shows the meaning in fixed words and never calls a deposit a removal. A correction that replaced a
record without ``t_s`` inherits the time of the record it replaced (same part, renamed), and says so.

The clip. At the deposit time the hands usually cover the device, so for a deposit the clip starts earlier: at the previous
deposit of the same phase (the person is then taking this part out), at most ``LEAD_MAX_S`` and at least ``LEAD_MIN_S``
before this deposit, and ends ``TAIL_S`` after it. It says only "recorded seconds before this part was put down; the
removal itself is not marked" because nothing in the record marks the removal. A removal window gets the clip from its start
to its end (at most ``LEAD_MAX_S``); any other time is shown ``AROUND_S`` either side. About 30 frames, at most 10 fps.

Frames are not seeked in the browser. ``replay_prepare`` runs ``replay_worker.py`` in its own process (OpenCV and the
sheet reader stay out of the UI process) and keeps rectified JPEG frames under ``<session>/evidence/replay/<key>/``, keyed
by the recorded video SHA-256 and the time. Browsers get frames only through ``replay_file`` (fixed names, no paths, no
video). Nothing here writes memory or calls a model.

Routes for ``server.py`` (query/body field ``session`` as everywhere; ``Handler.workspace_root`` is the root):

    GET  /api/replay?session=<id>&parts=<dec-0001,dec-0002,...>   -> replay_moments(root, session, [part ids])
         read-only; per part: status ``no_moment`` | ``not_prepared`` | ``ready`` | ``unregistered`` plus the observed time
    POST /api/replay/prepare   {"session", "part"}                  -> replay_prepare(root, session, part)  (under _mutate)
         extracts the frames once (about 5-15 s), then answers with the player data (``frames``, ``before``, ``after``)
    GET  /api/replay/file?session=<id>&key=<key>&name=<seq-000.jpg|before.jpg|after.jpg> -> replay_file(...)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import adapter
import assembly_help
from adapter import AdapterError

WORKER = Path(__file__).resolve().parent / "replay_worker.py"
EVIDENCE_DIR = "evidence"
REPLAY_DIR = "replay"
VERSION = 2  # 2: clips start before the deposit; version 1 folders are never read
LEAD_MAX_S, LEAD_MIN_S, TAIL_S, AROUND_S = 12.0, 3.0, 2.0, 1.0
MIN_PLAYBACK_FPS = 6.0
WORKER_TIMEOUT_S = 600
LABEL = "Reverse replay of the recorded disassembly, not a verified installation procedure."
NO_MOMENT_TIME = "No recorded moment can be shown for this part: its record has no video time."
NO_MOMENT_VIDEO = "No recorded moment can be shown for this part: the video of its phase is not available."
NO_MOMENT_REGISTERED = ("No recorded moment can be shown for this part: the frames around its video time could not be "
                        "registered to the calibration sheet.")
FILE_NAME = re.compile(r"seq-\d{3}\.jpg|before\.jpg|after\.jpg")
KEY = re.compile(r"[0-9a-f]{12}(?:-\d{1,9}){3}-v\d{1,3}")
QUESTION_ID = re.compile(r"answer to (Q-(?P<tag>[A-Za-z0-9]+)-\d+)")
CANDIDATE = re.compile(r"automatic candidate (?P<event>\S+) ")

CLIP_TEXTS = {
    "deposit_window_start": "Recorded seconds before this part was put down; the removal itself is not marked. "
                            "Clip: video {start_s:.1f}–{end_s:.1f} s.",
    "removal_window_start": "Clip: video {start_s:.1f}–{end_s:.1f} s, from the start of the window in which this part was removed.",
    "around": "Clip: video {start_s:.1f}–{end_s:.1f} s around this time.",
}
MEANINGS = {
    "deposit_window_start": "Video time {t:.1f} s is the start of the window in which this part was seen being put down{window}. "
                            "It marks the deposit, not the removal.",
    "removal_window_start": "Video time {t:.1f} s is the start of the window in which this part was seen being removed{window}.",
    "operator_stated": "Video time {t:.1f} s was stated by the operator, not observed by the system.",
    "unspecified": "Video time {t:.1f} s is recorded for this part; the record does not say whether it marks the removal "
                   "or the deposit.",
}


def _window_text(window: list | None) -> str:
    return f" ({window[0]:.1f}–{window[1]:.1f} s)" if window else ""


def _chain(memory: dict, event: dict) -> list[dict]:
    """The record and the records it replaced, newest first (corrections point at what they replace)."""
    out, seen = [], set()
    while event is not None and event["id"] not in seen:
        out.append(event)
        seen.add(event["id"])
        event = memory["by_id"].get(event.get("supersedes"))
    return out


def _phase_tag(state, event: dict, memory: dict) -> str | None:
    """The session phase of a record: the tag in its question id, else its phase label, else the only phase."""
    match = QUESTION_ID.search(event.get("evidence_note") or "")
    tags = [p["tag"] for p in state.doc["phases"]]
    if match and match["tag"] in tags:
        return match["tag"]
    label = (memory["by_id"].get(event.get("phase")) or {}).get("label")
    labelled = [p["tag"] for p in state.doc["phases"] if p.get("label") == label]
    if len(labelled) == 1:
        return labelled[0]
    return tags[0] if len(tags) == 1 else None


def _questions(state, tag: str) -> list[dict]:
    path = state.run_dir(tag) / "questions.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("questions", [])
    except (OSError, ValueError):
        return []


def _question(questions: list[dict], event: dict) -> dict | None:
    match = QUESTION_ID.search(event.get("evidence_note") or "")
    return next((q for q in questions if match and q.get("id") == match[1]), None)


def _meaning(event: dict, question: dict | None) -> tuple[str, list | None]:
    """``(meaning key, window)`` from the question the time came from (``tools/session/answers.py``)."""
    if question is None:
        stated = "operator-added part" in (event.get("evidence_note") or "")
        return ("operator_stated" if stated else "unspecified"), None
    if question["kind"] == "destination_of_removal":
        return "removal_window_start", question.get("window_s")
    if question["kind"] == "source_of_part":
        chosen = CANDIDATE.search(event.get("source") or "")
        if chosen:
            candidate = next((c for c in question.get("candidates", []) if c.get("removal_event") == chosen["event"]), None)
            return "removal_window_start", candidate.get("window_s") if candidate else None
        return "deposit_window_start", question.get("deposit_window_s")
    return "unspecified", None


def _clip(meaning: str, anchor_s: float, window: list | None, questions: list[dict]) -> dict:
    """Start and end of the clip for a recorded time (see the module docstring); ``kind`` picks the fixed clip sentence."""
    if meaning == "deposit_window_start":
        earlier = [q["deposit_window_s"][0] for q in questions
                   if q.get("deposit_window_s") and q["deposit_window_s"][0] < anchor_s - 0.5]
        start = max(anchor_s - LEAD_MAX_S, max(earlier)) if earlier else anchor_s - LEAD_MAX_S
        return {"kind": meaning, "start_s": round(max(0.0, min(start, anchor_s - LEAD_MIN_S)), 3),
                "end_s": round(anchor_s + TAIL_S, 3), "anchor_s": anchor_s}
    if meaning == "removal_window_start" and window:
        return {"kind": meaning, "start_s": round(max(0.0, window[0] - AROUND_S), 3),
                "end_s": round(min(max(window[1], window[0]) + AROUND_S, window[0] + LEAD_MAX_S), 3), "anchor_s": anchor_s}
    return {"kind": "around", "start_s": round(max(0.0, anchor_s - AROUND_S), 3), "end_s": round(anchor_s + AROUND_S, 3),
            "anchor_s": anchor_s}


def _key(video_sha256: str, clip: dict) -> str:
    ms = [int(round(clip[k] * 1000)) for k in ("anchor_s", "start_s", "end_s")]
    return f"{video_sha256[:12]}-{ms[0]}-{ms[1]}-{ms[2]}-v{VERSION}"


def _folder(session_dir: Path, key: str) -> Path:
    return session_dir / EVIDENCE_DIR / REPLAY_DIR / key


def _manifest(folder: Path) -> dict | None:
    path = folder / "manifest.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _moment(state, memory: dict, event: dict) -> dict:
    """What can be said about this part's recorded moment, without touching the video or any file."""
    base = {"part_id": event["id"], "label": event.get("operator_label"), "observed_s": None, "meaning": None,
            "message": NO_MOMENT_TIME, "status": "no_moment", "key": None, "phase_tag": None, "via_record": None,
            "window_s": None, "label_fixed": LABEL}
    holder = next((e for e in _chain(memory, event) if e.get("t_s") is not None), None)
    if holder is None:
        return base
    tag = _phase_tag(state, holder, memory)
    phase = state.phase(tag) if tag else None
    if phase is None or not phase.get("video_sha256") or not Path(phase["video"]).is_file():
        return {**base, "observed_s": holder["t_s"], "message": NO_MOMENT_VIDEO}
    questions = _questions(state, tag)
    meaning, window = _meaning(holder, _question(questions, holder))
    clip = _clip(meaning, holder["t_s"], window, questions)
    text = MEANINGS[meaning].format(t=holder["t_s"], window=_window_text(window))
    if holder["id"] != event["id"]:
        text += f" (The time comes from record {holder['id']}, which {event['id']} replaced.)"
    key = _key(phase["video_sha256"], clip)
    manifest = _manifest(_folder(state.root, key))
    status = "not_prepared" if manifest is None else manifest["status"]
    return {**base, "status": status, "observed_s": holder["t_s"], "meaning": meaning, "meaning_text": text,
            "clip": clip, "clip_text": CLIP_TEXTS[clip["kind"]].format(**clip),
            "message": NO_MOMENT_REGISTERED if status == "unregistered" else "", "key": key, "phase_tag": tag,
            "via_record": holder["id"], "window_s": window, "video_sha256": phase["video_sha256"]}


def _current_part(memory: dict, part_id: str) -> dict:
    event = next((e for e in memory["parts"] if e["id"] == part_id), None)
    if event is None:
        raise AdapterError(f"{part_id!r} is not a current declared part in the session memory.", 404)
    return event


def replay_moments(root: Path, session_id: str, part_ids: list[str]) -> dict:
    """The recorded moment of each part (read-only; no frame is extracted)."""
    if not part_ids or len(part_ids) > 100:
        raise AdapterError("Name between 1 and 100 parts.", 400)
    state = adapter._load_state(root, session_id)
    memory = assembly_help.read_memory(state)
    return {"schema": "anastylos-replay-moments@1", "label_fixed": LABEL,
            "moments": [_moment(state, memory, _current_part(memory, part_id)) for part_id in part_ids]}


def _player(manifest: dict) -> dict:
    """Manifest fields the browser may see: names, times and hashes, never a path or the video name."""
    keep = ("file", "t_s")
    pair = {k: (None if manifest[k] is None else {**{c: manifest[k][c] for c in keep}, "low_skin": manifest[k]["low_skin"]})
            for k in ("before", "after")}
    return {"frames": [{k: f[k] for k in keep} for f in manifest["frames"]],
            "skipped_frames": len(manifest["skipped"]), "rate_fps": manifest["rate_fps"],
            "playback_fps": max(manifest["rate_fps"], MIN_PLAYBACK_FPS), **pair,
            "px_per_mm": manifest["px_per_mm"], "generated_at": manifest["generated_at"]}


def replay_prepare(root: Path, session_id: str, part_id: str, run=subprocess.run) -> dict:
    """Extract (once) and return the moment with its player data; a cached folder is reused as it is."""
    state = adapter._load_state(root, session_id)
    memory = assembly_help.read_memory(state)
    moment = _moment(state, memory, _current_part(memory, part_id))
    if moment["status"] not in ("not_prepared", "ready", "unregistered"):
        return moment
    session_dir = adapter.resolve_session(root, session_id)
    folder = _folder(session_dir, moment["key"])
    if moment["status"] == "not_prepared":
        phase = state.phase(moment["phase_tag"])
        folder.parent.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "ANASTYLOS_DRAWING_CACHE": str(session_dir / EVIDENCE_DIR / "_hash-cache")}
        done = run([sys.executable, "-B", str(WORKER), "--video", phase["video"], "--video-sha256", phase["video_sha256"],
                    "--start-s", repr(moment["clip"]["start_s"]), "--end-s", repr(moment["clip"]["end_s"]),
                    "--anchor-s", repr(float(moment["clip"]["anchor_s"])), "--out", str(folder)],
                   capture_output=True, text=True, encoding="utf-8", timeout=WORKER_TIMEOUT_S, env=env)
        if done.returncode:
            reason = (done.stderr or "").strip().splitlines()
            raise AdapterError(f"The replay frames could not be prepared: {reason[-1] if reason else 'the tool reported an error'}", 502)
        moment = _moment(state, memory, _current_part(memory, part_id))
    manifest = _manifest(folder)
    if manifest is None:
        raise AdapterError("The replay frames could not be read back after extraction.", 502)
    if manifest["status"] != "ready":
        return moment
    return {**moment, "player": _player(manifest)}


def replay_file(root: Path, session_id: str, key: str, name: str) -> tuple[bytes, str]:
    """One replay JPEG by fixed name from a cache folder of this session."""
    if not KEY.fullmatch(key or "") or not FILE_NAME.fullmatch(name or ""):
        raise AdapterError(f"Unknown replay file {name!r}.", 404)
    folder = _folder(adapter.resolve_session(root, session_id), key)
    path = folder / name
    if not path.is_file() or path.resolve().parent != folder.resolve():
        raise AdapterError(f"Unknown replay file {name!r}.", 404)
    return path.read_bytes(), "image/jpeg"
