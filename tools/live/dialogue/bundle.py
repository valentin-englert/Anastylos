"""Bundled answers: one spoken answer for several small parts that share a cell.

``parse`` turns the operator's words into one name per part (all the same, or listed in the order the parts were
taken out), optional origins and an optional stated count. Names and origins are cut out of the operator's own
words by fixed rules; a part name is never guessed. If no clean name results the answer is "unclear" (re-ask once,
then defer). The coordinator still reads every part back and only a "yes" writes.
"""

from __future__ import annotations

import re

from .executor import _NOISE, extract_by_rule

NUMBER_WORDS = {w: i for i, w in enumerate("one two three four five six seven eight nine ten eleven twelve".split(), 1)}
_COUNT = r"(\d{1,2}|" + "|".join(NUMBER_WORDS) + r")"
_LEAD = re.compile(r"^(?:(?:these|those|there|they|this|that|it)(?:'re|'s)?(?:\s+(?:are|is|were|was))?|all(?:\s+of\s+them)?(?:\s+are)?|"
                   r"the\s+same|same|identical|yes|yeah|yep|okay|ok|well|so)\b[\s,.:;-]*", re.IGNORECASE)
_NO_LEAD = re.compile(r"^(?:no|nope|not really|different|they are different)\b[\s,.:;-]*", re.IGNORECASE)
_ORIGIN = re.compile(r"^(?P<head>.*)\b(?:from|out of|off of)\s+(?P<origin>.+?)[\s.!?]*$", re.IGNORECASE | re.DOTALL)
_CAME = re.compile(r"[\s,.;-]*\b(?:(?:it|they|these|those)\s+)?(?:all\s+)?(?:came|come|were taken|was taken)\s*$", re.IGNORECASE)
_ARTICLE = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)
_SEPARATORS = re.compile(r"\s*(?:,|;|\bthen\b|\band then\b|\band\b|\bnext\b|\bfirst\b|\bsecond\b|\bthird\b|\bfourth\b)\s*", re.IGNORECASE)
_FROM = re.compile(r"^\s*(?:(?:it|they|these|those)\s+)?(?:(?:came|were|was)\s+)?(?:from|out of|off)\s+", re.IGNORECASE)
_COUNTED = re.compile(r"^" + _COUNT + r"\s+(?P<rest>.+)$", re.IGNORECASE)
_NOT_PLURAL = {"lens", "glass", "brass", "gas", "bus", "plus"}
MAX_NAME_WORDS = 4


def parse_count(text: str) -> int | None:
    """'five', 'no, five screws', '5' -> 5; anything else None."""
    found = re.search(r"\b" + _COUNT + r"\b", text or "", re.IGNORECASE)
    if not found:
        return None
    token = found.group(1).lower()
    return int(token) if token.isdigit() else NUMBER_WORDS[token]


def singular(name: str) -> str:
    """Plural noun phrase -> singular by fixed rules ('screws' -> 'screw'); other words stay as said."""
    head, _, last = name.rpartition(" ")
    low = last.lower()
    if low in _NOT_PLURAL or len(low) < 4 or not low.endswith("s") or low.endswith(("ss", "us")):
        return name
    if low.endswith("ies"):
        last = last[:-3] + "y"
    elif low.endswith(("ches", "shes", "xes")):
        last = last[:-2]
    else:
        last = last[:-1]
    return f"{head} {last}".strip()


def _clean_name(text: str) -> str | None:
    text = text.strip()
    for _ in range(4):
        stripped = _LEAD.sub("", text, count=1).strip()
        if stripped == text:
            break
        text = stripped
    text = _CAME.sub("", text).strip(" ,.;:-!?")
    text = re.sub(r"\s*\b(?:from|out of)$", "", text, flags=re.IGNORECASE).strip(" ,.;:-!?")
    text = _ARTICLE.sub("", text).strip()
    words = re.findall(r"[A-Za-z0-9'-]+", text)
    if not words or len(words) > MAX_NAME_WORDS or set(w.lower() for w in words) <= _NOISE:
        return None
    return text


def split_name_origin(text: str) -> tuple[str | None, str | None]:
    """'there are all screws, it came from the back shell' -> ('screws', 'back shell')."""
    match = _ORIGIN.match(text.strip())
    head, origin = (match.group("head"), match.group("origin")) if match else (text, None)
    if origin:
        origin = _ARTICLE.sub("", _FROM.sub("", origin).strip(" ,.;:-!?")).strip() or None
    return _clean_name(head), origin


def parse(text: str, count: int, list_mode: bool = False) -> dict:
    """{kind: same|list|different|unsure|skip|unclear, names, origins, count?} for ``count`` detected parts."""
    plain = (text or "").strip()
    first = extract_by_rule(plain)["intent"]
    if first in ("unsure", "skip"):
        return {"kind": first}
    if not plain or first == "unclear":
        return {"kind": "unclear"}
    negated = _NO_LEAD.match(plain)
    if negated and not _NO_LEAD.sub("", plain, count=1).strip():
        return {"kind": "different"}
    if negated:
        plain, list_mode = _NO_LEAD.sub("", plain, count=1).strip(), True
    if first == "yes":
        return {"kind": "unclear"}  # a bare "yes" says they are the same, but not what they are
    has_lead = bool(_LEAD.match(plain))
    if has_lead or not _SEPARATORS.search(plain):
        stated, body = None, _LEAD.sub("", plain, count=1).strip() if has_lead else plain
        counted = _COUNTED.match(body)
        if counted:
            token = counted.group(1).lower()
            stated, body = (int(token) if token.isdigit() else NUMBER_WORDS[token]), counted.group("rest")
        name, origin = split_name_origin(body)
        if not name:
            return {"kind": "unclear"}
        name = singular(name)
        return {"kind": "same", "names": [name] * count, "origins": [origin] * count, "count": stated}
    items = [part for part in _SEPARATORS.split(plain) if part.strip()]
    if len(items) == count:
        pairs = [split_name_origin(part) for part in items]
        if all(name for name, _ in pairs):
            return {"kind": "list", "names": [n for n, _ in pairs], "origins": [o for _, o in pairs], "count": None}
    return {"kind": "unclear"}


def parse_origin(text: str) -> dict:
    """The answer to 'where did they come from?': {kind: origin|unsure|skip|unclear, origin}."""
    plain = (text or "").strip()
    intent = extract_by_rule(plain)["intent"]
    if intent in ("unsure", "skip"):
        return {"kind": intent}
    match = _ORIGIN.match(plain)
    candidate = match.group("origin") if match else _FROM.sub("", plain)
    origin = _ARTICLE.sub("", candidate.strip(" ,.;:-!?")).strip()
    words = re.findall(r"[A-Za-z0-9'-]+", origin)
    if intent == "unclear" or not words or len(words) > 5 or set(w.lower() for w in words) <= _NOISE:
        return {"kind": "unclear"}
    return {"kind": "origin", "origin": origin}


def _number_word(count: int) -> str:
    words = {v: k for k, v in NUMBER_WORDS.items()}
    return words.get(count, str(count))


def _plural(name: str, count: int) -> str:
    return name if count == 1 or name.lower().endswith("s") else name + "s"


def combined_read_back(where: str, names: list[str], origins: list[str], count: int | None = None) -> str:
    """One short claim for the whole cell: 'Four screws in cell 1, from the back shell. Is that right?'

    Identical parts are counted, not repeated. ``count`` (the operator's corrected number) applies only when every
    part has the same name and origin.
    """
    groups: list[list] = []
    for name, origin in zip(names, origins):
        if groups and groups[-1][0] == name and groups[-1][1] == origin:
            groups[-1][2] += 1
        else:
            groups.append([name, origin, 1])
    if count is not None and len(groups) == 1:
        groups[0][2] = count
    one_origin = len({g[1] for g in groups}) == 1
    items = [f"{_number_word(n)} {_plural(name, n)}" for name, _origin, n in groups]
    if not one_origin:
        items = [f"{item} from {g[1]}" for item, g in zip(items, groups)]
    tail = f", from {groups[0][1]}" if one_origin else ""
    sentence = f"{', '.join(items)} in {where}{tail}. Is that right?"
    return sentence[0].upper() + sentence[1:]


def split_items(text: str) -> list[str]:
    """The parts named in one list answer ('front shell, back shell and PCB'), each in the operator's words."""
    return [part.strip(" .") for part in _SEPARATORS.split(text or "") if part.strip(" .")]
