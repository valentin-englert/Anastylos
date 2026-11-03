"""Outlines of large parts lying on the bright A3 sheet, from one rectified view: offline, deterministic, no provider.

On the full page Muse's box is too loose for SAM. The sheet itself separates the parts: colour distance from the median
sheet colour inside the work area, Otsu, strict components as seeds, a looser mask for the extent, watershed so that parts
joined by a shadow stay apart, and an opening that removes thin things such as a cable.
This finds outlines, not identities: a part name here is a proposal and every job stays NEEDS_CONFIRMATION.

When the video starts with an empty sheet, that view is the reference and the method is better: the log ratio to it separates
material from cast shadow (``separate``). A part as pale as the sheet shows only through its shadow; it gets a tight box and SAM
draws the outline, and if SAM's mask does not hold up, the part is reported instead of drawn.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
from pathlib import Path

import cv2
import numpy as np

import draw
import frames
import muse_sam
import quality
import segment
import source
from drawing_job import render_layered_svg

WORK_AREA_MM = (58.0, 50.0, 254.0, 176.0)  # x, y, w, h of the dashed area on the A3 sheet
FULL_PAGE_MM = (0.0, 0.0, 420.0, 297.0)
PX_PER_MM = 6.0
LOOSE_FACTOR = 0.8  # share of the Otsu value that still counts as part; it includes soft edges and some shadow
CLOSE_MM = 2.5
OPEN_MM = 7.0  # wider than a cable, narrower than a part
SEED_ERODE_MM = 3.5
MIN_AREA_MM2 = 1000.0
CANDIDATE_STRIDE_S = 1.0
CANDIDATE_FRAMES = 12
MAX_SKIN = 0.02
EMPTY_TIMES_S = (0.8, 1.5, 2.2, 2.9, 3.6)
FINAL_TIMES_S = (0.3, 0.8, 1.3, 1.8, 2.3)
# Log-ratio limits measured on two recordings with locked exposure; a cast shadow stayed inside them, material outside.
SHADOW_FLOOR = -0.75
CHROMA_MATERIAL = 0.13
BRIGHTER_MATERIAL = 0.12
MIN_PART_MM2 = 300.0
BOX_MARGIN_MM = 6.0
PALE_SHADOW_LUM = -0.25  # on a part as pale as the sheet: about 0 on the part, about -0.45 in its shadow
MIN_KEPT_SHARE = 0.4
MIN_FIRM_COVER = 0.85  # SAM may add a pale end to a part, but must keep what was measured as sure material
SHARP_EDGE_PER_MM = 0.25  # log-ratio change per mm; a cast shadow's soft edge stays below it


def _ellipse(mm: float, ppm: float) -> np.ndarray:
    size = int(round(mm * ppm)) | 1
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))


def _solid(mask: np.ndarray, ppm: float) -> np.ndarray:
    closed = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, _ellipse(CLOSE_MM, ppm))
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    filled = np.zeros_like(closed)
    cv2.drawContours(filled, contours, -1, 1, -1)
    return cv2.morphologyEx(filled, cv2.MORPH_OPEN, _ellipse(OPEN_MM, ppm))


def find_outlines(image: np.ndarray, area_px: tuple[int, int, int, int], ppm: float) -> list[np.ndarray]:
    """Filled boolean masks of the large parts inside ``area_px`` (x, y, w, h), largest first."""
    x, y, w, h = area_px
    inside = np.zeros(image.shape[:2], bool)
    inside[y:y + h, x:x + w] = True
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32)
    sheet = np.median(lab[inside], axis=0)
    distance = np.sqrt((0.6 * (lab[..., 0] - sheet[0])) ** 2 + (1.5 * (lab[..., 1] - sheet[1])) ** 2 + (1.5 * (lab[..., 2] - sheet[2])) ** 2)
    scaled = np.clip(distance, 0, 255).astype(np.uint8)
    otsu = cv2.threshold(scaled[inside].reshape(-1, 1), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[0]
    loose = _solid((distance > otsu * LOOSE_FACTOR) & inside, ppm)
    strict = _solid((distance > otsu) & inside, ppm)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(strict, connectivity=8)
    minimum = MIN_AREA_MM2 * ppm * ppm
    seeds = [i for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] >= minimum]
    markers = np.zeros(image.shape[:2], np.int32)
    markers[loose == 0] = 1
    for number, seed in enumerate(seeds, start=2):
        markers[cv2.erode((labels == seed).astype(np.uint8), _ellipse(SEED_ERODE_MM, ppm)) > 0] = number
    relief = cv2.GaussianBlur(np.clip(distance * 3, 0, 255).astype(np.uint8), (5, 5), 0)
    regions = cv2.watershed(cv2.cvtColor(relief, cv2.COLOR_GRAY2BGR), markers)
    masks = [cv2.morphologyEx((regions == number).astype(np.uint8), cv2.MORPH_OPEN, _ellipse(OPEN_MM, ppm)) > 0
             for number in range(2, len(seeds) + 2)]
    return sorted((m for m in masks if m.sum() >= minimum), key=lambda m: -int(m.sum()))


def separate(image: np.ndarray, empty: np.ndarray, area_px: tuple[int, int, int, int], ppm: float) -> tuple[list[np.ndarray], list[list[int]]]:
    """Against the empty sheet: filled masks of material parts, and tight boxes where something shows only by its shadow."""
    x, y, w, h = area_px
    inside = np.zeros(image.shape[:2], bool)
    inside[y:y + h, x:x + w] = True
    ratio = np.log((cv2.GaussianBlur(image.astype(np.float32), (0, 0), 0.7) + 8) / (cv2.GaussianBlur(empty.astype(np.float32), (0, 0), 0.7) + 8))
    lum = ratio.mean(axis=2)
    chroma = np.sqrt(((ratio - lum[..., None]) ** 2).mean(axis=2))
    firm = ((lum < SHADOW_FLOOR) | (chroma > CHROMA_MATERIAL) | (lum > BRIGHTER_MATERIAL)) & inside
    firm = cv2.morphologyEx(cv2.morphologyEx(firm.astype(np.uint8), cv2.MORPH_OPEN, _ellipse(0.5, ppm)), cv2.MORPH_CLOSE, _ellipse(1.5, ppm))
    contours, _ = cv2.findContours(firm, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    masks = []
    for contour in contours:
        if cv2.contourArea(contour) >= MIN_PART_MM2 * ppm * ppm:
            mask = np.zeros(firm.shape, np.uint8)
            cv2.drawContours(mask, [contour], -1, 1, -1)
            masks.append(mask > 0)
    changed = ((np.abs(lum) > 0.15) | (chroma > 0.05)) & inside
    changed = cv2.morphologyEx(changed.astype(np.uint8), cv2.MORPH_OPEN, _ellipse(1.5, ppm))
    near_parts = cv2.dilate(sum(masks, np.zeros(firm.shape, bool)).astype(np.uint8), _ellipse(10.0, ppm)) if masks else np.zeros(firm.shape, np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(changed & (1 - near_parts), connectivity=8)
    margin = int(BOX_MARGIN_MM * ppm)
    boxes = []
    for i in range(1, count):
        if stats[i, cv2.CC_STAT_AREA] >= MIN_PART_MM2 * ppm * ppm:
            bx, by, bw, bh = stats[i, :4].tolist()
            boxes.append([max(bx - margin, 0), max(by - margin, 0), min(bx + bw + margin, image.shape[1]), min(by + bh + margin, image.shape[0])])
    return sorted(masks, key=lambda m: -int(m.sum())), boxes


def reference_view(video: Path) -> tuple[source.RectifiedFrame, np.ndarray] | None:
    """Median final view and median empty view, or None when the video does not start with an empty sheet."""
    capture = cv2.VideoCapture(str(video))
    fps, total = capture.get(cv2.CAP_PROP_FPS), int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    capture.release()

    def views(frame_numbers):
        out = []
        for frame in frame_numbers:
            try:
                out.append(source.rectify(video, frame, FULL_PAGE_MM, PX_PER_MM)[1])
            except source.SourceError:
                continue
        return out

    empties = views(int(fps * t) for t in EMPTY_TIMES_S)
    finals = views(total - 1 - int(fps * t) for t in FINAL_TIMES_S)
    if len(empties) < 2 or len(finals) < 2:
        return None
    empty = np.median(np.stack([v.image for v in empties]), axis=0).astype(np.uint8)
    if find_outlines(empty, tuple(int(v * PX_PER_MM) for v in WORK_AREA_MM), PX_PER_MM):
        return None
    final = np.median(np.stack([v.image for v in finals]), axis=0).astype(np.uint8)
    return dataclasses.replace(finals[0], image=final), empty


def without_shadow(mask: np.ndarray, image: np.ndarray, empty: np.ndarray, ppm: float, firm: np.ndarray | None = None) -> np.ndarray | None:
    """A mask with its measured cast shadow removed; ``firm`` pixels are material and stay. None when too little would remain."""
    ratio = np.log((cv2.GaussianBlur(image.astype(np.float32), (0, 0), 0.7) + 8) / (cv2.GaussianBlur(empty.astype(np.float32), (0, 0), 0.7) + 8))
    lit = ratio.mean(axis=2) >= PALE_SHADOW_LUM
    kept = (mask & (lit if firm is None else lit | firm)).astype(np.uint8)
    kept = cv2.morphologyEx(cv2.morphologyEx(kept, cv2.MORPH_CLOSE, _ellipse(4.0, ppm)), cv2.MORPH_OPEN, _ellipse(3.0, ppm)) & mask.astype(np.uint8)
    contours, _ = cv2.findContours(kept, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return None
    solid = np.zeros(kept.shape, np.uint8)
    cv2.drawContours(solid, [max(contours, key=cv2.contourArea)], -1, 1, -1)
    return solid > 0 if solid.sum() >= MIN_KEPT_SHARE * mask.sum() else None


def sam_outline(rectified: source.RectifiedFrame, empty: np.ndarray, box: list[int], folder: Path) -> tuple[np.ndarray | None, str]:
    """SAM's mask for a tight box without its shadow; None and the reason when there is none or a check fails."""
    folder.mkdir(parents=True, exist_ok=True)
    image_path = folder / "view.png"
    cv2.imwrite(str(image_path), rectified.image)
    mask, info = muse_sam.sam_mask(image_path, box, rectified.image.shape[:2], folder / "sam", folder / "sam_ledger.jsonl")
    if mask is None:
        return None, "SAM returned no mask"
    outer, _openings = muse_sam.outer_and_openings(mask)
    flags = quality.plausibility(outer, box, info.get("sam_box"))["flags"]
    if flags:
        return None, "SAM mask failed checks: " + ", ".join(flags)
    cleaned = without_shadow(outer, rectified.image, empty, rectified.px_per_mm)
    return (cleaned, "") if cleaned is not None else (None, "too little of SAM's mask remained without the shadow")


def reached_by_sheet(image: np.ndarray, empty: np.ndarray, area_px: tuple[int, int, int, int], ppm: float) -> np.ndarray:
    """Pixels joined to bare sheet without crossing a sharp edge. A cast shadow fades softly and is joined; a part's edge stops it."""
    x, y, w, h = area_px
    inside = np.zeros(image.shape[:2], bool)
    inside[y:y + h, x:x + w] = True
    soft = np.log((cv2.GaussianBlur(image.astype(np.float32), (0, 0), 2.0) + 8) / (cv2.GaussianBlur(empty.astype(np.float32), (0, 0), 2.0) + 8))
    lum = soft.mean(axis=2)
    across = np.stack([cv2.Sobel(soft[..., c], cv2.CV_32F, 1, 0, ksize=3) / 8 for c in range(3)], axis=2)
    down = np.stack([cv2.Sobel(soft[..., c], cv2.CV_32F, 0, 1, ksize=3) / 8 for c in range(3)], axis=2)
    edge = np.sqrt((across ** 2 + down ** 2).max(axis=2)) * ppm > SHARP_EDGE_PER_MM
    edge = cv2.dilate(edge.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    count, labels, stats, _ = cv2.connectedComponentsWithStats((~edge & (lum > -1.3) & inside).astype(np.uint8), connectivity=4)
    bare = (np.abs(lum) < 0.05) & inside
    reached = np.zeros(lum.shape, bool)
    for i in range(1, count):
        if stats[i, cv2.CC_STAT_AREA] > 55 * ppm * ppm and bare[labels == i].mean() > 0.2:
            reached |= labels == i
    return reached


def refine_part(rectified: source.RectifiedFrame, reached: np.ndarray, firm: np.ndarray, folder: Path) -> tuple[np.ndarray, str]:
    """SAM from the measured box and a point on sure material. It keeps the measured mask when SAM would lose part of that material.

    The measured mask can hold a grey part's shadow, so only what the sheet flood could not reach counts as sure."""
    ppm = rectified.px_per_mm
    sure = cv2.morphologyEx((firm & ~reached).astype(np.uint8), cv2.MORPH_OPEN, _ellipse(OPEN_MM, ppm))
    if not sure.any():
        return firm, "difference from the empty sheet"
    bx, by, bw, bh = cv2.boundingRect(firm.astype(np.uint8))
    margin = int(3.0 * ppm)
    h, w = firm.shape
    box = [max(bx - margin, 0), max(by - margin, 0), min(bx + bw + margin, w), min(by + bh + margin, h)]
    inner = cv2.distanceTransform(sure, cv2.DIST_L2, 5)
    py, px = np.unravel_index(np.argmax(inner), inner.shape)
    folder.mkdir(parents=True, exist_ok=True)
    image_path = folder / "view.png"
    cv2.imwrite(str(image_path), rectified.image)
    mask, _info = muse_sam.sam_mask(image_path, box, firm.shape, folder / "sam", folder / "sam_ledger.jsonl", points=[{"x": int(px), "y": int(py), "label": "positive"}])
    if mask is None:
        return firm, "difference from the empty sheet"
    outer, _openings = muse_sam.outer_and_openings(mask)
    if (outer & (sure > 0)).sum() < MIN_FIRM_COVER * sure.sum():
        return firm, "difference from the empty sheet"
    return outer, "SAM from a box and point measured against the empty sheet"


def _atomic_json(path: Path, document: dict) -> None:
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(document, indent=1), encoding="utf-8")
    os.replace(partial, path)


def _job_name(part: str) -> str:
    return "outline-" + re.sub(r"[^a-z0-9]+", "-", part.lower()).strip("-")


def write_job(session_dir: Path, rectified: source.RectifiedFrame, mask: np.ndarray, part: str, origin: str,
              method: str = "colour separation from the sheet") -> str:
    """One finished drawing job in the layout tools/explosion and the session UI already read."""
    job = Path(session_dir) / "drawings" / "jobs" / _job_name(part)
    job.mkdir(parents=True, exist_ok=False)
    ppm = rectified.px_per_mm
    outline, deviation = draw.simplify(segment.boundary(mask), ppm, 0.2)
    spec = {"part": part, "scope": "assembly", "view_side": None, "region_mm": list(FULL_PAGE_MM), "px_per_mm": ppm, "method": "sheet_outlines"}
    svg = render_layered_svg(rectified.px_to_mm(outline).round(2).tolist(), [], {
        "title": "Outline (unconfirmed)", "subtitle": f"{part} (assembly); {origin}",
        "legend": [f"Outline: {method}, max deviation {deviation:.2f} mm, sheet plane; not part dimensions.",
                   "Part name, view side and orientation need operator confirmation."]})
    cv2.imwrite(str(job / "chosen_rectified.png"), rectified.image)
    view = rectified.image.copy()
    cv2.polylines(view, [np.round(segment.boundary(mask)).astype(np.int32)], True, (0, 200, 0), 4, cv2.LINE_AA)
    cv2.imwrite(str(job / "overlay.jpg"), view)
    (job / "drawing.svg").write_text(svg, encoding="utf-8")
    _atomic_json(job / "spec.json", spec)
    _atomic_json(job / "progress.json", {"state": "done", "result": {
        "status": "NEEDS_CONFIRMATION", "spec": spec, "outline_points": len(outline), "opening_candidates": 0,
        "reasons": [f"Found by {method}; the part name is a proposal, please check the outline."]}})
    return job.name


def _named(masks: list[np.ndarray], parts: list[tuple[str, tuple[float, float]]], ppm: float) -> list[tuple[str, np.ndarray]]:
    """Each proposed name goes to the outline containing its sheet point; other outlines are numbered."""
    named, used = [], set()
    for name, (x, y) in parts:
        hit = next((i for i, m in enumerate(masks) if m[int(y * ppm), int(x * ppm)]), None)
        if hit is None:
            raise SystemExit(f"no outline contains the point given for {name!r}")
        named.append((name, masks[hit]))
        used.add(hit)
    return named + [(f"part {i + 1}", m) for i, m in enumerate(masks) if i not in used]


def best_view(video: Path) -> tuple[source.RectifiedFrame, int, list[np.ndarray]]:
    """The candidate frame showing the most separate parts; hands are avoided first by the skin share, ties go to the least skin."""
    sampled, _cache = frames.cached_sample(video, CANDIDATE_STRIDE_S, None)
    area = tuple(int(v * PX_PER_MM) for v in WORK_AREA_MM)
    best = None
    for candidate in frames.select(sampled, CANDIDATE_FRAMES):
        if candidate.skin > MAX_SKIN:
            continue
        try:
            _, rectified = source.rectify(video, candidate.frame_index, FULL_PAGE_MM, PX_PER_MM)
        except source.SourceError:
            continue
        masks = find_outlines(rectified.image, area, PX_PER_MM)
        score = (len(masks), -candidate.skin)
        if masks and (best is None or score > best[0]):
            best = (score, rectified, candidate.frame_index, masks)
    if best is None:
        raise SystemExit("no frame shows parts on the sheet without a hand")
    return best[1], best[2], best[3]


def _part(text: str) -> tuple[str, tuple[float, float]]:
    name, x, y = re.fullmatch(r"(.+)@([\d.]+),([\d.]+)", text).groups()
    return name, (float(x), float(y))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-dir", required=True, type=Path)
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--frame", required=True, help="video frame number, or auto")
    parser.add_argument("--part", action="append", type=_part, default=[], metavar="NAME@X,Y", help="proposed name and a sheet point (mm) inside it")
    args = parser.parse_args()
    if args.frame == "auto" and (reference := reference_view(args.video)) is not None:
        rectified, empty = reference
        masks, boxes = separate(rectified.image, empty, tuple(int(v * PX_PER_MM) for v in WORK_AREA_MM), PX_PER_MM)
        reached = reached_by_sheet(rectified.image, empty, tuple(int(v * PX_PER_MM) for v in WORK_AREA_MM), PX_PER_MM)
        written, not_drawn, numbered = [], [], len(masks)
        for number, mask in enumerate(masks, start=1):
            refined, method = refine_part(rectified, reached, mask, args.session_dir / "drawings" / "sam-boxes" / f"part-{number}")
            written.append(write_job(args.session_dir, rectified, refined, f"part {number}", "final view of the video", method))
        for box in boxes:
            numbered += 1
            mask, reason = sam_outline(rectified, empty, box, args.session_dir / "drawings" / "sam-boxes" / f"part-{numbered}")
            if mask is None:
                not_drawn.append({"part": f"part {numbered}", "box_px": box, "reason": reason})
            else:
                written.append(write_job(args.session_dir, rectified, mask, f"part {numbered}", "final view of the video", "SAM from a box found against the empty sheet"))
        print(json.dumps({"jobs": written, "not_drawn": not_drawn}))
        return
    if args.frame == "auto":
        rectified, frame, masks = best_view(args.video)
    else:
        frame = int(args.frame)
        _, rectified = source.rectify(args.video, frame, FULL_PAGE_MM, PX_PER_MM)
        masks = find_outlines(rectified.image, tuple(int(v * PX_PER_MM) for v in WORK_AREA_MM), PX_PER_MM)
    origin = f"video frame {frame}"
    print(json.dumps([write_job(args.session_dir, rectified, mask, name, origin) for name, mask in _named(masks, args.part, PX_PER_MM)]))


if __name__ == "__main__":
    main()
