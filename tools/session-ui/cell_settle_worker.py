"""When does each storage cell of the sheet change for good, measured against the empty sheet at the start of a video?

The deposit windows of the detector can be false hits (a cell's first window may belong to another part). A storage
cell is simpler to read: the rectified view of the cell compared with the empty sheet at the start of the video says when
its content last changed for good. This worker measures that once per video at 1 Hz and writes one JSON file; the UI
process (``replay.py``) only reads it. OpenCV and the sheet reader stay in this subprocess.

Reference: the median of the rectified frames at ``sheet_outlines.EMPTY_TIMES_S`` (the video must start with an empty
sheet: at least two of them register and ``sheet_outlines.find_outlines`` finds no part in the work area). Cells come from
the calibration (``anastylos_custody`` zones of ``custody-config.v15.json``), shrunk by ``CELL_INSET_MM``. A cell counts as
changed where the log-ratio against the empty sheet is large in luminance or chroma; ``final`` is the median change share
of the last five samples, the cell is ``occupied`` when it exceeds ``OCCUPIED_MIN``, and ``settled_s`` is the start of the
run, ending at the last sample, in which the share stayed above half of ``final``; ``first_s`` is the start of the run, ending at
the last sample, in which the share stayed above ``OCCUPIED_MIN`` (the first lasting change: the first of several parts). A cell that was not empty at the start
(its mean colour in the reference differs from the median of the twelve cells by more than ``EMPTY_CELL_DEVIATION``; a
small object in it would go unnoticed) has ``reference_ok`` false and no settle time.

    python cell_settle_worker.py --video V --video-sha256 H --out FILE.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "drawing-proof"))
import frames  # noqa: E402
import sheet_outlines  # noqa: E402
import source  # noqa: E402

SCHEMA = "anastylos-cell-settle@1"
PX_PER_MM = 4.0
STEP_S = 1.0
CELL_INSET_MM = 2.0
OCCUPIED_MIN = 0.03
SETTLE_SHARE = 0.5
LUMINANCE_CHANGE = 0.25
CHROMA_CHANGE = 0.1
EMPTY_CELL_DEVIATION = 0.1  # log-ratio of a cell's mean colour in the reference against the median cell
FINAL_SAMPLES = 5


def _rectify(video: Path, frame_index: int, ppm: float):
    """The rectified full-page view as float32 BGR, or None when the sheet does not register."""
    try:
        return source.rectify(video, frame_index, sheet_outlines.FULL_PAGE_MM, ppm)[1].image.astype(np.float32)
    except source.SourceError:
        return None


def _cell_rects() -> dict[str, tuple[float, float, float, float]]:
    """``{"cell-1": (x, y, w, h)}`` in sheet mm from the calibration."""
    from anastylos_custody.config import load_custody_config
    from anastylos_custody.sheet import SheetReader
    from anastylos_custody.zones import tray_cells
    custody, harness, kit, _ = load_custody_config(source.CUSTODY_CONFIG)
    return {zone.id: tuple(zone.rect_mm) for zone in tray_cells(SheetReader(harness, kit, custody["view"]).zones())}


def _changed(view: np.ndarray, empty: np.ndarray) -> np.ndarray:
    ratio = np.log((view + 8) / (empty + 8))
    luminance = ratio.mean(axis=2)
    chroma = np.sqrt(((ratio - luminance[..., None]) ** 2).mean(axis=2))
    return (np.abs(luminance) > LUMINANCE_CHANGE) | (chroma > CHROMA_CHANGE)


def settle_time(times: list[float], track: list[float], limit: float) -> float | None:
    """Start of the run that ends at the last sample and stays above ``limit``."""
    above = [value > limit for value in track]
    index = len(above) - 1
    if index < 0 or not above[index]:
        return None
    while index > 0 and above[index - 1]:
        index -= 1
    return round(times[index], 1)


def measure(video: Path, video_sha256: str, ppm: float = PX_PER_MM, rectify=None, cell_rects=None) -> dict:
    rectify, cell_rects = rectify or _rectify, cell_rects or _cell_rects
    actual = frames.video_sha256(video)
    if actual != video_sha256:
        raise ValueError("the video file differs from the SHA-256 recorded in the session")
    capture = cv2.VideoCapture(str(video))
    try:
        fps, total = capture.get(cv2.CAP_PROP_FPS), int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    finally:
        capture.release()
    if not fps or total <= 0:
        raise ValueError("the video cannot be read")
    document = {"schema": SCHEMA, "video": {"name": video.name, "sha256": actual, "fps": round(fps, 3),
                                            "duration_s": round(total / fps, 3)},
                "reference": {"status": "no_reference", "reason": "", "empty_times_s": list(sheet_outlines.EMPTY_TIMES_S)},
                "step_s": STEP_S, "px_per_mm": ppm, "times": [], "cells": {},
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "memory_written": False, "model_called": False}
    empties = [view for view in (rectify(video, int(fps * t), ppm) for t in sheet_outlines.EMPTY_TIMES_S) if view is not None]
    if len(empties) < 2:
        document["reference"]["reason"] = "fewer than two frames at the start of the video register to the sheet"
        return document
    empty = np.median(np.stack(empties), axis=0).astype(np.float32)
    work = tuple(int(v * ppm) for v in sheet_outlines.WORK_AREA_MM)
    if sheet_outlines.find_outlines(empty.astype(np.uint8), work, ppm):
        document["reference"]["reason"] = "the sheet is not empty at the start of the video"
        return document
    document["reference"].update(status="ok", empty_frames=len(empties))
    rects = cell_rects()
    boxes = {}
    for cell, (x, y, w, h) in rects.items():
        inset = CELL_INSET_MM
        boxes[cell] = [int(round(v * ppm)) for v in (x + inset, y + inset, x + w - inset, y + h - inset)]
    means = {cell: empty[y0:y1, x0:x1].reshape(-1, 3).mean(axis=0) for cell, (x0, y0, x1, y1) in boxes.items()}
    typical = np.median(np.stack(list(means.values())), axis=0)
    deviation = {cell: float(np.abs(np.log((mean + 8) / (typical + 8))).max()) for cell, mean in means.items()}
    times, tracks = [], {cell: [] for cell in boxes}
    for index in range(0, total, max(1, int(round(fps * STEP_S)))):
        view = rectify(video, index, ppm)
        if view is None:
            continue
        changed = _changed(view, empty)
        times.append(index / fps)
        for cell, (x0, y0, x1, y1) in boxes.items():
            tracks[cell].append(float(changed[y0:y1, x0:x1].mean()))
    document["times"] = [round(t, 2) for t in times]
    for cell, track in tracks.items():
        if not track:
            continue
        initial = deviation[cell]
        final = float(np.median(track[-FINAL_SAMPLES:]))
        reference_ok = initial <= EMPTY_CELL_DEVIATION
        occupied = reference_ok and final > OCCUPIED_MIN
        document["cells"][cell] = {"initial": round(initial, 3), "final": round(final, 3), "reference_ok": reference_ok,
                                   "occupied": occupied, "settled_s": settle_time(times, track, final * SETTLE_SHARE) if occupied else None,
                                   "first_s": settle_time(times, track, OCCUPIED_MIN) if occupied else None,
                                   "track": [round(v, 3) for v in track]}
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--video-sha256", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        document = measure(args.video, args.video_sha256)
    except (OSError, ValueError, source.SourceError) as exc:
        print(f"cell measurement failed: {exc}", file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    partial = args.out.with_name(args.out.name + ".partial")
    partial.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8", newline="\n")
    os.replace(partial, args.out)
    occupied = sorted(c for c, v in document["cells"].items() if v["occupied"])
    print(json.dumps({"reference": document["reference"]["status"], "occupied_cells": occupied}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
