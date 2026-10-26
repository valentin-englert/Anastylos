"""Annotated diagnostic overlays for PCB OCR results."""

from __future__ import annotations

import cv2

VERDICT_COLORS = {"ok": (0, 200, 0), "ambiguous": (0, 200, 230)}
IGNORED_COLOR = (150, 150, 150)


def annotate(image, result: dict):
    """Draw candidate and ignored-text boxes onto a copy of the image."""
    view = image.copy()
    for item in result.get("ignored_text", []):
        x, y, w, h = item["bbox_xywh"]
        cv2.rectangle(view, (x, y), (x + w, y + h), IGNORED_COLOR, 1)
    for item in result["candidates"]:
        x, y, w, h = item["bbox_xywh"]
        color = VERDICT_COLORS.get(item["verdict"], (0, 0, 230))
        cv2.rectangle(view, (x, y), (x + w, y + h), color, 2)
        label = f"{item['normalized']} {item['ocr_confidence']}"
        cv2.putText(view, label, (x, max(15, y - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
    verdict = f"{result['status']}: {len(result['candidates'])} candidates"
    cv2.putText(view, verdict, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 255), 2)
    return view
