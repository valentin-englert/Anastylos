"""Bundled answers: one spoken answer for several small parts that share a cell.

``parse`` turns the operator's words into one name per part (all the same, or listed in the order the parts were
taken out) plus optional origins. Names and origins are the operator's own words; nothing is added. The coordinator
still reads every part back and only a "yes" writes.
"""

from __future__ import annotations

import re

from .executor import extract_by_rule, ground

_SAME_LEAD = re.compile(r"^(?:yes|yeah|yep|same|all(?: of them)?(?: are| the same)?|they(?:'re| are)(?: all)?(?: the same)?|"
                        r"all the same|the same|identical)\b[\s,.:;-]*", re.IGNORECASE)
_SEPARATORS = re.compile(r"\s*(?:,|;|\bthen\b|\band then\b|\band\b|\bnext\b|\bfirst\b|\bsecond\b|\bthird\b|\bfourth\b)\s*",
                         re.IGNORECASE)
_NO_LEAD = re.compile(r"^(?:no|nope|not really|different|they are different)\b[\s,.:;-]*", re.IGNORECASE)
_FROM = re.compile(r"^\s*(?:they (?:came|were)\s+)?(?:from|out of|off)\s+", re.IGNORECASE)


def _item(text: str) -> dict:
    fields, _dropped = ground(extract_by_rule(text), text)
    return fields


def parse(text: str, count: int, list_mode: bool = False) -> dict:
    """{kind: same|list|different|unsure|skip|unclear, names: [..], origins: [..|None]} for ``count`` parts."""
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
    same = _SAME_LEAD.match(plain)
    if same and first != "yes":
        rest = plain[same.end():].strip()
        while _SAME_LEAD.match(rest) and _SAME_LEAD.sub("", rest, count=1).strip():
            rest = _SAME_LEAD.sub("", rest, count=1).strip()
        fields = _item(rest) if rest else {"intent": "unclear"}
        if fields["intent"] != "answer" or not fields["part_name"]:
            return {"kind": "unclear"}
        return {"kind": "same", "names": [fields["part_name"]] * count, "origins": [fields["origin"]] * count}
    if first == "yes":
        return {"kind": "unclear"}  # a bare "yes" says they are the same, but not what they are
    items = [part for part in _SEPARATORS.split(plain) if part.strip()]
    if len(items) == count:
        fields = [_item(part) for part in items]
        if all(f["intent"] == "answer" and f["part_name"] for f in fields):
            return {"kind": "list", "names": [f["part_name"] for f in fields], "origins": [f["origin"] for f in fields]}
    if list_mode and len(items) == 1:
        fields = _item(items[0])
        if fields["intent"] == "answer" and fields["part_name"]:
            return {"kind": "same", "names": [fields["part_name"]] * count, "origins": [fields["origin"]] * count}
    return {"kind": "unclear"}


def parse_origin(text: str) -> dict:
    """The answer to 'where did they come from?': {kind: origin|unsure|skip|unclear, origin}."""
    plain = (text or "").strip()
    intent = extract_by_rule(plain)["intent"]
    if intent in ("unsure", "skip"):
        return {"kind": intent}
    stripped = _FROM.sub("", plain).strip(" .,!?")
    fields, _ = ground({"intent": "answer", "part_name": stripped or None, "origin": None, "storage": None}, plain)
    if intent == "unclear" or not fields["part_name"]:
        return {"kind": "unclear"}
    return {"kind": "origin", "origin": fields["part_name"]}


def combined_read_back(where: str, names: list[str], origins: list[str]) -> str:
    """One sentence for the whole bundle; every part keeps its own name and origin wording."""
    parts = "; ".join(f"“{name}” from “{origin}”" for name, origin in zip(names, origins))
    return f"I will record {len(names)} parts in {where}: {parts}. Is that correct? Answer yes or no."
