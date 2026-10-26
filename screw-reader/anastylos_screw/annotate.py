"""Annotated diagnostic overlays for screw-head readings."""

from __future__ import annotations

import cv2

VERDICT_COLOR = {"ok": (0, 200, 0), "ambiguous": (0, 200, 230), "unknown": (150, 150, 150)}


def annotate(image, result: dict):
    """Draw the head circle, recess box, and verdict onto a copy."""
    view = image.copy()
    head = result.get("head")
    if head:
        cx, cy = [int(v) for v in head["center_xy"]]
        cv2.circle(view, (cx, cy), int(head["radius_px"]), (255, 200, 0), 2)
    recess = result.get("recess_bbox_xywh")
    if recess:
        x, y, w, h = recess
        cv2.rectangle(view, (x, y), (x + w, y + h), VERDICT_COLOR.get(result["status"], (0, 0, 230)), 2)
    if result["candidates"]:
        top = result["candidates"][0]
        verdict = f"{result['status']}: {top['class']} {top['score']}"
    else:
        verdict = f"{result['status']}: {result.get('reason', '')}"
    cv2.putText(view, verdict, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 255), 2)
    return view
