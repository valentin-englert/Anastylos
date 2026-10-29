"""H3 rubric R1–R7 for one delivered question text (docs/LIVE-01.md §6.3).

Ported unchanged in meaning from ``tools/live/seed/h3r0.py`` (the seed stays as provenance). Deterministic, no
model: every check is a regular expression or a word count on the text the operator actually saw.
"""

from __future__ import annotations

import re

MM = re.compile(r"\d+(?:[.,]\d+)?\s?mm\b|\b\d{2,3}/\d{2,3}\b")
MACHINE_ID = re.compile(r"\b(?:cell-\d+|[ELQ]-\d{4}|SRC-\d+|obs-\d+|dec-\d+|Q-P\d)\b")
TIME = re.compile(r"\d+[.,]\d+\s?s\b|\d+:\d{2}")
PHASE = re.compile(r"\bbeim\b", re.IGNORECASE)
YESNO_SINGLE = re.compile(r"\bKam es (?:aus|von)\b|\bWar es\b", re.IGNORECASE)
MAX_SPOKEN_WORDS = 40


def rubric(text: str, anchor_possible: bool, has_anchor: bool) -> dict:
    """R1–R7 for one question; the part before the first "(" is the spoken part, the rest is supporting."""
    main, _, _support = text.partition("(")
    checks = {
        "R1_no_mm": not MM.search(text),
        "R2_phase_in_words": bool(PHASE.search(text)),
        "R3_anchor_when_possible": has_anchor or not anchor_possible,
        "R4_time_only_supporting": not TIME.search(main),
        "R5_no_single_candidate_suggestion": not YESNO_SINGLE.search(text),
        "R6_no_machine_ids": not MACHINE_ID.search(text),
        "R7_spoken_part_at_most_40_words": len(main.split()) <= MAX_SPOKEN_WORDS,
    }
    checks["pass"] = all(checks.values())
    return checks
