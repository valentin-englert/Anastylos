"""Annotated diagnostic overlays for connector observations and matches."""

from __future__ import annotations

import cv2

from anastylos_connectors.observe import housing_mask

STATUS_COLOR = {"ok": (0, 200, 0), "ambiguous": (0, 200, 230),
                "unknown": (150, 150, 150), "unavailable": (150, 150, 150),
                "observed": (0, 200, 0)}


def annotate(image, result: dict, roi=None, observation=None):
    """Draw housing, contacts, keying, verdict, and ranking onto a copy."""
    view = image.copy()
    mask = housing_mask(image, roi)
    if mask is not None:
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(view, contours, -1, (255, 200, 0), 2)
    shown = observation or {}
    ox, oy = (roi[0], roi[1]) if roi else (0, 0)
    for blob in shown.get("pins", []):
        cx, cy = int(blob["xy"][0] + ox), int(blob["xy"][1] + oy)
        cv2.circle(view, (cx, cy), 6, (0, 255, 0), 2)
    for blob in shown.get("cavities", []):
        cx, cy = int(blob["xy"][0] + ox), int(blob["xy"][1] + oy)
        cv2.circle(view, (cx, cy), 6, (255, 0, 255), 2)
    housing = shown.get("housing") or {}
    bx, by, bw, bh = housing.get("bbox_xywh", [0, 0, 1, 1])
    for notch in shown.get("keying_notches", []):
        nx, ny = notch["at_xy"]
        cv2.drawMarker(view, (nx + ox, ny + oy), (0, 165, 255),
                       cv2.MARKER_TILTED_CROSS, 18, 2)
    color = STATUS_COLOR.get(result.get("status"), (0, 0, 230))
    lines = [f"{result.get('status')}: {result.get('reason') or ''}".strip()]
    if shown.get("role"):
        lines.append(f"role={shown['role']} contacts={shown.get('contact_count')}")
    for item in result.get("candidates", [])[:5]:
        lines.append(f"{item['id']} {item['score']}")
    for row, text in enumerate(lines):
        cv2.putText(view, text[:80], (10, 28 + row * 26), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, color if row == 0 else (200, 200, 200), 2)
    return view
