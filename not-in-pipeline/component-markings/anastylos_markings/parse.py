"""Component marking parsers. Standard library only.

Turns observed OCR text into structured readings with a strict
four-level split: observed text, parsed marking values, candidate
identities, and verified specifications (always absent here: nothing
in this module confirms a datasheet match).

Only well-defined notation is parsed. Bare numeric codes keep their
code form with the quantity explicitly unconfirmed, short codes stay
unresolved unless an operator-supplied mapping resolves them, and any
OCR-confusion repair is labeled on the reading that needed it.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from itertools import product
from pathlib import Path

CONFUSIONS = {"0": ("0", "O"), "O": ("O", "0"), "1": ("1", "I", "L"),
              "I": ("I", "1"), "L": ("L", "1"), "5": ("5", "S"),
              "S": ("S", "5"), "8": ("8", "B"), "B": ("B", "8")}
MAX_SUBSTITUTED_POSITIONS = 3
MAX_VARIANTS = 12
LOW_CONF = 40.0

TOLERANCE_LETTERS = "JKMZ"

_CAP_UNIT = re.compile(r"^(\d+(?:\.\d+)?)(P|N|U)(F?)$")
_CAP_DECIMAL = re.compile(r"^(\d)([PNU])(\d)$")
_EIA3_TOL = re.compile(r"^(\d{3})([A-Z])$")
_EIA3 = re.compile(r"^\d{3}$")
_VOLT = re.compile(r"^(\d+(?:\.\d+)?)V$")
_FUSE_COMBINED = re.compile(r"^([TF])(\d+(?:\.\d+)?)(MA|A)(\d+(?:\.\d+)?)V$")
_CURRENT = re.compile(r"^([TF])?(\d+(?:\.\d+)?)(MA|A)$")
_DATE4 = re.compile(r"^(\d{2})(0[1-9]|[1-4][0-9]|5[0-3])$")
_DATE3 = re.compile(r"^(\d)([0-4][0-9]|5[0-3])$")
_PARTNUM = re.compile(r"^(?=.*[A-Z])(?=.*\d)[A-Z0-9]{4,}$")
_SHORT = re.compile(r"^[A-Z0-9]{1,3}$")
_MFR = re.compile(r"^[A-Z]{2,}$")
_NUMERIC = re.compile(r"^\d+$")

UNIT_SCALE = {"P": Decimal("1e-12"), "N": Decimal("1e-9"), "U": Decimal("1e-6")}


def _exact(value: Decimal) -> str:
    return format(value.normalize(), "f")


def variants(raw_text: str) -> list[dict]:
    """Raw text plus OCR-confusion alternatives, cheapest first."""
    cleaned = (raw_text or "").strip().upper().replace(" ", "")
    spots = [i for i, ch in enumerate(cleaned) if ch in CONFUSIONS][:MAX_SUBSTITUTED_POSITIONS]
    out: dict[str, list] = {}
    for combo in product(*[CONFUSIONS[cleaned[i]] for i in spots]):
        chars = list(cleaned)
        subs = []
        for i, ch in zip(spots, combo):
            chars[i] = ch
            if ch != cleaned[i]:
                subs.append({"position": i, "from": cleaned[i], "to": ch})
        fixed = "".join(chars)
        if fixed not in out or len(subs) < len(out[fixed]):
            out[fixed] = subs
    ordered = sorted(out.items(), key=lambda item: (len(item[1]), item[0]))
    return [{"text": text, "subs": subs, "repaired": bool(subs)} for text, subs in ordered[:MAX_VARIANTS]]


def _match_text(text: str) -> str:
    return text.upper().replace("-", "")


def _capacitance(value: Decimal, prefix: str) -> dict:
    farads = value * UNIT_SCALE[prefix]
    return {"kind": "capacitance", "value_exact": _exact(value),
            "unit": {"P": "pF", "N": "nF", "U": "uF"}[prefix],
            "farads_exact": _exact(farads)}


def _eia3(digits: str) -> dict:
    first, second, mult = (int(d) for d in digits)
    picofarads = (first * 10 + second) * (10 ** mult)
    return {"kind": "eia3_code", "digits": digits,
            "picofarads_exact": str(picofarads),
            "farads_exact": _exact(Decimal(picofarads) * Decimal("1e-12")),
            "note": ("3-digit code form (also used for resistance and other "
                     "quantities); quantity unconfirmed")}


def _firm(text: str) -> list[dict]:
    """Well-defined notation with explicit units or code form."""
    parsed = []
    fuse = _FUSE_COMBINED.match(text)
    if fuse:
        speed, amount, unit, volts = fuse.groups()
        parsed.append(_current(speed, amount, unit))
        parsed.append(_voltage(volts))
        return parsed
    current = _CURRENT.match(text)
    if current:
        parsed.append(_current(*current.groups()))
        return parsed
    volt = _VOLT.match(text)
    if volt:
        parsed.append(_voltage(volt.group(1)))
        return parsed
    cap = _CAP_UNIT.match(text)
    if cap:
        parsed.append(_capacitance(Decimal(cap.group(1)), cap.group(2)))
        return parsed
    dotted = _CAP_DECIMAL.match(text)
    if dotted:
        whole, prefix, frac = dotted.groups()
        parsed.append(_capacitance(Decimal(f"{whole}.{frac}"), prefix))
        return parsed
    tol = _EIA3_TOL.match(text)
    if tol and tol.group(2) in TOLERANCE_LETTERS:
        parsed.append(_eia3(tol.group(1)))
        parsed.append({"kind": "tolerance_letter", "letter": tol.group(2),
                       "note": "letter observed; tolerance unconfirmed"})
        return parsed
    eia3 = _EIA3.match(text)
    if eia3:
        parsed.append(_eia3(text))
    date4 = _DATE4.match(text)
    if date4:
        parsed.append({"kind": "date_code", "format": "YYWW",
                       "year": date4.group(1), "week": date4.group(2),
                       "note": "date-like digits; could also be a lot code"})
    date3 = _DATE3.match(text) if not date4 else None
    if date3:
        parsed.append({"kind": "date_code", "format": "YWW",
                       "year": date3.group(1), "week": date3.group(2),
                       "note": "date-like digits; could also be a lot code"})
    return parsed


def _current(speed: str | None, amount: str, unit: str) -> dict:
    amps = Decimal(amount) / 1000 if unit == "MA" else Decimal(amount)
    entry: dict = {"kind": "current", "value_exact": amount,
                   "unit": "mA" if unit == "MA" else "A",
                   "amps_exact": _exact(amps)}
    if speed:
        entry["speed_letter"] = {
            "letter": speed,
            "context": ("T commonly marks time-lag and F fast-acting fuses; "
                        "behavior unconfirmed")}
    return entry


def _voltage(amount: str) -> dict:
    return {"kind": "voltage", "value_exact": amount, "unit": "V",
            "note": "voltage marking; component class unconfirmed"}


def _identity(text: str, parsed: list[dict], mapping: dict | None) -> dict | None:
    """Transcribed identifier; resolved only through an explicit mapping."""
    if parsed:
        return None
    if _PARTNUM.match(text):
        cls, note = "part_number", "transcribed identifier; not a datasheet match"
    elif _MFR.match(text):
        cls, note = "manufacturer_code", "alphabetic code only; manufacturer unconfirmed"
    elif _NUMERIC.match(text):
        cls, note = "lot_or_numeric", "bare digits; kind unconfirmed"
    elif _SHORT.match(text):
        cls, note = "short_code", "short code shared across parts; needs a mapping to resolve"
    elif re.fullmatch(r"[A-Z0-9]+", text):
        cls, note = "transcribed", "transcribed as seen; no interpretation"
    else:
        return None
    hit = (mapping or {}).get(text)
    if hit is None:
        return {"text": text, "class": cls, "resolved": False,
                "resolution_source": None, "note": note}
    name = hit if isinstance(hit, str) else hit.get("identity", text)
    return {"text": text, "class": cls, "resolved": True,
            "resolution_source": "operator-supplied mapping",
            "resolved_identity": name, "note": note}


def parse_text(text: str, mapping: dict | None = None) -> dict:
    """Parse one match string into parsed values plus an identity."""
    match = _match_text(text)
    if match in ("+", "-"):
        return {"match": match, "parsed": [{"kind": "polarity_mark", "symbol": match}],
                "identity": None}
    parsed = _firm(match)
    return {"match": match, "parsed": parsed,
            "identity": _identity(match, parsed, mapping)}


def _read_word(raw: str, mapping: dict | None) -> dict:
    """Raw reading first; a confusion variant may add a firm parse as well.

    Raw always wins when it parses, so nothing is silently rewritten. When
    raw transcribes only (identity, no parsed values) and a variant parses,
    both facets are kept and the repair is labeled on the word.
    """
    tries = variants(raw)
    base = parse_text(tries[0]["text"], mapping)
    entry = {"raw": raw, "chosen": tries[0]["text"], "subs": [], "repaired": False, **base}
    if base["parsed"]:
        return entry
    for variant in tries[1:]:
        reading = parse_text(variant["text"], mapping)
        if reading["parsed"]:
            return {"raw": raw, "chosen": variant["text"], "subs": variant["subs"],
                    "repaired": True, **reading, "identity": base["identity"]}
    return entry


def parse_line(words: list[str], mapping: dict | None = None) -> dict:
    """Parse one text line; word readings plus joint spaceless match."""
    read = [_read_word(word, mapping) for word in words]
    joint = "".join(item["chosen"] for item in read)
    joined = parse_text(joint, mapping) if len(read) > 1 else None
    parsed: list[dict] = []
    for item in read:
        parsed.extend(item["parsed"])
    joint_firm = False
    if joined:
        for item in joined["parsed"]:
            if item not in parsed:
                parsed.append({**item, "spanning_words": True})
        joint_firm = any(item["kind"] not in ("eia3_code", "date_code")
                         for item in joined["parsed"])
    identity = None if joint_firm else next(
        (item["identity"] for item in read if item["identity"]), None)
    subs = []
    for index, item in enumerate(read):
        subs.extend({**entry, "word": index} for entry in item["subs"])
    return {"text": " ".join(words), "match": _match_text(joint),
            "repaired": any(item["repaired"] for item in read), "subs": subs,
            "words": [{"raw": item["raw"], "chosen": item["chosen"],
                       "subs": item["subs"], "parsed": item["parsed"],
                       "identity": item["identity"]} for item in read],
            "parsed": parsed, "identity": identity, "verified_spec": None,
            "flags": [], "notes": []}


def load_mapping(path: str | Path) -> dict:
    """Operator-supplied code mapping; strict schema, no silent defaults."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read mapping {path}: {exc}") from exc
    codes = document.get("codes") if isinstance(document, dict) else None
    if not isinstance(codes, dict) or not codes:
        raise ValueError(f"mapping {path} needs a non-empty 'codes' object")
    for key, value in codes.items():
        if isinstance(value, str) and value.strip():
            continue
        if isinstance(value, dict) and str(value.get("identity", "")).strip():
            continue
        raise ValueError(f"mapping {path}: code {key!r} needs an identity string")
    return {str(key).upper(): value for key, value in codes.items()}


def _rival_values(lines: list[dict]) -> str | None:
    seen: dict[str, set] = {}
    for line in lines:
        for item in line["parsed"]:
            if item["kind"] in ("capacitance", "voltage", "current"):
                key = (item["kind"], item.get("value_exact"), item.get("unit"))
                seen.setdefault(item["kind"], set()).add(key)
    for kind, values in seen.items():
        if len(values) > 1:
            return f"rival {kind} values {sorted(values)}"
    return None


def interpret_crop(line_words: list[list[str]], line_conf: list[float | None],
                   mapping: dict | None = None) -> dict:
    """Parse text lines and arbitrate one verdict. Pure stdlib."""
    lines = [parse_line(words, mapping) for words in line_words]
    for number, line in enumerate(lines, 1):
        line["line"] = number
    for line, conf in zip(lines, line_conf):
        line["ocr_confidence"] = conf
        if conf is not None and conf < LOW_CONF:
            line["flags"].append("low_confidence")
        if line["repaired"]:
            line["flags"].append("repaired")
    useful = [line for line in lines if line["parsed"] or line["identity"]]
    if not line_words or not any(words for words in line_words):
        return {"status": "unknown", "reason": "OCR returned no text",
                "candidates": []}
    if not useful:
        return {"status": "unknown", "reason": "no interpretable marking text",
                "candidates": []}
    partnums = {line["identity"]["text"] for line in useful
                if line["identity"] and line["identity"]["class"] == "part_number"}
    shorts = [line for line in useful if line["identity"]
              and line["identity"]["class"] == "short_code"
              and not line["identity"]["resolved"]]
    kinds = {item["kind"] for line in useful for item in line["parsed"]}
    firm = kinds & {"capacitance", "voltage", "current"}
    reasons: list[str] = []
    if len(partnums) > 1:
        reasons.append(f"rival part-number candidates {sorted(partnums)}")
    rival = _rival_values(useful)
    if rival:
        reasons.append(rival)
    for line in useful:
        word_kinds = [item["kind"] for word in line["words"] for item in word["parsed"]]
        values = [kind for kind in word_kinds if kind in
                  ("eia3_code", "date_code", "capacitance", "voltage", "current")]
        if "eia3_code" in values and len(values) > 1:
            reasons.append(f"{line['match']}: code rivals another code-form reading")
            line["flags"].append("quantity_unconfirmed")
    if "eia3_code" in kinds and not firm:
        reasons.append("bare 3-digit code; quantity unconfirmed")
    if "date_code" in kinds and not partnums:
        reasons.append("date-like digits without part context")
    if shorts:
        codes = sorted({line['identity']['text'] for line in shorts})
        reasons.append(f"short code(s) {codes} shared across parts; unresolved")
    sole_unresolved = all(line["identity"] and not line["identity"]["resolved"]
                          and line["identity"]["class"] in
                          ("lot_or_numeric", "transcribed", "manufacturer_code")
                          and not line["parsed"] for line in useful)
    if sole_unresolved:
        reasons.append("transcribed text only; nothing parsed or resolved")
    if any(line["repaired"] for line in useful):
        reasons.append("top reading needs OCR-confusion repair (see subs)")
    if any("low_confidence" in line["flags"] for line in useful):
        reasons.append(f"decisive word confidence below {LOW_CONF}")
    for line in useful:
        if line["identity"] and not line["identity"]["resolved"]:
            line["notes"].append(line["identity"]["note"])
    if reasons:
        return {"status": "ambiguous", "reason": "; ".join(reasons),
                "candidates": useful}
    return {"status": "ok", "reason": None, "candidates": useful}
