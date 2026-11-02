"""Frame extraction for the evidence replay: one video moment becomes rectified sheet-view JPEGs in a folder.

Runs as a subprocess of the session UI (``replay.py``), because it needs OpenCV and the custody sheet reader that the
UI process does not import. Reuses ``tools/drawing-proof``: ``source.rectify`` (registration to the sheet, top view),
``frames.sample`` (sharpness, motion, skin share) and ``frames.video_sha256``. No model is called, nothing outside
``--out`` is written, and the folder appears atomically: it is built under ``<out>.partial`` and renamed.

Output folder: ``seq-000.jpg ...`` (about 10 fps over ``--window`` seconds around ``--t-s``; frames whose sheet
registration fails or has rms above 0.5 mm are skipped, not repaired), ``before.jpg`` and ``after.jpg`` (the best
registered, low-motion, low-skin frame near ``t - before`` and ``t + after``) and ``manifest.json``
(``anastylos-replay-evidence@1``: video SHA-256, every kept and skipped time, per-file SHA-256).

    python replay_worker.py --video V --video-sha256 H --t-s 22.2 --out DIR
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

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "drawing-proof"))
import frames  # noqa: E402
import source  # noqa: E402

SCHEMA = "anastylos-replay-evidence@1"
FULL_PAGE_MM = (0.0, 0.0, 420.0, 297.0)
MAX_RMS_MM = 0.5
MIN_SEQUENCE_FRAMES = 3
PICK_TRIES = 8
PICK_STRIDE_S = 0.2
PICK_SPAN_S = 1.0
JPEG_QUALITY = 82


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _registered(video: Path, frame_index: int, ppm: float):
    """The rectified frame, or ``(None, reason)``; a registration with rms above the gate counts as failed."""
    try:
        _raw, rectified = source.rectify(video, frame_index, FULL_PAGE_MM, ppm)
    except source.SourceError as exc:
        return None, str(exc)[:200]
    rms = rectified.provenance["registration"].get("rms_mm")
    if isinstance(rms, (int, float)) and rms > MAX_RMS_MM:
        return None, f"registration rms {rms:.2f} mm is above {MAX_RMS_MM} mm"
    return rectified, None


def _save(path: Path, image) -> None:
    if not cv2.imwrite(str(path), image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]):
        raise OSError(f"cannot write {path.name}")


def _extract_sequence(video: Path, out: Path, fps: float, total: int, t_s: float, window_s: float, rate: float, ppm: float):
    kept, skipped, seen = [], [], set()
    steps = int(round(window_s * rate))
    for step in range(steps + 1):
        at = t_s - window_s / 2 + step / rate
        index = int(round(at * fps))
        if index < 0 or index >= total or index in seen:
            continue
        seen.add(index)
        rectified, reason = _registered(video, index, ppm)
        if rectified is None:
            skipped.append({"t_s": round(index / fps, 3), "frame_index": index, "reason": reason})
            continue
        name = f"seq-{len(kept):03d}.jpg"
        _save(out / name, rectified.image)
        kept.append({"file": name, "t_s": round(index / fps, 3), "frame_index": index})
    return kept, skipped


def _pick(video: Path, out: Path, name: str, around_s: float, duration_s: float, fps: float, ppm: float):
    """Best registered frame near ``around_s``: lowest skin share, then lowest motion, then sharpest."""
    start, stop = max(0.0, around_s - PICK_SPAN_S), min(duration_s, around_s + PICK_SPAN_S)
    if stop <= start:
        return None, [{"reason": "outside the video"}]
    candidates = frames.sample(video, PICK_STRIDE_S, (start, stop))
    ranked = sorted(candidates, key=lambda c: (round(c.skin, 3), c.motion, -c.sharpness))
    refused = []
    for candidate in ranked[:PICK_TRIES]:
        rectified, reason = _registered(video, candidate.frame_index, ppm)
        if rectified is None:
            refused.append({"t_s": candidate.t_s, "frame_index": candidate.frame_index, "reason": reason})
            continue
        _save(out / f"{name}.jpg", rectified.image)
        return {"file": f"{name}.jpg", "t_s": candidate.t_s, "frame_index": candidate.frame_index,
                "requested_t_s": round(around_s, 3), "sharpness": round(candidate.sharpness, 1),
                "motion": round(candidate.motion, 2), "skin": round(candidate.skin, 4)}, refused
    return None, refused or [{"reason": "no candidate frames"}]


def extract(video: Path, video_sha256: str, t_s: float, out: Path, window_s: float = 2.0, rate: float = 10.0,
            before_s: float = 3.0, after_s: float = 3.0, ppm: float = 3.0) -> dict:
    if out.exists():
        raise FileExistsError(f"{out} already exists")
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
    partial = out.with_name(out.name + ".partial")
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True)
    kept, skipped = _extract_sequence(video, partial, fps, total, t_s, window_s, rate, ppm)
    before = after = None
    refused: dict[str, list] = {}
    if len(kept) >= MIN_SEQUENCE_FRAMES:
        before, refused["before"] = _pick(video, partial, "before", t_s - before_s, duration, fps, ppm)
        after, refused["after"] = _pick(video, partial, "after", t_s + after_s, duration, fps, ppm)
    manifest = {"schema": SCHEMA, "status": "ready" if len(kept) >= MIN_SEQUENCE_FRAMES else "unregistered",
                "requested_t_s": t_s, "window_s": window_s, "rate_fps": rate, "px_per_mm": ppm,
                "region_mm": list(FULL_PAGE_MM), "max_rms_mm": MAX_RMS_MM,
                "video": {"name": video.name, "sha256": actual, "fps": round(fps, 3), "duration_s": round(duration, 3)},
                "frames": kept, "skipped": skipped, "before": before, "after": after, "refused_picks": refused,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "memory_written": False, "model_called": False}
    manifest["files_sha256"] = {p.name: _sha256(p) for p in sorted(partial.glob("*.jpg"))}
    (partial / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8", newline="\n")
    os.replace(partial, out)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--video-sha256", required=True)
    parser.add_argument("--t-s", type=float, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = extract(args.video, args.video_sha256, args.t_s, args.out)
    except (OSError, ValueError, source.SourceError) as exc:
        print(f"replay extraction failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": manifest["status"], "frames": len(manifest["frames"]), "skipped": len(manifest["skipped"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
