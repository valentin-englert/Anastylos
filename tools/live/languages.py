"""Operator language of a live session: wording, interpretation, read-back, rubric and messages.

English (``en``) is the product language. German (``de``) is kept, unchanged, for the historical
FULL-01 / H3-R0 regression: its templates and rubric are the preregistered ones in ``seed/h3r0.py``.
The language never depends on a model being available: L0 exists in every language.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import english
import interpret
import templates

DE_MESSAGES = {
    "deferred": "Gut, ich frage später noch einmal.",
    "unclear": "Das habe ich nicht verstanden. ",
    "plan_rejected": "Das reicht mir nicht für einen Eintrag. ",
    "asked_again": "Dann bitte noch einmal. ",
    "yes_or_no": "Bitte nur ja oder nein. ",
    # status messages kept as lane A wrote them (the historical regression asserts on them)
    "stale": "the question changed (revision {revision}); ask it again",
    "duplicate": "this input was already handled; ignored",
    "foreign": "op_key {op_key} does not belong to this live session",
    "ignored": "question is {state}; nothing to answer",
}


@dataclass(frozen=True)
class Language:
    code: str
    build_prompt: Callable
    rubric: Callable  # (text, anchor_possible, has_anchor, phase_words) -> checks
    interpret_answer: Callable
    interpret_read_back: Callable
    read_back: Callable
    destination: Callable
    screen_word: str
    messages: dict
    check_language: Callable  # text -> list of foreign words (empty when the text is in this language)


GERMAN = Language("de", templates.build_prompt, lambda text, possible, has, _phase: templates.rubric(text, possible, has),
                  interpret.interpret_answer, interpret.interpret_read_back, interpret.read_back,
                  interpret.destination_value, "Bildschirm", DE_MESSAGES, lambda text: [])
ENGLISH = Language("en", english.build_prompt, english.rubric, english.interpret_answer, english.interpret_read_back,
                   english.read_back, english.destination_value, english.SCREEN_WORD, english.MESSAGES,
                   english.german_words)
LANGUAGES = {"de": GERMAN, "en": ENGLISH}


def language(code: str) -> Language:
    if code not in LANGUAGES:
        raise ValueError(f"unknown operator language {code!r}; supported: {sorted(LANGUAGES)}")
    return LANGUAGES[code]
