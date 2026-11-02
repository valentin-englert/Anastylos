"""The agent name as wake word: ASR keywords, likely mishearings and stripping the wake word from a transcript."""

from __future__ import annotations

import re

DEFAULT_NAMES = ("Muse", "Meta")
FILLERS = ("hey", "ok", "okay", "hi")

# Likely speech-to-text spellings of a name. Curated for the names we know; other names get `_generic`.
KNOWN_MISHEARINGS = {
    "muse": ("mews", "moose", "mues", "mus"),
    "meta": ("metta", "meter", "meda"),
    "herbert": ("hubert", "herbit", "herbet", "albert"),
}


def _generic(name: str) -> list[str]:
    """A few cheap variants for an unknown name: leading h toggled, doubled consonant, trailing s."""
    low = name.lower()
    out = [low[1:] if low.startswith("h") else "h" + low, re.sub(r"(.)\1", r"\1", low), low + "s"]
    return [v for v in out if v and v != low]


def wake_names(name: str) -> list[str]:
    name = (name or "").strip()
    return [name] if name else list(DEFAULT_NAMES)


def mishearings(name: str) -> list[str]:
    found: list[str] = []
    for each in wake_names(name):
        found += list(KNOWN_MISHEARINGS.get(each.lower()) or _generic(each))
    return list(dict.fromkeys(found))


def asr_keywords(name: str) -> list[str]:
    """Words handed to the speech-to-text keyword list: the name itself (never the guessed mishearings)."""
    return wake_names(name)


def strip_wake(transcript: str, name: str) -> tuple[bool, str]:
    """(heard, rest): whether the transcript opens with the wake word or a likely mishearing, and what follows."""
    spellings = sorted({w.lower() for w in wake_names(name)} | set(mishearings(name)), key=len, reverse=True)
    pattern = re.compile(r"^\W*(?:(?:%s)\W+)?(?:%s)\b[\W]*" % ("|".join(FILLERS), "|".join(re.escape(s) for s in spellings)),
                         re.IGNORECASE)
    match = pattern.match(transcript or "")
    return (True, transcript[match.end():].strip()) if match else (False, (transcript or "").strip())
