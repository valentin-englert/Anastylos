"""Count tray additions/removals from detection output. OpenCV + NumPy only.

Consumes one change-detection pair directory (result.json plus rectified
before/after images) and reports per-tray candidate events. Observations
(detected regions) are kept separate from inference: counts are never
invented, and a removal-to-addition pairing is only ever proposed, never
asserted.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

UNTRUSTED = ("registration_invalid", "view_mismatch", "input_error")
COUNT_NOTE = "one region may hold several overlapping objects; no count is inferred"


def load_result(path: str | Path) -> dict:
    try:
        result = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read result file {path}: {exc}") from exc
    if not isinstance(result, dict):
        raise ValueError(f"result file {path} is not a JSON object")
    return result


def _intersect(bbox: list, rect: list) -> float:
    x0, y0 = max(bbox[0], rect[0]), max(bbox[1], rect[1])
    x1, y1 = min(bbox[0] + bbox[2], rect[0] + rect[2]), min(bbox[1] + bbox[3], rect[1] + rect[3])
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def overlap_fraction(bbox_mm: list, rect_mm: list) -> float:
    area = bbox_mm[2] * bbox_mm[3]
    if area <= 0:
        return 0.0
    return _intersect(bbox_mm, rect_mm) / area


def mm_to_px(rect_mm: list, rectified: dict) -> tuple[int, int, int, int]:
    ppm = rectified["px_per_mm"]
    x0, y0 = rectified["origin_frame_mm"]
    x, y = (rect_mm[0] - x0) * ppm, (rect_mm[1] - y0) * ppm
    return int(round(x)), int(round(y)), int(round(rect_mm[2] * ppm)), int(round(rect_mm[3] * ppm))


def measure_delta(before: np.ndarray, after: np.ndarray, box_px: tuple[int, int, int, int]) -> dict:
    x, y, w, h = box_px
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(before.shape[1], x + w), min(before.shape[0], y + h)
    if x1 <= x0 or y1 <= y0:
        return {"delta": None, "pixels": 0}
    gray_before = cv2.cvtColor(before[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY).astype(float)
    gray_after = cv2.cvtColor(after[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY).astype(float)
    return {"delta": round(float(gray_after.mean() - gray_before.mean()), 2),
            "pixels": int(gray_before.size)}


def classify(delta: float | None, threshold: float, surface: str) -> str:
    if delta is None or abs(delta) < threshold:
        return "ambiguous"
    darkened = delta < 0
    if surface == "dark":
        darkened = not darkened
    return "addition" if darkened else "removal"


def _rectified_pair(pair_dir: Path, result: dict) -> tuple[np.ndarray | None, np.ndarray | None, dict]:
    files = result.get("output_files") or {}
    names = {"before": files.get("before_rectified", "before_rectified.jpg"),
             "after": files.get("after_rectified", "after_rectified.jpg")}
    images = {}
    for key, name in names.items():
        path = pair_dir / name
        images[key] = cv2.imread(str(path)) if path.is_file() else None
    return images["before"], images["after"], names


def _zone_verdict(hits: list[dict], images_ok: bool) -> tuple[str, str | None]:
    if not hits:
        return "unchanged", None
    if not images_ok:
        return "unknown", "rectified images unavailable; regions cannot be classified"
    kinds = {hit["event"] for hit in hits}
    if kinds == {"addition"}:
        return "addition", None
    if kinds == {"removal"}:
        return "removal", None
    return "ambiguous", "mixed or weak candidate events in one tray"


def _assess_zone(zone: dict, zone_id: str, result: dict, regions: list[dict],
                 view: str, images, names: dict, pair_dir: Path, params: dict) -> dict:
    if zone["frame"] != view:
        return {"tray_id": zone_id, "frame": zone["frame"], "verdict": "not_observed",
                "reason": f"pair shows {view}, zone lives on {zone['frame']}",
                "candidates": [], "count_estimate": None, "count_note": COUNT_NOTE}
    before, after = images
    images_ok = before is not None and after is not None and before.shape == after.shape
    rectified = result["rectified"]
    reg = [result.get("registration_before") or {}, result.get("registration_after") or {}]
    rms = [item.get("reprojection_rms_mm") for item in reg if item.get("reprojection_rms_mm") is not None]
    hits = []
    for region in regions:
        overlap = round(overlap_fraction(region["bbox_mm"], zone["rect_frame_mm"]), 3)
        if overlap <= 0:
            continue
        measurement = measure_delta(before, after, mm_to_px(region["bbox_mm"], rectified)) if images_ok else {"delta": None, "pixels": 0}
        event = classify(measurement["delta"], params["delta_threshold"], params["surface"]) if images_ok else "unknown"
        note = None
        if overlap < params["min_overlap"]:
            event, note = "ambiguous", f"boundary crossing: overlap {overlap} below {params['min_overlap']}"
        elif event == "ambiguous":
            note = "signal below threshold; possible occlusion, glare, or noise"
        hits.append({
            "event": event,
            "note": note,
            "region": region.get("label"),
            "bbox_mm": list(region["bbox_mm"]),
            "centroid_mm": list(region["centroid_mm"]),
            "area_mm2": region.get("area_mm2"),
            "overlap": overlap,
            "delta": measurement["delta"],
            "sign": region.get("sign"),
            "quality": {"method": "rectified_delta" if images_ok else "unavailable",
                        "threshold": params["delta_threshold"],
                        "pixels": measurement["pixels"],
                        "reprojection_rms_mm": rms or None},
            "evidence": {"result_json": str(pair_dir / "result.json"),
                         "before_rectified": str(pair_dir / names["before"]),
                         "after_rectified": str(pair_dir / names["after"])},
        })
    verdict, reason = _zone_verdict(hits, images_ok)
    return {"tray_id": zone_id, "frame": zone["frame"], "verdict": verdict, "reason": reason,
            "candidates": hits, "count_estimate": None, "count_note": COUNT_NOTE}


def _propose_links(device: dict, trays: list[dict]) -> list[dict]:
    removals = [hit for hit in device.get("candidates", []) if hit["event"] == "removal"]
    proposals = []
    for tray in trays:
        additions = [hit for hit in tray.get("candidates", []) if hit["event"] == "addition"]
        for taken in removals:
            for placed in additions:
                areas = (taken.get("area_mm2"), placed.get("area_mm2"))
                ratio = round(min(areas) / max(areas), 3) if all(a and a > 0 for a in areas) else None
                proposals.append({
                    "from_device_region": taken["region"],
                    "to_tray": tray["tray_id"],
                    "to_tray_region": placed["region"],
                    "basis": "removal and addition observed in the same before/after pair",
                    "area_ratio": ratio,
                    "requires_confirmation": True,
                    "assertion": "not the same object; co-occurrence only",
                })
    return proposals


def analyze_pair(result_path: str | Path, config: dict) -> dict:
    """Assess one detection pair directory against the tray configuration."""
    result_path = Path(result_path)
    pair_dir = result_path.parent
    result = load_result(result_path)
    status = result.get("status")
    answer: dict = {"pair_id": result.get("pair_id") or pair_dir.name,
                    "detection_status": status, "trays": [],
                    "device": None, "proposed_links": []}
    if status in UNTRUSTED or not isinstance(result.get("rectified"), dict):
        reason = f"detection {status or 'output'} is unusable: {status or 'missing rectified frame'}"
        for tray in config["trays"]:
            answer["trays"].append({"tray_id": tray["id"], "frame": tray["frame"],
                                    "verdict": "unknown", "reason": reason,
                                    "candidates": [], "count_estimate": None, "count_note": COUNT_NOTE})
        if config["device_area"] is not None:
            answer["device"] = {"tray_id": "device", "frame": config["device_area"]["frame"],
                                "verdict": "unknown", "reason": reason,
                                "candidates": [], "count_estimate": None, "count_note": COUNT_NOTE}
        return answer
    view = result["rectified"].get("view") or result.get("view")
    regions = result.get("regions") or []
    before, after, names = _rectified_pair(pair_dir, result)
    images = (before, after)
    for tray in config["trays"]:
        answer["trays"].append(_assess_zone(tray, tray["id"], result, regions, view,
                                            images, names, pair_dir, config["params"]))
    if config["device_area"] is not None:
        answer["device"] = _assess_zone(config["device_area"], "device", result, regions, view,
                                        images, names, pair_dir, config["params"])
        answer["proposed_links"] = _propose_links(answer["device"], answer["trays"])
    return answer


def find_results(pairs_dir: Path) -> list[Path]:
    return sorted(path for path in Path(pairs_dir).glob("*/result.json") if path.is_file())
