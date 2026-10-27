#!/usr/bin/env python3
"""Minimal QR Code encoder. Standard library only.

Byte mode, error-correction level M (15%), symbol versions 1-13. That is
all the pairing QR needs: the payload is short ASCII JSON, and level M
is the sane choice for codes photographed off a screen.

The spec constants (block structures, alignment centers, BCH
polynomials) were transcribed against the pure-Python `qrcode` 8.2
package as a reading reference; the implementation below is original.
Correctness is proven two ways: self-validating math (Reed-Solomon
syndromes, BCH re-decode, internal placement asserts) and independent
decodes with OpenCV's QR detector (`tests/test_qr.py`).
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

BYTE_CAPACITY_M = {
    1: 14, 2: 26, 3: 42, 4: 62, 5: 84, 6: 106, 7: 122, 8: 152,
    9: 180, 10: 213, 11: 251, 12: 287, 13: 331,
}

# Version -> groups of (block count, total codewords, data codewords).
# EC codewords per block = total - data (same within a version).
_BLOCKS_M = {
    1: ((1, 26, 16),),
    2: ((1, 44, 28),),
    3: ((1, 70, 44),),
    4: ((2, 50, 32),),
    5: ((2, 67, 43),),
    6: ((4, 43, 27),),
    7: ((4, 49, 31),),
    8: ((2, 60, 38), (2, 61, 39)),
    9: ((3, 58, 36), (2, 59, 37)),
    10: ((4, 69, 43), (1, 70, 44)),
    11: ((1, 80, 50), (4, 81, 51)),
    12: ((6, 58, 36), (2, 59, 37)),
    13: ((8, 59, 37), (1, 60, 38)),
}

_ALIGN_CENTERS = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30],
    6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46],
    10: [6, 28, 50], 11: [6, 30, 54], 12: [6, 32, 58], 13: [6, 34, 62],
}

_REMAINDER_BITS = {
    1: 0, 2: 7, 3: 7, 4: 7, 5: 7, 6: 7, 7: 0, 8: 0, 9: 0,
    10: 0, 11: 0, 12: 0, 13: 0,
}

_G15 = 0b10100110111
_G15_MASK = 0b101010000010010
_G18 = 0b1111100100101

_MODE_BYTE = 0b0100
_EC_BITS_M = 0b00
_PAD = (0xEC, 0x11)


class QREncodeError(ValueError):
    """The input cannot be encoded with this encoder's scope."""


# ---------------------------------------------------------------------------
# GF(256) + Reed-Solomon
# ---------------------------------------------------------------------------


def _build_gf_tables() -> tuple[list[int], list[int]]:
    exp = [0] * 512
    log = [0] * 256
    value = 1
    for i in range(512):
        exp[i] = value
        if i < 255:
            log[value] = i
        value <<= 1
        if value & 0x100:
            value ^= 0x11D
    return exp, log


_GF_EXP, _GF_LOG = _build_gf_tables()


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]


def _rs_generator(nsym: int) -> list[int]:
    """Generator polynomial of degree `nsym`, coefficients high-first."""
    poly = [1]
    for i in range(nsym):
        factor = [1, _GF_EXP[i]]
        out = [0] * (len(poly) + 1)
        for j, coef in enumerate(poly):
            out[j] ^= coef
            out[j + 1] ^= _gf_mul(coef, factor[1])
        poly = out
    return poly


def rs_remainder(data: bytes, nsym: int) -> bytes:
    """Reed-Solomon parity: remainder of data(x)*x^nsym / generator(x)."""
    if not 1 <= nsym <= 64:
        raise QREncodeError(f"bad RS parity length: {nsym}")
    generator = _rs_generator(nsym)
    work = bytearray(data) + bytes(nsym)
    for i in range(len(data)):
        coef = work[i]
        if coef == 0:
            continue
        for j in range(len(generator)):
            work[i + j] ^= _gf_mul(generator[j], coef)
    return bytes(work[len(data):])


# ---------------------------------------------------------------------------
# Data codewords
# ---------------------------------------------------------------------------


def _data_codeword_count(version: int) -> int:
    return sum(count * data for count, _, data in _BLOCKS_M[version])


def select_version(length: int) -> int:
    """Smallest version whose byte-mode capacity fits `length` bytes."""
    for version in range(1, 14):
        if length <= BYTE_CAPACITY_M[version]:
            return version
    raise QREncodeError(
        f"{length} bytes exceed the V13-M byte-mode capacity "
        f"({BYTE_CAPACITY_M[13]}); this encoder stops at V13."
    )


def _data_codewords(data: bytes, version: int) -> bytes:
    count_bits = 8 if version < 10 else 16
    capacity_bits = _data_codeword_count(version) * 8
    bits: list[int] = []
    for shift in (3, 2, 1, 0):
        bits.append((_MODE_BYTE >> shift) & 1)
    for shift in range(count_bits - 1, -1, -1):
        bits.append((len(data) >> shift) & 1)
    for byte in data:
        for shift in range(7, -1, -1):
            bits.append((byte >> shift) & 1)
    if len(bits) > capacity_bits:
        raise QREncodeError("payload overflows the selected version")
    bits += [0] * min(4, capacity_bits - len(bits))
    while len(bits) % 8:
        bits.append(0)
    out = bytearray()
    for i in range(0, len(bits), 8):
        value = 0
        for bit in bits[i:i + 8]:
            value = (value << 1) | bit
        out.append(value)
    pad_index = 0
    while len(out) < capacity_bits // 8:
        out.append(_PAD[pad_index % 2])
        pad_index += 1
    return bytes(out)


def _interleave(data: bytes, version: int) -> bytes:
    blocks: list[bytes] = []
    offset = 0
    parity_len = 0
    for count, total, data_count in _BLOCKS_M[version]:
        parity_len = total - data_count
        for _ in range(count):
            blocks.append(data[offset:offset + data_count])
            offset += data_count
    assert offset == len(data), "block sizes disagree with the data length"
    parities = [rs_remainder(block, parity_len) for block in blocks]
    out = bytearray()
    for i in range(max(len(block) for block in blocks)):
        for block in blocks:
            if i < len(block):
                out.append(block[i])
    for i in range(parity_len):
        for parity in parities:
            out.append(parity[i])
    return bytes(out)


# ---------------------------------------------------------------------------
# Matrix construction
# ---------------------------------------------------------------------------

_UNSET = -1


def _empty_matrix(size: int) -> list[list[int]]:
    return [[_UNSET] * size for _ in range(size)]


def _finder(matrix: list[list[int]], top: int, left: int) -> None:
    for r in range(7):
        for c in range(7):
            edge = r in (0, 6) or c in (0, 6)
            core = 2 <= r <= 4 and 2 <= c <= 4
            matrix[top + r][left + c] = 1 if (edge or core) else 0


def _function_patterns(version: int) -> tuple[list[list[int]], list[list[int]]]:
    size = 17 + 4 * version
    matrix = _empty_matrix(size)
    fixed = [[0] * size for _ in range(size)]

    def set_fixed(r: int, c: int, value: int) -> None:
        matrix[r][c] = value
        fixed[r][c] = 1

    corners = [(0, 0), (0, size - 7), (size - 7, 0)]
    for top, left in corners:
        _finder(matrix, top, left)
        for r in range(-1, 8):
            for c in range(-1, 8):
                rr, cc = top + r, left + c
                if 0 <= rr < size and 0 <= cc < size:
                    if matrix[rr][cc] == _UNSET:
                        matrix[rr][cc] = 0
                    fixed[rr][cc] = 1
    # Alignment before timing: patterns punch through the timing row/column
    # (only finder overlaps are skipped), and timing yields to them.
    for row in _ALIGN_CENTERS[version]:
        for col in _ALIGN_CENTERS[version]:
            if matrix[row][col] != _UNSET:
                continue
            for r in range(-2, 3):
                for c in range(-2, 3):
                    edge = r in (-2, 2) or c in (-2, 2)
                    set_fixed(row + r, col + c, 1 if (edge or (r == 0 and c == 0)) else 0)
    for i in range(8, size - 8):
        if matrix[6][i] == _UNSET:
            set_fixed(6, i, 1 if i % 2 == 0 else 0)
        if matrix[i][6] == _UNSET:
            set_fixed(i, 6, 1 if i % 2 == 0 else 0)
    set_fixed(size - 8, 8, 1)  # Dark module.
    return matrix, fixed


def _reserve_info_areas(fixed: list[list[int]], version: int) -> None:
    size = len(fixed)
    # (8,8) carries vertical format bit 7; timing row/column 6 is fixed already.
    for i in (0, 1, 2, 3, 4, 5, 7, 8):
        fixed[i][8] = 1
        fixed[8][i] = 1
    for i in range(8):
        fixed[size - 1 - i][8] = 1
        fixed[8][size - 1 - i] = 1
    if version >= 7:
        for r in range(6):
            for c in range(3):
                fixed[r][size - 11 + c] = 1
                fixed[size - 11 + c][r] = 1


def _place_bits(matrix: list[list[int]], fixed: list[list[int]], codewords: bytes) -> int:
    size = len(matrix)
    bits = [(byte >> shift) & 1 for byte in codewords for shift in range(7, -1, -1)]
    index = 0
    upward = True
    col = size - 1
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for c in (col, col - 1):
                if fixed[row][c]:
                    continue
                if index < len(bits):
                    matrix[row][c] = bits[index]
                    index += 1
        upward = not upward
        col -= 2
    remainder = 0
    for r in range(size):
        for c in range(size):
            if not fixed[r][c] and matrix[r][c] == _UNSET:
                matrix[r][c] = 0
                remainder += 1
    if index != len(bits):
        raise QREncodeError("data did not fit the symbol's data modules")
    return remainder


def _mask_applies(pattern: int, row: int, col: int) -> bool:
    if pattern == 0:
        return (row + col) % 2 == 0
    if pattern == 1:
        return row % 2 == 0
    if pattern == 2:
        return col % 3 == 0
    if pattern == 3:
        return (row + col) % 3 == 0
    if pattern == 4:
        return (row // 2 + col // 3) % 2 == 0
    if pattern == 5:
        return (row * col) % 2 + (row * col) % 3 == 0
    if pattern == 6:
        return ((row * col) % 2 + (row * col) % 3) % 2 == 0
    if pattern == 7:
        return ((row * col) % 3 + (row + col) % 2) % 2 == 0
    raise QREncodeError(f"bad mask pattern: {pattern}")  # Unreachable by loop range.


def _masked_copy(
    matrix: list[list[int]], fixed: list[list[int]], pattern: int
) -> list[list[int]]:
    size = len(matrix)
    out = [list(row) for row in matrix]
    for r in range(size):
        for c in range(size):
            if not fixed[r][c] and _mask_applies(pattern, r, c):
                out[r][c] ^= 1
    return out


def _bch_digits(value: int) -> int:
    return value.bit_length()


def format_bits(error_bits: int, mask: int) -> int:
    """15-bit format string (BCH + mask), for tests and placement."""
    data = (error_bits << 3) | mask
    shifted = data << 10
    while _bch_digits(shifted) - _bch_digits(_G15) >= 0:
        shifted ^= _G15 << (_bch_digits(shifted) - _bch_digits(_G15))
    return ((data << 10) | shifted) ^ _G15_MASK


def version_bits(version: int) -> int:
    """18-bit version string (BCH), for tests and placement."""
    shifted = version << 12
    while _bch_digits(shifted) - _bch_digits(_G18) >= 0:
        shifted ^= _G18 << (_bch_digits(shifted) - _bch_digits(_G18))
    return (version << 12) | shifted


def _write_format(matrix: list[list[int]], mask: int) -> None:
    size = len(matrix)
    bits = format_bits(_EC_BITS_M, mask)
    for i in range(15):
        bit = (bits >> i) & 1
        if i < 6:
            matrix[i][8] = bit
        elif i < 8:
            matrix[i + 1][8] = bit
        else:
            matrix[size - 15 + i][8] = bit
    for i in range(15):
        bit = (bits >> i) & 1
        if i < 8:
            matrix[8][size - 1 - i] = bit
        elif i == 8:
            matrix[8][7] = bit
        else:
            matrix[8][14 - i] = bit


def _write_version(matrix: list[list[int]], version: int) -> None:
    if version < 7:
        return
    size = len(matrix)
    bits = version_bits(version)
    for i in range(18):
        bit = (bits >> i) & 1
        matrix[i // 3][i % 3 + size - 11] = bit
        matrix[i % 3 + size - 11][i // 3] = bit


def _penalty(matrix: list[list[int]]) -> int:
    size = len(matrix)
    score = 0
    for r in range(size):
        run_color = matrix[r][0]
        run = 1
        for c in range(1, size + 1):
            color = matrix[r][c] if c < size else -1
            if color == run_color:
                run += 1
            else:
                if run >= 5:
                    score += run - 2
                run_color, run = color, 1
    for c in range(size):
        run_color = matrix[0][c]
        run = 1
        for r in range(1, size + 1):
            color = matrix[r][c] if r < size else -1
            if color == run_color:
                run += 1
            else:
                if run >= 5:
                    score += run - 2
                run_color, run = color, 1
    for r in range(size - 1):
        for c in range(size - 1):
            color = matrix[r][c]
            if matrix[r][c + 1] == color and matrix[r + 1][c] == color \
                    and matrix[r + 1][c + 1] == color:
                score += 3
    finder_like = ([1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0], [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1])
    for r in range(size):
        for c in range(size - 10):
            window = [matrix[r][c + i] for i in range(11)]
            if window in finder_like:
                score += 40
    for c in range(size):
        for r in range(size - 10):
            window = [matrix[r + i][c] for i in range(11)]
            if window in finder_like:
                score += 40
    dark = sum(sum(row) for row in matrix)
    percent = dark * 100 // (size * size)
    score += abs(percent - 50) // 5 * 10
    return score


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QRCode:
    version: int
    size: int
    mask: int
    modules: tuple[bytes, ...]  # 1 = dark, 0 = light, no quiet zone.


def encode_qr(data: bytes) -> QRCode:
    """Encode bytes into a QR symbol (byte mode, EC level M)."""
    if not isinstance(data, bytes):
        raise QREncodeError("encode_qr takes bytes; encode text first")
    if not data:
        raise QREncodeError("refusing to encode an empty payload")
    version = select_version(len(data))
    codewords = _interleave(_data_codewords(data, version), version)
    matrix, fixed = _function_patterns(version)
    _reserve_info_areas(fixed, version)
    remainder = _place_bits(matrix, fixed, codewords)
    expected = _REMAINDER_BITS[version]
    if remainder != expected:
        raise QREncodeError(
            f"V{version} placement left {remainder} spare modules, expected {expected}"
        )
    best: list[list[int]] | None = None
    best_mask = 0
    best_score = -1
    for mask in range(8):
        candidate = _masked_copy(matrix, fixed, mask)
        _write_format(candidate, mask)
        _write_version(candidate, version)
        score = _penalty(candidate)
        if best is None or score < best_score:
            best, best_mask, best_score = candidate, mask, score
    assert best is not None
    return QRCode(version, len(best), best_mask, tuple(bytes(row) for row in best))


def qr_ascii(code: QRCode, border: int = 2) -> str:
    """One character per module (`#`/space); fits an 80-column terminal."""
    if border < 0:
        raise QREncodeError("border must be non-negative")
    width = code.size + 2 * border
    lines = [" " * width]
    pad = " " * border
    for row in code.modules:
        lines.append(pad + "".join("#" if cell else " " for cell in row) + pad)
    lines.append(" " * width)
    return "\n".join(lines)


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(
        ">I", zlib.crc32(kind + payload) & 0xFFFFFFFF
    )


def qr_png_bytes(code: QRCode, scale: int = 10, border: int = 4) -> bytes:
    """Render the symbol (plus quiet zone) as grayscale PNG bytes."""
    if scale < 1 or border < 0:
        raise QREncodeError("scale must be >= 1 and border >= 0")
    width = code.size + 2 * border
    pixels = bytearray()
    for _ in range(border):
        pixels += bytes([255]) * width
    for row in code.modules:
        line = bytes([255]) * border + bytes(0 if cell else 255 for cell in row) \
            + bytes([255]) * border
        pixels += line
    for _ in range(border):
        pixels += bytes([255]) * width
    side = width * scale
    raw = bytearray()
    for r in range(width):
        raw.append(0)
        for value in pixels[r * width:(r + 1) * width]:
            raw += bytes([value]) * scale
        row_bytes = bytes(raw[-width * scale:])
        for _ in range(scale - 1):
            raw.append(0)
            raw += row_bytes
    ihdr = struct.pack(">IIBBBBB", side, side, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _png_chunk(b"IEND", b"")
    )
