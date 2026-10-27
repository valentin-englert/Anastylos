"""Heuristic comparison of observed parts across two read outputs.

Matches observations by calibrated (or pixel) dimension similarity.
Scores are explicitly uncalibrated heuristics: similarity is not
identity, and never an assembly relationship. Comparisons require
both results to share a view per pair and a unit system overall.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

TOP_DIMS = ("outer_d", "inner_d")
SIDE_DIMS = ("length_px", "thickness_px")

DISCLAIMER = ("heuristic similarity only: similar dimensions never prove "
              "two observations are the same part, matching hardware, or "
              "related in assembly")


def _load(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if "observations" not in data or "view" not in data:
        raise ValueError(f"{path} is not a washer read result")
    return data


def _unit(result: dict) -> str:
    return "mm" if result.get("geometry_mm") else "px"


def _dims(observation: dict, unit: str, view: str,
          mm_lookup: dict) -> dict[str, tuple[float, float]]:
    keys = TOP_DIMS if view == "top" else SIDE_DIMS
    found = {}
    for key in keys:
        if unit == "mm":
            entry = mm_lookup.get(observation["observation_id"], {}).get(key)
            if entry is None:
                continue
            found[key] = (entry["value_mm"], entry["uncertainty_mm"])
        else:
            entry = observation["geometry"].get(key)
            if not isinstance(entry, dict):
                continue
            found[key] = (entry["value_px"], entry["uncertainty_px"])
    return found


def _mm_lookup(result: dict) -> dict:
    table = {}
    for entry in (result.get("geometry_mm") or {}).get("observations", []):
        table[entry["observation_ref"]] = entry["dimensions_mm"]
    return table


def _distance(left: dict, right: dict) -> float | None:
    shared = [key for key in left if key in right]
    if not shared:
        return None
    total = 0.0
    for key in shared:
        value_l, unc_l = left[key]
        value_r, unc_r = right[key]
        scale = max(unc_l, unc_r, 1e-9)
        total += ((value_l - value_r) / scale) ** 2
    return math.sqrt(total / len(shared))


def compare_files(baseline: str | Path, candidate: str | Path) -> dict:
    """Match observations across two read results. JSON-serializable."""
    left, right = _load(baseline), _load(candidate)
    unit_l, unit_r = _unit(left), _unit(right)
    if unit_l != unit_r:
        raise ValueError(f"incomparable units: {unit_l} versus {unit_r} "
                         f"(calibrate both or neither)")
    lookup_l, lookup_r = _mm_lookup(left), _mm_lookup(right)
    used: set[str] = set()
    pairs = []
    for obs_l in left["observations"]:
        best, best_dist = None, None
        dims_l = _dims(obs_l, unit_l, left["view"], lookup_l)
        for obs_r in right["observations"]:
            if obs_r["observation_id"] in used:
                continue
            if right["view"] != left["view"]:
                continue
            dist = _distance(
                dims_l, _dims(obs_r, unit_r, right["view"], lookup_r))
            if dist is not None and (best_dist is None or dist < best_dist):
                best, best_dist = obs_r, dist
        if best is None:
            continue
        used.add(best["observation_id"])
        dims_r = _dims(best, unit_r, right["view"], lookup_r)
        deltas = {key: {"baseline": dims_l[key][0],
                        "candidate": dims_r[key][0],
                        "delta": round(dims_l[key][0] - dims_r[key][0], 3)}
                  for key in dims_l if key in dims_r}
        pairs.append(
            {"baseline_ref": obs_l["observation_id"],
             "candidate_ref": best["observation_id"], "view": left["view"],
             "unit": unit_l, "deltas": deltas,
             "similarity_heuristic": round(1.0 / (1.0 + (best_dist or 0)),
                                           3),
             "baseline_top": obs_l.get("top_family"),
             "candidate_top": best.get("top_family")})
    return {"pairs": pairs,
            "unmatched_baseline": [obs["observation_id"]
                                   for obs in left["observations"]
                                   if obs["observation_id"]
                                   not in {p["baseline_ref"] for p in pairs}],
            "unmatched_candidate": [obs["observation_id"]
                                    for obs in right["observations"]
                                    if obs["observation_id"] not in used],
            "unit": unit_l, "disclaimer": DISCLAIMER}
