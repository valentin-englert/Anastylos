"""Outlines of large parts lying on the bright A3 sheet, from one rectified view: offline, deterministic, no provider.

On the full page Muse's box is too loose for SAM. The sheet itself separates the parts: colour distance from the median
sheet colour inside the work area, Otsu, strict components as seeds, a looser mask for the extent, watershed so that parts
joined by a shadow stay apart, and an opening that removes thin things such as a cable.
This finds outlines, not identities: a part name here is a proposal and every job stays NEEDS_CONFIRMATION.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

import cv2
import numpy as np

import draw
import frames
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


def _atomic_json(path: Path, document: dict) -> None:
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(document, indent=1), encoding="utf-8")
    os.replace(partial, path)


def _job_name(part: str) -> str:
    return "outline-" + re.sub(r"[^a-z0-9]+", "-", part.lower()).strip("-")


def write_job(session_dir: Path, rectified: source.RectifiedFrame, mask: np.ndarray, part: str, origin: str) -> str:
    """One finished drawing job in the layout tools/explosion and the session UI already read."""
    job = Path(session_dir) / "drawings" / "jobs" / _job_name(part)
    job.mkdir(parents=True, exist_ok=False)
    ppm = rectified.px_per_mm
    outline, deviation = draw.simplify(segment.boundary(mask), ppm, 0.2)
    spec = {"part": part, "scope": "assembly", "view_side": None, "region_mm": list(FULL_PAGE_MM), "px_per_mm": ppm, "method": "sheet_outlines"}
    svg = render_layered_svg(rectified.px_to_mm(outline).round(2).tolist(), [], {
        "title": "Outline (unconfirmed)", "subtitle": f"{part} (assembly); {origin}",
        "legend": [f"Outline: colour separation from the sheet, max deviation {deviation:.2f} mm, sheet plane; not part dimensions.",
                   "Part name, view side and orientation need operator confirmation."]})
    cv2.imwrite(str(job / "chosen_rectified.png"), rectified.image)
    view = rectified.image.copy()
    cv2.polylines(view, [np.round(segment.boundary(mask)).astype(np.int32)], True, (0, 200, 0), 4, cv2.LINE_AA)
    cv2.imwrite(str(job / "overlay.jpg"), view)
    (job / "drawing.svg").write_text(svg, encoding="utf-8")
    _atomic_json(job / "spec.json", spec)
    _atomic_json(job / "progress.json", {"state": "done", "result": {
        "status": "NEEDS_CONFIRMATION", "spec": spec, "outline_points": len(outline), "opening_candidates": 0,
        "reasons": ["Found by colour on the sheet; the part name is a proposal, please check the outline."]}})
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
