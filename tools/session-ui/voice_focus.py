"""Voice follows the part selected on the desktop.

The explosion page tells the server which session and drawing job the operator has selected (``set_focus``; a null job
means "session open, nothing selected"). A spoken turn that no question round wants (``dialogue_flow``) is then answered
for the focused part: a phrase matched by ``part_intent`` goes to ``assembly_help.ask_about_part`` and only verified
fields are spoken (the claim-checked text of the guide package, a sentence built from ``belongs_here`` and the layer,
or the fixed message of the status). Nothing here writes memory or calls a model. The focus lives in server memory only
and is ignored after ``FOCUS_MAX_AGE_S``.

Routes for ``server.py``:

    POST /api/focus  {"session": "<id>", "job": "<job>" | null}  -> set_focus(...) -> view(...)   (no files are written)
    GET  /api/focus?session=<id>  -> view(session): {"focused", "session", "job", "age_s", "voice": last voice answer | null}
         ``voice`` = {"at", "heard", "spoken", "status", "target_job", "result": the ``ask_about_part`` result | null}; the
         page polls it and shows it in the assembly help panel.
"""

from __future__ import annotations

import re
import sys
import threading
import time
from pathlib import Path

import adapter
import assembly_help
import drawings
import part_intent
from adapter import AdapterError

sys.path.append(str(Path(__file__).resolve().parents[1] / "live"))
from dialogue import settings as dialogue_settings  # noqa: E402

FOCUS_MAX_AGE_S = 30 * 60
SELECT_FIRST = "Please select a part on the screen first."
UNAVAILABLE_FOR_PART = "I could not answer that for the selected part. Please use the screen."
NO_PART_SELECTED = "No part is selected."
LISTED_GROUPS = 2

_CITATION = re.compile(r"\s*\[[^\]]*\]")
_CELL = re.compile(r"cell-(\d+)")
_lock = threading.Lock()
_state: dict = {"focus": None, "voice": None}


def _speak(text: str, kind: str = "status", basis_ref: str | None = None) -> dict:
    return {"text": text, "kind": kind, "basis_ref": basis_ref}


# ------------------------------------------------------------------ focus (server memory only)

def set_focus(root: Path, session_id: str, job: str | None, now: float | None = None) -> dict:
    """Remember the desktop selection; the session and the job are validated, nothing is written."""
    adapter.resolve_session(root, session_id)
    if job is not None:
        if not isinstance(job, str) or not job:
            raise AdapterError("The job must be a drawing job id or null.", 400)
        drawings.drawing_status(root, session_id, job)
    with _lock:
        _state["focus"] = {"root": str(root), "session": session_id, "job": job, "set_at": time.time() if now is None else now}
    return view(root, session_id, now)


def current(root: Path, now: float | None = None) -> dict | None:
    """The focus of this workspace, or None when there is none or it is older than ``FOCUS_MAX_AGE_S``."""
    with _lock:
        focus = _state["focus"]
    stamp = time.time() if now is None else now
    if focus is None or focus["root"] != str(root) or stamp - focus["set_at"] > FOCUS_MAX_AGE_S:
        return None
    return focus


def view(root: Path, session_id: str, now: float | None = None) -> dict:
    adapter.resolve_session(root, session_id)
    focus = current(root, now)
    if focus is None or focus["session"] != session_id:
        return {"focused": False, "session": session_id, "job": None, "age_s": None, "voice": None}
    with _lock:
        voice = _state["voice"]
    stamp = time.time() if now is None else now
    return {"focused": True, "session": session_id, "job": focus["job"], "age_s": round(stamp - focus["set_at"], 1),
            "voice": voice if voice and voice["session"] == session_id else None}


def _record_voice(focus: dict, heard: str, spoken: str, status: str, result: dict | None, now: float | None) -> None:
    with _lock:
        _state["voice"] = {"at": time.time() if now is None else now, "session": focus["session"], "heard": heard,
                           "spoken": spoken, "status": status, "target_job": focus["job"], "result": result}


# ------------------------------------------------------------------ what is spoken

def _place(location: str | None) -> str:
    if not location:
        return "an unrecorded place"
    cell = _CELL.fullmatch(location)
    return f"cell {cell.group(1)}" if cell else location


def _group_phrase(group: dict) -> str:
    where = _place(group.get("storage_location"))
    count = group["count"]
    return f"{group['label']} in {where}" if count == 1 else f"{count} {group['label']} parts in {where}"


def _belongs_sentence(result: dict) -> str:
    groups = result["belongs_here"]
    listed = [_group_phrase(g) for g in groups[:LISTED_GROUPS]]
    more = sum(g["count"] for g in groups[LISTED_GROUPS:])
    if more:
        listed.append(f"and {more} more")
    label = (result.get("part_label") or "This part")
    text = f"{label[:1].upper()}{label[1:]}: {', '.join(listed)}."
    layer = result.get("layer")
    if layer:
        near = [f"{word} it the {layer[key]}" for word, key in (("above", "above"), ("below", "below")) if layer.get(key)]
        text += f" Layer {layer['position']} of {layer['of']}" + (f", {' and '.join(near)}." if near else ".")
    return text


def speak_for(result: dict) -> dict:
    """The spoken line for one ``ask_about_part`` result: verified text, a sentence from verified fields, or a fixed message."""
    status, basis = result["status"], result.get("package_sha256")
    answer = result.get("answer")
    if status == "parts_belong_here":
        return _speak(_belongs_sentence(result), "guide_verified", basis)
    if status == "answered" and answer and answer["accepted"]:
        return _speak(_CITATION.sub("", answer["text"]), "guide_verified", basis)
    if status == "coarse_origin" and answer and answer["accepted"]:
        return _speak(f"{_CITATION.sub('', answer['text'])} {result['message']}", "guide_verified", basis)
    return _speak(result["message"], "status", basis)


def _strip_wake(root: Path, text: str) -> str:
    view_ = dialogue_settings.public_view(root)
    names = {n.lower() for n in [*view_["wake_words"], *view_["mishearings"]]}
    words = text.strip().split(maxsplit=1)
    if words and words[0].strip(",.:;!?").lower() in names:
        return words[1] if len(words) > 1 else ""
    return text.strip()


def answer_phrase(root: Path, text: str, now: float | None = None) -> dict | None:
    """The spoken answer for a part phrase, or None when this turn is not about the focused part."""
    focus = current(root, now)
    if focus is None:
        return None
    heard = _strip_wake(root, text or "")
    if part_intent.match(heard) is None:
        return None
    if focus["job"] is None:
        speak = _speak(SELECT_FIRST)
        _record_voice(focus, heard, speak["text"], "no_selection", None, now)
        return speak
    try:
        result = assembly_help.ask_about_part(root, focus["session"], focus["job"], heard)
    except AdapterError:
        speak = _speak(UNAVAILABLE_FOR_PART)
        _record_voice(focus, heard, speak["text"], "unavailable", None, now)
        return speak
    speak = speak_for(result)
    _record_voice(focus, heard, speak["text"], result["status"], result, now)
    return speak


def _job_label(root: Path, session_id: str, job: str) -> str | None:
    try:
        summary = drawings.drawing_status(root, session_id, job)
    except AdapterError:
        return None
    layer = assembly_help.layer_of(adapter.resolve_session(root, session_id) / drawings.DRAWINGS / "jobs" / job)
    return (layer or {}).get("label") or summary.get("part")


def name_focus(root: Path, now: float | None = None) -> dict | None:
    """A bare wake word with nothing left to ask: say which session and part the desktop shows, so a wrong one is noticed."""
    focus = current(root, now)
    if focus is None:
        return None
    try:
        thing = adapter._load_state(root, focus["session"]).doc["object"]
    except AdapterError:
        return None
    label = _job_label(root, focus["session"], focus["job"]) if focus["job"] else None
    chosen = NO_PART_SELECTED if focus["job"] is None else f"Selected part: {label}." if label else "A part is selected."
    return _speak(f"{thing}. {chosen}")


def reset() -> None:
    """Forget the focus and the last voice answer (tests)."""
    with _lock:
        _state.update(focus=None, voice=None)
