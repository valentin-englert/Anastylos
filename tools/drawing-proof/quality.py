"""Quality gates for one part mask, independent reference comparison, and the capture request on failure.

Gates are fixed before a run (``GATES``); a failed gate is a correct system answer ("needs capture or
review"), never a reason to retune the segmentation for that image. Edge support is a diagnostic only:
shadows, inner edges and wrong regions have edges too.
"""

from __future__ import annotations

import cv2
import numpy as np

GATES = {
    "border_margin_px": 3,          # mask this close to the region border: part may be clipped
    "max_skin_fraction": 0.02,      # share of mask pixels in the skin colour range
    "min_edge_support": 0.90,       # share of boundary points within 2 px of a Canny edge
    "max_shadow_fraction": 0.25,    # rejected shadow area / seed area
    "min_area_fraction": 0.01,      # mask area / region area
}


def skin_fraction(image: np.ndarray, mask: np.ndarray) -> float:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    skin = ((h <= 20) | (h >= 170)) & (s >= 40) & (s <= 180) & (v >= 60)
    return float((skin & mask).sum() / max(mask.sum(), 1))


def gate(image: np.ndarray, mask: np.ndarray, seed: np.ndarray, shadow: np.ndarray, edge_support: float) -> dict:
    margin = GATES["border_margin_px"]
    border = np.ones(mask.shape, bool)
    border[margin:-margin, margin:-margin] = False
    measures = {
        "touches_region_border": bool((mask & border).any()),
        "skin_fraction": round(skin_fraction(image, mask), 4),
        "edge_support": round(edge_support, 4),
        "shadow_fraction": round(float(shadow.sum() / max(seed.sum(), 1)), 4),
        "area_fraction": round(float(mask.mean()), 4),
    }
    flags = []
    if measures["area_fraction"] < GATES["min_area_fraction"]:
        flags.append("NO_PART_IN_REGION")
    if measures["touches_region_border"]:
        flags.append("INCOMPLETE_VIEW")
    if measures["skin_fraction"] > GATES["max_skin_fraction"]:
        flags.append("HAND_IN_MASK")
    if measures["edge_support"] < GATES["min_edge_support"]:
        flags.append("LOW_EDGE_SUPPORT")
    if measures["shadow_fraction"] > GATES["max_shadow_fraction"]:
        flags.append("SHADOW_DOMINANT")
    return {"gates": GATES, "measures": measures, "flags": flags,
            "status": "ready_for_operator_review" if not flags else "needs_capture_or_review"}


def capture_gap(flags: list[str], binding: dict, source_sha256: str, gap_id: str) -> dict | None:
    """``anastylos-capture-gap-input@1`` for the existing planner (tools/agent/capture_gaps), or None."""
    outer = [f for f in flags if f in ("INCOMPLETE_VIEW", "HAND_IN_MASK", "SHADOW_DOMINANT", "LOW_EDGE_SUPPORT", "NO_PART_IN_REGION")]
    if not outer:
        return None
    detail = "Drawing-proof gates failed: " + ", ".join(outer) + "."
    gap = {"id": gap_id, "kind": "outer_boundary", "binding": binding, "source_image_sha256s": [source_sha256], "detail": detail}
    return {"schema": "anastylos-capture-gap-input@1", "gap": gap, "current_binding": binding}


def reference_comparison(candidate: np.ndarray, reference: np.ndarray, px_per_mm: float, tolerance_mm: float) -> dict:
    """Candidate mask against an independently drawn reference mask (same rectified pixels).

    missed_boundary: share of the reference rim farther than ``tolerance_mm`` from the candidate rim;
    extra/missed area: foreign area included / part area left out, as a share of the reference area.
    """
    def rim(mask):
        contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        return np.vstack([c.reshape(-1, 2) for c in contours])

    def distance_to(mask):
        edge = np.zeros(mask.shape, np.uint8)
        cv2.drawContours(edge, [c for c in cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)[0]], -1, 1, 1)
        return cv2.distanceTransform((edge == 0).astype(np.uint8), cv2.DIST_L2, 5)

    ref_rim, cand_rim = rim(reference), rim(candidate)
    d_ref = distance_to(candidate)[ref_rim[:, 1], ref_rim[:, 0]] / px_per_mm
    d_cand = distance_to(reference)[cand_rim[:, 1], cand_rim[:, 0]] / px_per_mm
    area = max(reference.sum(), 1)
    return {"tolerance_mm": tolerance_mm,
            "missed_boundary_share": round(float((d_ref > tolerance_mm).mean()), 4),
            "spurious_boundary_share": round(float((d_cand > tolerance_mm).mean()), 4),
            "extra_area_share": round(float((candidate & ~reference).sum() / area), 4),
            "missed_area_share": round(float((reference & ~candidate).sum() / area), 4),
            "rim_distance_p95_mm": round(float(np.percentile(d_ref, 95)), 3)}


PLAUSIBILITY = {
    "image_edge_px": 3,            # Muse box this close to the image border: the part may continue outside
    "max_box_edge_contact": 0.25,  # share of the SAM box rim covered by mask: mask clipped by its box
    "min_box_fill": 0.30,          # mask area / Muse box area
    "min_box_iou": 0.50,           # mask bounding box vs Muse box
}


def plausibility(mask: np.ndarray, muse_box: list[int], sam_box: dict | None) -> dict:
    """Technical consistency of box and mask. Flags report doubt; passing proves no identity or completeness."""
    h, w = mask.shape
    left, top, right, bottom = muse_box
    margin = PLAUSIBILITY["image_edge_px"]
    flags, measures = [], {}
    if left <= margin or top <= margin or right >= w - margin or bottom >= h - margin:
        flags.append("BOX_AT_IMAGE_EDGE")
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return {"flags": ["NO_PART_IN_REGION"], "measures": {}}
    box_area = max((right - left) * (bottom - top), 1)
    measures["box_fill"] = round(float(mask[top:bottom, left:right].sum() / box_area), 3)
    if measures["box_fill"] < PLAUSIBILITY["min_box_fill"]:
        flags.append("MASK_SPARSE_IN_BOX")
    mx0, my0, mx1, my1 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
    inter = max(0, min(mx1, right) - max(mx0, left)) * max(0, min(my1, bottom) - max(my0, top))
    union = (mx1 - mx0) * (my1 - my0) + box_area - inter
    measures["mask_box_iou"] = round(float(inter / max(union, 1)), 3)
    if measures["mask_box_iou"] < PLAUSIBILITY["min_box_iou"]:
        flags.append("MASK_BOX_MISMATCH")
    if sam_box:
        l, t, r, b = (int(sam_box[k]) for k in ("left", "top", "right", "bottom"))
        rim = np.concatenate([mask[t, l:r], mask[b - 1, l:r], mask[t:b, l], mask[t:b, r - 1]])
        measures["sam_box_edge_contact"] = round(float(rim.mean()), 3)
        if measures["sam_box_edge_contact"] > PLAUSIBILITY["max_box_edge_contact"]:
            flags.append("MASK_CLIPPED_BY_BOX")
    if mask[0, :].any() or mask[-1, :].any() or mask[:, 0].any() or mask[:, -1].any():
        flags.append("MASK_AT_IMAGE_EDGE")
    return {"flags": flags, "measures": measures, "limits": PLAUSIBILITY}
