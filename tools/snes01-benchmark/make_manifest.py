#!/usr/bin/env python3
"""Build source_manifest.json for the SNES-01 benchmark.

Stdlib-only. Hashes the original video and every extracted frame,
records ffprobe metadata (via a system ffprobe), parses JPEG
dimensions without image libraries, and records the declared
frame<->timestamp mapping plus its visual-verification checkpoints.

Never modifies the inputs. Writes exactly one JSON file.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import struct
import subprocess
import sys

SCHEMA = "snes01-benchmark/manifest@1"
CHUNK = 1 << 20


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(CHUNK)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)


def jpeg_dimensions(path: str) -> tuple[int, int]:
    """Return (width, height) from JPEG SOF0/SOF1/SOF2. No dependencies."""
    with open(path, "rb") as handle:
        data = handle.read()
    if data[0:2] != b"\xff\xd8":
        raise ValueError(f"not a JPEG (missing SOI): {path}")
    pos = 2
    size = len(data)
    while pos + 4 <= size:
        if data[pos] != 0xFF:
            raise ValueError(f"bad marker at offset {pos}: {path}")
        marker = data[pos + 1]
        pos += 2
        if marker in (0xD8, 0xD9):  # SOI / EOI
            continue
        if 0xD0 <= marker <= 0xD7:  # RSTn: no length field
            continue
        if marker == 0x01:  # TEM: no length field
            continue
        (length,) = struct.unpack(">H", data[pos : pos + 2])
        if length < 2:
            raise ValueError(f"bad segment length at offset {pos}: {path}")
        if marker in (0xC0, 0xC1, 0xC2):  # SOF0/1/2
            height, width = struct.unpack(">HH", data[pos + 3 : pos + 7])
            return (width, height)
        if marker == 0xDA:  # SOS: entropy data follows; no SOF after this
            raise ValueError(f"reached SOS without SOF: {path}")
        pos += length
    raise ValueError(f"no SOF marker found: {path}")


def run_ffprobe(ffprobe: str, video: str) -> dict:
    proc = subprocess.run(
        [
            ffprobe,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            video,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def tool_version(executable: str, args: list[str]) -> str:
    try:
        proc = subprocess.run(
            [executable, *args], capture_output=True, text=True, check=False
        )
    except OSError as exc:
        return f"unavailable ({exc})"
    if proc.returncode != 0:
        return f"unavailable (exit {proc.returncode})"
    return (proc.stdout + proc.stderr).splitlines()[0].strip() if (
        proc.stdout + proc.stderr
    ).splitlines() else "unknown"


def parse_mapping_checks(raw: str) -> list[dict[str, str]]:
    checks = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item or ":" not in item:
            raise ValueError(f"bad mapping check (want t=<sec>:frame_NNNN): {item!r}")
        time_part, frame = item.split(":", 1)
        seconds = time_part[2:] if time_part.startswith("t=") else time_part
        checks.append({"video_time_s": seconds, "frame": frame})
    return checks


def build_manifest(args: argparse.Namespace) -> dict:
    video_size = os.path.getsize(args.video)
    video_sha256 = sha256_file(args.video)
    probe = run_ffprobe(args.ffprobe, args.video)

    video_stream = next(
        (s for s in probe.get("streams", []) if s.get("codec_type") == "video"),
        {},
    )
    rotation = None
    for side in video_stream.get("side_data_list", []) or []:
        if side.get("side_data_type") == "Display Matrix" and "rotation" in side:
            rotation = side["rotation"]

    names = sorted(
        name
        for name in os.listdir(args.frames_dir)
        if name.lower().endswith(".jpg")
    )
    if not names:
        raise RuntimeError(f"no JPEG frames in {args.frames_dir}")
    frame_files = []
    dims: set[tuple[int, int]] = set()
    for name in names:
        path = os.path.join(args.frames_dir, name)
        width, height = jpeg_dimensions(path)
        dims.add((width, height))
        frame_files.append(
            {
                "name": name,
                "bytes": os.path.getsize(path),
                "sha256": sha256_file(path),
                "width": width,
                "height": height,
            }
        )

    siblings = []
    for extra in args.sibling or []:
        siblings.append(
            {
                "name": os.path.basename(extra),
                "bytes": os.path.getsize(extra),
                "sha256": sha256_file(extra),
                "note": (
                    "pre-existing sibling artifact; provenance unknown; "
                    "not used as benchmark evidence"
                ),
            }
        )

    return {
        "schema": SCHEMA,
        "benchmark": "snes-01",
        "generated_at_utc": (
            datetime.datetime.now(datetime.timezone.utc).isoformat()
        ),
        "generator": "tools/snes01-benchmark/make_manifest.py",
        "tool_versions": {
            "python": sys.version.split()[0],
            "ffmpeg": tool_version(
                args.ffmpeg, ["-hide_banner", "-version"]
            ),
            "ffprobe": tool_version(args.ffprobe, ["-version"]),
        },
        "source_video": {
            "path": os.path.abspath(args.video),
            "bytes": video_size,
            "sha256": video_sha256,
            "format": probe.get("format", {}).get("format_name"),
            "duration_s": probe.get("format", {}).get("duration"),
            "creation_time": probe.get("format", {}).get("tags", {}).get(
                "creation_time"
            ),
            "video_stream": {
                "codec": video_stream.get("codec_name"),
                "stored_width": video_stream.get("width"),
                "stored_height": video_stream.get("height"),
                "display_rotation_deg": rotation,
                "avg_frame_rate": video_stream.get("avg_frame_rate"),
                "nb_frames": video_stream.get("nb_frames"),
                "duration_s": video_stream.get("duration"),
            },
        },
        "frames": {
            "dir": os.path.abspath(args.frames_dir),
            "count": len(frame_files),
            "naming": "frame_%04d.jpg, 1-based",
            "dimensions": (
                [{"width": w, "height": h} for (w, h) in sorted(dims)]
            ),
            "mapping_rule": "frame_N corresponds to video time t=(N-1) seconds",
            "mapping_basis": (
                "1 fps extraction starting at t=0; extraction command "
                "unknown, mapping verified by visual side-by-side "
                "comparison of video seeks against extracted frames"
            ),
            "mapping_checks": parse_mapping_checks(args.mapping_checks),
            "files": frame_files,
        },
        "sibling_artifacts": siblings,
        "integrity_note": (
            "Originals were only read, never modified. SHA-256 values "
            "are over the exact bytes on disk at generation time."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build source_manifest.json for the SNES-01 benchmark."
    )
    parser.add_argument("--video", required=True, help="Original .mp4 path.")
    parser.add_argument(
        "--frames-dir", required=True, help="Directory of frame_*.jpg files."
    )
    parser.add_argument("--out", required=True, help="Output JSON path.")
    parser.add_argument(
        "--sibling",
        action="append",
        default=[],
        help="Extra sibling file to record (repeatable).",
    )
    parser.add_argument(
        "--mapping-checks",
        default="",
        help="Comma-separated t=<sec>:frame_NNNN visual checkpoints.",
    )
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args(argv)

    manifest = build_manifest(args)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=False)
        handle.write("\n")
    print(f"wrote {args.out} ({manifest['frames']['count']} frames)")
    print(f"video sha256: {manifest['source_video']['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
