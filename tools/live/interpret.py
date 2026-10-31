"""Deterministic (L0) interpretation of ``anastylos-live-operator-input@1``.

Typed text, a screen choice and a speech transcript are treated identically once interpreted.
The interpretation has the shape of the ``answer_interpretation`` task output (intent, choice,
place_text, label_text) so an L1 proposal and the L0 result are interchangeable; it is turned
into an ``answers.py``-compatible answer plus a read-back text. Nothing is guessed: anything the
rules do not recognise is ``unclear`` and the question is asked again.
"""

from __future__ import annotations

import re

from templates import NUMBER_WORDS

INPUT_SCHEMA = "anastylos-live-operator-input@1"
SOURCES = ("typed", "screen_choice", "speech_transcript")
# Screen tokens that are not candidates (the PWA's fixed buttons).
YES, NO, SKIP, UNSURE, NOT_A_PART = "yes", "no", "skip", "unsure", "not_a_part"

_YES = {"ja", "jawohl", "stimmt", "ja stimmt", "ja genau", "genau", "richtig", "ja richtig", "korrekt", "bestätigt",
        "ok", "okay", "passt", "ja passt", "ja das stimmt", "das stimmt"}
_NO = {"nein", "falsch", "stimmt nicht", "das stimmt nicht", "nicht richtig", "nein falsch", "nee", "nö"}
_SKIP = {"überspringen", "später", "weiter", "nächste frage", "skip"}
_UNSURE = {"weiß nicht", "weiss nicht", "ich weiß nicht", "ich weiss nicht", "keine ahnung", "unsicher", "nicht sicher"}
_NOT_A_PART = {"kein teil", "das ist kein teil", "das war kein teil", "war kein teil", "gar kein teil"}
_NUMBER = re.compile(r"(?:nummer |option |möglichkeit )?(eins|zwei|drei|vier|fünf|[1-5])")
_LETTER = re.compile(r"(?:buchstabe |option |markierung )?([a-e])")
_LABEL_SOURCE = re.compile(r"^(?P<label>.+?)\s(?:aus|von)\s(?P<place>.+)$", re.IGNORECASE)
_LABEL_DEST = re.compile(r"^(?P<label>.+?)\s(?:liegt in|liegt auf|liegt bei|in|nach|auf|zu)\s(?P<place>.+)$", re.IGNORECASE)
_CELL = re.compile(r"^(?:in |nach |auf )?(?:die |der )?(?:zelle|fach|cell)[ -]?(\d{1,2})$", re.IGNORECASE)
MAX_LABEL_WORDS = 6


class InputError(ValueError):
    """The operator input object does not follow its schema."""


def check_input(item: dict) -> None:
    if not isinstance(item, dict) or item.get("schema") != INPUT_SCHEMA:
        raise InputError(f"operator input must carry schema {INPUT_SCHEMA!r}")
    if item.get("source") not in SOURCES:
        raise InputError(f"operator input source must be one of {SOURCES}")
    if not isinstance(item.get("op_key"), str) or not isinstance(item.get("received_at"), str):
        raise InputError("operator input needs 'op_key' and 'received_at' strings")
    extra = set(item) - {"schema", "op_key", "source", "text", "choice", "received_at", "transcript"}
    if extra:
        raise InputError(f"operator input has unknown fields {sorted(extra)}")
    if item["source"] == "screen_choice" and not isinstance(item.get("choice"), str):
        raise InputError("a screen_choice needs 'choice'")
    if item["source"] != "screen_choice" and not isinstance(item.get("text"), str):
        raise InputError(f"a {item['source']} input needs 'text'")
    if item["source"] == "speech_transcript" and not isinstance(item.get("transcript"), dict):
        raise InputError("a speech_transcript needs 'transcript' (provider, model, language)")


def normalize(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    return re.sub(r"[.!?,;:]+$", "", text).strip()


def _key(text: str) -> str:
    return re.sub(r"[,.!?;:]", "", normalize(text).lower()).strip()


def interpretation(intent: str, choice=None, place=None, label=None, not_a_part=False) -> dict:
    # not_a_part has no intent in the answer_interpretation schema (contract request in README); L0 carries it apart
    return {"intent": intent, "choice": choice, "place_text": place, "label_text": label, "not_a_part": not_a_part}


def interpret_read_back(item: dict) -> dict:
    """yes / no to a read-back; anything else is unclear (the read-back is repeated, nothing is written)."""
    if item["source"] == "screen_choice":
        token = item["choice"]
        return interpretation({YES: "confirm", NO: "reject"}.get(token, "unclear"))
    key = _key(item["text"])
    if key in _YES:
        return interpretation("confirm")
    if key in _NO:
        return interpretation("reject")
    return interpretation("unclear")


def interpret_answer(item: dict, kind: str, prompt: dict) -> dict:
    """Interpret an answer to a question whose L0 prompt (with its screen options) the operator saw."""
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
            return interpretation("choose", choice=token, label=label)  # "L-Taste aus B" / "L-Taste von zwei"
        return interpretation("describe_place", place=place, label=label)
    label, place = _split(text, _LABEL_DEST)
    return interpretation("describe_place", place=place, label=label)


def _option_token(key: str, prompt: dict) -> str | None:
    """Screen token named by a spoken number ('zwei', '2') or a screen letter ('b'), if it was offered."""
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
    """'<label> aus <place>' when the label is short; otherwise the whole text is the place."""
    match = pattern.match(text)
    if match and len(match.group("label").split()) <= MAX_LABEL_WORDS and match.group("place").strip():
        return match.group("label").strip(), match.group("place").strip()
    return None, text


def destination_value(place: str) -> str:
    """'Zelle 4' -> 'cell-4' (the memory's cell location); any other place name stays verbatim."""
    cell = _CELL.match(place.strip())
    return f"cell-{int(cell.group(1))}" if cell else place.strip()


def to_answer(question: dict, interp: dict, prompt: dict, op_key: str, source: str, destination=None) -> dict:
    """The answers.py answer for an interpretation with intent choose / describe_place.
    ``destination``: the session language's place parser (German ``destination_value`` when omitted)."""
    destination = destination or destination_value
    answer = {"question": question["id"], "choice": None, "source": None, "destination": None,
              "label": interp.get("label_text"), "identical_to": [], "not_a_part": bool(interp.get("not_a_part")),
              "skip": False, "provenance": f"live op_key={op_key}; operator input {source}"}
    if answer["not_a_part"]:
        answer["label"] = None
        return answer
    if interp["intent"] == "choose":
        option = next(o for o in prompt["screen_options"] if o["token"] == interp["choice"])
        answer["choice"] = option["removal_event"]
    elif question["kind"] == "source_of_part":
        answer["source"] = interp["place_text"]
    else:
        answer["destination"] = destination(interp["place_text"])
    return answer


def _place_words(location: str) -> str:
    cell = re.fullmatch(r"cell-(\d+)", location)
    return f"in Zelle {cell.group(1)}" if cell else f"bei „{location}“"


def read_back(question: dict, answer: dict, prompt: dict) -> str:
    """What the coordinator says back before anything is written; the operator must answer yes."""
    name = f"„{answer['label']}“" if answer.get("label") else "ein Teil ohne Namen"
    if answer["not_a_part"]:
        where = _place_words(question["cell"]) if question["kind"] == "source_of_part" else "an dieser Stelle"
        return f"Ich trage ein: {where} ist kein Teil, das gehört nicht zur Zerlegung. Stimmt das? Sag ja oder nein."
    if question["kind"] == "source_of_part":
        if answer["choice"]:
            option = next(o for o in prompt["screen_options"] if o["removal_event"] == answer["choice"])
            origin = (f"von Möglichkeit {option['spoken_number']} ({option['description']})" if option["spoken_number"]
                      else f"von der auf dem Bildschirm mit {option['token']} markierten Stelle")
            warn = (f" Achtung: diese Stelle hast du schon für „{option['claimed_by']}“ genannt."
                    if option.get("claimed_by") else "")
        else:
            origin, warn = f"aus „{answer['source']}“", ""
        return f"Ich trage ein: {name} kam {origin} und liegt {_place_words(question['cell'])}.{warn} Stimmt das? Sag ja oder nein."
    return f"Ich trage ein: {name} wurde herausgenommen und liegt jetzt {_place_words(answer['destination'])}. Stimmt das? Sag ja oder nein."
