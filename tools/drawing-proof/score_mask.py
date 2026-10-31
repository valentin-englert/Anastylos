"""Score an externally produced part mask (e.g. SAM) with the same gates and overlay as the classical run.

    python tools/drawing-proof/score_mask.py --rectified <run>/rectified.png --masks <dir with mask_*.png> --out <dir>

Selection rule, fixed before any comparison: if several object masks are returned, the largest one is scored.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

import draw
import quality
import segment

EDGE_FIT = {"edge_low": 40, "edge_high": 120}


def place_mask(raw: np.ndarray, box: dict | None, shape: tuple[int, int]) -> np.ndarray:
    """SAM image masks are rasters of the returned object box (``records[].box``), not of the whole image."""
    full = np.zeros(shape, bool)
    if box is None:
        return cv2.resize(raw, (shape[1], shape[0]), interpolation=cv2.INTER_NEAREST) > 127
    left, top, right, bottom = (int(box[k]) for k in ("left", "top", "right", "bottom"))
    full[top:bottom, left:right] = cv2.resize(raw, (right - left, bottom - top), interpolation=cv2.INTER_NEAREST)[:shape[0] - top, :shape[1] - left] > 127
    return full


def _record_boxes(mask_dir: Path) -> dict[str, dict]:
    boxes = {}
    for evidence in mask_dir.parent.glob("evidence.json"):
        for record in json.loads(evidence.read_text(encoding="utf-8")).get("records", []):
            boxes[Path(record["mask"]["png"]).name] = record.get("box")
    return boxes


def score(rectified: Path, mask_paths: list[Path], px_per_mm: float, tolerance_mm: float) -> tuple[dict, np.ndarray | None]:
    image = cv2.imread(str(rectified))
    boxes = _record_boxes(mask_paths[0].parent) if mask_paths else {}
    masks = [place_mask(cv2.imread(str(p), cv2.IMREAD_GRAYSCALE), boxes.get(p.name), image.shape[:2]) for p in mask_paths]
    if not masks or max(m.sum() for m in masks) == 0:
        return {"status": "needs_capture_or_review", "flags": ["NO_PART_IN_REGION"], "masks_returned": len(masks)}, None
    mask = segment.fill_holes(segment._largest_component(max(masks, key=lambda m: m.sum())))
    dense = segment.boundary(mask)
    fit = segment.edge_fit(image, dense, **EDGE_FIT)
    checked = quality.gate(image, mask, mask, np.zeros_like(mask), fit["share_within_2px"])
    outline, deviation = draw.simplify(dense, px_per_mm, tolerance_mm)
    overlay = image.copy()
    cv2.polylines(overlay, [np.round(outline).astype(np.int32)], True, (0, 170, 0), 2, cv2.LINE_AA)
    return {**checked, "masks_returned": len(masks), "outline_points": len(outline), "max_deviation_mm": round(deviation, 3),
            "area_mm2": round(float(mask.sum()) / px_per_mm ** 2, 1), "edge_fit_px": fit}, overlay


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rectified", required=True, type=Path)
    parser.add_argument("--masks", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--px-per-mm", type=float, default=10.0)
    parser.add_argument("--tolerance-mm", type=float, default=0.2)
    args = parser.parse_args()
    report, overlay = score(args.rectified, sorted(args.masks.glob("mask_*.png")), args.px_per_mm, args.tolerance_mm)
    args.out.mkdir(parents=True, exist_ok=True)
    if overlay is not None:
        cv2.imwrite(str(args.out / "overlay.png"), overlay)
    (args.out / "score.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("status", "flags", "masks_returned")}))


if __name__ == "__main__":
    main()
