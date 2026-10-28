"""Minimal 8-bit grayscale PNG encode/decode. Stdlib only.

Keeps mask-raster persistence independent of OpenCV so that evidence
writing and verification never require the pinned vision environment.
"""

from __future__ import annotations

import struct
import zlib

_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + tag + payload + struct.pack(
        ">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)


def encode_gray8(width: int, height: int, raster: bytes) -> bytes:
    """Encode row-major 0/1 bytes to a gray-8 PNG (0/255)."""
    if width <= 0 or height <= 0:
        raise ValueError("dimensions must be positive")
    if len(raster) != width * height:
        raise ValueError(
            f"raster has {len(raster)} bytes, want {width * height}")
    rows = b"".join(
        b"\x00" + bytes(255 if value else 0 for value in
                        raster[row * width:(row + 1) * width])
        for row in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (_SIGNATURE + _chunk(b"IHDR", ihdr) +
            _chunk(b"IDAT", zlib.compress(rows, 9)) + _chunk(b"IEND", b""))


def decode_gray8(data: bytes) -> tuple[int, int, bytes]:
    """Decode a gray-8 PNG (no interlace) to row-major 0/1 bytes."""
    if not data.startswith(_SIGNATURE):
        raise ValueError("not a PNG")
    pos = len(_SIGNATURE)
    width = height = 0
    bit_depth = color = 0
    idat = b""
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            width, height, bit_depth, color, _, _, _ = struct.unpack(
                ">IIBBBBB", payload)
        elif tag == b"IDAT":
            idat += payload
        elif tag == b"IEND":
            break
        pos += 12 + length
    if bit_depth != 8 or color != 0:
        raise ValueError("only gray-8 PNG is supported")
    raw = zlib.decompress(idat)
    stride = width + 1
    if len(raw) != stride * height:
        raise ValueError("PNG raster size mismatch")
    out = bytearray()
    prev = bytearray(width)
    for row in range(height):
        line = raw[row * stride:(row + 1) * stride]
        filt, scan = line[0], bytearray(line[1:])
        if filt == 1:
            for i in range(1, width):
                scan[i] = (scan[i] + scan[i - 1]) & 0xFF
        elif filt == 2:
            for i in range(width):
                scan[i] = (scan[i] + prev[i]) & 0xFF
        elif filt not in (0,):
            raise ValueError(f"unsupported PNG filter {filt}")
        out.extend(1 if value >= 128 else 0 for value in scan)
        prev = scan
    return width, height, bytes(out)
