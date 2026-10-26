"""Fingerprint matching: transparent similarity, ranking, verdicts.

Every candidate reports individual similarity components plus the
combined heuristic score. The score is a weighted mean of available
components — it is NOT a calibrated probability, and the best score
is never proof that two images show the same physical object:
visually identical components legitimately tie.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from anastylos_fingerprints.describe import DESCRIPTOR_VERSION

DEFAULT_WEIGHTS = {"silhouette": 0.20, "geometry": 0.15, "size": 0.10,
                   "color": 0.10, "appearance": 0.10, "local": 0.25, "text": 0.10}
DEFAULT_EMBEDDING_WEIGHT = 0.10
ACCEPT_SCORE = 0.55
MARGIN = 0.10
HAMMING_LIMIT = 48

IDENTITY_NOTE = ("Similarity scores are uncalibrated heuristics. The best score "
                 "is not proof that two images show the same physical object; "
                 "identical-looking components stay ambiguous without further evidence.")


def resolve_weights(overrides):
    """Merge caller weights over the defaults; reject unknown names."""
    weights = dict(DEFAULT_WEIGHTS)
    for name, value in (overrides or {}).items():
        if name not in weights and not name.startswith("embedding:"):
            raise ValueError(f"unknown similarity component {name!r}; "
                             f"known: {sorted(weights)} plus 'embedding:<name>'")
        if not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"weight for {name!r} must be a non-negative number")
        weights[name] = float(value)
    if sum(weights.values()) <= 0:
        raise ValueError("at least one similarity weight must be positive")
    return weights


def _falloff(distance):
    return round(1.0 / (1.0 + max(distance, 0.0)), 6)


def _silhouette(query, gallery):
    if query["hu"] is None or gallery["hu"] is None:
        return None, "silhouette unavailable on one side"
    # hu1-hu3 only: hu4-hu7 swing wildly (hu7 flips sign) on rasterization noise.
    dist = float(np.linalg.norm(np.array(query["hu"][:3]) - np.array(gallery["hu"][:3])))
    return _falloff(dist), f"hu1-3 distance {round(dist, 4)}"


def _geometry(query, gallery):
    first, second = query["shape"], gallery["shape"]
    if first is None or second is None:
        return None, "shape unavailable on one side"
    aspect = abs(first["aspect"] - second["aspect"]) / max(first["aspect"], second["aspect"], 1e-9)
    vertices = abs(first["vertices"] - second["vertices"]) / 8.0
    defects = abs(first["defects"] - second["defects"]) / 6.0
    solidity = abs(first["solidity"] - second["solidity"]) * 2.0
    ellipse = abs(first["ellipse_ratio"] - second["ellipse_ratio"]) / max(
        first["ellipse_ratio"], second["ellipse_ratio"], 1e-9)
    holes = abs(query["holes"]["count"] - gallery["holes"]["count"]) / 4.0 \
        if query["holes"] and gallery["holes"] else 0.0
    dist = aspect + vertices + defects + solidity + ellipse + holes
    return _falloff(dist), f"geometry distance {round(dist, 4)}"


def _size(query, gallery):
    first, second = query.get("size_mm"), gallery.get("size_mm")
    if first is None or second is None:
        return None, "calibrated size unavailable on one side; pixels never compared as mm"
    diffs = [abs(a - b) / max(a, b, 1e-9) for a, b in zip(first, second)]
    return _falloff(sum(diffs) / len(diffs)), f"mean relative size difference {round(sum(diffs) / len(diffs), 4)}"


def _bhattacharyya(first, second):
    first = np.array(first, dtype=float)
    second = np.array(second, dtype=float)
    total = (first.sum(), second.sum())
    if total[0] <= 0 or total[1] <= 0:
        return 1.0
    coefficient = float(np.sqrt(first / total[0] * (second / total[1])).sum())
    return math.sqrt(max(0.0, 1.0 - min(coefficient, 1.0)))


def _color(query, gallery):
    channels = ["h", "s", "v"]
    if query["color"]["mean_saturation"] < 40 and gallery["color"]["mean_saturation"] < 40:
        channels = ["s", "v"]
    dist = float(np.mean([_bhattacharyya(query["color"][ch], gallery["color"][ch])
                          for ch in channels]))
    return round(1.0 - dist, 6), f"mean bhattacharyya distance {round(dist, 4)} over {''.join(channels)}"


def _ncc(first, second):
    first = first.astype(float)
    second = second.astype(float)
    if first.std() == 0 or second.std() == 0:
        return None
    return float(((first - first.mean()) * (second - second.mean())).mean()
                 / (first.std() * second.std()))


def _appearance(query, gallery):
    size = int(math.sqrt(len(query["thumb"]["pixels"])))
    first = np.array(query["thumb"]["pixels"], dtype=np.float32).reshape(size, size)
    second = np.array(gallery["thumb"]["pixels"], dtype=np.float32).reshape(size, size)
    center = ((size - 1) / 2.0, (size - 1) / 2.0)
    best, angle = None, 0
    for deg in range(0, 360, 45):
        matrix = cv2.getRotationMatrix2D(center, float(deg), 1.0)
        turned = cv2.warpAffine(second, matrix, (size, size),
                                flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        value = _ncc(first, turned)
        if value is not None and (best is None or value > best):
            best, angle = value, deg
    if best is None:
        return None, "thumbnails have no contrast"
    return round(max(0.0, min(1.0, best)), 6), f"best ncc over 8 rotations at {angle} deg"


def _local(query, gallery):
    first = [bytes.fromhex(item) for item in query["orb"]["descriptors"]]
    second = [bytes.fromhex(item) for item in gallery["orb"]["descriptors"]]
    if len(first) < 4 or len(second) < 4:
        return None, "fewer than 4 ORB descriptors on one side"
    matrix = lambda rows: np.frombuffer(b"".join(rows), dtype=np.uint8).reshape(len(rows), -1)
    matches = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(matrix(first), matrix(second))
    good = sum(1 for item in matches if item.distance <= HAMMING_LIMIT)
    score = 2 * good / (len(first) + len(second))
    return round(score, 6), f"{good} cross-checked matches of {len(first)}/{len(second)}"


def _text(query, gallery):
    first, second = query.get("text"), gallery.get("text")
    if first is None or second is None:
        return None, "marking text not supplied on one side"
    same = first["normalized"] == second["normalized"]
    return (1.0 if same else 0.0), ("markings agree" if same else "markings differ")


def _embeddings(query, gallery):
    shared = [name for name in query.get("embeddings", {})
              if name in gallery.get("embeddings", {})
              and gallery["embeddings"][name]["version"] == query["embeddings"][name]["version"]]
    out = {}
    for name in sorted(shared):
        first = np.array(query["embeddings"][name]["vector"], dtype=float)
        second = np.array(gallery["embeddings"][name]["vector"], dtype=float)
        if first.shape != second.shape or np.linalg.norm(first) == 0 or np.linalg.norm(second) == 0:
            out[name] = (None, "embedding shapes differ or carry no signal")
            continue
        cosine = float(first @ second / (np.linalg.norm(first) * np.linalg.norm(second)))
        out[name] = (round(max(0.0, min(1.0, cosine)), 6), "cosine similarity")
    return out


def compare(query, gallery, weights=None):
    """Score one query record against one gallery record."""
    resolved = resolve_weights(weights)
    parts = {"silhouette": _silhouette(query, gallery), "geometry": _geometry(query, gallery),
             "size": _size(query, gallery), "color": _color(query, gallery),
             "appearance": _appearance(query, gallery), "local": _local(query, gallery),
             "text": _text(query, gallery)}
    components = {name: {"score": score, "detail": detail, "weight": resolved[name]}
                  for name, (score, detail) in parts.items()}
    for name, (score, detail) in _embeddings(query, gallery).items():
        key = f"embedding:{name}"
        components[key] = {"score": score, "detail": detail,
                           "weight": resolved.get(key, DEFAULT_EMBEDDING_WEIGHT)}
    available = {name: item for name, item in components.items()
                 if item["score"] is not None and item["weight"] > 0}
    mass = sum(item["weight"] for item in available.values())
    if not available or mass <= 0:
        return {"score": 0.0, "components": components, "used": []}
    combined = sum(item["score"] * item["weight"] for item in available.values()) / mass
    return {"score": round(combined, 6), "components": components,
            "used": sorted(available)}


def match_gallery(query, records, excluded=(), top_k=5, weights=None,
                  accept=ACCEPT_SCORE, margin=MARGIN):
    """Rank gallery records for one query record; deterministic verdicts."""
    if not query["quality"]["usable"]:
        return {"status": "rejected", "reason": query["quality"]["reason"],
                "candidates": [], "excluded": list(excluded),
                "query_quality": query["quality"], "identity_note": IDENTITY_NOTE}
    scored = []
    for record in records:
        result = compare(query, record, weights)
        scored.append({"id": record["id"], "score": result["score"],
                       "components": result["components"], "used": result["used"],
                       "size_source": record.get("size_source")})
    scored.sort(key=lambda item: (-item["score"], item["id"]))
    top = scored[:max(top_k, 1)]
    if not scored:
        return {"status": "unknown", "reason": "gallery holds no comparable fingerprints",
                "candidates": [], "excluded": list(excluded),
                "query_quality": query["quality"], "identity_note": IDENTITY_NOTE}
    best = scored[0]
    if best["score"] < accept:
        return {"status": "unknown",
                "reason": f"best score {best['score']} below acceptance {accept}",
                "candidates": top, "excluded": list(excluded),
                "query_quality": query["quality"], "identity_note": IDENTITY_NOTE}
    tied = [item for item in scored if item["score"] == best["score"]]
    if len(tied) > 1:
        return {"status": "ambiguous",
                "reason": f"tie at {best['score']} between {[item['id'] for item in tied]}",
                "candidates": top, "excluded": list(excluded),
                "query_quality": query["quality"], "identity_note": IDENTITY_NOTE}
    if len(scored) > 1 and best["score"] - scored[1]["score"] < margin:
        return {"status": "ambiguous",
                "reason": (f"rival {scored[1]['id']} within margin "
                           f"({best['score']} vs {scored[1]['score']})"),
                "candidates": top, "excluded": list(excluded),
                "query_quality": query["quality"], "identity_note": IDENTITY_NOTE}
    return {"status": "ok", "reason": None, "candidates": top,
            "excluded": list(excluded), "query_quality": query["quality"],
            "identity_note": IDENTITY_NOTE}


def check_version(record):
    """Refuse records from another descriptor generation, loudly."""
    version = record.get("descriptor_version")
    if version != DESCRIPTOR_VERSION:
        return f"descriptor version {version}, current is {DESCRIPTOR_VERSION}"
    return None
