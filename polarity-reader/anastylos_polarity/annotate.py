"""Annotated diagnostic overlays for polarity readings."""

from __future__ import annotations

import math

import cv2

VERDICT_COLOR = {"ok": (0, 200, 0), "ambiguous": (0, 200, 230),
                 "unknown": (150, 150, 150),
                 "unavailable": (180, 80, 180)}
MARKER_COLOR = {"band": (0, 230, 255), "stripe": (0, 140, 255),
                "dot": (0, 220, 0), "notch": (255, 0, 220),
                "key": (255, 120, 0), "topmark": (200, 200, 0)}


def annotate(image, result: dict):
    """Draw body, marker boxes, orientation arrows, and verdict."""
    view = image.copy()
    body = result.get("body")
    if body:
        x, y, w, h = body["bbox_xywh"]
        cv2.rectangle(view, (x, y), (x + w, y + h), (255, 200, 0), 2)
    for marker in result.get("markers", []):
        color = MARKER_COLOR.get(marker["marker_type"], (0, 0, 230))
        x, y, w, h = marker["bbox_xywh"]
        cv2.rectangle(view, (x, y), (x + w, y + h), color, 2)
        ccx, ccy = [int(v) for v in marker["centroid_xy"]]
        cv2.circle(view, (ccx, ccy), 3, color, -1)
        dx, dy = marker["orientation_vector_xy"]
        length = max(24.0, 0.25 * math.hypot(w, h))
        tip = (int(ccx + dx * length), int(ccy + dy * length))
        cv2.arrowedLine(view, (ccx, ccy), tip, color, 2,
                        tipLength=0.25)
        label = (f"{marker['marker_id']}:{marker['marker_type']} "
                 f"{marker['orientation_angle_deg']}")
        cv2.putText(view, label, (x, max(12, y - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    status = result.get("status", "unknown")
    candidates = result.get("candidates", [])
    if candidates:
        top = candidates[0]
        verdict = (f"{status}: {top['marker_type']} "
                   f"{top['interpretation']} {top['confidence']}")
    else:
        verdict = f"{status}: {result.get('reason', '')}"
    cv2.putText(view, verdict, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                (0, 200, 255), 2)
    return view
