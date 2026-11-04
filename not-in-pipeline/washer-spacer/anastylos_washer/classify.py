"""Family ranking for washer/spacer segments. Standard library only.

Scores visual hypotheses (never verified part identities) from
measured geometry, applies family hints, caps confidence under
degradation, and assembles observations with dimensions and
uncertainty. No nominal hardware standards are inferred: sizes are
reported, never mapped to M-designations.
"""

from __future__ import annotations

MIN_SCORE = 0.55
MARGIN = 0.15
PLAUSIBLE = 0.30

FAMILIES = frozenset({"flat_washer", "split_washer", "spacer", "standoff",
                      "unclassified"})


def _uncertainty_px(base: float, rms: float, flagged: bool) -> float:
    value = 2.0 * rms * base + 1.0
    if flagged:
        value *= 1.5
    return round(value, 1)


def _candidate(family: str, confidence: float, evidence: dict,
               explanation: str) -> dict:
    return {"family": family, "confidence": round(min(confidence, 0.99), 3),
            "evidence": evidence, "explanation": explanation}


def _unexpected(kind: str, hint: str, strength: float) -> dict:
    return _candidate(
        "unclassified", min(strength * 0.6, 0.50),
        {"declared_family": hint, "observed_shape": kind},
        f"observed {kind} is not a {hint} shape; treated as unclassified")


def _score_top(geometry: dict, hint: str | None) -> tuple[list[dict], dict]:
    outer = geometry["outer"]
    if outer is None or outer["mean_d"] < 24:
        return [], {"limit": "outer boundary unmeasurable or below 24px"}
    hole = geometry["hole"]
    hole_ratio = (hole["diameter_px"] / outer["mean_d"]) if hole else None
    circular = outer["eccentricity"] < 0.6
    concentric = (geometry["concentricity"] is not None
                  and geometry["concentricity"] < 0.12)
    split = (geometry["gap_deg"] >= 12.0 and hole is None
             and 0.50 <= geometry["coverage"] <= 0.97)
    lobes = (geometry["defect_separation_deg"] is not None
             and geometry["defect_separation_deg"] > 120.0
             and len(geometry["defects"]) >= 2)
    uncertain = outer["eccentricity"] >= 0.35
    ranked: list[dict] = []
    shape = "ring"
    if lobes:
        shape = "merged lobes"
        ranked.append(_candidate(
            "flat_washer", 0.55,
            {"grouped": True, "lobes": True,
             "defect_separation_deg": geometry["defect_separation_deg"]},
            "touching parts share one contour; grouped, count unknown"))
        ranked.append(_candidate("unclassified", 0.50, {"grouped": True},
                                 "grouped shape rival"))
    elif split:
        shape = "interrupted ring"
        ranked.append(_candidate(
            "split_washer", 0.90,
            {"gap_deg": geometry["gap_deg"],
             "gap_at_deg": geometry["gap_at_deg"],
             "coverage": geometry["coverage"]},
            f"ring interrupted by a {geometry['gap_deg']} deg gap"))
        ranked.append(_candidate(
            "unclassified", 0.68, {"gap_deg": geometry["gap_deg"]},
            "gap could be damage rather than a split"))
    elif geometry["hexagon_likely"] and hole:
        shape = "hexagon with hole"
        ranked.append(_candidate(
            "standoff", 0.92,
            {"vertices": geometry["vertices"],
             "isoperimetric_q": geometry["isoperimetric_q"],
             "hole_ratio": round(hole_ratio or 0, 3)},
            "hexagonal outline with central hole"))
        ranked.append(_candidate(
            "unclassified", 0.80, {"vertices": geometry["vertices"]},
            "hex nuts share this top view; family unverified"))
    elif hole and circular and concentric:
        shape = "circular ring"
        ranked.append(_candidate(
            "flat_washer", 0.78,
            {"hole_ratio": round(hole_ratio or 0, 3),
             "concentricity": geometry["concentricity"],
             "coverage": geometry["coverage"]},
            "circular concentric ring: washer-like from above"))
        ranked.append(_candidate(
            "spacer", 0.72,
            {"hole_ratio": round(hole_ratio or 0, 3)},
            "a spacer end looks identical from above; height unknown"))
    elif hole:
        shape = "irregular ring"
        ranked.append(_candidate(
            "flat_washer", 0.50,
            {"eccentricity": outer["eccentricity"],
             "concentricity": geometry["concentricity"]},
            "ring present but irregular or off-center"))
        ranked.append(_candidate("unclassified", 0.48, {},
                                 "irregular ring rival"))
    elif hole is None and circular and geometry["coverage"] > 0.9:
        shape = "solid disc"
        ranked.append(_candidate(
            "unclassified", 0.75, {"hole": None},
            "solid disc: no hole, no split, no hexagon"))
    else:
        shape = "indistinct blob"
        ranked.append(_candidate("unclassified", 0.45, {},
                                 "no ring, split, or hexagon resolved"))
    if hole is not None and hole["diameter_px"] < 8:
        for item in ranked:
            if item["family"] in ("flat_washer", "spacer", "standoff"):
                item["confidence"] = min(item["confidence"], 0.50)
                item["explanation"] += "; hole below 8px, unmeasurable"
    if geometry["coverage"] < 0.75 and not lobes:
        for item in ranked:
            item["confidence"] = min(item["confidence"], 0.50)
        ranked[0]["explanation"] += (
            f"; partial outline (coverage {geometry['coverage']})")
    if hint is not None:
        wanted = [item for item in ranked if item["family"] == hint]
        if wanted and wanted[0]["confidence"] >= PLAUSIBLE:
            single = dict(wanted[0])
            single["explanation"] = (f"declared {hint}: "
                                     f"{single['explanation']}")
            ranked = [single]
        else:
            ranked = [_unexpected(shape, hint, ranked[0]["confidence"]
                                  if ranked else 0.4)]
    limits = {"shape": shape}
    if uncertain:
        limits["perspective_suspect"] = True
    return ranked, limits


def _score_side(geometry: dict, hint: str | None) -> tuple[list[dict], dict]:
    aspect = geometry["aspect"]
    thick = geometry["thickness_px"]
    length = geometry["length_px"]
    ranked: list[dict] = []
    shape = "rectangle"
    if aspect > 4.5:
        if geometry["interfaces_long"] >= 1:
            shape = "thin bar with slit"
            ranked.append(_candidate(
                "split_washer", 0.90,
                {"aspect": aspect, "slit": True},
                "thin side profile with a slit"))
            ranked.append(_candidate(
                "flat_washer", 0.60, {"aspect": aspect},
                "slit could be a mark; flat rival"))
        else:
            shape = "thin bar"
            ranked.append(_candidate(
                "flat_washer", 0.90, {"aspect": aspect},
                "thin side profile: washer-like"))
            ranked.append(_candidate(
                "unclassified", 0.50, {"aspect": aspect},
                "thin shim stock shares this profile"))
    elif geometry["interfaces_transverse"] >= 1 \
            and geometry["rect_fill"] > 0.85:
        shape = "stacked bands"
        ranked.append(_candidate(
            "flat_washer", 0.70,
            {"grouped": True,
             "band_interfaces": geometry["band_interfaces"]},
            f"{geometry['interfaces_transverse']} layer interface(s): "
            f"stacked parts share one contour; grouped, count unknown"))
        ranked.append(_candidate("unclassified", 0.55, {"grouped": True},
                                 "grouped shape rival"))
    elif aspect >= 1.2:
        shape = "tall block"
        openings = geometry["openings"]
        flats = geometry["flats_likely"]
        if openings == 2 and flats:
            ranked.append(_candidate(
                "standoff", 0.90,
                {"openings": openings, "flats": True, "aspect": aspect},
                "tall body, visible end openings, flat bands"))
            ranked.append(_candidate(
                "spacer", 0.68, {"openings": openings},
                "round spacer rival; flats unexplained"))
        elif openings == 2:
            ranked.append(_candidate(
                "spacer", 0.88,
                {"openings": openings, "aspect": aspect},
                "tall body with visible openings at both ends"))
            ranked.append(_candidate(
                "standoff", 0.55, {"openings": openings},
                "standoff rival; no flat bands seen"))
        elif openings == 1:
            ranked.append(_candidate(
                "spacer", 0.62, {"openings": openings},
                "one visible opening; far end hidden or blind"))
            ranked.append(_candidate(
                "unclassified", 0.58, {"openings": openings},
                "single-opening rival"))
        else:
            ranked.append(_candidate(
                "unclassified", 0.62, {"openings": openings},
                "tall solid body without visible openings"))
            ranked.append(_candidate(
                "spacer", 0.60, {"openings": openings},
                "spacer rival; openings not visible"))
    else:
        shape = "compact block"
        ranked.append(_candidate("unclassified", 0.60, {"aspect": aspect},
                                 "compact profile fits no family"))
        ranked.append(_candidate("spacer", 0.55, {"aspect": aspect},
                                 "short-spacer rival"))
    if hint is not None:
        wanted = [item for item in ranked if item["family"] == hint]
        if wanted and wanted[0]["confidence"] >= PLAUSIBLE:
            single = dict(wanted[0])
            single["explanation"] = (f"declared {hint}: "
                                     f"{single['explanation']}")
            ranked = [single]
        else:
            ranked = [_unexpected(shape, hint, ranked[0]["confidence"]
                                  if ranked else 0.4)]
    limits: dict = {"shape": shape}
    if geometry["edge_ratio"] is not None and geometry["edge_ratio"] < 0.85:
        limits["perspective_suspect"] = True
    if thick < 40:
        limits["ends_unresolved"] = "profile too thin for opening analysis"
    return ranked, limits


def _dimensions_top(geometry: dict, limits: dict) -> dict:
    outer = geometry["outer"]
    flagged = bool(limits.get("perspective_suspect"))
    dims: dict = {}
    if outer is not None:
        unc = _uncertainty_px(outer["mean_d"], outer["rms"], flagged)
        dims["outer_d"] = {"value_px": outer["mean_d"], "uncertainty_px": unc}
        dims["outer_eccentricity"] = outer["eccentricity"]
    hole = geometry["hole"]
    if hole is not None:
        unc = _uncertainty_px(hole["diameter_px"], 0.01, flagged)
        dims["inner_d"] = {"value_px": hole["diameter_px"],
                           "uncertainty_px": unc}
        dims["hole_center_xy"] = hole["centroid_xy"]
    if geometry["concentricity"] is not None:
        dims["concentricity"] = geometry["concentricity"]
    if geometry["gap_deg"]:
        dims["gap_deg"] = geometry["gap_deg"]
        dims["gap_at_deg"] = geometry["gap_at_deg"]
    dims["coverage"] = geometry["coverage"]
    if outer is not None:
        dims["center_xy"] = outer["center_xy"]
    for key in ("across_corners_px", "across_flats_px"):
        value = geometry.get("hex_calipers", {}).get(key)
        if value is not None:
            short = key.replace("_px", "")
            unc = round(2.0 * (1.5 if flagged else 1.0), 1)
            dims[short] = {"value_px": value, "uncertainty_px": unc}
    return dims


def _dimensions_side(geometry: dict, limits: dict) -> dict:
    flagged = bool(limits.get("perspective_suspect"))
    unc_len = _uncertainty_px(geometry["length_px"], 0.005, flagged)
    unc_th = _uncertainty_px(geometry["thickness_px"], 0.005, flagged)
    dims = {"length_px": {"value_px": geometry["length_px"],
                          "uncertainty_px": unc_len},
            "thickness_px": {"value_px": geometry["thickness_px"],
                             "uncertainty_px": unc_th},
            "aspect": geometry["aspect"]}
    if geometry["openings"] is not None:
        dims["openings_visible"] = geometry["openings"]
    if geometry["band_interfaces"]:
        dims["band_interfaces"] = geometry["band_interfaces"]
    if geometry["edge_ratio"] is not None:
        dims["edge_ratio"] = geometry["edge_ratio"]
    return dims


def _verdict(ranked: list[dict], limits: dict) -> tuple[str, str | None]:
    if not ranked:
        return "unknown", str(limits.get("limit", "no measurable geometry"))
    plausible = [item for item in ranked if item["confidence"] >= PLAUSIBLE]
    if not plausible:
        return "unknown", "evidence below the plausibility floor"
    top = plausible[0]
    if any(item.get("evidence", {}).get("grouped") for item in plausible[:2]):
        return "ambiguous", "grouped observation: individuals inseparable"
    if top["confidence"] < MIN_SCORE:
        return "ambiguous", "best hypothesis below the acceptance floor"
    rivals = [item for item in plausible[1:]
              if item["family"] != top["family"]]
    if rivals and top["confidence"] - rivals[0]["confidence"] < MARGIN:
        return "ambiguous", "rival families within margin"
    return "ok", None


def classify(segments: list[dict], view: str,
             family: str | None = None) -> list[dict]:
    """Rank families per segment. JSON-serializable (no contours)."""
    observations = []
    for index, segment in enumerate(segments, 1):
        geometry = segment["geometry"]
        if view == "top":
            ranked, limits = _score_top(geometry, family)
            dims = _dimensions_top(geometry, limits)
        else:
            ranked, limits = _score_side(geometry, family)
            dims = _dimensions_side(geometry, limits)
        if segment["truncated"]:
            for item in ranked:
                item["confidence"] = min(item["confidence"], 0.60)
            limits["truncated"] = True
        status, reason = _verdict(ranked, limits)
        if status == "ok" and segment["truncated"]:
            status, reason = "ambiguous", "part touches the crop border"
        top = ranked[0]["family"] if ranked else None
        observations.append(
            {"observation_id": f"o{index}",
             "bbox_xywh": list(segment["bbox_xywh"]),
             "status": status, "reason": reason,
             "candidates": ranked[:3], "top_family": top,
             "geometry": {**dims, "limits": limits},
             "quality": segment["quality"],
             "explanation": (ranked[0]["explanation"] if ranked
                             else str(limits.get("limit", "no geometry")))})
    return observations
