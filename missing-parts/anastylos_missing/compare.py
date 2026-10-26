"""Workflow A: visual state comparison, original versus current.

Consumes a change-detection ``result.json`` (preferred, provenance
preserved) or a direct original/current image pair. Without a declared
comparison context the output stays neutral: observed differences
only, never absent or displaced parts. Cross-frame inputs are rejected
unless the context carries a verified transform.
"""

from __future__ import annotations

import hashlib
import json

import cv2
import numpy as np

from anastylos_missing import classify as rules
from anastylos_missing import vision
from anastylos_missing.inputs import load_detection, sha256_file

NOT_CLAIMED = {
    "confirmed_missing": "no visual difference alone confirms a part missing",
    "part_identity": "regions are observations; identities need external evidence",
    "completeness": "an ok verdict means evaluated, never universally complete",
}

SIGN_MAP = {"darker_after": "darker_in_current", "brighter_after": "brighter_in_current"}


def _comparison_id(*parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"cmp-{digest}"


def _detection_sign(value: str | None) -> str:
    return SIGN_MAP.get(value or "", "no_polarity")


def _attach_associations_note(regions):
    for region in regions:
        if region.get("associations"):
            region["evidence"]["association_identity_note"] = rules.IDENTITY_NOTE


def _from_detection(detection_path: str, context: dict, associations: list[dict]) -> dict:
    loaded = load_detection(detection_path)
    provenance, issues = loaded["provenance"], list(loaded["issues"])
    pair_dir = loaded["pair_dir"]
    view = provenance["view"]
    for side in ("original", "current"):
        frame = context[side]["frame"]
        if frame is not None and view is not None and frame != view:
            return {"unavailable": True,
                    "failure": f"context {side} frame {frame} differs from evidence view {view}",
                    "provenance": provenance, "issues": issues}
    if not loaded["trusted"]:
        return {"unavailable": True,
                "failure": f"detection output is unusable ({provenance['status'] or 'no rectified view'})",
                "provenance": provenance, "issues": issues}
    names = loaded["rectified_files"]
    before = cv2.imread(str(pair_dir / names["before"]))
    after = cv2.imread(str(pair_dir / names["after"]))
    images_ok = before is not None and after is not None and before.shape == after.shape
    if not images_ok:
        issues.append("rectified images unavailable; template and hole checks skipped, "
                      "polarity taken from detection sign")
    gray_o = vision.to_gray(before) if images_ok else None
    gray_c = vision.to_gray(after) if images_ok else None
    holes_o = vision.detect_holes(gray_o) if images_ok else []
    holes_c = vision.detect_holes(gray_c) if images_ok else []
    hole_pairs = vision.match_holes(holes_o, holes_c) if images_ok else []
    regions = []
    for item in loaded["regions"]:
        bbox = [int(v) for v in item.get("bbox_px") or [0, 0, 0, 0]]
        region = {"label": item.get("label", f"R{len(regions) + 1}"),
                  "bbox": bbox, "centroid": [0, 0],
                  "area_px": int(item.get("area_px") or 0),
                  "bbox_mm": [float(v) for v in item["bbox_mm"]],
                  "centroid_mm": [float(v) for v in item["centroid_mm"]],
                  "area_mm2": item.get("area_mm2"),
                  "sign": _detection_sign(item.get("sign")),
                  "mean_diff": item.get("mean_diff"), "max_diff": item.get("max_diff"),
                  "quality": {"pixels": int(item.get("area_px") or 0),
                              "method": "detection_sign"},
                  "ncc": None,
                  "evidence": {"detection_region": item.get("label"),
                               "detection_sign": item.get("sign")}}
        if images_ok and gray_o.shape[1] >= bbox[0] + bbox[2] and gray_o.shape[0] >= bbox[1] + bbox[3]:
            region["centroid"] = [round(bbox[0] + bbox[2] / 2, 1), round(bbox[1] + bbox[3] / 2, 1)]
            region["quality"] = vision.region_quality(gray_o, gray_c, bbox)
            region["quality"]["method"] = "rectified_images"
            region["ncc"] = vision.ncc_search(gray_o, gray_c, region["centroid"])
        else:
            if images_ok:
                issues.append(f"region {region['label']}: bbox outside rectified images")
            region["centroid"] = [0, 0]
        regions.append(region)
    classified = [rules.classify_one(r, context, hole_pairs, holes_c, associations) for r in regions]
    rules.merge_shift_edges(classified)
    links = rules.pair_displaced(classified, context)
    _attach_associations_note(classified)
    comparison_id = _comparison_id(provenance["result_sha256"] or provenance["result_json"],
                                   json.dumps(context, sort_keys=True))
    verdict = rules.run_verdict(classified, None, True, True)
    return {"unavailable": False, "comparison_id": comparison_id,
            "mode": "classified" if context["declared"] else "neutral",
            "context": context, "original": {"path": provenance["before"], "role": context["original"]["role"]},
            "current": {"path": provenance["after"], "role": context["current"]["role"]},
            "detection": provenance, "frame": {"name": view, "mm_validated": True,
                                               "note": "rectified detection frame"},
            "registration": {"method": "detection_rectified",
                             "details": {"pair_id": provenance["pair_id"]}},
            "regions": classified, "displacement_links": links,
            "holes": {"original": len(holes_o), "current": len(holes_c),
                      "matched_unchanged": len(hole_pairs), "pairs": hole_pairs},
            "quality": {"diff_source": "detection_result_json",
                        "detection_warnings": provenance["warnings"]},
            "issues": issues, **verdict, "not_claimed": NOT_CLAIMED}


def _from_images(original_path: str, current_path: str, context: dict,
                 frame_meta, associations: list[dict],
                 aligner=None, segmenter=None) -> dict:
    original = cv2.imread(str(original_path))
    current = cv2.imread(str(current_path))
    if original is None:
        raise ValueError(f"cannot read original image {original_path}")
    if current is None:
        raise ValueError(f"cannot read current image {current_path}")
    gray_o = vision.to_gray(original)
    gray_c_raw = vision.to_gray(current)
    usable_o, reason_o = vision.image_usable(gray_o)
    usable_c, reason_c = vision.image_usable(gray_c_raw)
    evidence_usable = usable_o and usable_c
    issues = []
    if not usable_o:
        issues.append(f"original image unusable: {reason_o}")
    if not usable_c:
        issues.append(f"current image unusable: {reason_c}")
    if not evidence_usable:
        return {"unusable": True, "provenance": None, "issues": issues}
    frames = {context["original"]["frame"], context["current"]["frame"]} - {None}
    transform = context.get("transform")
    registration: dict
    gray_c: np.ndarray | None = None
    valid: np.ndarray | None = None
    if len(frames) == 2 and transform is None:
        return {"unavailable": True,
                "failure": "frames differ without a verified transform; comparison refused",
                "provenance": None, "issues": issues}
    if transform is not None:
        moved = vision.apply_verified_transform(
            gray_c_raw, transform["homography"], (gray_o.shape[1], gray_o.shape[0]))
        if not moved["ok"]:
            return {"unavailable": True, "failure": f"verified transform failed: {moved['reason']}",
                    "provenance": None, "issues": issues}
        gray_c, valid = moved["warped"], moved["valid"]
        registration = {"method": "verified_transform",
                        "details": {"from_frame": transform["from_frame"],
                                    "to_frame": transform["to_frame"],
                                    "provenance": transform["provenance"],
                                    "reprojection_rms_mm": transform["reprojection_rms_mm"],
                                    "coverage": round(moved["coverage"], 3)}}
    else:
        if original.shape != current.shape:
            return {"unavailable": True,
                    "failure": "image dimensions differ; views are not comparable",
                    "provenance": None, "issues": issues}
        aligned = vision.align_translation(gray_o, gray_c_raw,
                                           context["params"]["max_shift_px"], aligner)
        if not aligned["ok"]:
            return {"unavailable": True, "failure": f"registration failed: {aligned['reason']}",
                    "provenance": None, "issues": issues}
        gray_c, valid = aligned["warped"], aligned["valid"]
        registration = {"method": "ecc_translation",
                        "details": {"dx_px": aligned["dx"], "dy_px": aligned["dy"],
                                    "correlation": aligned["cc"]}}
    frame_name = (context["original"]["frame"] or context["current"]["frame"]
                  or (frame_meta or {}).get("view") or "undeclared_image_space")
    mm_validated = frame_meta is not None
    ppm = (frame_meta or {}).get("px_per_mm")
    mask, outcome = vision.difference_mask(vision.normalize_masked(gray_o, valid),
                                           vision.normalize_masked(gray_c, valid),
                                           valid, context["params"]["diff_threshold"],
                                           context["params"]["min_area_px"])
    if segmenter is not None:
        provider = segmenter(gray_o, gray_c, valid)
        if provider is not None:
            if provider.shape[:2] != mask.shape:
                raise ValueError("segmenter mask must match the image size")
            mask = np.bitwise_and(mask, (provider > 0).astype(np.uint8) * 255)
            narrowed = np.bitwise_and(valid, (provider > 0).astype(np.uint8) * 255)
            _, redo = vision.difference_mask(vision.normalize_masked(gray_o, narrowed),
                                             vision.normalize_masked(gray_c, narrowed),
                                             narrowed,
                                             context["params"]["diff_threshold"],
                                             context["params"]["min_area_px"])
            outcome = redo
            issues.append("provider segmenter confirmed the change mask")
    holes_o = vision.detect_holes(gray_o)
    holes_c = vision.detect_holes(gray_c)
    hole_pairs = vision.match_holes(holes_o, holes_c)
    regions = []
    for index, item in enumerate(outcome["regions"], 1):
        region = {"label": f"R{index}", "bbox": item["bbox"], "centroid": item["centroid"],
                  "area_px": item["area_px"], "bbox_mm": None, "centroid_mm": None,
                  "area_mm2": None, "sign": vision.polarity(item["median_delta"]),
                  "mean_diff": item["mean_diff"], "max_diff": item["max_diff"],
                  "quality": vision.region_quality(gray_o, gray_c, item["bbox"]),
                  "ncc": vision.ncc_search(gray_o, gray_c, item["centroid"]),
                  "evidence": {"median_delta": item["median_delta"]}}
        if mm_validated:
            x, y, w, h = item["bbox"]
            region["bbox_mm"] = [round(x / ppm, 2), round(y / ppm, 2),
                                 round(w / ppm, 2), round(h / ppm, 2)]
            region["centroid_mm"] = [round(item["centroid"][0] / ppm, 2),
                                     round(item["centroid"][1] / ppm, 2)]
            region["area_mm2"] = round(item["area_px"] / ppm ** 2, 2)
        regions.append(region)
    classified = [rules.classify_one(r, context, hole_pairs, holes_c, associations) for r in regions]
    rules.merge_shift_edges(classified)
    links = rules.pair_displaced(classified, context)
    _attach_associations_note(classified)
    comparison_id = _comparison_id(sha256_file(original_path) or str(original_path),
                                   sha256_file(current_path) or str(current_path),
                                   json.dumps(context, sort_keys=True))
    verdict = rules.run_verdict(classified, None, evidence_usable, True)
    return {"unavailable": False, "comparison_id": comparison_id,
            "mode": "classified" if context["declared"] else "neutral",
            "context": context,
            "original": {"path": str(original_path), "sha256": sha256_file(original_path),
                         "role": context["original"]["role"]},
            "current": {"path": str(current_path), "sha256": sha256_file(current_path),
                        "role": context["current"]["role"]},
            "detection": None,
            "frame": {"name": frame_name, "mm_validated": mm_validated,
                      "note": ("millimetres from validated frame metadata"
                               if mm_validated else "pixel coordinates only")},
            "registration": registration, "regions": classified,
            "displacement_links": links,
            "holes": {"original": len(holes_o), "current": len(holes_c),
                      "matched_unchanged": len(hole_pairs), "pairs": hole_pairs},
            "quality": {"changed_fraction": outcome["stats"]["changed_fraction"],
                        "diff_p99": outcome["stats"]["diff_p99"]},
            "issues": issues, **verdict, "not_claimed": NOT_CLAIMED}


def run_comparison(original: str | None = None, current: str | None = None,
                   context: dict | None = None, detection: str | None = None,
                   frame_meta: dict | None = None, associations=None,
                   aligner=None, segmenter=None,
                   comparison_label: str = "") -> dict:
    """Compare an original state against a current state. JSON-serializable."""
    context = context or {"declared": False, "original": {"role": None, "frame": None},
                          "current": {"role": None, "frame": None}, "surface": "light",
                          "transform": None,
                          "params": {"max_shift_px": 25, "link_distance_px": 120,
                                     "min_area_px": 25, "diff_threshold": 30.0,
                                     "ncc_present": 0.85, "ncc_contradict": 0.90,
                                     "search_radius_px": 12, "max_present_offset_px": 8},
                          "reasons": ["no comparison context declared"]}
    associations = associations or []
    if detection is not None and (original is not None or current is not None):
        raise ValueError("--detection and --original/--current are mutually exclusive")
    if detection is not None:
        result = _from_detection(detection, context, associations)
    else:
        if original is None or current is None:
            raise ValueError("need --detection or both --original and --current")
        result = _from_images(original, current, context, frame_meta, associations,
                              aligner, segmenter)
    if result.get("unavailable") or result.get("unusable"):
        if result.get("unavailable"):
            verdict = rules.run_verdict([], result["failure"], False, False)
        else:
            verdict = rules.run_verdict([], None, False, True)
        result = {"comparison_id": _comparison_id(comparison_label or "unavailable",
                                                  json.dumps(context, sort_keys=True)),
                  "mode": "classified" if context["declared"] else "neutral",
                  "context": context, "original": {"path": original}, "current": {"path": current},
                  "detection": result.get("provenance"), "frame": {"name": None, "mm_validated": False},
                  "registration": {"method": "none", "details": {}},
                  "regions": [], "displacement_links": [],
                  "holes": {"original": 0, "current": 0, "matched_unchanged": 0, "pairs": []},
                  "quality": {}, "issues": result.get("issues", []),
                  **verdict, "not_claimed": NOT_CLAIMED}
    result["comparison_label"] = comparison_label
    return vision._py(result)
