"""Annotated diagnostic overlays for washer/spacer readings."""

from __future__ import annotations

import cv2

STATUS_COLOR = {"ok": (0, 200, 0), "ambiguous": (0, 200, 230),
                "unknown": (150, 150, 150),
                "unavailable": (180, 80, 180)}


def _dim_text(observation: dict) -> str:
    geometry = observation["geometry"]
    parts = []
    for key, label in (("outer_d", "OD"), ("inner_d", "ID"),
                       ("length_px", "L"), ("thickness_px", "T")):
        entry = geometry.get(key)
        if isinstance(entry, dict):
            parts.append(f"{label}{entry['value_px']:.0f}")
    return " ".join(parts)


def annotate(image, result: dict):
    """Draw boxes, holes, measurement lines, and classifications."""
    view = image.copy()
    for obs in result.get("observations", []):
        color = STATUS_COLOR.get(obs["status"], (0, 0, 230))
        x, y, w, h = obs["bbox_xywh"]
        cv2.rectangle(view, (x, y), (x + w, y + h), color, 2)
        geometry = obs["geometry"]
        center = geometry.get("center_xy")
        if center:
            ccx, ccy = int(center[0]), int(center[1])
            cv2.circle(view, (ccx, ccy), 3, color, -1)
            outer = geometry.get("outer_d")
            if isinstance(outer, dict):
                radius = int(outer["value_px"] / 2)
                cv2.circle(view, (ccx, ccy), radius, (255, 200, 0), 1)
                cv2.line(view, (ccx - radius, ccy), (ccx + radius, ccy),
                         (255, 200, 0), 1)
            hole_center = geometry.get("hole_center_xy")
            inner = geometry.get("inner_d")
            if hole_center and isinstance(inner, dict):
                hcx, hcy = int(hole_center[0]), int(hole_center[1])
                cv2.circle(view, (hcx, hcy),
                           int(inner["value_px"] / 2), (255, 0, 255), 1)
        label = f"{obs['observation_id']}:{obs.get('top_family', '?')} " \
                f"{_dim_text(obs)}"
        cv2.putText(view, label, (x, max(12, y - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
        verdict = f"{obs['observation_id']}:{obs['status']}"
        cv2.putText(view, verdict, (x, y + h + 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    header = f"{result.get('status', '?')} {result.get('view', '')}: " \
             f"{len(result.get('observations', []))} obs"
    cv2.putText(view, header, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                (0, 200, 255), 2)
    return view
