"""Closed-set intent matcher for questions about one selected part (typed or transcribed voice).

Two intents exist and nothing else is guessed:

- ``origin``: where the part came from / belongs (the install origin recorded at removal)
- ``storage``: where the part lies now (the storage place recorded after removal)

Matching is deterministic: the question is normalised (case, punctuation, politeness
prefixes) and must fully match one of the listed English phrasings. Anything else is
``None``; the caller then answers with the supported phrasings and does not guess.
"""

from __future__ import annotations

import re

ORIGIN, STORAGE = "origin", "storage"

_SUBJECT = r"(?:this part|that part|the part|the selected part|selected part|this one|that one|this|that|it)"
_PREFIX = re.compile(r"^(?:(?:hey|ok|okay|please|so|and|now)\s+)*(?:(?:can|could|would) you\s+)?(?:please\s+)?"
                     r"(?:(?:just )?tell me\s+)?")

# phrase id -> (intent, full-match pattern on the normalised question)
PHRASINGS: dict[str, tuple[str, re.Pattern]] = {
    "belong": (ORIGIN, re.compile(rf"where (?:does|do) {_SUBJECT} belong(?: to)?")),
    "go": (ORIGIN, re.compile(rf"where (?:does|do|should) {_SUBJECT} go(?: back| in| again)?")),
    "install": (ORIGIN, re.compile(rf"where (?:does|do|should) {_SUBJECT} "
                                   rf"(?:get )?(?:be )?(?:put back|installed|reinstalled|mounted|fit|fitted)")),
    "was": (ORIGIN, re.compile(rf"where (?:was|were) {_SUBJECT}(?: originally| before| mounted| installed)?")),
    "taken_from": (ORIGIN, re.compile(rf"where (?:was|were|did) {_SUBJECT} "
                                      rf"(?:(?:taken|removed|come) from|come from)")),
    "put": (STORAGE, re.compile(rf"where did (?:i|you|we) (?:put|place|leave|store) {_SUBJECT}(?: down| away)?")),
    "have_put": (STORAGE, re.compile(rf"where (?:have|had) (?:i|you|we) (?:put|placed|left|stored) {_SUBJECT}")),
    "is": (STORAGE, re.compile(rf"where(?: is|'s) {_SUBJECT}(?: now| right now| stored| lying| located)?")),
    "is_stored": (STORAGE, re.compile(rf"where is {_SUBJECT} (?:stored|lying|kept|located)")),
}

SUPPORTED = ("where does this part belong", "where does it go", "where was this", "where did this come from",
             "where did I put it", "where is it")


def normalise(question: str) -> str:
    text = re.sub(r"[^a-z0-9' ]+", " ", question.lower().replace("’", "'"))
    text = re.sub(r"\s+", " ", text).strip()
    return _PREFIX.sub("", text).strip()


def match(question: str) -> dict | None:
    """``{"intent", "phrase"}`` for a supported phrasing, else ``None``."""
    text = normalise(question or "")
    for phrase, (intent, pattern) in PHRASINGS.items():
        if pattern.fullmatch(text):
            return {"intent": intent, "phrase": phrase}
    return None
