"""Frame extraction for the evidence replay: one video clip becomes rectified sheet-view JPEGs in a folder.

Runs as a subprocess of the session UI (``replay.py``), because it needs OpenCV and the custody sheet reader that the
UI process does not import. Reuses ``tools/drawing-proof``: ``source.rectify`` (registration to the sheet, top view),
``quality.skin_fraction`` and ``frames.video_sha256``. No model is called, nothing outside ``--out`` is written, and the
folder appears atomically: it is built under ``<out>.partial`` and renamed.

Output folder: ``seq-000.jpg ...`` (frames from ``--start-s`` to ``--end-s``, at most ``--max-rate`` fps and about
``--target-frames`` frames; frames whose sheet registration fails or has rms above 0.5 mm are skipped, not repaired),
``before.jpg`` (the last registered frame at or before ``--anchor-s`` with few skin pixels, else the one with the fewest),
``after.jpg`` (the first registered low-skin frame after the anchor, else the first registered one) and ``manifest.json``
(``anastylos-replay-evidence@2``: video SHA-256, every kept and skipped time, skin share per frame, per-file SHA-256).

    python replay_worker.py --video V --video-sha256 H --start-s 12 --end-s 24 --anchor-s 22 --out DIR
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "drawing-proof"))
import frames  # noqa: E402
import quality  # noqa: E402
import rectified_frames  # noqa: E402
import source  # noqa: E402

SCHEMA = "anastylos-replay-evidence@2"
FULL_PAGE_MM = (0.0, 0.0, 420.0, 297.0)
MAX_RMS_MM = 0.5
MIN_SEQUENCE_FRAMES = 3
LOW_SKIN = 0.02  # share of skin-coloured pixels in the rectified frame that still counts as "no hands in view"
JPEG_QUALITY = 82


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _registered(sheet: rectified_frames.SheetFrames, frame_index: int):
    """``(object with .image, None)`` for a registered frame, else ``(None, reason)`` (rms above the gate counts as failed)."""
    image, reason = sheet.rectified(frame_index)
    return (None, reason) if image is None else (SimpleNamespace(image=image), None)


def _save(path: Path, image) -> None:
    if not cv2.imwrite(str(path), image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]):
        raise OSError(f"cannot write {path.name}")


def _skin(image) -> float:
    return round(quality.skin_fraction(image, np.ones(image.shape[:2], bool)), 4)


def _extract_sequence(sheet, out: Path, fps: float, total: int, start_s: float, end_s: float, rate: float):
    kept, skipped, seen = [], [], set()
    for step in range(int((end_s - start_s) * rate) + 1):
        index = int(round((start_s + step / rate) * fps))
        if index < 0 or index >= total or index in seen:
            continue
        seen.add(index)
        rectified, reason = _registered(sheet, index)
        if rectified is None:
            skipped.append({"t_s": round(index / fps, 3), "frame_index": index, "reason": reason})
            continue
        name = f"seq-{len(kept):03d}.jpg"
        _save(out / name, rectified.image)
        kept.append({"file": name, "t_s": round(index / fps, 3), "frame_index": index, "skin": _skin(rectified.image)})
    return kept, skipped


def choose_pair(kept: list[dict], anchor_s: float) -> tuple[dict | None, dict | None]:
    """``(before, after)`` frames of the sequence around the anchor; each carries ``low_skin`` (no hands in view)."""
    earlier = [f for f in kept if f["t_s"] <= anchor_s]
    later = [f for f in kept if f["t_s"] > anchor_s]
    before = (next((f for f in reversed(earlier) if f["skin"] <= LOW_SKIN), None)
              or min(earlier, key=lambda f: (f["skin"], -f["t_s"]), default=None))
    after = next((f for f in later if f["skin"] <= LOW_SKIN), None) or (later[0] if later else None)
    return ({**before, "low_skin": before["skin"] <= LOW_SKIN} if before else None,
            {**after, "low_skin": after["skin"] <= LOW_SKIN} if after else None)


def extract(video: Path, video_sha256: str, start_s: float, end_s: float, anchor_s: float, out: Path,
            max_rate: float = 10.0, target_frames: int = 30, ppm: float = 3.0) -> dict:
    if out.exists():
        raise FileExistsError(f"{out} already exists")
    if not 0 <= start_s < end_s:
        raise ValueError("the clip needs 0 <= start < end")
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
    duration = total / fps
    end_s = min(end_s, duration)
    if end_s <= start_s:
        raise ValueError("the clip lies outside the video")
    rate = min(max_rate, target_frames / (end_s - start_s))
    partial = out.with_name(out.name + ".partial")
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True)
    with rectified_frames.SheetFrames(video, ppm, FULL_PAGE_MM) as sheet:
        kept, skipped = _extract_sequence(sheet, partial, fps, total, start_s, end_s, rate)
    ready = len(kept) >= MIN_SEQUENCE_FRAMES
    before = after = None
    if ready:
        before, after = choose_pair(kept, anchor_s)
        for name, pick in (("before", before), ("after", after)):
            if pick:
                shutil.copyfile(partial / pick["file"], partial / f"{name}.jpg")
                pick["source_file"], pick["file"] = pick["file"], f"{name}.jpg"
    manifest = {"schema": SCHEMA, "status": "ready" if ready else "unregistered",
                "start_s": start_s, "end_s": round(end_s, 3), "anchor_s": anchor_s, "rate_fps": round(rate, 3),
                "px_per_mm": ppm, "region_mm": list(FULL_PAGE_MM), "max_rms_mm": MAX_RMS_MM, "low_skin": LOW_SKIN,
                "video": {"name": video.name, "sha256": actual, "fps": round(fps, 3), "duration_s": round(duration, 3)},
                "frames": kept if ready else [], "skipped": skipped, "before": before, "after": after,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "memory_written": False, "model_called": False}
    manifest["files_sha256"] = {p.name: _sha256(p) for p in sorted(partial.glob("*.jpg"))}
    (partial / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8", newline="\n")
    os.replace(partial, out)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--video-sha256", required=True)
    parser.add_argument("--start-s", type=float, required=True)
    parser.add_argument("--end-s", type=float, required=True)
    parser.add_argument("--anchor-s", type=float, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = extract(args.video, args.video_sha256, args.start_s, args.end_s, args.anchor_s, args.out)
    except (OSError, ValueError, source.SourceError) as exc:
        print(f"replay extraction failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": manifest["status"], "frames": len(manifest["frames"]), "skipped": len(manifest["skipped"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
