"""PCB reference-designator parsing. Standard library only.

Separates what the text says from what it means: a parsed designator keeps
its raw OCR text, its normalized form, and cheaper alternates for ambiguous
characters (O/0, I/L/1). A suggested component type is a hint from the
prefix alone; it never proves anything about a visible part.
"""

from __future__ import annotations

import re
import string
from itertools import product

PREFIX_TYPES = {
    "R": "resistor", "C": "capacitor", "L": "inductor", "D": "diode",
    "LED": "diode (LED)", "Q": "transistor", "U": "integrated circuit",
    "J": "connector", "Y": "crystal", "X": "crystal/oscillator",
    "T": "transformer", "F": "fuse", "K": "relay", "S": "switch",
    "SW": "switch", "P": "plug/pin", "W": "wire/jumper", "M": "motor",
    "Z": "zener diode", "RT": "thermistor", "RV": "varistor",
    "FB": "ferrite bead", "TP": "test point", "H": "mounting hole",
    "BT": "battery", "RN": "resistor network", "CN": "capacitor network",
}

DESIGNATOR = re.compile(r"^([A-Z]{1,3})(\d{1,4})([A-Z]{0,2})$")
AMBIGUOUS = {"O": ("O", "0"), "0": ("0", "O"), "I": ("I", "1"),
             "L": ("L", "1"), "1": ("1", "I")}
STRIP = string.whitespace + ".,;:\"'`|!-_" + "_"


def _variants(text: str) -> list[tuple[str, int]]:
    spots = [i for i, ch in enumerate(text) if ch in AMBIGUOUS][:4]
    out = []
    for combo in product(*[AMBIGUOUS[text[i]] for i in spots]):
        chars = list(text)
        for i, ch in zip(spots, combo):
            chars[i] = ch
        fixed = "".join(chars)
        subs = sum(1 for i, ch in zip(spots, combo) if ch != text[i])
        out.append((fixed, subs))
    seen: dict[str, int] = {}
    for fixed, subs in out:
        if fixed not in seen or subs < seen[fixed]:
            seen[fixed] = subs
    return sorted(seen.items(), key=lambda item: item[1])


def parse(raw_text: str) -> dict:
    """Parse one OCR word into a designator reading with alternates."""
    cleaned = raw_text.strip().upper().strip(STRIP).replace(" ", "").replace("-", "")
    readings = []
    if not any(ch.isalpha() for ch in cleaned):
        return {"raw_text": raw_text, "normalized": None,
                "is_designator_like": False, "alternates": []}
    for fixed, subs in _variants(cleaned):
        if subs > 2:
            continue
        match = DESIGNATOR.match(fixed)
        if match:
            prefix, number, suffix = match.groups()
            readings.append({
                "normalized": fixed, "prefix": prefix, "number": number,
                "suffix": suffix, "subs": subs,
                "score": round(1.0 - 0.15 * subs, 3),
                "listed_prefix": prefix in PREFIX_TYPES,
            })
    readings.sort(key=lambda item: (item["subs"], not item["listed_prefix"], item["normalized"]))
    answer: dict = {"raw_text": raw_text, "normalized": None,
                    "is_designator_like": bool(readings), "alternates": []}
    if readings:
        top, rest = readings[0], readings[1:5]
        answer.update({key: top[key] for key in
                       ("normalized", "prefix", "number", "suffix", "score", "listed_prefix")})
        answer["corrections"] = top["subs"]
        answer["suggested_type"] = PREFIX_TYPES.get(top["prefix"])
        answer["type_note"] = (
            f"{top['prefix']} suggests {PREFIX_TYPES[top['prefix']]}; "
            "silkscreen alone does not prove the part") if top["listed_prefix"] else (
            f"{top['prefix']} is not a listed prefix; no type suggested")
        answer["alternates"] = [{
            "normalized": item["normalized"], "score": item["score"],
            "subs": item["subs"]} for item in rest]
    return answer
