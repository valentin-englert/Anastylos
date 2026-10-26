"""Annotated diagnostic overlays for component-marking readings."""

from __future__ import annotations

import cv2

VERDICT_COLOR = {"ok": (0, 200, 0), "ambiguous": (0, 200, 230),
                 "unknown": (150, 150, 150), "unavailable": (150, 150, 150)}


def _label(line: dict) -> str:
    kinds = ",".join(item["kind"] for item in line.get("parsed", []))
    identity = line.get("identity") or {}
    if kinds and identity.get("text"):
        return f"{line['match']} [{kinds}] ~{identity['text']}"
    if kinds:
        return f"{line['match']} [{kinds}]"
    if identity.get("text"):
        return f"~{identity['text']}"
    return line["match"]


def annotate(image, result: dict):
    """Draw observed words, line readings, and the verdict onto a copy."""
    view = image.copy()
    for item in result.get("observations", []):
        x, y, w, h = item["bbox_xywh"]
        cv2.rectangle(view, (x, y), (x + w, y + h), (180, 180, 180), 1)
    color = VERDICT_COLOR.get(result["status"], (0, 0, 230))
    for item in result.get("candidates", []):
        x, y, w, h = item["bbox_xywh"]
        cv2.rectangle(view, (x, y), (x + w, y + h), color, 2)
        cv2.putText(view, _label(item)[:60], (x, max(15, y - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
    if result["candidates"]:
        verdict = f"{result['status']}: {result['candidates'][0]['match']}"
    else:
        verdict = f"{result['status']}: {result.get('reason', '')}"
    cv2.putText(view, verdict[:90], (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                (0, 200, 255), 2)
    return view
