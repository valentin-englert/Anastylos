"""Annotated diagnostic overlays for SMD readings."""

from __future__ import annotations

import cv2

VERDICT_COLOR = {"ok": (0, 200, 0), "ambiguous": (0, 200, 230),
                 "unknown": (150, 150, 150), "unavailable": (150, 150, 150)}


def annotate(image, result: dict):
    """Draw observed words and the top reading onto a copy of the image."""
    view = image.copy()
    for item in result.get("observations", []):
        x, y, w, h = item["bbox_xywh"]
        cv2.rectangle(view, (x, y), (x + w, y + h), (180, 180, 180), 1)
        cv2.putText(view, item["raw_text"], (x, max(15, y - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 180, 180), 2)
    color = VERDICT_COLOR.get(result["status"], (0, 0, 230))
    for item in result.get("candidates", [])[:3]:
        x, y, w, h = item["bbox_xywh"]
        cv2.rectangle(view, (x, y), (x + w, y + h), color, 2)
    if result["candidates"]:
        top = result["candidates"][0]
        verdict = f"{result['status']}: {top['code']} = {top['resistance_ohms_exact']} ohm"
    else:
        verdict = f"{result['status']}: {result.get('reason', '')}"
    cv2.putText(view, verdict, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 255), 2)
    return view
