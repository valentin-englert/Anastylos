"""MF-1: fuse outline pieces of a hand-held part that are visible in different frames (plan: specs/plan_ev1_mf1.json).

    python tools/drawing-proof/fusion.py --video <mp4> --t0 12 --t1 24 --part "back shell of the game controller" --out <dir>

Each frame: frozen r4.1 Muse box → SAM mask; pixels near skin are occluded. Masks are aligned (ECC, affine) to the
frame with the least skin. A fused rim pixel counts as observed if at least one aligned frame shows it unoccluded;
rim never seen unoccluded is reported as unknown. Held parts tilt and come closer to the camera: the alignment
residual is reported, not hidden.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

import frames
import muse_sam
import segment
import source

REGION_MM, PX_PER_MM, COUNT, OCCLUSION_PX = (58, 50, 254, 177), 4.0, 6, 6


def pick(candidates: list[frames.Candidate], count: int) -> list[frames.Candidate]:
    """Time buckets, best sharpness and stillness per bucket; hands are expected, so skin is not penalised."""
    buckets = np.array_split(np.arange(len(candidates)), min(count, len(candidates)))
    return [candidates[min(b, key=lambda i: candidates[i].motion - 1e-3 * candidates[i].sharpness)] for b in buckets if len(b)]


def skin_zone(image: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    skin = (((h <= 20) | (h >= 170)) & (s >= 40) & (s <= 180) & (v >= 60)).astype(np.uint8)
    skin = cv2.morphologyEx(skin, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    return cv2.dilate(skin, np.ones((2 * OCCLUSION_PX + 1,) * 2, np.uint8)) > 0


def align(reference: np.ndarray, moving: np.ndarray) -> tuple[np.ndarray, float]:
    ref = cv2.GaussianBlur(reference.astype(np.float32), (31, 31), 0)
    mov = cv2.GaussianBlur(moving.astype(np.float32), (31, 31), 0)
    warp = np.eye(2, 3, dtype=np.float32)
    _, warp = cv2.findTransformECC(ref, mov, warp, cv2.MOTION_AFFINE, (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 200, 1e-6), None, 5)
    return warp, 0.0


def run(video: Path, t0: float, t1: float, part: str, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    candidates, _ = frames.cached_sample(video, 0.5, (t0, t1))
    key = muse_sam.client.load_key()
    views = []
    for c in pick(candidates, COUNT):
        _, rf = source.rectify(video, c.frame_index, REGION_MM, PX_PER_MM)
        path = out / f"frame-{c.frame_index}.png"
        cv2.imwrite(str(path), rf.image)
        box = muse_sam.muse_box(rf.image, part, key)
        entry = {"frame": c.frame_index, "t_s": c.t_s, "muse": box["answer"], "valid": box["valid"]}
        if box["valid"]:
            mask, info = muse_sam.sam_mask(path, box["box_full"], rf.image.shape[:2], out / f"sam-{c.frame_index}", out / "sam_ledger.jsonl")
            if mask is not None:
                outer, _ = muse_sam.outer_and_openings(mask)
                occluded = skin_zone(rf.image)
                entry.update(mask=outer, occluded=occluded, image=rf.image, skin_on_rim=float((occluded & outer).sum() / max(outer.sum(), 1)))
        views.append(entry)
    usable = [v for v in views if "mask" in v]
    if len(usable) < 2:
        return {"status": "NOT_ENOUGH_FRAMES", "frames": [{k: v for k, v in e.items() if k not in ("mask", "occluded", "image")} for e in views]}
    ref = min(usable, key=lambda v: v["skin_on_rim"])
    shape = ref["mask"].shape
    seen = np.zeros(shape, np.uint16)       # rim pixels seen unoccluded, per pixel count
    union = np.zeros(shape, bool)
    report = []
    for v in usable:
        warp, _ = align(ref["mask"], v["mask"]) if v is not ref else (np.eye(2, 3, dtype=np.float32), 0.0)
        mask = cv2.warpAffine(v["mask"].astype(np.uint8), warp, shape[::-1], flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP) > 0
        occluded = cv2.warpAffine(v["occluded"].astype(np.uint8), warp, shape[::-1], flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP) > 0
        rim = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0
        seen += (rim & ~occluded).astype(np.uint16)
        union |= mask
        report.append({"frame": v["frame"], "t_s": v["t_s"], "skin_on_rim": round(v["skin_on_rim"], 4),
                       "iou_with_reference": round(float((mask & ref["mask"]).sum() / max((mask | ref["mask"]).sum(), 1)), 4)})
    fused = segment.fill_holes(segment._largest_component(union))
    rim = segment.boundary(fused)
    near = cv2.dilate((seen > 0).astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
    observed = np.array([near[int(y), int(x)] for x, y in rim])
    view = ref["image"].copy()
    for (x, y), ok in zip(rim.astype(int), observed):
        cv2.circle(view, (x, y), 1, (0, 190, 0) if ok else (0, 0, 230), -1)
    single = segment.boundary(ref["mask"]).astype(np.int32)
    cv2.polylines(view, [single], True, (255, 140, 0), 1, cv2.LINE_AA)
    cv2.imwrite(str(out / "fused_overlay.jpg"), view)
    result = {"status": "fused", "reference_frame": ref["frame"], "frames": report, "rim_observed_share": round(float(observed.mean()), 4),
              "fused_area_mm2": round(float(fused.sum()) / PX_PER_MM ** 2), "reference_area_mm2": round(float(ref["mask"].sum()) / PX_PER_MM ** 2),
              "muse": [{"frame": e["frame"], "fully_visible": e["muse"]["fully_visible"], "reason": e["muse"]["reason"][:120]} for e in views]}
    (out / "fusion.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--t0", type=float, required=True)
    parser.add_argument("--t1", type=float, required=True)
    parser.add_argument("--part", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.video, args.t0, args.t1, args.part, args.out), indent=1))


if __name__ == "__main__":
    main()
