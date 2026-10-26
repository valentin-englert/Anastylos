"""Region classification and verdict rules.

A strict vocabulary separates what was seen from what it might mean:
observed_difference (neutral visual change), candidate_absent_region,
candidate_newly_occupied, candidate_displaced (a linked pair),
shifted_present (still there), and hole_unchanged (void in both
states). Nothing here confirms a part missing; every candidate carries
requires_confirmation. Run verdicts: ok means the comparison was
successfully evaluated, never that the assembly is complete.
"""

from __future__ import annotations

import math

CLASSIFICATIONS = ("observed_difference", "candidate_absent_region",
                   "candidate_newly_occupied", "candidate_displaced",
                   "shifted_present", "hole_unchanged")

VERDICT_MEANING = {
    "ok": "the comparison was successfully evaluated; not a completeness claim",
    "ambiguous": "evidence is weak, partial, or contradictory; human review required",
    "unknown": "the comparison ran but the evidence is unusable",
    "unavailable": "the comparison could not be performed",
}

IDENTITY_NOTE = ("visual similarity is not identity; repeated parts stay "
                 "indistinguishable without external confirmation")


def _inside(point: list[float], bbox: list) -> bool:
    x, y = point
    return bbox[0] <= x <= bbox[0] + bbox[2] and bbox[1] <= y <= bbox[1] + bbox[3]


def _tentative(sign: str, surface: str) -> str:
    brighter_means_absent = surface == "light"
    if sign == "brighter_in_current":
        return "candidate_absent_region" if brighter_means_absent else "candidate_newly_occupied"
    if sign == "darker_in_current":
        return "candidate_newly_occupied" if brighter_means_absent else "candidate_absent_region"
    return "observed_difference"


def _weaknesses(region: dict) -> list[str]:
    quality = region.get("quality") or {}
    reasons = []
    if quality.get("uniform_current"):
        reasons.append("current patch is uniform while the original had texture; "
                       "possible hand/tool occlusion")
    if (quality.get("glare_current") or 0) > 0.25:
        reasons.append(f"glare covers {quality['glare_current']} of the current patch")
    if quality.get("touches_border"):
        reasons.append("region touches the image border; shape is incomplete")
    return reasons


def classify_one(region: dict, context: dict, hole_pairs: list[dict],
                 current_holes: list[dict], associations: list[dict]) -> dict:
    """Classify a single region. Returns the region with verdict fields."""
    out = dict(region)
    out["reasons"] = list(region.get("reasons") or [])
    out["requires_confirmation"] = False
    out["strength"] = "supported"
    out["evidence"] = dict(region.get("evidence") or {})
    centroid = region.get("centroid", [0, 0])
    for pair in hole_pairs:
        if _inside(centroid, pair["original"]["bbox"]) \
                and _inside(centroid, pair["current"]["bbox"]):
            out["classification"] = "hole_unchanged"
            out["reasons"].append("void present in both states; not a missing part")
            out["evidence"]["hole_pair_distance_px"] = pair["distance_px"]
            out["associations"] = []
            return out
    if not context.get("declared"):
        out["classification"] = "observed_difference"
        out["reasons"].append("neutral mode: no comparison context declared")
        out["reasons"].extend(_weaknesses(region))
        if out["reasons"][1:]:
            out["strength"] = "weak"
        out["associations"] = []
        return out
    tentative = _tentative(region.get("sign", "no_polarity"), context.get("surface", "light"))
    params = context.get("params", {})
    ncc = region.get("ncc") or {}
    if (region.get("quality") or {}).get("uniform_current") \
            and tentative != "observed_difference":
        out["classification"] = "observed_difference"
        out["strength"] = "weak"
        out["reasons"].append("possible hand/tool occlusion covers the region; "
                             "polarity is unreadable, no absence or presence claimed")
        out["reasons"].extend(w for w in _weaknesses(region) if "occlusion" not in w)
        out["associations"] = []
        return out
    if tentative == "observed_difference":
        out["classification"] = "observed_difference"
        out["strength"] = "weak"
        out["reasons"].append("polarity is too weak to tell absence from new presence")
    else:
        out["classification"] = tentative
        out["requires_confirmation"] = True
        if tentative == "candidate_absent_region":
            out["reasons"].append("polarity matches a disappearance; identity and cause unconfirmed")
        else:
            out["reasons"].append("polarity matches newly placed material; identity unconfirmed")
        if ncc.get("usable"):
            offset = math.hypot(*ncc.get("offset", [0, 0]))
            if tentative == "candidate_absent_region" \
                    and ncc["score"] >= params.get("ncc_contradict", 0.90) and offset <= 2:
                out["classification"] = "observed_difference"
                out["strength"] = "contradictory"
                out["requires_confirmation"] = False
                out["reasons"].append(f"patch still matches in place (NCC {ncc['score']}); "
                                     "polarity contradicts presence; possible illumination artifact")
            elif tentative == "candidate_absent_region" \
                    and ncc["score"] >= params.get("ncc_present", 0.85) \
                    and offset <= params.get("max_present_offset_px", 8):
                out["classification"] = "shifted_present"
                out["requires_confirmation"] = False
                out["reasons"].append(f"part still matches nearby (NCC {ncc['score']}, "
                                     f"offset {ncc['offset']}px); shifted but present")
                out["evidence"]["ncc_offset_px"] = ncc["offset"]
        for hole in current_holes:
            if out["classification"] == "candidate_absent_region" \
                    and _inside(hole["centroid"], region.get("bbox", [0, 0, 0, 0])):
                out["evidence"]["mounting_hole_visible_in_current"] = True
                out["reasons"].append("a hole-like void is visible inside the region in "
                                     "the current state; supports absence, still unconfirmed")
                break
    weak = _weaknesses(region)
    if weak and out["strength"] == "supported":
        out["strength"] = "weak"
    out["reasons"].extend(weak)
    linked = [dict(item, requires_confirmation=True, note=IDENTITY_NOTE)
              for item in associations if item["region"] == region.get("label")]
    out["associations"] = sorted(linked, key=lambda item: -item["score"])
    if len(linked) > 1:
        out["reasons"].append(f"{len(linked)} similar candidates proposed; no identity asserted")
    return out


def merge_shift_edges(regions: list[dict]) -> None:
    """Fold crescents hugging a shifted part into the shift itself."""
    shifted = [r for r in regions if r["classification"] == "shifted_present"]
    for target in shifted:
        for other in regions:
            if other["classification"] == "candidate_newly_occupied":
                pass
            elif other["classification"] == "observed_difference" \
                    and other["strength"] == "weak":
                pass
            else:
                continue
            dist = math.hypot(target["centroid"][0] - other["centroid"][0],
                              target["centroid"][1] - other["centroid"][1])
            reach = 2.5 * math.sqrt(max(target.get("area_px", 0), other.get("area_px", 0), 1))
            if dist <= reach:
                other["classification"] = "shifted_present"
                other["requires_confirmation"] = False
                other["reasons"].append(f"trailing edge of the small shift at {target['label']}; "
                                       "still present, not a new part")


def pair_displaced(regions: list[dict], context: dict) -> list[dict]:
    """Link nearby absent/newly-occupied regions as displacement evidence."""
    if not context.get("declared"):
        return []
    link_distance = context.get("params", {}).get("link_distance_px", 80)
    absent = [r for r in regions if r["classification"] == "candidate_absent_region"
              and r["strength"] != "contradictory"]
    occupied = [r for r in regions if r["classification"] == "candidate_newly_occupied"
                and r["strength"] != "contradictory"]
    pairs = []
    for gone in absent:
        for filled in occupied:
            dist = math.hypot(gone["centroid"][0] - filled["centroid"][0],
                              gone["centroid"][1] - filled["centroid"][1])
            areas = [gone.get("area_px", 0), filled.get("area_px", 0)]
            ratio = min(areas) / max(areas) if all(a > 0 for a in areas) else 0.0
            if dist <= link_distance and ratio >= 0.25:
                pairs.append((dist, gone, filled))
    pairs.sort(key=lambda item: item[0])
    links = []
    used = set()
    for index, (dist, gone, filled) in enumerate(pairs, 1):
        if gone["label"] in used or filled["label"] in used:
            continue
        used.add(gone["label"])
        used.add(filled["label"])
        link_id = f"link-{index:02d}"
        for item, role in ((gone, "absence_side"), (filled, "presence_side")):
            item["classification"] = "candidate_displaced"
            item["requires_confirmation"] = True
            item["evidence"]["displacement_link"] = link_id
            item["evidence"]["displacement_role"] = role
            item["reasons"].append(f"paired with {filled['label'] if role == 'absence_side' else gone['label']} "
                                   f"({dist:.1f}px apart) as one possible displacement; "
                                   "co-occurrence only, not identity")
        links.append({"id": link_id, "absence_region": gone["label"],
                      "presence_region": filled["label"],
                      "distance_px": round(dist, 1),
                      "requires_confirmation": True})
    return links


def run_verdict(regions: list[dict], failure: str | None,
                evidence_usable: bool, evaluated: bool) -> dict:
    """Run-level verdict from classified regions and comparison health."""
    if failure is not None or not evaluated:
        return {"verdict": "unavailable", "verdict_meaning": VERDICT_MEANING["unavailable"],
                "reasons": [failure or "comparison could not be performed"],
                "requires_confirmation": False,
                "confirmation_required_for": []}
    if not evidence_usable:
        return {"verdict": "unknown", "verdict_meaning": VERDICT_MEANING["unknown"],
                "reasons": ["image evidence is unusable (uniform or empty)"],
                "requires_confirmation": False, "confirmation_required_for": []}
    flagged = [r for r in regions if r["classification"] in
               ("candidate_absent_region", "candidate_newly_occupied", "candidate_displaced")]
    weak = [r for r in regions if r["strength"] in ("weak", "contradictory")]
    pending = sorted({r["label"] for r in flagged})
    if weak:
        return {"verdict": "ambiguous", "verdict_meaning": VERDICT_MEANING["ambiguous"],
                "reasons": [f"{len(weak)} region(s) rest on weak or contradictory evidence"]
                + ([f"{len(flagged)} unconfirmed candidate(s)"] if flagged else []),
                "requires_confirmation": bool(pending),
                "confirmation_required_for": pending}
    if flagged:
        return {"verdict": "ok", "verdict_meaning": VERDICT_MEANING["ok"],
                "reasons": [f"{len(flagged)} unconfirmed candidate(s); human confirmation required"],
                "requires_confirmation": True, "confirmation_required_for": pending}
    return {"verdict": "ok", "verdict_meaning": VERDICT_MEANING["ok"],
            "reasons": ["no candidate regions; states match within sensitivity"],
            "requires_confirmation": False, "confirmation_required_for": []}
