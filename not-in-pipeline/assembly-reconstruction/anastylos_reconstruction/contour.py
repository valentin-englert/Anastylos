"""Stdlib-only silhouette extraction from change-detection mask PNGs.

Reads an 8-bit grayscale or palette PNG, crops to a cited bbox_px, and
traces the external boundary of nonzero pixels. Never invents geometry:
no signal in the ROI yields an explicit failure, never a guessed shape.
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from pathlib import Path


class ContourError(ValueError):
    """Mask contour could not be derived honestly."""


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def read_grayscale_png(path: Path) -> tuple[int, int, list[list[int]]]:
    """Decode an 8-bit grayscale or palette PNG to a row-major gray grid."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ContourError(f"not a PNG: {path}")
    offset = 8
    width = height = bit_depth = color_type = None
    idat = bytearray()
    palette: list[tuple[int, int, int]] | None = None
    while offset + 8 <= len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        ctype = data[offset + 4:offset + 8]
        chunk = data[offset + 8:offset + 8 + length]
        offset += 12 + length
        if ctype == b"IHDR":
            width, height, bit_depth, color_type, *_ = struct.unpack(
                ">IIBBBBB", chunk)
        elif ctype == b"PLTE":
            palette = [
                (chunk[i], chunk[i + 1], chunk[i + 2])
                for i in range(0, len(chunk), 3)
            ]
        elif ctype == b"IDAT":
            idat.extend(chunk)
        elif ctype == b"IEND":
            break
    if width is None or height is None or bit_depth is None or color_type is None:
        raise ContourError(f"PNG missing IHDR: {path}")
    if bit_depth != 8 or color_type not in (0, 3):
        raise ContourError(
            f"unsupported PNG form bit_depth={bit_depth} color_type={color_type} "
            f"in {path} (need 8-bit gray or palette)")
    raw = zlib.decompress(bytes(idat))
    stride = width + 1
    if len(raw) < stride * height:
        raise ContourError(f"PNG IDAT too short for {path}")
    rows: list[list[int]] = []
    prev = bytearray(width)
    for row_i in range(height):
        start = row_i * stride
        filt = raw[start]
        scan = bytearray(raw[start + 1:start + 1 + width])
        recon = bytearray(width)
        for i, value in enumerate(scan):
            left = recon[i - 1] if i else 0
            up = prev[i]
            up_left = prev[i - 1] if i else 0
            if filt == 0:
                recon[i] = value
            elif filt == 1:
                recon[i] = (value + left) & 255
            elif filt == 2:
                recon[i] = (value + up) & 255
            elif filt == 3:
                recon[i] = (value + ((left + up) // 2)) & 255
            elif filt == 4:
                recon[i] = (value + _paeth(left, up, up_left)) & 255
            else:
                raise ContourError(f"unsupported PNG filter {filt} in {path}")
        if color_type == 3:
            if palette is None:
                raise ContourError(f"palette PNG without PLTE: {path}")
            gray = []
            for index in recon:
                r, g, b = palette[index]
                gray.append((r + g + b) // 3)
            rows.append(gray)
        else:
            rows.append(list(recon))
        prev = recon
    return width, height, rows


def _on(grid: list[list[int]], x: int, y: int, w: int, h: int) -> bool:
    return 0 <= x < w and 0 <= y < h and grid[y][x] > 0


def _trace_boundary(grid: list[list[int]]) -> list[tuple[int, int]]:
    """Moore-neighborhood exterior trace. Coordinates are crop-local px."""
    height = len(grid)
    width = len(grid[0]) if height else 0
    start = None
    for y in range(height):
        for x in range(width):
            if grid[y][x] > 0:
                start = (x, y)
                break
        if start is not None:
            break
    if start is None:
        raise ContourError("ROI contains no nonzero mask pixels")
    # 8-connected neighbors clockwise starting east
    deltas = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]
    contour: list[tuple[int, int]] = []
    x, y = start
    back = 4  # came from west of the first pixel
    guard = width * height * 8 + 8
    for _ in range(guard):
        contour.append((x, y))
        found = False
        for step in range(8):
            direction = (back + step) % 8
            dx, dy = deltas[direction]
            nx, ny = x + dx, y + dy
            if _on(grid, nx, ny, width, height):
                x, y = nx, ny
                back = (direction + 6) % 8  # turn left relative to entry
                found = True
                break
        if not found:
            break
        if (x, y) == start and len(contour) > 2:
            break
    if len(contour) < 3:
        raise ContourError("mask boundary too short for a closed contour")
    return contour


def _simplify(points: list[tuple[int, int]], max_points: int) -> list[tuple[int, int]]:
    if len(points) <= max_points:
        return points
    step = max(1, len(points) // max_points)
    sampled = points[::step]
    if sampled[-1] != points[-1]:
        sampled.append(points[-1])
    if sampled[0] != sampled[-1]:
        sampled.append(sampled[0])
    return sampled[: max_points + 1]


def extract_mask_contour(
    mask_path: str | Path,
    bbox_px: list[int] | tuple[int, int, int, int],
    *,
    max_points: int = 64,
) -> dict:
    """Return a provenance-bearing contour dict for one bbox on one mask PNG."""
    path = Path(mask_path)
    if not path.is_file():
        raise ContourError(f"mask file missing: {path}")
    if (not isinstance(bbox_px, (list, tuple)) or len(bbox_px) != 4
            or any(not isinstance(v, (int, float)) for v in bbox_px)):
        raise ContourError(f"bbox_px must be [x, y, w, h], got {bbox_px!r}")
    x0, y0, bw, bh = (int(bbox_px[0]), int(bbox_px[1]),
                      int(bbox_px[2]), int(bbox_px[3]))
    if bw <= 0 or bh <= 0:
        raise ContourError(f"degenerate bbox_px {bbox_px}")
    width, height, full = read_grayscale_png(path)
    if x0 < 0 or y0 < 0 or x0 + bw > width or y0 + bh > height:
        raise ContourError(
            f"bbox_px {bbox_px} outside mask {width}x{height} for {path}")
    crop = [row[x0:x0 + bw] for row in full[y0:y0 + bh]]
    local = _trace_boundary(crop)
    simplified = _simplify(local, max_points)
    points = [[x0 + px, y0 + py] for px, py in simplified]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "kind": "mask_contour",
        "method": "change-detection-mask-boundary-v1",
        "parameters": {
            "max_points": max_points,
            "nonzero_threshold": 1,
            "connectivity": 8,
        },
        "source_image": str(path),
        "source_sha256": digest,
        "roi_px": [x0, y0, bw, bh],
        "coordinate_space": "rectified_image_px",
        "image_size_px": [width, height],
        "points_px": points,
        "point_count": len(points),
        "note": "external boundary of nonzero change-detection mask pixels "
                "inside the cited region bbox; layout-scaled later, not a "
                "measured assembly pose",
    }
