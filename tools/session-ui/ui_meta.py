"""Small UI-only metadata: session names and the history cutoff. Never touches memory.jsonl or session state.

- ``<session>/ui-meta.json``      {"schema", "name"}: the operator's name for one session.
- ``<workspace>/ui-settings.json`` {"schema", "next_session_name", "history_cutoff"}: the name given before
  recording (used for the next session that is created) and the time before which sessions are hidden by default.

Start fresh from a terminal:  python ui_meta.py start-fresh <workspace>
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

SESSION_FILE = "ui-meta.json"
SETTINGS_FILE = "ui-settings.json"
MAX_NAME = 60


def clean_name(value: object) -> str:
    return " ".join(str(value or "").split())[:MAX_NAME]


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(document, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(partial, path)


def session_name(session_dir: Path) -> str:
    return clean_name(_read(Path(session_dir) / SESSION_FILE).get("name"))


def set_session_name(session_dir: Path, name: object) -> str:
    name = clean_name(name)
    _write(Path(session_dir) / SESSION_FILE, {"schema": "anastylos-ui-session-meta@1", "name": name})
    return name


def settings(root: Path) -> dict:
    data = _read(Path(root) / SETTINGS_FILE)
    return {"schema": "anastylos-ui-settings@1", "next_session_name": clean_name(data.get("next_session_name")),
            "history_cutoff": data.get("history_cutoff") if isinstance(data.get("history_cutoff"), (int, float)) else None}


def _save(root: Path, **changes) -> dict:
    current = {**settings(root), **changes}
    _write(Path(root) / SETTINGS_FILE, current)
    return current


def set_next_session_name(root: Path, name: object) -> str:
    return _save(root, next_session_name=clean_name(name))["next_session_name"]


def take_next_session_name(root: Path) -> str:
    """The name waiting for the next session; it is used once."""
    name = settings(root)["next_session_name"]
    if name:
        _save(root, next_session_name="")
    return name


def start_fresh(root: Path, now: float | None = None) -> float:
    """Hide every session created before now from the default list. Nothing is deleted."""
    stamp = datetime.now(timezone.utc).timestamp() if now is None else now
    _save(root, history_cutoff=stamp)
    return stamp


def created_at(session_dir: Path) -> float:
    return Path(session_dir).stat().st_ctime  # creation time on Windows


def decorate_and_filter(root: Path, sessions: list[dict], show_all: bool) -> dict:
    """Add each session's name; unless ``show_all``, drop sessions created before the cutoff."""
    cutoff = settings(root)["history_cutoff"]
    shown, older = [], 0
    for entry in sessions:
        folder = Path(root) / entry["id"]
        entry = {**entry, "name": session_name(folder)}
        if cutoff is not None and not show_all and created_at(folder) < cutoff:
            older += 1
            continue
        shown.append(entry)
    return {"sessions": shown, "hidden_older": older, "cutoff": cutoff}


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "start-fresh":
        print(f"History cutoff set to {datetime.fromtimestamp(start_fresh(Path(sys.argv[2]))).isoformat(timespec='seconds')}")
    else:
        sys.exit("usage: python ui_meta.py start-fresh <workspace>")
