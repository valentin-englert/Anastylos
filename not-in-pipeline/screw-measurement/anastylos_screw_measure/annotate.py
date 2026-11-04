"""Annotated diagnostic overlays for screw measurements.

Draws exactly the boundaries and measurement lines the pipeline used:
the fitted head circle, center, and diameter line (top), or the
silhouette contour, overall-length line, shaft line, and head/shaft
split (side), plus the verdict.
"""

from __future__ import annotations

import cv2
import numpy as np

VERDICT_COLOR = {"ok": (0, 200, 0), "ambiguous": (0, 200, 230), "unknown": (150, 150, 150)}


def _entry(result: dict, name: str) -> dict | None:
    for item in result.get("measurements", []):
        if item.get("name") == name:
            return item
    return None


def _verdict_line(result: dict) -> str:
    names = {"top": "visible_head_diameter", "side": "visible_overall_length"}
    entry = _entry(result, names.get(result.get("mode"), ""))
    if entry and entry.get("value_px") is not None:
        text = f"{entry['value_px']}px"
        if entry.get("value_mm") is not None:
            text += f" = {entry['value_mm']}mm"
        return f"{result['verdict']}: {entry['name']} {text} [{entry['status']}]"
    return f"{result['verdict']}: {(result.get('reasons') or [''])[0]}"


def annotate_top(view: np.ndarray, result: dict) -> np.ndarray:
    geometry = result.get("geometry")
    if not geometry:
        return view
    cx, cy = int(geometry["center_xy"][0]), int(geometry["center_xy"][1])
    radius = int(geometry["radius_px"])
    color = VERDICT_COLOR.get(result["verdict"], (0, 0, 230))
    cv2.circle(view, (cx, cy), radius, (255, 200, 0), 2)
    cv2.drawMarker(view, (cx, cy), (0, 0, 255), cv2.MARKER_CROSS, 24, 2)
    cv2.line(view, (cx - radius, cy), (cx + radius, cy), color, 2)
    axes = geometry.get("ellipse_axes_px")
    if axes and geometry.get("axes_ratio", 1.0) < 0.92:
        cv2.ellipse(view, (cx, cy), (int(axes[1] / 2), int(axes[0] / 2)),
                    0, 0, 360, (255, 0, 255), 1)
    return view


def annotate_side(view: np.ndarray, result: dict, gray_crop=None, crop_box=None) -> np.ndarray:
    geometry = result.get("geometry")
    if not geometry:
        return view
    color = VERDICT_COLOR.get(result["verdict"], (0, 0, 230))
    (x1, y1), (x2, y2) = geometry["endpoints_xy"]
    cv2.line(view, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
    for pt in ((x1, y1), (x2, y2)):
        cv2.circle(view, (int(pt[0]), int(pt[1])), 4, color, -1)
    if geometry.get("split_xy"):
        sx, sy = geometry["split_xy"]
        angle = np.deg2rad(geometry.get("axis_angle_deg", 0.0))
        nx, ny = -np.sin(angle), np.cos(angle)
        shaft = _entry(result, "visible_shaft_diameter")
        half = (shaft["value_px"] / 2 if shaft and shaft.get("value_px") else 20.0) + 8
        cv2.line(view, (int(sx - nx * half), int(sy - ny * half)),
                 (int(sx + nx * half), int(sy + ny * half)), (255, 200, 0), 2)
    return view


def annotate(image: np.ndarray, result: dict) -> np.ndarray:
    """Draw the used boundaries, measurement lines, and verdict."""
    view = image.copy()
    if result.get("mode") == "top":
        view = annotate_top(view, result)
    elif result.get("mode") == "side":
        view = annotate_side(view, result)
    cv2.putText(view, _verdict_line(result)[:90], (10, 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)
    return view
