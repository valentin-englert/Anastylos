"""SMD resistor marking decoder. Standard library only.

Supports 3-digit, 4-digit, R-notation, EIA-96, and zero-ohm jumpers with
exact decimal arithmetic. Several schemes may read one code; distinct
values are all preserved and the result is ambiguous. E-series values
are never used to prefer a reading, and tolerance is never inferred.
"""

from __future__ import annotations

import re
from decimal import Decimal

# E96 series, index 01..96. Order and count are asserted below.
E96 = (
    100, 102, 105, 107, 110, 113, 115, 118, 121, 124, 127, 130, 133, 137,
    140, 143, 147, 150, 154, 158, 162, 165, 169, 174, 178, 182, 187, 191,
    196, 200, 205, 210, 215, 221, 226, 232, 237, 243, 249, 255, 261, 267,
    274, 280, 287, 294, 301, 309, 316, 324, 332, 340, 348, 356, 365, 374,
    383, 392, 402, 412, 422, 432, 442, 453, 464, 475, 487, 499, 511, 523,
    536, 549, 562, 576, 590, 604, 619, 634, 649, 665, 681, 698, 715, 732,
    750, 768, 787, 806, 825, 845, 866, 887, 909, 931, 953, 976,
)
assert len(E96) == 96 and all(a < b for a, b in zip(E96, E96[1:]))

# Default EIA-96 multiplier chart (A-F only). Sub-unity letters (X/Y versus
# S/R) and H/Z vary by manufacturer, so they are never silently assumed;
# pass them explicitly via `aliases` (documented in the README).
EIA96_MULTIPLIERS = {
    "A": Decimal(1), "B": Decimal(10), "C": Decimal(100),
    "D": Decimal(1000), "E": Decimal(10000), "F": Decimal(100000),
}

ZERO_JUMPERS = ("0", "00", "000", "0000")

TOLERANCE_CONTEXT = {
    "3-digit": "3-digit markings are commonly +-5% parts, but the marking does not confirm tolerance.",
    "4-digit": "4-digit markings are commonly +-1% parts, but the marking does not confirm tolerance.",
    "R-notation": "R-notation tolerance varies by series and this marking does not confirm tolerance.",
    "EIA-96": "EIA-96 markings are commonly +-1% parts, but the marking does not confirm tolerance.",
    "zero-jumper": "Zero-ohm jumpers are specified by current rating; tolerance does not apply.",
}

_THREE = re.compile(r"^(\d{2})(\d)$")
_FOUR = re.compile(r"^(\d{3})(\d)$")
_R_NOTATION = re.compile(r"^(\d*)R(\d*)$")
_EIA96 = re.compile(r"^(\d{2})([A-Z])$")


def _exact(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _candidate(schemes: list[str], ohms: Decimal, explanation: str) -> dict:
    exact = _exact(ohms)
    try:
        as_float = float(exact)
    except OverflowError:
        as_float = float("inf")
    return {
        "schemes": schemes,
        "resistance_ohms_exact": exact,
        "resistance_ohms": as_float,
        "explanation": explanation,
        "tolerance_percent": None,
        "tolerance_context": " | ".join(TOLERANCE_CONTEXT[scheme] for scheme in schemes),
    }


def _merge(candidates: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for item in candidates:
        key = item["resistance_ohms_exact"]
        if key in merged:
            known = merged[key]
            known["schemes"] = sorted(set(known["schemes"]) | set(item["schemes"]))
            known["explanation"] += f"; also {item['explanation']}"
            known["tolerance_context"] = " | ".join(
                TOLERANCE_CONTEXT[scheme] for scheme in known["schemes"])
        else:
            merged[key] = item
    return sorted(merged.values(), key=lambda item: Decimal(item["resistance_ohms_exact"]))


def decode(code: str, aliases: dict[str, object] | None = None) -> dict:
    """Decode one marking in every applicable scheme.

    `aliases` maps extra EIA-96 multiplier letters to factors (e.g.
    {"S": "0.1", "R": "0.01"}) for manufacturer-specific charts. An alias
    that disagrees with the default chart yields an extra candidate and
    an ambiguous verdict; it never overwrites the default reading.
    """
    normalized = (code or "").strip().upper()
    answer: dict = {"code": code, "normalized": normalized, "candidates": []}
    if not normalized:
        return {**answer, "status": "invalid", "reason": "empty marking"}
    found: list[dict] = []
    if normalized in ZERO_JUMPERS:
        found.append(_candidate(["zero-jumper"], Decimal(0), f"{normalized} is a zero-ohm jumper"))
    match = _THREE.fullmatch(normalized)
    if match:
        digits, power = int(match.group(1)), int(match.group(2))
        found.append(_candidate(["3-digit"], Decimal(digits) * (Decimal(10) ** power),
                                f"{digits} x 10^{power}"))
    match = _FOUR.fullmatch(normalized)
    if match:
        digits, power = int(match.group(1)), int(match.group(2))
        found.append(_candidate(["4-digit"], Decimal(digits) * (Decimal(10) ** power),
                                f"{digits} x 10^{power}"))
    match = _R_NOTATION.fullmatch(normalized)
    if match and (match.group(1) or match.group(2)):
        whole, frac = match.group(1) or "0", match.group(2) or ""
        ohms = Decimal(f"{int(whole)}.{frac}" if frac else whole)
        found.append(_candidate(["R-notation"], ohms, f"R marks the decimal point: {whole or '0'}.{frac}"))
    match = _EIA96.fullmatch(normalized)
    if match:
        index, letter = int(match.group(1)), match.group(2)
        if 1 <= index <= 96:
            factors = dict(EIA96_MULTIPLIERS)
            extra: dict[str, Decimal] = {}
            for key, value in (aliases or {}).items():
                factor = value if isinstance(value, Decimal) else Decimal(str(value))
                if key in factors and factors[key] != factor:
                    extra[key] = factor
                else:
                    factors[key] = factor
            base = Decimal(E96[index - 1])
            if letter in factors:
                found.append(_candidate(
                    ["EIA-96"], base * factors[letter],
                    f"E96 index {index:02d} = {E96[index - 1]} x {factors[letter]}"))
            if letter in extra:
                found.append(_candidate(
                    ["EIA-96"], base * extra[letter],
                    f"alias chart: E96 index {index:02d} = {E96[index - 1]} x {extra[letter]}"))
    candidates = _merge(found)
    if not candidates:
        return {**answer, "status": "invalid",
                "reason": f"{normalized!r} matches no supported notation (3-digit, 4-digit, R-notation, EIA-96, jumper)"}
    if len(candidates) > 1:
        return {**answer, "status": "ambiguous", "candidates": candidates,
                "reason": "several schemes read this code differently"}
    return {**answer, "status": "ok", "candidates": candidates, "reason": None}
