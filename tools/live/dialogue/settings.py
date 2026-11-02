"""Per-workspace dialogue settings: persona and agent name (the wake word).

Stored as ``dialogue-settings.json`` in the session-UI workspace root. Reads never fail: a missing or damaged
file means the defaults (persona ``serious``, no name, so the wake words are "Muse" and "Meta").
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from catalog.selector import DEFAULT_PERSONA, PERSONAS

from . import wake

SCHEMA = "anastylos-dialogue-settings@1"
FILE_NAME = "dialogue-settings.json"
_NAME = re.compile(r"[A-Za-z][A-Za-z '\-]{0,23}")


class SettingsError(ValueError):
    pass


def _path(workspace_root: Path) -> Path:
    return Path(workspace_root) / FILE_NAME


def load(workspace_root: Path) -> dict:
    persona, name = DEFAULT_PERSONA, ""
    try:
        data = json.loads(_path(workspace_root).read_text(encoding="utf-8"))
        if data.get("persona") in PERSONAS:
            persona = data["persona"]
        if isinstance(data.get("agent_name"), str) and _NAME.fullmatch(data["agent_name"].strip()):
            name = data["agent_name"].strip()
    except (OSError, ValueError, AttributeError):
        pass
    return {"schema": SCHEMA, "persona": persona, "agent_name": name}


def save(workspace_root: Path, persona: str | None = None, agent_name: str | None = None) -> dict:
    """Update the given fields; an empty ``agent_name`` clears the name. Raises SettingsError on bad input."""
    current = load(workspace_root)
    if persona is not None:
        if persona not in PERSONAS:
            raise SettingsError(f"persona must be one of {', '.join(PERSONAS)}")
        current["persona"] = persona
    if agent_name is not None:
        agent_name = agent_name.strip()
        if agent_name and not _NAME.fullmatch(agent_name):
            raise SettingsError("The name may use letters, spaces, apostrophes and hyphens (at most 24 characters).")
        current["agent_name"] = agent_name
    target = _path(workspace_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".partial")
    partial.write_text(json.dumps(current, indent=1) + "\n", encoding="utf-8", newline="\n")
    os.replace(partial, target)
    return current


def public_view(workspace_root: Path) -> dict:
    """What the phone reads: persona, name, the ASR keywords to send and the spellings accepted as the wake word."""
    current = load(workspace_root)
    name = current["agent_name"]
    return {**current, "personas": list(PERSONAS), "wake_words": wake.wake_names(name),
            "asr_keywords": wake.asr_keywords(name), "mishearings": wake.mishearings(name)}
