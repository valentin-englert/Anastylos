#!/usr/bin/env python3
"""Render the labeled SNES-01 event contact sheet via ffmpeg.

Stdlib-only driver: reads before/after tiles (frame + pixel crop +
label) from event_candidates.json, builds one deterministic ffmpeg
filter graph, and runs it. Run with --dry-run to print the ffmpeg
command without executing it.

Tile order: for each event in file order, BEFORE then AFTER, laid out
left-to-right, top-to-bottom in --cols columns.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

TILE_SCHEMA_KEYS = ("frame", "crop", "region")


def default_font() -> str | None:
    candidates = [
        r"C:\Windows\Fonts\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def load_tiles(events_path: str) -> list[dict]:
    with open(events_path, encoding="utf-8") as handle:
        events_doc = json.load(handle)
    tiles: list[dict] = []
    for event in events_doc.get("events", []):
        spec = event.get("contact_tile", {})
        for side in ("before", "after"):
            tile = spec.get(side, {})
            missing = [k for k in TILE_SCHEMA_KEYS if k not in tile]
            if missing:
                raise ValueError(
                    f"event {event.get('id')}: contact_tile.{side} "
                    f"missing {missing}"
                )
            tiles.append(
                {
                    "event": event.get("id"),
                    "side": side.upper(),
                    "frame": tile["frame"],
                    "crop": tile["crop"],
                    "region": tile["region"],
                }
            )
    if not tiles:
        raise ValueError("no contact tiles found in events file")
    return tiles


def escape_drawtext(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("%", "\\%")
    )


def build_filter(
    tiles: list[dict],
    tile_width: int,
    tile_height: int,
    cols: int,
    font: str,
) -> str:
    parts: list[str] = []
    for index, tile in enumerate(tiles):
        x, y, w, h = tile["crop"]
        label = (
            f"{tile['event']} {tile['side']} "
            f"{tile['frame']} {tile['region']}".upper()
        )
        parts.append(
            f"[{index}]crop={w}:{h}:{x}:{y},scale={tile_width}:-1,"
            f"pad={tile_width}:{tile_height}:0:0:black,"
            f"drawtext=text='{escape_drawtext(label)}':"
            f"fontfile='{font}':fontsize=22:fontcolor=white:"
            f"box=1:boxcolor=black@0.8:x=8:y=8[v{index}]"
        )
    streams = "".join(f"[v{i}]" for i in range(len(tiles)))
    layout = "|".join(
        f"{(i % cols) * tile_width}_{(i // cols) * tile_height}"
        for i in range(len(tiles))
    )
    parts.append(f"{streams}xstack=inputs={len(tiles)}:layout={layout}")
    return ";".join(parts)


def build_command(args: argparse.Namespace, tiles: list[dict], font: str) -> list[str]:
    inputs: list[str] = []
    for tile in tiles:
        path = os.path.join(args.frames_dir, tile["frame"] + ".jpg")
        if not os.path.exists(path):
            raise FileNotFoundError(f"missing frame file: {path}")
        inputs += ["-i", path]
    filt = build_filter(tiles, args.tile_width, args.tile_height, args.cols, font)
    return [
        args.ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        *inputs,
        "-filter_complex",
        filt,
        "-q:v",
        "3",
        args.out,
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render the SNES-01 event contact sheet."
    )
    parser.add_argument(
        "--events", required=True, help="event_candidates.json path."
    )
    parser.add_argument(
        "--frames-dir", required=True, help="Directory of frame_*.jpg files."
    )
    parser.add_argument("--out", required=True, help="Output JPEG path.")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--font", default=None, help="TTF font for labels.")
    parser.add_argument("--tile-width", type=int, default=480)
    parser.add_argument("--tile-height", type=int, default=520)
    parser.add_argument("--cols", type=int, default=4)
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the command and exit."
    )
    args = parser.parse_args(argv)

    font = args.font or default_font()
    if font is None:
        print(
            "error: no label font found; pass --font PATH_TO_TTF",
            file=sys.stderr,
        )
        return 2
    font_ffmpeg = font.replace("\\", "/").replace(":", "\\:")

    tiles = load_tiles(args.events)
    command = build_command(args, tiles, font_ffmpeg)
    if args.dry_run:
        print(" ".join(f'"{c}"' if " " in c else c for c in command))
        return 0
    proc = subprocess.run(command, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        print(f"error: ffmpeg failed: {proc.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"wrote {args.out} ({len(tiles)} tiles, font {font})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
