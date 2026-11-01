"""EV-1: what left or arrived between two quiet hand-free frames, from the shadow-corrected difference (no model).

    python tools/drawing-proof/footprint.py --video <mp4> --events <custody events.json> --out <dir>

Parameters are fixed in specs/plan_ev1_mf1.json. The changed region is evidence of a removal or deposit at that
place and time; which part it is stays a binding candidate (overlapping custody events) until the operator confirms.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

import frames
import segment
import source

REGION_MM, PX_PER_MM = (58, 50, 254, 177), 4.0
DELTA_E, SHADOW_AB, SHADOW_L = 14.0, 4.0, (0.45, 0.92)
MIN_COMPONENT_MM2 = 300


def quiet_pairs(candidates: list[frames.Candidate]) -> list[tuple[frames.Candidate, frames.Candidate]]:
    motion = np.percentile([c.motion for c in candidates], 25)
    skin = np.percentile([c.skin for c in candidates], 25)
    quiet = [c.motion <= motion and c.skin <= skin for c in candidates]
    pairs, last = [], None
    for i, c in enumerate(candidates):
        if quiet[i]:
            if last is not None and i - last > 1:
                pairs.append((candidates[last], c))
            last = i
    return pairs


def change_mask(before: np.ndarray, after: np.ndarray) -> np.ndarray:
    a = cv2.cvtColor(before, cv2.COLOR_BGR2LAB).astype(np.float32)
    b = cv2.cvtColor(after, cv2.COLOR_BGR2LAB).astype(np.float32)
    a[..., 0] *= 100 / 255
    b[..., 0] *= 100 / 255
    delta = np.sqrt(((a - b) ** 2).sum(-1))
    ratio = (np.minimum(a[..., 0], b[..., 0]) + 1) / (np.maximum(a[..., 0], b[..., 0]) + 1)
    shadow = (np.abs(a[..., 1] - b[..., 1]) <= SHADOW_AB) & (np.abs(a[..., 2] - b[..., 2]) <= SHADOW_AB) & \
             (ratio >= SHADOW_L[0]) & (ratio <= SHADOW_L[1])
    mask = ((delta > DELTA_E) & ~shadow).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8)) > 0


def components(mask: np.ndarray) -> list[np.ndarray]:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    keep = [i for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] / PX_PER_MM ** 2 >= MIN_COMPONENT_MM2]
    return [segment.fill_holes(labels == i) for i in sorted(keep, key=lambda i: -stats[i, cv2.CC_STAT_AREA])]


def run(video: Path, events_path: Path, out: Path) -> list[dict]:
    out.mkdir(parents=True, exist_ok=True)
    candidates, _ = frames.cached_sample(video, 0.5, None)
    events = [e for e in json.loads(events_path.read_text(encoding="utf-8"))["events"] if e.get("kind") == "site_changed"]
    rows = []
    for before, after in quiet_pairs(candidates):
        try:
            _, rb = source.rectify(video, before.frame_index, REGION_MM, PX_PER_MM)
            _, ra = source.rectify(video, after.frame_index, REGION_MM, PX_PER_MM)
        except source.SourceError as exc:
            rows.append({"before_s": before.t_s, "after_s": after.t_s, "status": "REGISTRATION_FAILED", "detail": str(exc)[:120]})
            continue
        parts = components(change_mask(rb.image, ra.image))
        if not parts:
            continue
        name = f"{before.t_s:07.1f}-{after.t_s:07.1f}"
        view = np.hstack([rb.image, ra.image])
        for k, part in enumerate(parts):
            contour = np.round(segment.boundary(part)).astype(np.int32)
            colour = (0, 0, 230) if k == 0 else (0, 160, 230)
            cv2.polylines(view, [contour], True, colour, 2, cv2.LINE_AA)
            cv2.polylines(view, [contour + (rb.image.shape[1], 0)], True, colour, 2, cv2.LINE_AA)
        cv2.imwrite(str(out / f"{name}.jpg"), view)
        overlapping = [e["id"] for e in events if e["t_before_s"] <= after.t_s and e["t_after_s"] >= before.t_s]
        rows.append({"before_s": before.t_s, "after_s": after.t_s, "image": f"{name}.jpg", "status": "changed",
                     "areas_mm2": [round(float(p.sum()) / PX_PER_MM ** 2) for p in parts], "binding_candidates": overlapping})
    (out / "pairs.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    for row in run(args.video, args.events, args.out):
        print(json.dumps(row))


if __name__ == "__main__":
    main()
