"""Connector matching: complementary-geometry ranking with verdicts.

Each candidate reports a visual similarity, a mating-geometry score
built from transparent components, and two verification flags that
are ALWAYS false here: visual similarity alone never establishes
mechanical or electrical compatibility, and the best rank never
proves that two observations show mating parts.
"""

from __future__ import annotations

import math

import numpy as np

from anastylos_connectors.observe import OBSERVATION_VERSION

DEFAULT_WEIGHTS = {"housing": 0.15, "count": 0.20, "layout": 0.20,
                   "keying": 0.10, "role": 0.15, "pitch": 0.10, "text": 0.10}
ACCEPT_SCORE = 0.55
MARGIN = 0.10

COMPAT_NOTE = ("Scores are uncalibrated visual/mating-geometry heuristics. "
               "A top rank is not proof of mechanical fit and never of "
               "electrical compatibility; both need verification outside "
               "this module.")

# Mating transforms on canonical (u, v): two faces that mate are mirror
# images of each other, up to the canonical frame's own 180-degree
# ambiguity. Only these two orientation-reversing maps are tried, so
# same-handed layouts (plug against plug geometry) do not score as
# mating. Swaps are excluded: the canonical frame already normalizes
# relative roll, and near-diagonal points would false-align under a
# swap. Near-square housings weaken frame stability (see README).
MATING_TRANSFORMS = {
    "mirror": lambda u, v: (1 - u, v),
    "mirror_rot180": lambda u, v: (u, 1 - v),
}


def resolve_weights(overrides):
    """Merge caller weights over the defaults; reject unknown names."""
    weights = dict(DEFAULT_WEIGHTS)
    for name, value in (overrides or {}).items():
        if name not in weights:
            raise ValueError(f"unknown similarity component {name!r}; "
                             f"known: {sorted(weights)}")
        if not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"weight for {name!r} must be a non-negative number")
        weights[name] = float(value)
    if sum(weights.values()) <= 0:
        raise ValueError("at least one similarity weight must be positive")
    return weights


def _falloff(distance):
    return float(round(1.0 / (1.0 + max(float(distance), 0.0)), 6))


def _housing(query, gallery):
    first, second = query["housing"], gallery["housing"]
    if first is None or second is None:
        return None, "housing unavailable on one side"
    aspect = abs(first["aspect"] - second["aspect"]) / max(first["aspect"], second["aspect"])
    solidity = abs(first["solidity"] - second["solidity"]) * 2.0
    vertices = abs(first["vertices"] - second["vertices"]) / 8.0
    color = math.sqrt(sum((a - b) ** 2 for a, b in
                          zip(query["color"]["mean_bgr"], gallery["color"]["mean_bgr"]))) / 441.0
    dist = aspect + solidity + vertices + color
    return _falloff(dist), f"housing distance {round(dist, 4)}"


def _count(query, gallery):
    if not query["mating_face_visible"] or not gallery["mating_face_visible"]:
        return None, "mating face not visible on one side"
    first, second = query["contact_count"], gallery["contact_count"]
    if max(first, second) == 0:
        return None, "no contacts on either side"
    score = 1.0 - abs(first - second) / max(first, second)
    return round(score, 6), f"counts {first} vs {second}"


def _chamfer(first, second):
    if not first or not second:
        return None
    total = 0.0
    for point in first:
        total += min(float(np.hypot(point[0] - other[0], point[1] - other[1]))
                     for other in second)
    for point in second:
        total += min(float(np.hypot(point[0] - other[0], point[1] - other[1]))
                     for other in first)
    return float(total / (len(first) + len(second)))


def _point_set(query, gallery, key):
    first = [item["cuv"] for item in query[key]]
    second = [item["cuv"] for item in gallery[key]]
    if not first and not second:
        return 1.0, f"no {key} on either side"
    if not first or not second:
        return 0.0, f"{key} present on one side only"
    best, name = None, ""
    for label, transform in MATING_TRANSFORMS.items():
        moved = [transform(u, v) for u, v in second]
        dist = _chamfer(first, moved)
        if best is None or dist < best:
            best, name = dist, label
    count_penalty = abs(len(first) - len(second)) / max(len(first), len(second))
    return _falloff(best * 4.0 + count_penalty), f"chamfer {round(best, 4)} via {name}"


def _layout(query, gallery):
    if not query["mating_face_visible"] or not gallery["mating_face_visible"]:
        return None, "mating face not visible on one side"
    first = [blob["cuv"] for blob in query["pins"] + query["cavities"]]
    second = [blob["cuv"] for blob in gallery["pins"] + gallery["cavities"]]
    if not first or not second:
        return None, "no contact positions on one side"
    best, name = None, ""
    for label, transform in MATING_TRANSFORMS.items():
        moved = [transform(u, v) for u, v in second]
        dist = _chamfer(first, moved)
        if best is None or dist < best:
            best, name = dist, label
    count_penalty = abs(len(first) - len(second)) / max(len(first), len(second))
    rows = (query["layout"]["rows"], gallery["layout"]["rows"])
    row_penalty = 0.0 if rows[0] == rows[1] else 0.5
    return _falloff(best * 4.0 + count_penalty + row_penalty), \
        f"chamfer {round(best, 4)} via {name}; rows {rows[0]} vs {rows[1]}"


def _keying(query, gallery):
    if query["housing"] is None or gallery["housing"] is None:
        return None, "housing unavailable on one side", 1.0
    notch_score, notch_detail = _point_set(query, gallery, "keying_notches")
    latch_score, latch_detail = _point_set(query, gallery, "latches")
    score = (notch_score + latch_score) / 2.0
    gate = 0.6 if notch_score < 0.5 else 1.0
    detail = f"notches [{notch_detail}]; latches [{latch_detail}]"
    if gate < 1.0:
        detail += "; keying conflict caps mating (x0.6)"
    return round(score, 6), detail, gate


def _role(query, gallery):
    first, second = query["role"], gallery["role"]
    if first == "unknown" or second == "unknown":
        return 0.5, f"role unconfirmed on one side ({first} vs {second})"
    if first != second:
        return 1.0, f"complementary roles ({first} vs {second})"
    return 0.0, f"same role on both sides ({first}); cannot mate"


def _pitch(query, gallery):
    first, second = query.get("pitch_mm"), gallery.get("pitch_mm")
    if first is None or second is None:
        return None, "calibrated pitch unavailable on one side"
    diff = abs(first - second) / max(first, second)
    return _falloff(diff * 4.0), f"pitch {first} vs {second} mm"


def _text(query, gallery):
    first, second = query.get("text"), gallery.get("text")
    if first is None or second is None:
        return None, "marking text not supplied on one side"
    same = first["normalized"] == second["normalized"]
    return (1.0 if same else 0.0), ("markings agree" if same else "markings differ")


def _visual(query, gallery):
    housing, _ = _housing(query, gallery)
    return housing if housing is not None else 0.0


def compare(query, gallery, weights=None):
    """Score one query observation against one gallery observation."""
    resolved = resolve_weights(weights)
    keying_score, keying_detail, keying_gate = _keying(query, gallery)
    parts = {"housing": _housing(query, gallery), "count": _count(query, gallery),
             "layout": _layout(query, gallery),
             "keying": (keying_score, keying_detail),
             "role": _role(query, gallery), "pitch": _pitch(query, gallery),
             "text": _text(query, gallery)}
    components = {name: {"score": score, "detail": detail, "weight": resolved[name]}
                  for name, (score, detail) in parts.items()}
    available = {name: item for name, item in components.items()
                 if item["score"] is not None and item["weight"] > 0}
    mass = sum(item["weight"] for item in available.values())
    mating = float(sum(item["score"] * item["weight"] for item in available.values()) / mass) \
        if available and mass > 0 else 0.0
    gates = []
    if keying_gate < 1.0 and components["keying"] in available.values():
        gates.append({"gate": "keying_conflict", "factor": keying_gate,
                      "reason": "keying does not align under any mating transform"})
    if components["role"]["score"] == 0.0:
        gates.append({"gate": "same_role", "factor": 0.5,
                      "reason": "same role on both sides cannot mate"})
    pins = query["contact_count"] if query["role"] == "plug" else None
    cavities = gallery["contact_count"] if gallery["role"] == "socket" else None
    if query["role"] == "socket" and gallery["role"] == "plug":
        pins, cavities = gallery["contact_count"], query["contact_count"]
    if pins is not None and cavities is not None and pins > cavities:
        gates.append({"gate": "contact_interference", "factor": 0.5,
                      "reason": f"{pins} pins cannot fit {cavities} cavities"})
    for gate in gates:
        mating *= gate["factor"]
    return {"mating_score": round(mating, 6), "visual_similarity": _visual(query, gallery),
            "verified_mechanical": False, "verified_electrical": False,
            "components": components, "used": sorted(available), "gates": gates}


def match_gallery(query, records, excluded=(), top_k=5, weights=None,
                  accept=ACCEPT_SCORE, margin=MARGIN):
    """Rank gallery observations for one query; deterministic verdicts."""
    if not query["quality"]["usable"]:
        return {"status": "unknown", "reason": query["quality"]["reason"],
                "candidates": [], "excluded": list(excluded),
                "query_quality": query["quality"], "compat_note": COMPAT_NOTE}
    if not query["mating_face_visible"]:
        return {"status": "unknown", "reason": "insufficient mating-face visibility",
                "candidates": [], "excluded": list(excluded),
                "query_quality": query["quality"], "compat_note": COMPAT_NOTE}
    scored = []
    for record in records:
        result = compare(query, record, weights)
        scored.append({"id": record["id"], "score": result["mating_score"],
                       "visual_similarity": result["visual_similarity"],
                       "verified_mechanical": False, "verified_electrical": False,
                       "components": result["components"], "used": result["used"],
                       "gates": result["gates"]})
    scored.sort(key=lambda item: (-item["score"], item["id"]))
    top = scored[:max(top_k, 1)]
    if not scored:
        return {"status": "unknown", "reason": "gallery holds no comparable observations",
                "candidates": [], "excluded": list(excluded),
                "query_quality": query["quality"], "compat_note": COMPAT_NOTE}
    best = scored[0]
    if best["score"] < accept:
        return {"status": "unknown",
                "reason": f"best mating score {best['score']} below acceptance {accept}",
                "candidates": top, "excluded": list(excluded),
                "query_quality": query["quality"], "compat_note": COMPAT_NOTE}
    tied = [item for item in scored if item["score"] == best["score"]]
    if len(tied) > 1:
        return {"status": "ambiguous",
                "reason": f"tie at {best['score']} between {[item['id'] for item in tied]}",
                "candidates": top, "excluded": list(excluded),
                "query_quality": query["quality"], "compat_note": COMPAT_NOTE}
    if len(scored) > 1 and best["score"] - scored[1]["score"] < margin:
        return {"status": "ambiguous",
                "reason": (f"rival {scored[1]['id']} within margin "
                           f"({best['score']} vs {scored[1]['score']})"),
                "candidates": top, "excluded": list(excluded),
                "query_quality": query["quality"], "compat_note": COMPAT_NOTE}
    return {"status": "ok", "reason": None, "candidates": top,
            "excluded": list(excluded), "query_quality": query["quality"],
            "compat_note": COMPAT_NOTE}


def check_version(record):
    """Refuse records from another observation generation, loudly."""
    version = record.get("observation_version")
    if version != OBSERVATION_VERSION:
        return f"observation version {version}, current is {OBSERVATION_VERSION}"
    return None
