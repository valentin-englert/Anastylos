"""Family-dependent interpretation of observed markers. Stdlib only.

Three concepts stay separate: the observed visual marker (geometry in
the source frame), the component-family-dependent interpretation
(what the marker means electrically), and the original installation
orientation (claimed only with an explicit reference observation).
A stripe is never universally equated with one electrical polarity:
without a declared family, rival hypotheses stay ranked and the
verdict stays ambiguous.
"""

from __future__ import annotations

MIN_SCORE = 0.55
MARGIN = 0.15
PLAUSIBLE = 0.30

FAMILIES = frozenset({"diode", "aluminum_electrolytic", "tantalum", "ic",
                      "connector"})

# marker_type -> family -> (interpretation, polarity, confidence scale).
RULES = {
    "band": {
        "diode": ("cathode_at_marker", "marker end is cathode", 0.95),
        "tantalum": ("anode_at_marker", "marker end is anode", 0.88),
    },
    "stripe": {
        "aluminum_electrolytic": ("negative_at_marker",
                                  "marked side is negative", 0.95),
        "tantalum": ("positive_at_marker",
                     "marked side is positive if tantalum; verify", 0.84),
    },
    "dot": {
        "ic": ("pin1_adjacent", "pin 1 adjacent to dot", 0.92),
        None: ("cosmetic_mark", "mold or ejector mark, not pin 1", 0.78),
    },
    "notch": {
        "ic": ("pin1_left_of_notch",
               "pin 1 counter-clockwise from notch", 0.85),
        None: ("package_bevel", "mechanical bevel, not pin 1", 0.72),
    },
    "key": {
        "connector": ("keyed_side_at_marker",
                      "keyed side faces marker", 0.93),
        None: ("mechanical_damage",
               "asymmetric damage, not keying", 0.82),
    },
}


def _candidate(marker: dict, family: str | None, hint: str | None) -> dict:
    kind = marker["marker_type"]
    meaning, polarity, scale = RULES[kind][family]
    confidence = round(min(marker["strength"] * scale, 0.99), 3)
    if family is None:
        explanation = (f"{kind} {marker['marker_id']} could be a "
                       f"{meaning.replace('_', ' ')}; no family declared")
    elif hint is None:
        explanation = (f"{kind} {marker['marker_id']} reads as {meaning} "
                       f"if the part is {family}")
    else:
        explanation = (f"declared {hint}: {kind} {marker['marker_id']} "
                       f"reads as {meaning}")
    return {"marker_ref": marker["marker_id"], "marker_type": kind,
            "family": family, "interpretation": meaning,
            "polarity": polarity,
            "orientation_vector_xy": marker["orientation_vector_xy"],
            "orientation_angle_deg": marker["orientation_angle_deg"],
            "confidence": confidence,
            "evidence": {"strength": marker["strength"],
                         "marker_bbox_xywh": marker["bbox_xywh"],
                         **{f"marker_{key}": value for key, value
                            in marker.get("evidence", {}).items()}},
            "explanation": explanation}


def _unexpected(marker: dict, hint: str) -> dict:
    """Marker kind the declared family does not use: never confident."""
    return {"marker_ref": marker["marker_id"],
            "marker_type": marker["marker_type"], "family": None,
            "interpretation": "unexpected_marker_kind", "polarity": None,
            "orientation_vector_xy": marker["orientation_vector_xy"],
            "orientation_angle_deg": marker["orientation_angle_deg"],
            "confidence": round(min(marker["strength"] * 0.6, 0.50), 3),
            "evidence": {"strength": marker["strength"],
                         "declared_family": hint},
            "explanation": (f"{marker['marker_type']} "
                            f"{marker['marker_id']} is not a {hint} "
                            f"marker; treated as unexplained feature")}


def _hypotheses(markers: list[dict], hint: str | None) -> list[dict]:
    ranked = []
    for marker in markers:
        kind = marker["marker_type"]
        if kind == "topmark":
            ranked += _topmark_hypotheses(marker)
            continue
        options = RULES.get(kind, {})
        if hint is not None:
            if hint in options:
                ranked.append(_candidate(marker, hint, hint))
            else:
                ranked.append(_unexpected(marker, hint))
        else:
            for family in options:
                ranked.append(_candidate(marker, family, hint))
    ranked.sort(key=lambda item: -item["confidence"])
    return ranked


def _topmark_hypotheses(marker: dict) -> list[dict]:
    """Printed top marks need OCR: 0/180 deg rivals, always ambiguous."""
    base = {"marker_ref": marker["marker_id"], "marker_type": "topmark",
            "family": None,
            "evidence": {"strength": marker["strength"],
                         "marker_bbox_xywh": marker["bbox_xywh"],
                         "axis_only": True},
            "explanation": ("printed top mark: text orientation needs OCR "
                            "or operator read; 0 deg and 180 deg stay tied")}
    angle = marker["orientation_angle_deg"]
    vector = marker["orientation_vector_xy"]
    flipped = [round(-vector[0], 4), round(-vector[1], 4)]
    return [
        {**base, "interpretation": "print_upright_0deg",
         "polarity": None, "orientation_vector_xy": vector,
         "orientation_angle_deg": angle, "confidence": 0.45,
         "forced_ambiguous": True},
        {**base, "interpretation": "print_upright_180deg",
         "polarity": None, "orientation_vector_xy": flipped,
         "orientation_angle_deg": round((angle + 180.0) % 360.0, 1),
         "confidence": 0.42, "forced_ambiguous": True},
    ]


def _dot_notch_agree(markers: list[dict]) -> bool:
    dots = [item for item in markers if item["marker_type"] == "dot"]
    notches = [item for item in markers if item["marker_type"] == "notch"]
    if not dots or not notches:
        return False
    dot = max(dots, key=lambda item: item["strength"])
    notch = max(notches, key=lambda item: item["strength"])
    # Pin 1 sits 45 deg counter-clockwise (minus in this frame) from
    # the notch direction on standard dual-row packages.
    expected = (notch["orientation_angle_deg"] - 45.0) % 360.0
    gap = abs((dot["orientation_angle_deg"] - expected + 180.0) % 360.0
              - 180.0)
    return gap < 40.0


def _boost_agreement(ranked: list[dict]) -> None:
    for item in ranked:
        if item.get("family") == "ic" and item["marker_type"] in ("dot",
                                                                  "notch"):
            item["confidence"] = round(min(item["confidence"] + 0.10, 0.99),
                                       3)
            item["agree_group"] = "ic-pin1"
            item["explanation"] += "; dot and notch agree on pin 1"


def _installation(top: dict | None, reference: dict | None) -> dict:
    if top is None:
        return {"claimed": False,
                "explanation": "no marker observed; nothing to anchor"}
    if reference is None:
        return {"claimed": False,
                "explanation": ("no reference observation supplied; marker "
                                "geometry is preserved in the source frame "
                                "only and proves nothing about original "
                                "installation")}
    relative = round((top["orientation_angle_deg"]
                      - reference["angle_deg"]) % 360.0, 1)
    return {"claimed": True, "reference_kind": reference["kind"],
            "reference_angle_deg": reference["angle_deg"],
            "relative_angle_deg": relative,
            "explanation": (f"marker at {top['orientation_angle_deg']} deg "
                            f"versus {reference['kind']} reference at "
                            f"{reference['angle_deg']} deg")}


def interpret(markers: list[dict], body: dict, quality: dict,
              family: str | None = None,
              reference: dict | None = None) -> dict:
    """Rank family-dependent hypotheses. JSON-serializable."""
    if not markers:
        return {"status": "unknown",
                "reason": "no polarity marker observed",
                "candidates": [],
                "installation": _installation(None, reference),
                "explanation": "body localized but unmarked: no band, "
                               "stripe, dot, notch, key, or top mark"}
    ranked = _hypotheses(markers, family)
    if _dot_notch_agree(markers):
        _boost_agreement(ranked)
        ranked.sort(key=lambda item: -item["confidence"])
    plausible = [item for item in ranked
                 if item["confidence"] >= PLAUSIBLE]
    if not plausible:
        return {"status": "unknown",
                "reason": "marker evidence below the plausibility floor",
                "candidates": ranked[:3],
                "installation": _installation(None, reference),
                "explanation": "marker-like features too weak to interpret"}
    top = plausible[0]
    installation = _installation(top, reference)
    if top.get("forced_ambiguous"):
        return {"status": "ambiguous",
                "reason": "printed top mark needs OCR or operator read",
                "candidates": plausible[:3], "installation": installation,
                "explanation": top["explanation"]}
    if top["confidence"] < MIN_SCORE:
        return {"status": "ambiguous",
                "reason": "best hypothesis below the acceptance floor",
                "candidates": plausible[:3], "installation": installation,
                "explanation": top["explanation"]}
    group = top.get("agree_group")
    rivals = [item for item in plausible[1:]
              if item["interpretation"] != top["interpretation"]
              and not (group is not None
                       and item.get("agree_group") == group)]
    if rivals and top["confidence"] - rivals[0]["confidence"] < MARGIN:
        return {"status": "ambiguous",
                "reason": "rival interpretations within margin",
                "candidates": plausible[:3], "installation": installation,
                "explanation": (f"{top['explanation']} rivalled by "
                                f"{rivals[0]['explanation']}")}
    return {"status": "ok", "reason": None, "candidates": plausible[:3],
            "installation": installation,
            "explanation": top["explanation"]}
