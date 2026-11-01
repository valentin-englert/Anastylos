"""English L0 wording, interpretation, read-back and rubric: the product language (English-first).

The German path (``templates.py``, ``interpret.py``) stays unchanged as the historical FULL-01 / H3-R0
regression; this module mirrors it for English operators. It uses the same facts (committed anchors of
the same phase, operator-supplied phase words, candidates and their screen tokens) and the same
answers.py answer shape, so only the words differ. Operator labels and places are kept verbatim,
whatever language the operator used; they are evidence, not verified identities.
"""

from __future__ import annotations

import re

from interpret import interpretation, normalize
from templates import MACHINE_ID, MAX_SPOKEN_CANDIDATES, MM, NEAR_MM, TIME, MissingPhaseWords, cap, centroid, mmss

NUMBER_WORDS = ["one", "two", "three", "four", "five"]
MAX_SPOKEN_WORDS = 40  # R7
SCREEN_WORD = "screen"

MESSAGES = {
    "deferred": "OK, I will ask again later.",
    "unclear": "I did not understand that. ",
    "plan_rejected": "That is not enough for a record. ",
    "asked_again": "Then once more. ",
    "yes_or_no": "Please answer yes or no. ",
    "stale": "This question changed (revision {revision}); it will be asked again.",
    "duplicate": "This input was already handled; ignored.",
    "foreign": "This answer ({op_key}) does not belong to the current session.",
    "ignored": "The question is {state}; there is nothing to answer.",
}

# ------------------------------------------------------------------ wording


def direction(frm, to) -> str:
    """Camera-image direction of `to` seen from `frm` (sheet mm, +x right, +y down)."""
    dx, dy = to[0] - frm[0], to[1] - frm[1]
    if (dx * dx + dy * dy) ** 0.5 <= NEAR_MM:
        return "close to"
    horiz = "right of" if dx > 0 else "left of"
    vert = "below" if dy > 0 else "above"
    if abs(dx) > 2 * abs(dy):
        return horiz
    if abs(dy) > 2 * abs(dx):
        return vert
    return f"{vert} and {horiz}"


def part_words(anchor) -> str:
    if anchor["basis"] == "automatic":
        return f"the part from cell {anchor['cell']}"
    return f"the part “{anchor['label']}”" if anchor["label"] else "the part you named before"


def place_words(anchor) -> str:
    if anchor["basis"] == "automatic":
        return f"where the part in cell {anchor['cell']} came from"
    return f"where “{anchor['label']}” was" if anchor["label"] else "where the part you named before was"


def describe_candidate(cand, anchors_geo) -> str | None:
    c = centroid([s.get("centroid_mm") for s in cand["sources"]])
    if c is None or not anchors_geo:
        return None  # not describable in words: the option goes to the screen, never into speech
    best = min(anchors_geo, key=lambda a: (a["xy"][0] - c[0]) ** 2 + (a["xy"][1] - c[1]) ** 2)
    rel = direction(best["xy"], c)
    return f"{rel} {place_words(best)}"  # camera-image directions; the spoken tail says so once


def _window(question: dict) -> list[float]:
    return question.get("deposit_window_s") or question.get("window_s")


def build_prompt(question: dict, parts_report: dict, phase_words: dict, anchors: list[dict], claimed: dict[str, str]) -> dict:
    """English L0 wording of one question; same structure and facts as ``templates.build_prompt``."""
    tag = question["phase_tag"]
    if not str(phase_words.get(tag) or "").strip():
        raise MissingPhaseWords(f"no phase words for {tag}; the operator setup must supply them")
    phase = phase_words[tag]
    window = _window(question)
    past = sorted([a for a in anchors if a["t"] is not None and a["t"] < window[0]], key=lambda a: a["t"])
    prev = past[-1] if past else None
    geo = [a for a in past if a.get("xy")]
    cands = question.get("candidates", [])
    screen = []
    if question["kind"] == "source_of_part":
        cell = question["cell"].split("-")[1]
        same_cell = any(p["cell"] == question["cell"] and p["deposit_window_s"][0] < window[0] for p in parts_report["parts"])
        what = ("also " if same_cell else "") + f"put in cell {cell}"
        lead = (f"{cap(phase)}: what did you take out after {part_words(prev)} and {what}?"
                if prev else f"{cap(phase)}: where did the first part you {what} come from?")
        described = [describe_candidate(c, geo) for c in cands]
        open_idx = [i for i, c in enumerate(cands) if c["removal_event"] not in claimed]
        spoken = [described[i] for i in open_idx]
        speakable = (0 < len(open_idx) <= MAX_SPOKEN_CANDIDATES and all(d is not None for d in spoken)
                     and len(set(spoken)) == len(spoken))
        if speakable:
            spoken_tail = " In the camera image: " + "; ".join(
                f"{NUMBER_WORDS[n]}, {described[i]}" for n, i in enumerate(open_idx)) + ". Or describe it."
            # options that do not fit the spoken limit (R7) go to the screen instead of being cut
            speakable = len((lead + spoken_tail).split()) <= MAX_SPOKEN_WORDS
        number_of = {i: NUMBER_WORDS[n] for n, i in enumerate(open_idx)} if speakable else {}
        for i, c in enumerate(cands):
            screen.append({"token": chr(ord("A") + i), "removal_event": c["removal_event"], "description": described[i],
                           "spoken_number": number_of.get(i), "claimed_by": claimed.get(c["removal_event"])})
        if not cands:
            mode, tail = "open", ""
        elif speakable:
            mode = "spoken_options"
            tail = spoken_tail
        else:
            mode, tail = "open_with_screen_options", f" The possible spots are marked on the {SCREEN_WORD}."
        text = lead + tail + f" (placed at about {mmss(window[0])})"
        facts = {"phase_words": phase, "cell": cell, "time": mmss(window[0]),
                 "options": [s["description"] for s in screen if s["spoken_number"]]}
    else:
        where = describe_candidate({"sources": question["sources"]}, geo)
        at = f" {where}" if where else ""
        lead = (f"{cap(phase)}: after {part_words(prev)}, you took something out{at} "
                "but did not put it in a cell. Where did you put it?") if prev else \
               (f"{cap(phase)}: you took something out{at} but did not put it in a cell. Where did it go?")
        text, mode = lead + f" (at about {mmss(window[0])})", "destination"
        facts = {"phase_words": phase, "time": mmss(window[0]), "where": where}
    if prev:
        facts["anchor"] = part_words(prev)
    facts["labels"] = sorted({a["label"] for a in anchors if a.get("label")})
    facts["claimed_labels"] = sorted(set(claimed.values()))
    return {"question_id": question["id"], "kind": question["kind"], "mode": mode, "text": text, "screen_options": screen,
            "anchor": prev["ref"] if prev else None, "anchor_basis": prev["basis"] if prev else None,
            "anchor_possible": prev is not None, "facts": facts}


# ------------------------------------------------------------------ rubric (English counterpart of R1-R7)

YESNO_SINGLE = re.compile(r"\bdid it come from\b|\bwas it\b", re.IGNORECASE)
# Function words that mark German text; an English product question must not carry them (model leakage).
_GERMAN = re.compile(r"\b(?:der|die|das|und|nicht|hast|du|wo|woher|wohin|zelle|bildschirm|teil|beim|aus|von|mit|ist)\b",
                     re.IGNORECASE)
GERMAN_WORDS_ALLOWED = 1


def german_words(text: str, quoted_ok: bool = True) -> list[str]:
    """German function words outside quoted operator labels (labels stay verbatim, in any language)."""
    outside = re.sub(r"“[^”]*”|\"[^\"]*\"", " ", text) if quoted_ok else text
    return _GERMAN.findall(outside)


def rubric(text: str, anchor_possible: bool, has_anchor: bool, phase_words: str | None) -> dict:
    """R1-R7 for English questions. R2 checks the operator's own phase words, not a German keyword.
    R8 (English only): no German function words outside quoted labels."""
    main, _, _ = text.partition("(")
    checks = {
        "R1_no_mm": not MM.search(text),
        "R2_phase_in_words": bool(phase_words) and phase_words.casefold() in text.casefold(),
        "R3_anchor_when_possible": has_anchor or not anchor_possible,
        "R4_time_only_supporting": not TIME.search(main),
        "R5_no_single_candidate_suggestion": not YESNO_SINGLE.search(text),
        "R6_no_machine_ids": not MACHINE_ID.search(text),
        "R7_spoken_part_at_most_40_words": len(main.split()) <= 40,
        "R8_english": len(german_words(text)) <= GERMAN_WORDS_ALLOWED,
    }
    checks["pass"] = all(checks.values())
    return checks


# ------------------------------------------------------------------ interpretation

YES, NO, SKIP, UNSURE, NOT_A_PART = "yes", "no", "skip", "unsure", "not_a_part"
_YES = {"yes", "y", "yes correct", "correct", "right", "that's right", "that is right", "thats right", "confirm",
        "confirmed", "ok", "okay", "yes that's right", "yes that is right", "yep", "yeah",
        # spoken read-back replies as transcribed (punctuation is stripped by _key)
        "that is correct", "that's correct", "thats correct", "yes that is correct", "yes that's correct",
        "yes thats correct", "yes it is", "yes please"}
_NO = {"no", "n", "wrong", "incorrect", "not correct", "that's wrong", "that is wrong", "no that's wrong", "nope",
       "that is not correct", "that's not correct", "no that is wrong", "no that is not correct", "no that's not right"}
_SKIP = {"skip", "later", "ask later", "ask me later", "next", "next question"}
_UNSURE = {"i don't know", "i dont know", "don't know", "dont know", "not sure", "i'm not sure", "im not sure",
           "unsure", "no idea"}
_NOT_A_PART = {"not a part", "that's not a part", "that is not a part", "it's not a part", "it is not a part",
               "no part", "that was not a part"}
_NUMBER = re.compile(r"(?:number |option |choice )?(one|two|three|four|five|[1-5])")
_LETTER = re.compile(r"(?:letter |option |marker |spot )?([a-e])")
_LABEL_SOURCE = re.compile(r"^(?P<label>.+?)\s(?:from|out of)\s(?P<place>.+)$", re.IGNORECASE)
_LABEL_DEST = re.compile(r"^(?P<label>.+?)\s(?:is in|is on|is at|went to|went into|into|in|on|at|to)\s(?P<place>.+)$",
                         re.IGNORECASE)
_CELL = re.compile(r"^(?:in |into |to |on )?(?:the )?(?:tray )?(?:cell|compartment)[ -]?(\d{1,2})$", re.IGNORECASE)
MAX_LABEL_WORDS = 6


def _key(text: str) -> str:
    return re.sub(r"[,.!?;:]", "", normalize(text).lower()).replace("’", "'").strip()


def interpret_read_back(item: dict) -> dict:
    """yes / no to a read-back; anything else is unclear (the read-back is repeated, nothing is written)."""
    if item["source"] == "screen_choice":
        return interpretation({YES: "confirm", NO: "reject"}.get(item["choice"], "unclear"))
    key = _key(item["text"])
    if key in _YES:
        return interpretation("confirm")
    if key in _NO:
        return interpretation("reject")
    return interpretation("unclear")


def interpret_answer(item: dict, kind: str, prompt: dict) -> dict:
    """Interpret an English answer to a question whose L0 prompt (with its screen options) the operator saw."""
    tokens = {o["token"] for o in prompt.get("screen_options", [])}
    if item["source"] == "screen_choice":
        token = item["choice"]
        if token in tokens:
            return interpretation("choose", choice=token)
        fixed = {SKIP: interpretation("skip"), UNSURE: interpretation("unsure"),
                 NOT_A_PART: interpretation("describe_place", not_a_part=True)}
        return fixed.get(token, interpretation("unclear"))
    text = normalize(item["text"])
    key = _key(text)
    if not key:
        return interpretation("unclear")
    if key in _SKIP:
        return interpretation("skip")
    if key in _UNSURE:
        return interpretation("unsure")
    if key in _NOT_A_PART:
        return interpretation("describe_place", not_a_part=True)
    if key in _YES or key in _NO:
        return interpretation("unclear")  # a bare yes/no is no answer to an open question
    if kind == "source_of_part":
        if _NUMBER.fullmatch(key) or _LETTER.fullmatch(key):
            token = _option_token(key, prompt)
            return interpretation("choose", choice=token) if token else interpretation("unclear")
        label, place = _split(text, _LABEL_SOURCE)
        token = _option_token(_key(place), prompt) if label else None
        if token:
            return interpretation("choose", choice=token, label=label)  # "L button from B" / "L button from two"
        return interpretation("describe_place", place=place, label=label)
    label, place = _split(text, _LABEL_DEST)
    return interpretation("describe_place", place=place, label=label)


def _option_token(key: str, prompt: dict) -> str | None:
    options = prompt.get("screen_options", [])
    number = _NUMBER.fullmatch(key)
    if number:
        word = number.group(1)
        word = NUMBER_WORDS[int(word) - 1] if word.isdigit() else word
        return next((o["token"] for o in options if o["spoken_number"] == word), None)
    letter = _LETTER.fullmatch(key)
    if letter:
        return next((o["token"] for o in options if o["token"] == letter.group(1).upper()), None)
    return None


def _split(text: str, pattern: re.Pattern) -> tuple[str | None, str]:
    match = pattern.match(text)
    if match and len(match.group("label").split()) <= MAX_LABEL_WORDS and match.group("place").strip():
        return match.group("label").strip(), match.group("place").strip()
    return None, text


def destination_value(place: str) -> str:
    """'cell 4' -> 'cell-4' (the memory's cell location); any other place stays verbatim."""
    cell = _CELL.match(place.strip())
    return f"cell-{int(cell.group(1))}" if cell else place.strip()


# ------------------------------------------------------------------ read-back


def _place_words(location: str) -> str:
    cell = re.fullmatch(r"cell-(\d+)", location)
    return f"in cell {cell.group(1)}" if cell else f"at “{location}”"


def read_back(question: dict, answer: dict, prompt: dict) -> str:
    """What the coordinator says back before anything is written; the operator must answer yes."""
    name = f"“{answer['label']}”" if answer.get("label") else "a part without a name"
    if answer["not_a_part"]:
        where = _place_words(question["cell"]) if question["kind"] == "source_of_part" else "this spot"
        return (f"I will record: what is {where} is not a part of the disassembly. "
                "Is that correct? Answer yes or no.")
    if question["kind"] == "source_of_part":
        if answer["choice"]:
            option = next(o for o in prompt["screen_options"] if o["removal_event"] == answer["choice"])
            origin = (f"from option {option['spoken_number']} ({option['description']})" if option["spoken_number"]
                      else f"from the spot marked {option['token']} on the {SCREEN_WORD}")
            warn = (f" Note: you already named this spot for “{option['claimed_by']}”."
                    if option.get("claimed_by") else "")
        else:
            origin, warn = f"from “{answer['source']}”", ""
        return f"I will record: {name} came {origin} and is {_place_words(question['cell'])}.{warn} Is that correct? Answer yes or no."
    return (f"I will record: {name} was taken out and is now {_place_words(answer['destination'])}. "
            "Is that correct? Answer yes or no.")
