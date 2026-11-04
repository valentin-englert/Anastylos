"""Labelled evaluation for cable extraction and assembly graphs.

Label files are human-authored JSON; the workflows here only measure.
Synthetic fixture success is pipeline self-consistency, never evidence
of real-hardware accuracy.

Cable label schema:
  {"images": [{"file": "photo01.png",
               "cables": [{"polyline_xy": [[x, y], ...],
                           "width_px": 12.0}],
               "features": [{"kind": "crossing", "xy": [x, y]}]}]}

Graph fixture layout: <name>/memory.jsonl, <name>/results/*.json,
<name>/confirmations.json (optional), <name>/expected.json with
{"confirmed": [["before", "after"], ...], "reassembly_valid": true}.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import cv2

from anastylos_cable.cables import analyze
from anastylos_cable.compare import polyline_drift
from anastylos_cable.graph import build_graph

CABLE_MATCH_PX = 30.0
FEATURE_MATCH_PX = 15.0


def _centroid(points):
    return [sum(p[0] for p in points) / len(points),
            sum(p[1] for p in points) / len(points)]


def _match_cables(predicted: list, labeled: list) -> list:
    scored = []
    for i, first in enumerate(predicted):
        first_points = [point for branch in first["branches"]
                        for point in branch["polyline_xy"]]
        if not first_points:
            continue
        center = _centroid(first_points)
        for j, second in enumerate(labeled):
            if not second["polyline_xy"]:
                continue
            gap = math.hypot(center[0] - _centroid(second["polyline_xy"])[0],
                             center[1] - _centroid(second["polyline_xy"])[1])
            if gap <= CABLE_MATCH_PX:
                scored.append((gap, i, j))
    scored.sort()
    used, matches = set(), []
    for gap, i, j in scored:
        if i in used or any(item[1] == j for item in matches):
            continue
        used.add(i)
        matches.append((i, j, gap))
    return matches


def evaluate_cables(labels_path: str, images_dir: str | None = None) -> dict:
    """Score cable extraction against hand labels. JSON-serializable."""
    labels_file = Path(labels_path)
    base = Path(images_dir) if images_dir else labels_file.parent
    labels = json.loads(labels_file.read_text(encoding="utf-8"))
    per_image, totals = [], {"matched": 0, "missed": 0, "extra": 0,
                             "drift_px": [], "width_err_px": [],
                             "features_hit": 0, "features_missed": 0,
                             "features_extra": 0}
    for entry in labels.get("images", []):
        image = cv2.imread(str(base / entry["file"]), cv2.IMREAD_COLOR)
        if image is None:
            per_image.append({"file": entry["file"], "error": "unreadable"})
            continue
        result = analyze(image)
        labeled = entry.get("cables", [])
        matches = _match_cables(result["cables"], labeled)
        matched_labeled = {j for _, j, _ in matches}
        drifts, widths = [], []
        for i, j, _ in matches:
            first = result["cables"][i]["branches"][0]["polyline_xy"] \
                if result["cables"][i]["branches"] else []
            drift = polyline_drift(first, labeled[j]["polyline_xy"]) \
                if first and labeled[j]["polyline_xy"] else None
            if drift:
                drifts.append(drift["mean_px"])
            predicted_width = result["cables"][i]["branches"][0].get(
                "width_px") if result["cables"][i]["branches"] else None
            expected_width = labeled[j].get("width_px")
            if predicted_width is not None and expected_width:
                widths.append(abs(predicted_width - expected_width))
        wanted = entry.get("features", [])
        hits, used_pred = 0, set()
        for want in wanted:
            for index, got in enumerate(result["features"]):
                if index in used_pred or got["kind"] != want["kind"]:
                    continue
                if math.hypot(got["xy"][0] - want["xy"][0],
                              got["xy"][1] - want["xy"][1]
                              ) <= FEATURE_MATCH_PX:
                    hits += 1
                    used_pred.add(index)
                    break
        totals["matched"] += len(matches)
        totals["missed"] += len(labeled) - len(matched_labeled)
        totals["extra"] += len(result["cables"]) - len(matches)
        totals["drift_px"].extend(drifts)
        totals["width_err_px"].extend(widths)
        totals["features_hit"] += hits
        totals["features_missed"] += len(wanted) - hits
        totals["features_extra"] += len(result["features"]) - hits
        per_image.append({"file": entry["file"], "status": result["status"],
                          "matched": len(matches),
                          "missed": len(labeled) - len(matched_labeled),
                          "extra": len(result["cables"]) - len(matches),
                          "mean_drift_px": round(sum(drifts) / len(drifts), 2)
                          if drifts else None,
                          "features": {"hit": hits,
                                       "missed": len(wanted) - hits,
                                       "extra": len(result["features"]) - hits}})
    drifts = totals.pop("drift_px")
    widths = totals.pop("width_err_px")
    summary = {**totals,
               "mean_drift_px": round(sum(drifts) / len(drifts), 2)
               if drifts else None,
               "mean_width_err_px": round(sum(widths) / len(widths), 2)
               if widths else None}
    return {"labels": str(labels_file), "images": per_image,
            "summary": summary}


def _respects(order: list, edges: list) -> bool:
    position = {node: index for index, node in enumerate(order)}
    return all(position[edge["before"]] < position[edge["after"]]
               for edge in edges
               if edge["before"] in position and edge["after"] in position)


def evaluate_graph(fixtures_dir: str) -> dict:
    """Score graph building against labelled fixtures. JSON-serializable."""
    per_fixture, totals = [], {"confirmed_hit": 0, "confirmed_missed": 0,
                               "confirmed_extra": 0, "valid_orders": 0,
                               "fixtures": 0}
    for fixture in sorted(Path(fixtures_dir).iterdir()):
        if not fixture.is_dir():
            continue
        memory = fixture / "memory.jsonl"
        expected_file = fixture / "expected.json"
        if not memory.is_file() or not expected_file.is_file():
            continue
        expected = json.loads(expected_file.read_text(encoding="utf-8"))
        confirmations = fixture / "confirmations.json"
        graph = build_graph(
            str(memory), results_dir=str(fixture / "results"),
            confirmations_path=str(confirmations)
            if confirmations.is_file() else None)
        want = {tuple(edge) for edge in expected.get("confirmed", [])}
        got = {(edge["before"], edge["after"])
               for edge in graph.get("confirmed", [])}
        order = (graph.get("orderings") or {}).get("canonical_disassembly",
                                                  [])
        valid = _respects(order, [{"before": b, "after": a}
                                 for b, a in want]) if order else False
        totals["confirmed_hit"] += len(want & got)
        totals["confirmed_missed"] += len(want - got)
        totals["confirmed_extra"] += len(got - want)
        totals["valid_orders"] += 1 if valid else 0
        totals["fixtures"] += 1
        per_fixture.append({"fixture": fixture.name,
                            "status": graph["status"],
                            "confirmed_hit": len(want & got),
                            "confirmed_missed": sorted(want - got),
                            "confirmed_extra": sorted(got - want),
                            "order_valid": valid,
                            "unresolved": ((graph.get("reassembly") or {})
                                           .get("unresolved", []))})
    return {"fixtures_dir": str(fixtures_dir), "fixtures": per_fixture,
            "summary": totals}
