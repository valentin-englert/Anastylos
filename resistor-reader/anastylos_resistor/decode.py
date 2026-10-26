"""IEC 60062 resistor color-code decoder. Standard library only.

Decodes 4-band and 5-band sequences, with 6-band TCR as an option. Both
reading directions are tried; validity rules (not standard values) decide.
A sequence that is valid in both directions yields two candidates, and a
sequence valid in neither yields none. Standard E-series values are never
used to prefer or force a reading.
"""

from __future__ import annotations

DIGITS = {
    "black": 0, "brown": 1, "red": 2, "orange": 3, "yellow": 4,
    "green": 5, "blue": 6, "violet": 7, "gray": 8, "white": 9,
}

MULTIPLIERS = {**{color: 10.0 ** digit for color, digit in DIGITS.items()},
               "gold": 0.1, "silver": 0.01}

TOLERANCES = {
    "brown": 1.0, "red": 2.0, "green": 0.5, "blue": 0.25,
    "violet": 0.1, "gray": 0.05, "gold": 5.0, "silver": 10.0,
}

TCR_PPM_K = {
    "brown": 100, "red": 50, "orange": 15, "yellow": 25,
    "green": 20, "blue": 10, "violet": 5, "gray": 1,
}

ALIASES = {"grey": "gray", "purple": "violet"}


def normalize(color: str) -> str:
    color = color.strip().lower()
    return ALIASES.get(color, color)


def _read_order(sequence: list[str], band_count: int, orientation: str) -> list[str] | None:
    if len(sequence) != band_count:
        return None
    ordered = [normalize(color) for color in sequence]
    if orientation == "reversed":
        ordered = ordered[::-1]
    if any(color not in DIGITS and color not in ("gold", "silver") for color in ordered):
        return None
    if band_count == 6:
        digits, multiplier, tolerance, tcr = ordered[:3], ordered[3], ordered[4], ordered[5]
        if tcr not in TCR_PPM_K:
            return None
    else:
        digits, multiplier, tolerance = ordered[:-2], ordered[-2], ordered[-1]
    if digits[0] not in DIGITS or DIGITS[digits[0]] == 0:
        return None
    if any(color not in DIGITS for color in digits[1:]):
        return None
    if multiplier not in MULTIPLIERS or tolerance not in TOLERANCES:
        return None
    return ordered


def format_resistance(ohms: float) -> str:
    for divisor, suffix in ((1_000_000.0, "MΩ"), (1_000.0, "kΩ"), (1.0, "Ω")):
        if ohms >= divisor or divisor == 1.0:
            scaled = ohms / divisor
            text = f"{scaled:.3f}".rstrip("0").rstrip(".")
            return f"{text} {suffix}"
    raise AssertionError("unreachable")


def decode(sequence: list[str], band_count: int | None = None) -> list[dict]:
    """Decode one color sequence in both directions.

    Returns zero, one, or two candidates. Each candidate carries the read
    order, resistance, tolerance, and (for 6 bands) the TCR.
    """
    band_count = band_count if band_count is not None else len(sequence)
    if band_count not in (4, 5, 6):
        return []
    candidates = []
    for orientation in ("forward", "reversed"):
        ordered = _read_order(list(sequence), band_count, orientation)
        if ordered is None:
            continue
        digits = ordered[:3] if band_count == 6 else ordered[:-2]
        value = 0
        for color in digits:
            value = value * 10 + DIGITS[color]
        multiplier = ordered[3] if band_count == 6 else ordered[-2]
        tolerance = ordered[4] if band_count == 6 else ordered[-1]
        ohms = value * MULTIPLIERS[multiplier]
        candidate = {
            "sequence": ordered,
            "band_count": band_count,
            "orientation": orientation,
            "resistance_ohms": ohms,
            "resistance": format_resistance(ohms),
            "tolerance_percent": TOLERANCES[tolerance],
            "tcr_ppm_k": TCR_PPM_K[ordered[-1]] if band_count == 6 else None,
        }
        candidates.append(candidate)
    return candidates
