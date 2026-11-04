"""Annotated diagnostic overlays for fingerprint enrollment and matching."""

from __future__ import annotations

import cv2
import numpy as np

from anastylos_fingerprints.describe import silhouette_mask

STATUS_COLOR = {"ok": (0, 200, 0), "ambiguous": (0, 200, 230),
                "unknown": (150, 150, 150), "rejected": (0, 0, 230)}


def annotate(image, result: dict, roi=None):
    """Draw the silhouette, verdict, and ranked candidates onto a copy."""
    view = image.copy()
    mask = silhouette_mask(image, roi)
    if mask is not None:
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(view, contours, -1, (255, 200, 0), 2)
    color = STATUS_COLOR.get(result.get("status"), (0, 0, 230))
    lines = [f"{result.get('status')}: {result.get('reason') or ''}".strip()]
    for item in result.get("candidates", [])[:5]:
        lines.append(f"{item['id']} {item['score']}")
    for row, text in enumerate(lines):
        cv2.putText(view, text[:80], (10, 28 + row * 26), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, color if row == 0 else (200, 200, 200), 2)
    return view
