"""Deterministic phrase selection and slot filling for the question catalog.

The variant is a pure function of (event id, question type, persona), moved forward past variants used
recently. Slots are filled only from verified data: an unknown cell is spoken as "the sheet", never guessed.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

PERSONAS = ("serious", "playful", "unhinged")
DEFAULT_PERSONA = "serious"
QUESTION_TYPES = ("part_removed", "origin_unseen", "part_stored", "unclear_answer", "defer_to_desktop",
                  "need_photo", "read_back", "recorded", "bundle_same", "bundle_list",
                  "assemblies_left", "missed_anything", "missed_cell")
UNKNOWN_PLACE = "the sheet"
RECENT_WINDOW = 3

_CELL = re.compile(r"[1-9][0-9]{0,2}")
_SLOT = re.compile(r"\{([a-z_]+)\}")

CATALOG = json.loads(Path(__file__).with_name("phrases.json").read_text(encoding="utf-8"))


def where_words(cell) -> str:
    """'cell 4' for a verified cell number, otherwise 'the sheet'."""
    text = str(cell).strip().removeprefix("cell-") if cell is not None else ""
    return f"cell {text}" if _CELL.fullmatch(text) else UNKNOWN_PLACE


def variants(question_type: str, persona: str) -> list[str]:
    return CATALOG["phrases"][question_type][persona]


def pick_index(event_id: str, question_type: str, persona: str, recent: tuple[int, ...] = ()) -> int:
    count = len(variants(question_type, persona))
    digest = hashlib.sha256(f"{event_id}|{question_type}|{persona}".encode("utf-8")).digest()
    start = int.from_bytes(digest[:4], "big") % count
    for step in range(count):
        index = (start + step) % count
        if index not in recent:
            return index
    return start


def render(question_type: str, persona: str, event_id: str, slots: dict[str, str],
           recent: tuple[int, ...] = ()) -> tuple[str, int]:
    """Spoken text and the variant index used. Raises KeyError when a required slot is missing."""
    persona = persona if persona in PERSONAS else DEFAULT_PERSONA
    needed = CATALOG["slots"][question_type]
    missing = [name for name in needed if name not in slots]
    if missing:
        raise KeyError(f"{question_type} needs slots {missing}")
    index = pick_index(event_id, question_type, persona, recent)
    template = variants(question_type, persona)[index]
    return _SLOT.sub(lambda m: str(slots[m.group(1)]), template), index


class Recents:
    """The last few variant indexes per (type, persona), so a persona does not repeat itself back to back."""

    def __init__(self):
        self._used: dict[tuple[str, str], list[int]] = {}

    def recent(self, question_type: str, persona: str) -> tuple[int, ...]:
        return tuple(self._used.get((question_type, persona), ()))

    def note(self, question_type: str, persona: str, index: int) -> None:
        used = self._used.setdefault((question_type, persona), [])
        used.append(index)
        del used[:-RECENT_WINDOW]
