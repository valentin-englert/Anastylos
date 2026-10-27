"""Workflow B: evidence-based inventory reconciliation.

Expected-component observations (an explicit inventory file or a
read-only Assembly Memory log) are matched against the visual evidence
of one comparison result. Outcomes per expectation: confirmed_missing
or confirmed_present (external confirmation only), candidate_absent
(visual absence evidence, unconfirmed), likely_present (part-like
evidence at the location, identity unconfirmed), or
expected_unconfirmed (no evidence either way). Visual candidates with
no expectation are reported separately, never silently equated with
missing parts.
"""

from __future__ import annotations

STATUSES = ("confirmed_missing", "confirmed_present", "candidate_absent",
            "likely_present", "expected_unconfirmed")

VISUAL_ABSENT = ("candidate_absent_region", "candidate_displaced")
VISUAL_PRESENT = ("shifted_present", "candidate_newly_occupied")


def _iou(a: list, b: list) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def _centroid_inside(centroid: list, bbox: list) -> bool:
    return bbox[0] <= centroid[0] <= bbox[0] + bbox[2] \
        and bbox[1] <= centroid[1] <= bbox[1] + bbox[3]


def _linked_regions(expected: dict, regions: list[dict]) -> list[dict]:
    links = []
    for region in regions:
        bbox = region.get("bbox_mm")
        centroid = region.get("centroid_mm")
        if bbox is None or centroid is None:
            continue
        if _iou(expected["bbox_mm"], bbox) >= 0.2 or _centroid_inside(centroid, expected["bbox_mm"]) \
                or _centroid_inside([expected["bbox_mm"][0] + expected["bbox_mm"][2] / 2,
                                     expected["bbox_mm"][1] + expected["bbox_mm"][3] / 2], bbox):
            links.append(region)
    return links


def reconcile_one(expected: dict, regions: list[dict], frame_name: str | None,
                  mm_validated: bool, confirmations: list[dict],
                  comparison_verdict: str) -> dict:
    """Reconcile one expectation against visual evidence."""
    for confirmation in confirmations:
        if confirmation["expected_id"] != expected["id"]:
            continue
        if confirmation["statement"] == "missing":
            return {"id": expected["id"], "status": "confirmed_missing",
                    "reasons": [f"externally confirmed missing by {confirmation['confirmed_by']}"],
                    "requires_confirmation": False, "confirmed_by": confirmation["confirmed_by"],
                    "linked_regions": [], "contradiction": None}
        return {"id": expected["id"], "status": "confirmed_present",
                "reasons": [f"externally confirmed present by {confirmation['confirmed_by']}"],
                "requires_confirmation": False, "confirmed_by": confirmation["confirmed_by"],
                "linked_regions": [], "contradiction": None}
    if not mm_validated or frame_name is None or expected["frame"] != frame_name:
        return {"id": expected["id"], "status": "expected_unconfirmed",
                "reasons": [f"no common validated frame (expected on {expected['frame']}, "
                            f"comparison on {frame_name})"],
                "requires_confirmation": True, "confirmed_by": None,
                "linked_regions": [], "contradiction": None}
    if comparison_verdict in ("unavailable", "unknown"):
        return {"id": expected["id"], "status": "expected_unconfirmed",
                "reasons": [f"comparison {comparison_verdict}; no visual evidence available"],
                "requires_confirmation": True, "confirmed_by": None,
                "linked_regions": [], "contradiction": None}
    linked = _linked_regions(expected, regions)
    absent = [r for r in linked if r["classification"] in VISUAL_ABSENT]
    present = [r for r in linked if r["classification"] in VISUAL_PRESENT]
    labels = [r["label"] for r in linked]
    if absent and not present:
        return {"id": expected["id"], "status": "candidate_absent",
                "reasons": [f"absence evidence at the expected location ({', '.join(r['label'] for r in absent)}); "
                            "identity and cause unconfirmed"],
                "requires_confirmation": True, "confirmed_by": None,
                "linked_regions": labels, "contradiction": None}
    if present and not absent:
        return {"id": expected["id"], "status": "likely_present",
                "reasons": [f"part-like evidence at the expected location ({', '.join(r['label'] for r in present)}); "
                            "not an identity claim"],
                "requires_confirmation": True, "confirmed_by": None,
                "linked_regions": labels, "contradiction": None}
    if absent and present:
        return {"id": expected["id"], "status": "candidate_absent",
                "reasons": ["absence and presence evidence overlap at the expected location"],
                "requires_confirmation": True, "confirmed_by": None,
                "linked_regions": labels,
                "contradiction": f"absence ({', '.join(r['label'] for r in absent)}) contradicts "
                                 f"presence ({', '.join(r['label'] for r in present)}); human review required"}
    return {"id": expected["id"], "status": "expected_unconfirmed",
            "reasons": ["no visual evidence at the expected location either way"],
            "requires_confirmation": True, "confirmed_by": None,
            "linked_regions": [], "contradiction": None}


def run_reconciliation(comparison: dict, expected: list[dict],
                       expected_provenance: dict,
                       confirmations: list[dict] | None = None) -> dict:
    """Reconcile expected observations against one comparison. JSON-serializable."""
    confirmations = confirmations or []
    known = {item["id"] for item in expected}
    for confirmation in confirmations:
        if confirmation["expected_id"] not in known:
            raise ValueError(f"confirmation targets unknown expectation "
                             f"{confirmation['expected_id']!r}")
    frame = (comparison.get("frame") or {}).get("name")
    mm_validated = bool((comparison.get("frame") or {}).get("mm_validated"))
    regions = comparison.get("regions") or []
    verdict = comparison.get("verdict", "unavailable")
    obligations = [reconcile_one(item, regions, frame, mm_validated, confirmations, verdict)
                   for item in expected]
    linked_labels = {label for item in obligations for label in item["linked_regions"]}
    unassigned = [r["label"] for r in regions
                  if r["classification"] in VISUAL_ABSENT and r["label"] not in linked_labels]
    counts: dict[str, int] = {status: 0 for status in STATUSES}
    for item in obligations:
        counts[item["status"]] += 1
    contradictions = [item["id"] for item in obligations if item["contradiction"]]
    pending = [item["id"] for item in obligations if item["requires_confirmation"]]
    if contradictions:
        run_verdict, reasons = "ambiguous", [f"{len(contradictions)} expectation(s) hold "
                                             "contradictory evidence"]
    elif verdict in ("unavailable", "unknown") and expected:
        run_verdict, reasons = verdict, [f"comparison {verdict}; obligations stay open"]
    else:
        run_verdict, reasons = "ok", ["reconciliation evaluated"]
        if not expected:
            reasons = ["no expectations declared; nothing to reconcile"]
    return {"comparison_id": comparison.get("comparison_id"),
            "comparison_verdict": verdict,
            "expected_source": expected_provenance,
            "frame": {"name": frame, "mm_validated": mm_validated},
            "obligations": obligations,
            "unassigned_visual_candidates": unassigned,
            "unassigned_note": ("visual candidates without an expectation; not missing parts "
                                "until linked to an expectation and confirmed"),
            "counts": counts, "contradictions": contradictions,
            "verdict": run_verdict, "reasons": reasons,
            "requires_confirmation": bool(pending),
            "confirmation_required_for": sorted(pending)}
