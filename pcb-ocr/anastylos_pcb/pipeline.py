"""OCR pipeline: preprocess, rotate, recognize, parse, localize."""

from __future__ import annotations

import cv2
import numpy as np

from anastylos_pcb import preprocess
from anastylos_pcb.designators import parse

ORIENTATIONS = (0, 90, 180, 270)
LOW_CONF = 40.0
MERGE_IOU = 0.4
SAME_INK_OVERLAP = 0.5


def _map_box(box: list[int], src_w: int, src_h: int, angle: int) -> list[int]:
    x, y, w, h = box
    corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    if angle == 90:
        mapped = [(cy, src_h - 1 - cx) for cx, cy in corners]
    elif angle == 180:
        mapped = [(src_w - 1 - cx, src_h - 1 - cy) for cx, cy in corners]
    elif angle == 270:
        mapped = [(src_w - 1 - cy, cx) for cx, cy in corners]
    else:
        mapped = corners
    xs, ys = [p[0] for p in mapped], [p[1] for p in mapped]
    return [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]


def _rotate(image: np.ndarray, angle: int) -> np.ndarray:
    if angle == 90:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    if angle == 180:
        return cv2.rotate(image, cv2.ROTATE_180)
    if angle == 270:
        return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return image


def _iou(a: list, b: list) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def _overlap_coefficient(a: list, b: list) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    smaller = min(a[2] * a[3], b[2] * b[3])
    return inter / smaller if smaller > 0 else 0.0


def _to_source(box: list[int], crop_xy: tuple[int, int]) -> list[int]:
    scale = preprocess.UPSCALE
    ox, oy = crop_xy
    x, y, w, h = box
    return [int(round(x / scale + ox)), int(round(y / scale + oy)),
            int(round(w / scale)), int(round(h / scale))]


def _validate_frame_meta(meta: dict | None) -> dict | None:
    if meta is None:
        return None
    if not isinstance(meta, dict):
        raise ValueError("frame metadata must be an object")
    ppm, origin = meta.get("px_per_mm"), meta.get("origin_frame_mm")
    if not isinstance(ppm, (int, float)) or ppm <= 0:
        raise ValueError("frame metadata needs a positive 'px_per_mm'")
    if (not isinstance(origin, list) or len(origin) != 2
            or not all(isinstance(v, (int, float)) for v in origin)):
        raise ValueError("frame metadata needs 'origin_frame_mm' as [x0, y0]")
    if not isinstance(meta.get("view"), str) or not meta["view"].strip():
        raise ValueError("frame metadata needs a non-empty 'view'")
    return {"view": meta["view"], "px_per_mm": float(ppm),
            "origin_frame_mm": [float(origin[0]), float(origin[1])]}


def _to_mm(box: list[int], meta: dict) -> list[float]:
    ppm, (x0, y0) = meta["px_per_mm"], meta["origin_frame_mm"]
    x, y, w, h = box
    return [round(x / ppm + x0, 3), round(y / ppm + y0, 3),
            round(w / ppm, 3), round(h / ppm, 3)]


def _merge(raw: list[dict]) -> list[dict]:
    merged: list[dict] = []
    for item in sorted(raw, key=lambda i: -(i["ocr_confidence"] or 0)):
        for kept in merged:
            if kept["normalized"] == item["normalized"] and _iou(kept["bbox_xywh"], item["bbox_xywh"]) > MERGE_IOU:
                kept["occurrences"] += 1
                for key in ("orientations_seen", "polarities_seen"):
                    kept[key] = sorted(set(kept[key]) | set(item[key]))
                break
        else:
            merged.append(item)
    return merged


def _verdict(item: dict, duplicate: bool) -> tuple[str, str | None]:
    conf = item["ocr_confidence"]
    if conf is not None and conf < LOW_CONF:
        return "ambiguous", f"OCR confidence {conf} below {LOW_CONF}"
    if item.get("corrections"):
        return "ambiguous", f"{item['corrections']} ambiguous-character correction(s); see alternates"
    if duplicate:
        return "ambiguous", "same label seen at another location"
    return "ok", None


def analyze(image: np.ndarray, backend, roi: tuple[int, int, int, int] | None = None,
            frame_meta: dict | None = None) -> dict:
    """Run the full pipeline on a BGR image; result is JSON-serializable."""
    meta = _validate_frame_meta(frame_meta)
    height, width = image.shape[:2]
    x, y, w, h = roi or (0, 0, width, height)
    if not (0 <= x < width and 0 <= y < height and w > 0 and h > 0 and x + w <= width and y + h <= height):
        raise ValueError(f"ROI {(x, y, w, h)} is outside {width}x{height}")
    info = {"name": backend.name, "version": backend.version(), "available": backend.available()}
    base: dict = {"backend": info, "candidates": [], "ignored_text": [],
                  "frame": meta, "preprocessing": {"upscale": preprocess.UPSCALE,
                                                   "orientations": list(ORIENTATIONS),
                                                   "polarities": ["normal", "inverted"]}}
    if not backend.available():
        return {**base, "status": "unavailable",
                "reason": f"OCR backend {backend.name!r} is not installed; no text was read"}
    crop = image[y:y + h, x:x + w]
    variants = preprocess.prepare(crop)
    attempts = []
    words_seen = 0
    for angle in ORIENTATIONS:
        for polarity, variant in variants.items():
            attempt = _rotate(variant, angle)
            words = backend.recognize(attempt)
            words_seen += len(words)
            src_h, src_w = variant.shape[:2]
            for word in words:
                box = _to_source(_map_box(word["box_xywh"], src_w, src_h, angle), (x, y))
                attempts.append((word, angle, polarity, box))
    scored = []
    for word, angle, polarity, box in attempts:
        reading = parse(word["text"])
        clean = reading["is_designator_like"] and reading["corrections"] == 0
        scored.append((0 if clean else 1, -(word["confidence"] or 0), 0 if angle == 0 else 1,
                       angle, word, polarity, box, reading))
    scored.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    raw, ignored, kept = [], [], []
    for _cost, _neg, _upright, angle, word, polarity, box, reading in scored:
        rival = next((entry for entry in kept
                      if _overlap_coefficient(box, entry["bbox_xywh"]) > SAME_INK_OVERLAP), None)
        if rival is not None:
            if reading.get("normalized") and reading["normalized"] == rival.get("normalized"):
                rival["occurrences"] += 1
                rival["orientations_seen"] = sorted(set(rival["orientations_seen"]) | {angle})
                rival["polarities_seen"] = sorted(set(rival["polarities_seen"]) | {polarity})
            continue
        entry = {"bbox_xywh": box, "ocr_confidence": word["confidence"],
                 "orientations_seen": [angle], "polarities_seen": [polarity]}
        if reading["is_designator_like"]:
            kept.append({**entry, **reading, "occurrences": 1})
        else:
            kept.append({**entry, "normalized": None})
            ignored.append({"raw_text": reading["raw_text"], "bbox_xywh": box,
                            "ocr_confidence": word["confidence"]})
    raw = [entry for entry in kept if entry.get("normalized")]
    candidates = _merge(raw)
    ignored = list({(item["raw_text"], tuple(item["bbox_xywh"])): item for item in ignored}.values())
    by_label: dict[str, int] = {}
    for item in candidates:
        by_label[item["normalized"]] = by_label.get(item["normalized"], 0) + 1
    final = []
    for index, item in enumerate(sorted(candidates, key=lambda i: (i["bbox_xywh"][1], i["bbox_xywh"][0])), 1):
        duplicate = by_label[item["normalized"]] > 1
        verdict, reason = _verdict(item, duplicate)
        final.append({
            "id": f"cand-{index:03d}", "raw_text": item["raw_text"],
            "normalized": item["normalized"], "prefix": item["prefix"],
            "number": item["number"], "suffix": item["suffix"],
            "suggested_type": item["suggested_type"], "type_note": item["type_note"],
            "listed_prefix": item["listed_prefix"],
            "bbox_xywh": item["bbox_xywh"],
            "bbox_mm": _to_mm(item["bbox_xywh"], meta) if meta else None,
            "ocr_confidence": item["ocr_confidence"], "parse_score": item["score"],
            "corrections": item["corrections"],
            "orientation": item["orientations_seen"][0], "orientations_seen": item["orientations_seen"],
            "polarities_seen": item["polarities_seen"], "occurrences": item["occurrences"],
            "duplicate_label": duplicate, "verdict": verdict, "verdict_reason": reason,
            "alternates": item["alternates"],
        })
    base.update({"candidates": final, "ignored_text": ignored,
                 "stats": {"words_seen": words_seen}})
    if not final:
        return {**base, "status": "unknown", "reason": "no designator-like text found"}
    if any(item["verdict"] == "ambiguous" for item in final):
        return {**base, "status": "ambiguous", "reason": "at least one candidate needs review"}
    return {**base, "status": "ok", "reason": None}
