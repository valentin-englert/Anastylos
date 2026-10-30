"""The agent's session file `agent-session.json` (`anastylos-agent-session@1`): what this disassembly is and which
phases ran where. It never holds evidence; it points to the memory folder and the per-phase run folders.

Written atomically (temporary file, then replace), so a crash leaves the previous version intact.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "anastylos-agent-session@1"
PHASE_WORDS_SCHEMA = "anastylos-live-phase-words@1"
STATE_NAME = "agent-session.json"
PHASE_TAG = re.compile(r"^P[1-9][0-9]?$")
# A phase run that started but has no `done` record is never resumed in place (session.py cannot resume, B10).
PHASE_STATUSES = ("running", "done", "failed")


class SessionError(Exception):
    """The request conflicts with the session as recorded; nothing was changed."""


def t_wall() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class SessionState:
    def __init__(self, root: Path, document: dict):
        self.root = Path(root)
        self.doc = document

    # ------------------------------------------------------------------ files

    @property
    def path(self) -> Path:
        return self.root / STATE_NAME

    @property
    def memory_dir(self) -> Path:
        return self.root / "memory"

    def run_dir(self, tag: str) -> Path:
        return self.root / "runs" / tag

    @property
    def phase_words_path(self) -> Path:
        return self.root / "phase-words.json"

    @classmethod
    def create(cls, root: Path, object_name: str, operator: str) -> "SessionState":
        root = Path(root)
        if (root / STATE_NAME).exists():
            raise SessionError(f"{root / STATE_NAME} exists; a session is created once")
        if root.exists() and any(root.iterdir()):
            raise SessionError(f"{root} is not empty; choose a new session folder")
        state = cls(root, {"schema": SCHEMA, "object": object_name, "operator": operator, "created": t_wall(),
                           "phases": []})
        state.memory_dir.mkdir(parents=True)
        state.save()
        return state

    @classmethod
    def load(cls, root: Path) -> "SessionState":
        path = Path(root) / STATE_NAME
        if not path.is_file():
            raise SessionError(f"no session at {root} ({STATE_NAME} missing); run `new` first")
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("schema") != SCHEMA:
            raise SessionError(f"{path} is not a {SCHEMA} file")
        return cls(root, document)

    def save(self) -> None:
        partial = self.path.with_suffix(".partial")
        partial.write_text(json.dumps(self.doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
        os.replace(partial, self.path)

    # ------------------------------------------------------------------ phases

    def phase(self, tag: str) -> dict | None:
        return next((p for p in self.doc["phases"] if p["tag"] == tag), None)

    def begin_phase(self, tag: str, label: str, video: Path, video_sha256: str) -> dict:
        if not PHASE_TAG.match(tag):
            raise SessionError(f"phase tag {tag!r} must look like P1, P2, …")
        if self.phase(tag) is not None:
            raise SessionError(f"phase {tag} is already recorded ({self.phase(tag)['status']}); "
                               "a phase runs once — use the next tag")
        unfinished = [p["tag"] for p in self.doc["phases"] if p["status"] == "running"]
        if unfinished:
            raise SessionError(f"phase {unfinished[0]} never finished; check its run folder before going on")
        if self.run_dir(tag).exists():
            raise SessionError(f"{self.run_dir(tag)} exists although the session does not list {tag}")
        if not label.strip():
            raise SessionError("the phase needs the operator's words for it (e.g. 'beim Öffnen des Gehäuses')")
        entry = {"tag": tag, "label": label, "video": str(video), "video_sha256": video_sha256,
                 "run_dir": str(self.run_dir(tag)), "status": "running", "started": t_wall(), "finished": None,
                 "counts": None, "error": None}
        self.doc["phases"].append(entry)
        self.save()
        self.write_phase_words()
        return entry

    def finish_phase(self, tag: str, counts: dict) -> None:
        entry = self.phase(tag)
        entry.update(status="done", finished=t_wall(), counts=counts)
        self.save()

    def fail_phase(self, tag: str, error: str) -> None:
        entry = self.phase(tag)
        entry.update(status="failed", finished=t_wall(), error=error[-2000:])
        self.save()

    def done_runs(self) -> list[Path]:
        return [Path(p["run_dir"]) for p in self.doc["phases"] if p["status"] == "done"]

    def write_phase_words(self) -> None:
        """The coordinator's phase words (`anastylos-live-phase-words@1`), from the operator's phase labels."""
        document = {"schema": PHASE_WORDS_SCHEMA, "provenance": f"operator phase labels in {STATE_NAME}",
                    "phases": {p["tag"]: p["label"] for p in self.doc["phases"]}}
        self.phase_words_path.write_text(json.dumps(document, ensure_ascii=False, indent=1) + "\n",
                                         encoding="utf-8", newline="\n")
