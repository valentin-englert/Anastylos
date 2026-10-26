"""Annotated diagnostic overlays for missing-parts comparisons.

Side-by-side original/current views with region boxes in shared
coordinates: solid boxes for supported candidates, dashed for weak or
neutral evidence, link lines for displacement pairs, and distinct
full-frame banners when the comparison is unknown or unavailable.
"""

from __future__ import annotations

import cv2
import numpy as np

COLORS = {
    "candidate_absent_region": (0, 0, 230),
    "candidate_newly_occupied": (230, 120, 0),
    "candidate_displaced": (0, 180, 230),
    "shifted_present": (0, 180, 0),
    "hole_unchanged": (150, 150, 150),
    "observed_difference": (180, 180, 0),
}
BANNER = {"ok": (0, 170, 0), "ambiguous": (0, 180, 230),
          "unknown": (150, 150, 150), "unavailable": (60, 60, 60)}


def _dashed(view: np.ndarray, x: int, y: int, w: int, h: int,
            color: tuple[int, int, int], dash: int = 8):
    for x0 in range(x, x + w, dash * 2):
        cv2.line(view, (x0, y), (min(x0 + dash, x + w), y), color, 2)
        cv2.line(view, (x0, y + h), (min(x0 + dash, x + w), y + h), color, 2)
    for y0 in range(y, y + h, dash * 2):
        cv2.line(view, (x, y0), (x, min(y0 + dash, y + h)), color, 2)
        cv2.line(view, (x + w, y0), (x + w, min(y0 + dash, y + h)), color, 2)


def _draw_regions(view: np.ndarray, regions: list[dict], offset_x: int = 0):
    by_label = {r["label"]: r for r in regions}
    for region in regions:
        x, y, w, h = (int(v) + (offset_x if i % 2 == 0 else 0)
                      for i, v in enumerate(region.get("bbox") or [0, 0, 0, 0]))
        if w <= 0 or h <= 0:
            continue
        color = COLORS.get(region.get("classification", ""), (255, 255, 255))
        if region.get("strength") == "supported" and region.get("classification") \
                not in ("observed_difference", "hole_unchanged", "shifted_present"):
            cv2.rectangle(view, (x, y), (x + w, y + h), color, 2)
        else:
            _dashed(view, x, y, w, h, color)
        cv2.putText(view, region["label"], (x + 3, max(y - 4, 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    return by_label


def _draw_links(view: np.ndarray, result: dict, offset_x: int = 0):
    by_label = {r["label"]: r for r in result.get("regions") or []}
    for link in result.get("displacement_links") or []:
        left = by_label.get(link["absence_region"])
        right = by_label.get(link["presence_region"])
        if not left or not right:
            continue
        p1 = (int(left["centroid"][0]) + offset_x, int(left["centroid"][1]))
        p2 = (int(right["centroid"][0]) + offset_x, int(right["centroid"][1]))
        cv2.line(view, p1, p2, COLORS["candidate_displaced"], 2)
        cv2.putText(view, link["id"], ((p1[0] + p2[0]) // 2, (p1[1] + p2[1]) // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLORS["candidate_displaced"], 1)


def _banner(view: np.ndarray, result: dict):
    verdict = result.get("verdict", "unknown")
    line = f"{verdict}: {result.get('comparison_id', '')} " \
           f"{len(result.get('regions') or [])} regions"
    cv2.rectangle(view, (0, 0), (min(view.shape[1], 640), 34),
                  BANNER.get(verdict, (255, 255, 255)), -1)
    cv2.putText(view, line[:80], (8, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)


def _text_canvas(message: str, detail: str) -> np.ndarray:
    view = np.full((300, 640, 3), 40, np.uint8)
    cv2.putText(view, message[:60], (16, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 200, 255), 2)
    for index, chunk in enumerate([detail[i:i + 78] for i in range(0, len(detail), 78)][:6]):
        cv2.putText(view, chunk, (16, 110 + index * 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (220, 220, 220), 1)
    return view


def annotate(result: dict, original: np.ndarray | None = None,
             current: np.ndarray | None = None) -> np.ndarray:
    """Render the comparison overlay. Works without images too."""
    verdict = result.get("verdict", "unknown")
    if original is None and current is None:
        detail = "; ".join(result.get("reasons") or []) or "no image evidence"
        view = _text_canvas(f"{verdict.upper()}: {result.get('comparison_id', '')}", detail)
        for index, region in enumerate(result.get("regions") or []):
            cv2.putText(view, f"{region['label']} {region.get('classification')}",
                        (16, 110 + 6 * 28 + index * 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 1)
        return view
    if original is None:
        original = np.zeros_like(current)
    if current is None:
        current = np.zeros_like(original)
    if original.shape != current.shape:
        height = max(original.shape[0], current.shape[0])
        width = max(original.shape[1], current.shape[1])
        pad = lambda img: cv2.copyMakeBorder(img, 0, height - img.shape[0], 0, width - img.shape[1],
                                             cv2.BORDER_CONSTANT, value=(0, 0, 0))
        original, current = pad(original), pad(current)
    view = np.hstack([original.copy(), current.copy()])
    split = original.shape[1]
    regions = result.get("regions") or []
    _draw_regions(view, regions, 0)
    _draw_regions(view, regions, split)
    _draw_links(view, result, split)
    cv2.line(view, (split, 0), (split, view.shape[0]), (255, 255, 255), 1)
    cv2.putText(view, "original", (8, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    cv2.putText(view, "current", (split + 8, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    for pair in (result.get("holes") or {}).get("pairs") or []:
        for hole, offset in ((pair["original"], 0), (pair["current"], split)):
            cx, cy = int(hole["centroid"][0]) + offset, int(hole["centroid"][1])
            cv2.circle(view, (cx, cy), 6, (150, 150, 150), 1)
    _banner(view, result)
    return view


def annotate_reconciliation(comparison: dict, overlay: np.ndarray,
                            reconciliation: dict) -> np.ndarray:
    """Outline regions linked to obligations with their obligation status."""
    view = overlay.copy()
    height, width = view.shape[:2]
    split = width // 2 if any(r.get("bbox") for r in comparison.get("regions") or []) \
        and width > 400 else 0
    colors = {"confirmed_missing": (0, 0, 255), "confirmed_present": (0, 200, 0),
              "candidate_absent": (0, 0, 230), "likely_present": (0, 180, 0),
              "expected_unconfirmed": (160, 160, 160)}
    by_label = {r["label"]: r for r in comparison.get("regions") or []}
    for row, item in enumerate(reconciliation.get("obligations") or []):
        color = colors.get(item.get("status", ""), (255, 255, 255))
        cv2.putText(view, f"{item['id']}:{item.get('status')}", (8 + split, 72 + row * 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
        for label in item.get("linked_regions") or []:
            region = by_label.get(label)
            if not region or not region.get("bbox"):
                continue
            x, y, w, h = (int(v) for v in region["bbox"])
            cv2.rectangle(view, (x + split - 3, y - 3), (x + split + w + 3, y + h + 3), color, 1)
    footer = f"reconcile:{reconciliation.get('verdict')} " \
             f"pending:{len(reconciliation.get('confirmation_required_for') or [])}"
    cv2.putText(view, footer[:70], (8, height - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 200, 255), 2)
    return view
