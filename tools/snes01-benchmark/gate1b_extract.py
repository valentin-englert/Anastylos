"""Gate 1B extraction: full-video 10 fps working frames + PTS index.

Decodes the original SNES-01 recording ONCE at 10 fps, half resolution
(1080x1920, display orientation = rotation already applied, matching
the existing 1 fps frames). Display-full coordinates = extracted * 2.

Every extracted frame gets its absolute video timestamp from the
showinfo filter, stored in frames10_index.json. The 5 fps arm
subsamples every 2nd frame deterministically; the 1 fps arm reuses the
existing frames-1fps (never re-extracted).

Never modifies the video or existing frames. Deterministic given the
same ffmpeg build (version recorded in the index).

Usage:
    python gate1b_extract.py --video C:\\ANASTYLOS\\incoming\\...mp4 ^
        --out C:\\ANASTYLOS\\data\\snes-01\\gate1b\\frames10
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

SCHEMA = "snes01-gate1b/frames-index@1"
FPS = 10
SCALE = "1080:1920"
COORD_SCALE_TO_DISPLAY = 2  # extracted px * 2 = 2160x3840 display px


def _die(message: str):
    print(f"gate1b_extract: ERROR: {message}", file=sys.stderr)
    sys.exit(2)


def ffmpeg_version() -> str:
    proc = subprocess.run(["ffmpeg", "-version"], capture_output=True,
                          text=True)
    if proc.returncode != 0:
        _die("ffmpeg not runnable")
    return proc.stdout.splitlines()[0].strip()


def parse_showinfo(stderr: str) -> list:
    """Absolute pts_time per output frame, in output order."""
    times = []
    for line in stderr.splitlines():
        match = re.search(r"pts_time:([0-9.]+)", line)
        if "[Parsed_showinfo" in line and match:
            times.append(float(match.group(1)))
    return times


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Gate 1B frame extraction.")
    parser.add_argument("--video", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    video = Path(args.video)
    if not video.is_file():
        _die(f"video not found: {video}")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if any(out.glob("frm_*.jpg")):
        _die(f"refusing to overwrite existing extraction in {out}")
    version = ffmpeg_version()
    cmd = ["ffmpeg", "-v", "info", "-y", "-i", str(video), "-vf",
           f"fps={FPS},scale={SCALE},showinfo", "-q:v", "2",
           str(out / "frm_%04d.jpg")]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        _die(f"ffmpeg failed: {proc.stderr[-2000:]}")
    files = sorted(out.glob("frm_*.jpg"))
    times = parse_showinfo(proc.stderr)
    if len(files) != len(times):
        _die(f"showinfo frames ({len(times)}) != files ({len(files)})")
    index = {
        "schema": SCHEMA,
        "video": str(video),
        "video_sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
        "ffmpeg": version,
        "command": cmd,
        "fps": FPS,
        "extracted_size": [1080, 1920],
        "coord_scale_to_display": COORD_SCALE_TO_DISPLAY,
        "frames": [{"file": f.name, "pts_time_s": round(t, 3)}
                   for f, t in zip(files, times)],
    }
    (out / "frames10_index.json").write_text(json.dumps(index, indent=2),
                                             encoding="utf-8")
    print(json.dumps({"frames": len(files),
                      "first_pts": times[0] if times else None,
                      "last_pts": times[-1] if times else None}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
