"""What is in a cell: plain-answer classification, deterministic fallback parser and the transcript check.

The model (or, on timeout, the rules) proposes ``items``. Nothing it proposes is used unless every name and origin
word occurs in what the operator said and the name is a real noun phrase (no question words, no filler, no fragment).
"""

from __future__ import annotations

import re

from .bundle import NUMBER_WORDS, _ARTICLE, _CAME, _SEPARATORS, singular
from .executor import extract_by_rule

QUESTION_WORDS = {"why", "which", "what", "where", "how", "who", "when", "whose", "huh", "pardon", "cell", "cells"}
FILLER = {"the", "a", "an", "is", "are", "was", "were", "there", "only", "one", "part", "parts", "thing", "things", "it", "its",
          "this", "that", "these", "those", "they", "uh", "um", "yes", "yeah", "no", "and", "of", "from", "in", "on", "at", "to",
          "with", "here", "some", "just", "all", "same", "i", "you", "think", "say", "would", "like", "also", "then", "so",
          "well", "okay", "ok", "came", "come", "out", "off", "my", "mm", "mmm", "hmm", "uhh", "umm", "eh", "huh"}
NUMBERS = set(NUMBER_WORDS) | {str(n) for n in range(1, 100)}
OPEN_ENDINGS = {"and", "the", "a", "an", "of", "from", "with", "uh", "um", "is", "are", "there", "to", "in", "on", "at", "or", "but"}
_EMPTY = re.compile(r"^\W*(?:nothing|none|empty|no part|no parts|it'?s empty|(?:there(?:'s| is) )?nothing(?: (?:here|there|in (?:it|there)))?"
                    r"|(?:cell \w+ )?is empty)\W*$", re.IGNORECASE)
_WORD = re.compile(r"[a-z0-9']+")
MAX_NAME_WORDS = 4
UNKNOWN = "unknown"
_REPEATED_CVC = re.compile(r"(?i)(?<![aeiou])([bcdfghjklmnpqrstvwxz][aeiou][bcdfghjklmnpqrstvwxz])\1$")


def words(text: str) -> list[str]:
    return _WORD.findall((text or "").lower().replace("’", "'"))


def classify_plain(text: str) -> str | None:
    """yes | no | skip | unsure | empty | meta | incomplete, or None when the answer needs real understanding."""
    plain = (text or "").strip()
    tokens = words(plain)
    if not tokens:
        return "unclear"
    if _EMPTY.match(plain):
        return "empty"
    intent = extract_by_rule(plain)["intent"]
    if intent in ("yes", "no", "skip", "unsure"):
        return intent
    if tokens[0] in QUESTION_WORDS and (plain.rstrip().endswith("?") or len(tokens) <= 3):
        return "meta"
    if tokens[-1] in OPEN_ENDINGS:
        return "incomplete"
    return None


def classify_confirmation(text: str) -> str | None:
    """Accept only unhedged yes/no phrases; mixed or qualified answers need another turn."""
    plain = classify_plain(text)
    if plain in ("yes", "no"):
        return plain
    tokens = words(text)
    if not tokens:
        return None
    positive = {"yes", "yeah", "yep", "correct", "right", "sure", "ok", "okay", "affirmative"}
    negative = {"no", "nope", "wrong", "incorrect", "negative"}
    if set(tokens) <= positive or tokens == ["that's", "right"]:
        return "yes"
    if set(tokens) <= negative:
        return "no"
    return None


def _forms(word: str) -> set[str]:
    return {word, word + "s", word[:-1] if word.endswith("s") else word}


def _in_transcript(phrase: str, said: set[str]) -> bool:
    tokens = words(phrase)
    return bool(tokens) and all(_forms(t) & said for t in tokens)


def clean_name(name: str | None, said: set[str]) -> str | None:
    """A usable part name or None: grounded in the transcript, 1-4 words, not a question, filler or fragment."""
    if not name:
        return None
    text = _ARTICLE.sub("", name.strip(" ,.;:-!?")).strip()
    text = re.sub(r"^(?:(?:uh|um|the|a|an|in|on|at|it|its|it's|is|was|are|there|that|this)\s+)+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?:\s+(?:uh|um|of|and|the|a|an))+$", "", text, flags=re.IGNORECASE).strip()
    tokens = words(text)
    if not tokens or len(tokens) > MAX_NAME_WORDS or not _in_transcript(text, said):
        return None
    if _has_repeated_cvc(tokens):
        return None
    if any(t.rstrip("s") in QUESTION_WORDS or t in QUESTION_WORDS for t in tokens):
        return None
    content = [t for t in tokens if t not in FILLER and t not in NUMBERS]
    if not content:
        return None
    return singular(text) if tokens[-1].endswith("s") else text


def clean_origin(origin: str | None, said: set[str]) -> str | None:
    if not origin:
        return None
    text = _ARTICLE.sub("", origin.strip(" ,.;:-!?")).strip()
    tokens = words(text)
    if not tokens or len(tokens) > 5 or not _in_transcript(text, said):
        return None
    if _has_repeated_cvc(tokens):
        return None
    if tokens[0] in QUESTION_WORDS or all(t in FILLER for t in tokens):
        return None
    return UNKNOWN if text.lower() in ("unknown", "don't know") else text


def _has_repeated_cvc(tokens: list[str]) -> bool:
    """Flag an immediately repeated consonant-vowel-consonant fragment for clarification, without normalizing it."""
    return any(_REPEATED_CVC.search(token) for token in tokens)


def has_repeated_cvc_fragment(text: str) -> bool:
    """Whether the raw transcript contains a repeated syllable-like artifact that needs clarification."""
    return _has_repeated_cvc(words(text))


def check_items(raw: list[dict], text: str) -> list[dict]:
    """Keep the proposed items that survive the transcript check; [] when any item fails (never a partial guess)."""
    said = set(words(text))
    origin_tail = re.search(r"\b(?:from|out of|off of)\s+(.+)$", text.lower().replace("’", "'"))
    out = []
    for item in raw:
        name = clean_name(item.get("name"), said)
        if name is None:
            return []
        before = text.lower()[:origin_tail.start()] if origin_tail else text.lower()
        if origin_tail and not _in_transcript(name, set(words(before))):
            return []  # the "name" is only ever spoken as the place the part came from
        count = item.get("count")
        stated = isinstance(count, int) and not isinstance(count, bool) and 1 <= count <= 99 and (
            str(count) in said or (count <= 12 and any(NUMBER_WORDS.get(w) == count for w in said)))
        last = words(name)[-1]
        plural = singular(name) != name or (item.get("name") or "").strip().lower().endswith("s") or last + "s" in said
        out.append({"name": name, "count": count if stated else (None if plural else 1),
                    "origin": clean_origin(item.get("origin"), said)})
    return out


def rule_items(text: str) -> list[dict]:
    """Deterministic fallback when the model is unavailable: 'two screws and a left button from the back shell'."""
    plain = text.strip()
    parts = [part for part in _SEPARATORS.split(plain) if part.strip()]
    if not parts:
        return []
    raw = []
    for part in parts:
        match = re.match(r"^\W*(?:there (?:is|are)\s+|it'?s\s+|these are\s+|those are\s+)?(?:(\d{1,2}|" + "|".join(NUMBER_WORDS) + r")\s+)?(.+)$",
                         part, re.IGNORECASE)
        if not match:
            return []
        number = match.group(1)
        rest = match.group(2)
        origin = None
        found = re.match(r"^(.*?)\b(?:from|out of|off of)\s+(.+?)[\s.!?]*$", rest, re.IGNORECASE)
        if found:
            rest, origin = found.group(1), found.group(2)
        rest = _CAME.sub("", rest)
        count = None if number is None else (int(number) if number.isdigit() else NUMBER_WORDS[number.lower()])
        raw.append({"name": rest.strip(" ,.;"), "count": count, "origin": origin})
    if len(raw) > 1 and raw[-1]["origin"] and not any(r["origin"] for r in raw[:-1]):
        for item in raw[:-1]:
            item["origin"] = raw[-1]["origin"]
    return check_items(raw, text)


def total_count(items: list[dict]) -> int | None:
    """Parts the answer names in total, or None while a plural item has no number."""
    if any(item["count"] is None for item in items):
        return None
    return sum(item["count"] for item in items)


def sentence(cell_label: str, items: list[dict]) -> str:
    """'Cell 3: two screws and a left button, from the back shell. Right?' (one sentence, no template talk)."""
    def phrase(item):
        n, name = item["count"], item["name"]
        if n and n > 1:
            number = {v: k for k, v in NUMBER_WORDS.items()}.get(n, str(n))
            return f"{number} {name if name.endswith('s') else name + 's'}"
        return name
    parts = [phrase(i) for i in items]
    listed = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    origins = {i["origin"] for i in items}
    if origins == {None} or origins == {UNKNOWN}:
        tail = ", origin unknown"
    elif len(origins) == 1:
        tail = f", from {origins.copy().pop()}"
    else:
        listed = " and ".join(f"{phrase(i)} from {i['origin'] or UNKNOWN}" for i in items)
        tail = ""
    return f"{cell_label}: {listed}{tail}. Right?"
