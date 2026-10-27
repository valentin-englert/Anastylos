"""Compare two cable analyses of one registered frame. Stdlib only.

Matches cables across a before/after pair by centroid proximity and
reports polyline drift for matches plus added/removed cables.
Comparison is only valid when both results describe the same frame:
identical image size and coordinate convention. No cable identity is
claimed across different frames.
"""

from __future__ import annotations

import math

MATCH_RADIUS_PX = 40.0
RESAMPLE_POINTS = 50


def _resample(polyline: list, count: int) -> list:
    """Evenly spaced points along a polyline."""
    if len(polyline) < 2:
        return [polyline[0]] * count if polyline else []
    lengths = [0.0]
    for i in range(1, len(polyline)):
        lengths.append(lengths[-1] + math.hypot(
            polyline[i][0] - polyline[i - 1][0],
            polyline[i][1] - polyline[i - 1][1]))
    total = lengths[-1] or 1.0
    points, index = [], 0
    for step in range(count):
        target = total * step / max(count - 1, 1)
        while index < len(lengths) - 2 and lengths[index + 1] < target:
            index += 1
        first, second = polyline[index], polyline[index + 1]
        span = lengths[index + 1] - lengths[index] or 1.0
        mix = (target - lengths[index]) / span
        points.append([first[0] + (second[0] - first[0]) * mix,
                       first[1] + (second[1] - first[1]) * mix])
    return points


def polyline_drift(first: list, second: list) -> dict:
    """Mean/max point distance between resampled polylines."""
    sample_a = _resample(first, RESAMPLE_POINTS)
    sample_b = _resample(second, RESAMPLE_POINTS)
    gaps = [math.hypot(a[0] - b[0], a[1] - b[1])
            for a, b in zip(sample_a, sample_b)]
    gaps_forward = [min(math.hypot(a[0] - b[0], a[1] - b[1]) for b in sample_b)
                    for a in sample_a]
    gaps_backward = [min(math.hypot(a[0] - b[0], a[1] - b[1]) for a in sample_a)
                     for b in sample_b]
    both = gaps_forward + gaps_backward
    return {"mean_px": round(sum(both) / len(both), 2),
            "max_px": round(max(both), 2),
            "endpoint_order_px": round(sum(gaps) / len(gaps), 2)}


def _centroid(cable: dict):
    points = [point for branch in cable["branches"]
              for point in branch["polyline_xy"]]
    if not points:
        return None
    return [sum(p[0] for p in points) / len(points),
            sum(p[1] for p in points) / len(points)]


def compare(before: dict, after: dict) -> dict:
    """Match cables before/after; drift stats per match. JSON-serializable."""
    if before.get("image_size") != after.get("image_size"):
        return {"status": "unavailable",
                "reason": "frame mismatch: analyses describe different "
                          "image sizes; registration is not valid",
                "matches": [], "added": [], "removed": []}
    if before.get("coordinate_convention") != after.get("coordinate_convention"):
        return {"status": "unavailable",
                "reason": "coordinate convention mismatch",
                "matches": [], "added": [], "removed": []}
    before_cables = before.get("cables", [])
    after_cables = after.get("cables", [])
    before_points = [_centroid(cable) for cable in before_cables]
    after_points = [_centroid(cable) for cable in after_cables]
    scored = []
    for i, first in enumerate(before_points):
        for j, second in enumerate(after_points):
            if first is None or second is None:
                continue
            gap = math.hypot(first[0] - second[0], first[1] - second[1])
            if gap <= MATCH_RADIUS_PX:
                scored.append((gap, i, j))
    scored.sort()
    used_before, used_after, matches = set(), set(), []
    for gap, i, j in scored:
        if i in used_before or j in used_after:
            continue
        used_before.add(i)
        used_after.add(j)
        first = before_cables[i]["branches"][0]["polyline_xy"] \
            if before_cables[i]["branches"] else []
        second = after_cables[j]["branches"][0]["polyline_xy"] \
            if after_cables[j]["branches"] else []
        drift = polyline_drift(first, second) if first and second else None
        matches.append({"before": before_cables[i]["cable_id"],
                        "after": after_cables[j]["cable_id"],
                        "centroid_gap_px": round(gap, 1),
                        "drift": drift,
                        "note": "same-frame correspondence candidate; "
                                "not a persistent physical identity"})
    matches.sort(key=lambda item: item["centroid_gap_px"])
    removed = [cable["cable_id"] for index, cable in enumerate(before_cables)
               if index not in used_before]
    added = [cable["cable_id"] for index, cable in enumerate(after_cables)
             if index not in used_after]
    if not before_cables and not after_cables:
        status, reason = "unknown", "neither analysis found cables"
    else:
        status, reason = "ok", None
    return {"status": status, "reason": reason,
            "matches": matches, "added": added, "removed": removed,
            "explanation": f"{len(matches)} matched, {len(added)} added, "
                           f"{len(removed)} removed"}
