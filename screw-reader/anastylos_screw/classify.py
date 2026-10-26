"""Screw-drive classification rules. Standard library only.

Scores candidate classes from rotation-invariant geometric features.
Families (slot, cross, six-point star) compete first; lookalikes inside
one family (Phillips/Pozidriv, Torx/hex) split only on their specific
evidence and stay ambiguous otherwise. There are no popularity priors:
weak evidence yields several candidates or unknown, never a default.
"""

from __future__ import annotations

MIN_SCORE = 0.55
FAMILY_MARGIN = 0.12
WITHIN_MARGIN = 0.15
PLAUSIBLE = 0.35
MIN_CONTRAST = 0.12

FAMILIES = {"slotted": "slot", "phillips": "cross", "pozidriv": "cross",
            "torx": "star", "hex": "star"}


def _ramp(value: float, low: float, high: float) -> float:
    if high <= low:
        return 1.0 if value >= high else 0.0
    return max(0.0, min(1.0, (value - low) / (high - low)))


def _band(value: float, low: float, high: float, feather: float = 0.08) -> float:
    return _ramp(value, low - feather, low) * (1.0 - _ramp(value, high, high + feather))


def score_slotted(features: dict) -> tuple[float, dict]:
    evidence = {
        "eccentricity": round(_ramp(features["eccentricity"], 0.75, 0.92), 3),
        "twofold": round(_ramp(features["sym2"], 0.6, 0.8), 3),
        "not_fourfold": round(1.0 - _ramp(features["sym4"], 0.45, 0.65), 3),
        "two_lobes": 1.0 if features["lobes"] == 2 else 0.0,
    }
    score = sum(evidence.values()) / len(evidence) * _ramp(features["sym2"], 0.35, 0.55)
    return round(score, 3), evidence


def _score_cross(features: dict, want_ticks: bool) -> tuple[float, dict]:
    ticks = features["ticks"]
    tick_match = _ramp(ticks, 0.06, 0.12) if want_ticks else 1.0 - _ramp(ticks, 0.06, 0.12)
    evidence = {
        "fourfold": round(_ramp(features["sym4"], 0.6, 0.8), 3),
        "four_lobes": 1.0 if features["lobes"] == 4 else 0.0,
        "ticks": round(tick_match, 3),
        "compact": round(1.0 - _ramp(features["eccentricity"], 0.5, 0.75), 3),
    }
    score = sum(evidence.values()) / len(evidence) * _ramp(features["sym4"], 0.35, 0.55)
    return round(score, 3), evidence


def score_phillips(features: dict) -> tuple[float, dict]:
    return _score_cross(features, want_ticks=False)


def score_pozidriv(features: dict) -> tuple[float, dict]:
    return _score_cross(features, want_ticks=True)


def _score_star(features: dict, want_convex: bool) -> tuple[float, dict]:
    solidity = features["solidity"]
    shape = _ramp(solidity, 0.93, 0.97) if want_convex else 1.0 - _ramp(solidity, 0.9, 0.95)
    verts = features["vertices"]
    corner_match = _band(verts, 5, 7, feather=1.5) if want_convex else _ramp(verts, 9, 11)
    evidence = {
        "sixfold": round(_ramp(features["sym6"], 0.6, 0.8), 3),
        "six_lobes": 1.0 if features["lobes"] == 6 else 0.0,
        "shape": round(shape, 3),
        "corners": round(corner_match, 3),
    }
    score = sum(evidence.values()) / len(evidence) * _ramp(features["sym6"], 0.35, 0.55)
    return round(score, 3), evidence


def score_torx(features: dict) -> tuple[float, dict]:
    return _score_star(features, want_convex=False)


def score_hex(features: dict) -> tuple[float, dict]:
    return _score_star(features, want_convex=True)


SCORERS = {"slotted": score_slotted, "phillips": score_phillips,
           "pozidriv": score_pozidriv, "torx": score_torx, "hex": score_hex}


def classify(features: dict | None, quality: dict | None = None) -> dict:
    """Rank drive classes for one feature set; JSON-serializable."""
    if features is None:
        return {"status": "unknown", "reason": "no screw head localized", "candidates": []}
    contrast = (quality or {}).get("contrast", 1.0)
    if contrast < MIN_CONTRAST:
        return {"status": "unknown", "reason": f"recess contrast {contrast} below {MIN_CONTRAST}",
                "candidates": []}
    ranked = []
    for name, scorer in SCORERS.items():
        score, evidence = scorer(features)
        ranked.append({"class": name, "family": FAMILIES[name],
                       "score": score, "evidence": evidence})
    ranked.sort(key=lambda item: -item["score"])
    plausible = [item for item in ranked if item["score"] >= PLAUSIBLE]
    if not plausible:
        return {"status": "unknown", "reason": "no class reaches the plausibility floor",
                "candidates": ranked[:3]}
    if features["lobes"] not in (2, 4, 6):
        return {"status": "ambiguous", "reason": "lobe count fits no known drive geometry",
                "candidates": plausible[:3]}
    families: dict[str, list] = {}
    for item in plausible:
        families.setdefault(item["family"], []).append(item)
    order = sorted(families, key=lambda family: -families[family][0]["score"])
    top_family, top = order[0], families[order[0]][0]
    if top["score"] < MIN_SCORE:
        return {"status": "ambiguous", "reason": "best score below the acceptance floor",
                "candidates": plausible[:3]}
    if len(order) > 1 and top["score"] - families[order[1]][0]["score"] < FAMILY_MARGIN:
        return {"status": "ambiguous", "reason": "rival families within margin",
                "candidates": plausible[:3]}
    if len(families[top_family]) > 1 and top["score"] - families[top_family][1]["score"] < WITHIN_MARGIN:
        return {"status": "ambiguous",
                "reason": f"lookalikes inside {top_family} family within margin",
                "candidates": plausible[:3]}
    return {"status": "ok", "reason": None, "candidates": plausible[:3]}
