"""Input & evidence: file intake, integrity checks, provenance, originals.

Originals are read as bytes, hashed, decoded from memory (``cv2.imdecode``,
which also handles non-ASCII Windows paths that ``cv2.imread`` rejects)
and copied byte-for-byte into the inspection's evidence folder. Nothing
ever rotates, recompresses or overwrites an original. EXIF orientation is
recorded as metadata only.
"""

from __future__ import annotations

import os
import shutil
import struct
from pathlib import Path, PurePath, PureWindowsPath

from anastylos_microscopy.util import sha256_bytes, sha256_file

MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_PIXELS = 120_000_000
MIN_SIDE_PX = 8
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff")

# Advisory, uncalibrated heuristics (documented in LIMITATIONS.md). They
# never change a native reader verdict; they only add uncertainty.
UNIFORM_STD = 2.0
LOW_FOCUS_LAPLACIAN_VAR = 20.0
HEAVY_GLARE_FRACTION = 0.25
DARK_FRACTION = 0.6
# v0.1.1: per-channel clipping. Gray-level saturation (>=250) misses strongly
# overexposed *coloured* regions (e.g. a red band clipped at R=255 has gray ~90).
CLIP_LEVEL = 250
CHANNEL_CLIPPING_FRACTION = 0.25


class IntakeError(ValueError):
    """Input could not be accepted; carries a machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def detect_format(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:2] == b"BM":
        return "bmp"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    return None


EXT_FOR = {"jpeg": ".jpg", "png": ".png", "bmp": ".bmp", "tiff": ".tif"}


def _jpeg_exif_orientation(data: bytes) -> int | None:
    """Return the EXIF Orientation tag (1..8) from a JPEG APP1 segment, if any."""
    pos = 2
    try:
        while pos + 4 <= len(data) and data[pos] == 0xFF:
            marker = data[pos + 1]
            if marker in (0xD9, 0xDA):
                return None
            length = struct.unpack(">H", data[pos + 2:pos + 4])[0]
            segment = data[pos + 4:pos + 2 + length]
            if marker == 0xE1 and segment[:6] == b"Exif\x00\x00":
                tiff = segment[6:]
                endian = "<" if tiff[:2] == b"II" else ">"
                offset = struct.unpack(endian + "I", tiff[4:8])[0]
                count = struct.unpack(endian + "H", tiff[offset:offset + 2])[0]
                for index in range(count):
                    entry = tiff[offset + 2 + 12 * index: offset + 14 + 12 * index]
                    tag = struct.unpack(endian + "H", entry[:2])[0]
                    if tag == 0x0112:
                        value = struct.unpack(endian + "H", entry[8:10])[0]
                        return value if 1 <= value <= 8 else None
                return None
            pos += 2 + length
    except (struct.error, IndexError):
        return None
    return None


def _integrity_checks(data: bytes, fmt: str) -> list[dict]:
    checks = [{"check": "non_empty", "status": "pass"},
              {"check": "format_signature", "status": "pass", "detail": fmt}]
    if fmt == "jpeg":
        eoi = data.rstrip(b"\x00").endswith(b"\xff\xd9")
        checks.append({"check": "jpeg_eoi_marker", "status": "pass" if eoi else "fail",
                       "detail": None if eoi else "missing JPEG EOI marker (truncated file?)"})
    if fmt == "png":
        iend = data.endswith(b"IEND\xaeB`\x82")
        checks.append({"check": "png_iend_chunk", "status": "pass" if iend else "fail",
                       "detail": None if iend else "missing PNG IEND chunk (truncated file?)"})
    return checks


def display_name(path_text: str) -> str:
    """File name for either a POSIX or a Windows style path string."""
    if "\\" in path_text or (len(path_text) > 1 and path_text[1] == ":"):
        return PureWindowsPath(path_text).name
    return PurePath(path_text).name


def quality_indicators(image, roi=None) -> dict:
    import cv2
    import numpy as np

    x, y, w, h = roi or (0, 0, image.shape[1], image.shape[0])
    crop = image[y:y + h, x:x + w]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    lap = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    std = float(np.std(gray))
    glare = float(np.mean(gray >= 250))
    dark = float(np.mean(gray <= 5))
    peak = crop.max(axis=2) if crop.ndim == 3 else crop
    clipped = float(np.mean(peak >= CLIP_LEVEL))
    flags = []
    if std < UNIFORM_STD:
        flags.append({"flag": "uniform_image", "blocking": True,
                      "detail": f"gray std {std:.2f} < {UNIFORM_STD}: no visible structure"})
    elif lap < LOW_FOCUS_LAPLACIAN_VAR:
        flags.append({"flag": "low_focus_proxy", "blocking": True,
                      "detail": f"Laplacian variance {lap:.1f} < {LOW_FOCUS_LAPLACIAN_VAR}"})
    if glare > HEAVY_GLARE_FRACTION:
        flags.append({"flag": "heavy_glare", "blocking": True,
                      "detail": f"{glare:.1%} of pixels saturated (>=250)"})
    if clipped > CHANNEL_CLIPPING_FRACTION:
        flags.append({"flag": "channel_clipping", "blocking": False,
                      "detail": f"{clipped:.1%} of pixels have a colour channel >= {CLIP_LEVEL}: colour "
                                "evidence there is unreliable (advisory; colour-band readers are "
                                "additionally checked by the Core assessment)"})
    if dark > DARK_FRACTION:
        flags.append({"flag": "mostly_dark", "blocking": False,
                      "detail": f"{dark:.1%} of pixels <=5"})
    return {
        "region": "roi" if roi else "full_frame",
        "laplacian_variance": round(lap, 3),
        "gray_std": round(std, 3),
        "glare_fraction": round(glare, 5),
        "dark_fraction": round(dark, 5),
        "channel_clipped_fraction": round(clipped, 5),
        "flags": flags,
        "note": "advisory, uncalibrated heuristics; they never change a native reader verdict",
    }


def intake(path: str | os.PathLike, data: bytes | None = None) -> tuple[dict, object]:
    """Read and validate one image. Returns (source_record, BGR uint8 image).

    With ``data`` (v0.1.1) the caller has already read the file exactly once
    (e.g. a confined capture-manifest read); the path is then only a label and
    is never opened again, so the hashed, decoded and preserved bytes are the
    same bytes. Raises IntakeError for anything that must not reach a reader.
    """
    import cv2
    import numpy as np

    path_text = str(path)
    if data is None:
        file_path = Path(path)
        if not file_path.is_file():
            raise IntakeError("file_not_found", f"image not found: {path_text}")
        size = file_path.stat().st_size
        if size == 0:
            raise IntakeError("empty_file", f"image file is empty: {path_text}")
        if size > MAX_FILE_BYTES:
            raise IntakeError("file_too_large", f"image exceeds {MAX_FILE_BYTES} bytes")
        data = file_path.read_bytes()
    size = len(data)
    if size == 0:
        raise IntakeError("empty_file", f"image file is empty: {path_text}")
    if size > MAX_FILE_BYTES:
        raise IntakeError("file_too_large", f"image exceeds {MAX_FILE_BYTES} bytes")
    sha = sha256_bytes(data)
    fmt = detect_format(data)
    record = {"original_path": path_text, "file_name": display_name(path_text),
              "size_bytes": size, "sha256": sha, "format": fmt}
    if fmt is None:
        raise IntakeError("unsupported_format",
                          "unsupported or unrecognized image signature (JPEG/PNG/BMP/TIFF accepted)")
    checks = _integrity_checks(data, fmt)
    record["integrity"] = {"checks": checks,
                           "status": "fail" if any(c["status"] == "fail" for c in checks) else "pass"}
    if record["integrity"]["status"] == "fail":
        failed = [c["detail"] for c in checks if c["status"] == "fail"]
        raise IntakeError("integrity_failed", "; ".join(failed))
    buffer = np.frombuffer(data, np.uint8)
    unchanged = cv2.imdecode(buffer, cv2.IMREAD_UNCHANGED)
    if unchanged is None:
        raise IntakeError("decode_failed", "image bytes could not be decoded")
    height, width = unchanged.shape[:2]
    if width * height > MAX_PIXELS:
        raise IntakeError("too_many_pixels", f"{width}x{height} exceeds {MAX_PIXELS} pixels")
    if min(width, height) < MIN_SIDE_PX:
        raise IntakeError("too_small", f"{width}x{height} is below {MIN_SIDE_PX} px per side")
    channels = 1 if unchanged.ndim == 2 else unchanged.shape[2]
    # IMREAD_COLOR applies EXIF rotation for JPEGs; the readers must see the
    # stored pixel grid, so decode with IGNORE_ORIENTATION.
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
    if image is None:
        raise IntakeError("decode_failed", "image bytes could not be decoded as color")
    record.update({
        "resolution": {"width": int(width), "height": int(height)},
        "channels": int(channels),
        "dtype": str(unchanged.dtype),
        "reader_input": {"color": "BGR", "dtype": "uint8",
                         "conversion": None if (channels == 3 and str(unchanged.dtype) == "uint8")
                         else f"decoded {channels}ch {unchanged.dtype} -> BGR uint8 for readers"},
        "exif_orientation": _jpeg_exif_orientation(data) if fmt == "jpeg" else None,
    })
    return record, image


def preserve_original(source_path: str | os.PathLike, sha256: str, fmt: str,
                      evidence_dir: Path, data: bytes | None = None) -> dict:
    """Copy the original byte-for-byte (write-once, verified).

    With ``data`` the already-read bytes are written; the source is not reopened.
    """
    evidence_dir.mkdir(parents=True, exist_ok=True)
    target = evidence_dir / f"{sha256}{EXT_FOR.get(fmt, '.bin')}"
    if target.exists():
        if sha256_file(target) != sha256:
            raise IntakeError("evidence_conflict",
                              f"existing evidence file {target.name} does not match its hash")
    else:
        tmp = target.with_name("." + target.name + ".tmp")
        if data is not None:
            tmp.write_bytes(data)
        else:
            shutil.copyfile(source_path, tmp)
        if sha256_file(tmp) != sha256:
            tmp.unlink(missing_ok=True)
            raise IntakeError("copy_mismatch", "original copy does not match source hash")
        os.replace(tmp, target)
    return {"path": target.name, "sha256": sha256, "role": "original_copy"}


def validate_roi(roi, width: int, height: int) -> list[int] | None:
    if roi is None:
        return None
    if isinstance(roi, str):
        try:
            roi = [int(v) for v in roi.split(",")]
        except ValueError as exc:
            raise IntakeError("roi_invalid", f"ROI must be x,y,w,h integers, got {roi!r}") from exc
    if not isinstance(roi, (list, tuple)) or len(roi) != 4 or \
            not all(isinstance(v, int) and not isinstance(v, bool) for v in roi):
        raise IntakeError("roi_invalid", f"ROI must be four integers x,y,w,h, got {roi!r}")
    x, y, w, h = roi
    if not (0 <= x < width and 0 <= y < height and w > 0 and h > 0
            and x + w <= width and y + h <= height):
        raise IntakeError("roi_out_of_bounds", f"ROI {list(roi)} is outside {width}x{height}")
    return [x, y, w, h]


def list_images(directory: Path) -> list[Path]:
    return sorted((p for p in directory.iterdir()
                   if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS),
                  key=lambda p: p.name)
