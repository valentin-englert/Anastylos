"""Overlays, contact sheets, and review videos. Needs OpenCV + NumPy.

All geometry stays in the evidence pixel space (display pixels,
origin top-left). Mask rasters are box-local (official parser
``SegmentationMaskBounds``) and pasted at the box origin with
nearest-neighbor; a size mismatch resizes to the box, never to the
frame, so a coarse mask is never mistaken for pixel-exact truth.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _cv2():
    try:
        import cv2  # noqa: PLC0415
        import numpy  # noqa: PLC0415
    except ImportError as exc:
        raise SystemExit(
            "render needs the pinned OpenCV environment "
            "(see calibration/README.md)") from exc
    return cv2, numpy


_PALETTE = [
    (86, 180, 233), (230, 159, 0), (0, 158, 115), (240, 228, 66),
    (0, 114, 178), (213, 94, 0), (204, 121, 167),
]


def color_for(object_id: str) -> tuple[int, int, int]:
    return _PALETTE[abs(hash(object_id)) % len(_PALETTE)]


def draw_overlay(image_bgr: Any, records: list[dict[str, Any]],
                 raster_of: Any, *, alpha: float = 0.45) -> Any:
    """Draw boxes + masks + id/conf labels. ``raster_of(record)`` returns
    (width, height, row-major 0/1 bytes) or None."""
    cv2, numpy = _cv2()
    canvas = image_bgr.copy()
    frame_h, frame_w = canvas.shape[:2]
    for item in records:
        box = item.get("box") or {}
        color = color_for(str(item.get("object_id")))
        left, top = int(box.get("left", 0)), int(box.get("top", 0))
        right, bottom = int(box.get("right", 0)), int(box.get("bottom", 0))
        decoded = raster_of(item)
        if decoded is not None:
            mask_w, mask_h, raster = decoded
            small = (numpy.frombuffer(bytes(raster), dtype=numpy.uint8)
                     .reshape(mask_h, mask_w) * 255).astype(numpy.uint8)
            box_w, box_h = right - left, bottom - top
            if box_w > 0 and box_h > 0:
                if (mask_w, mask_h) != (box_w, box_h):
                    small = cv2.resize(small, (box_w, box_h),
                                       interpolation=cv2.INTER_NEAREST)
                full = numpy.zeros((frame_h, frame_w), dtype=numpy.uint8)
                x0, y0 = max(0, left), max(0, top)
                x1, y1 = min(frame_w, right), min(frame_h, bottom)
                if x1 > x0 and y1 > y0:
                    full[y0:y1, x0:x1] = small[(y0 - top):(y1 - top),
                                              (x0 - left):(x1 - left)]
                    tint = numpy.zeros_like(canvas)
                    tint[:] = color
                    where = full > 0
                    canvas[where] = (canvas[where] * (1 - alpha) +
                                     tint[where].astype(float) * alpha
                                     ).astype(numpy.uint8)
        cv2.rectangle(canvas, (left, top), (right - 1, bottom - 1), color, 2)
        conf = item.get("confidence")
        label = f"id{item.get('object_id')}" + (
            f" {conf:.2f}" if isinstance(conf, (int, float)) else " n/c")
        cv2.putText(canvas, label, (left, max(0, top - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2, cv2.LINE_AA)
    return canvas


def contact_sheet(tiles: list[tuple[Any, str]], *, columns: int = 4,
                  tile_width: int = 540) -> Any:
    """Grid of (image, caption) tiles with baked-in labels."""
    cv2, numpy = _cv2()
    if not tiles:
        raise ValueError("no tiles")
    rows: list[Any] = []
    row: list[Any] = []
    for image, caption in tiles:
        scale = tile_width / image.shape[1]
        tile = cv2.resize(image, (tile_width, int(image.shape[0] * scale)))
        bar = numpy.zeros((34, tile_width, 3), dtype=numpy.uint8)
        cv2.putText(bar, caption[:72], (8, 23), cv2.FONT_HERSHEY_SIMPLEX,
                    0.65, (255, 255, 255), 1, cv2.LINE_AA)
        row.append(numpy.vstack([bar, tile]))
        if len(row) == columns:
            rows.append(numpy.hstack(row))
            row = []
    if row:
        blank = numpy.zeros_like(row[0])
        while len(row) < columns:
            row.append(blank)
        rows.append(numpy.hstack(row))
    return numpy.vstack(rows)


def write_mp4(path: Path, frames_bgr: list[Any], fps: float) -> None:
    cv2, _numpy = _cv2()
    if not frames_bgr:
        raise ValueError("no frames")
    height, width = frames_bgr[0].shape[:2]
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                             fps, (width, height))
    if not writer.isOpened():
        raise SystemExit(f"cannot open video writer for {path}")
    for frame in frames_bgr:
        writer.write(frame)
    writer.release()
