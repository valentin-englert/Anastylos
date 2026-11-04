"""Gate 2B.1: build the v2 positive-control annotation package.

Input: curated pair definitions (HUMAN-CURATED, disclosed in-file).
Output: annotate_v2/ with tight crops, context crops, outlined
full-frame references, short evidence clips (ffmpeg stream copy),
review sheet, candidate manifest, empty v2 answer-key template, and
annotation guide. No verdicts assigned. Original v1 package untouched.

Usage:
    python gate2b_prepare_v2.py --curated <json> --frames-dir <dir> ^
        --manifest <source_manifest> --video <mp4> --out <annotate_v2>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

try:
    import cv2
    import numpy as np
except ImportError as exc:  # pragma: no cover
    sys.exit(f"gate2b_prepare_v2 needs OpenCV + NumPy: {exc}")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate2a_run as g2a

SCHEMA = "snes01-gate2b/pairs@1"
KEY_SCHEMA = "snes01-gate2b/answer-key@2"
CONTEXT_SCALE = 2.2
CLIPS = {"V2-P02": (58.0, 62.0), "V2-P03": (93.0, 97.0)}
# The video file is verified against an EXPLICIT --video-sha256 given
# on the command line (never a silently substituted pin). See the
# Gate 2B.1 report: container bytes differ from the 18:10 manifest
# hash while decoded pixels are proven identical.


def _die(message: str):
    print(f"gate2b_prepare_v2: ERROR: {message}", file=sys.stderr)
    sys.exit(2)


def widen(box: list, factor: float, width: int, height: int) -> list:
    cx, cy = box[0] + box[2] / 2.0, box[1] + box[3] / 2.0
    w, h = box[2] * factor, box[3] * factor
    x = max(0, min(int(cx - w / 2), width - 1))
    y = max(0, min(int(cy - h / 2), height - 1))
    return [x, y, max(1, min(int(w), width - x)),
            max(1, min(int(h), height - y))]


def cut_clip(video: Path, start: float, end: float, out: Path) -> dict:
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", str(start), "-to",
           str(end), "-i", str(video), "-c", "copy", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not out.is_file():
        _die(f"ffmpeg clip failed: {proc.stderr[-500:]}")
    return {"file": out.name, "t_start_s": start, "t_end_s": end,
            "method": "stream copy (original bytes, keyframe-approx "
                      "boundaries)",
            "sha256": hashlib.sha256(out.read_bytes()).hexdigest()}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build v2 package.")
    parser.add_argument("--curated", required=True)
    parser.add_argument("--frames-dir", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--video", required=True)
    parser.add_argument("--video-sha256", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        curated = json.loads(Path(args.curated).read_text(encoding="utf-8"))
        manifest = json.loads(Path(args.manifest).read_text(
            encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _die(f"cannot read input: {exc}")
    if curated.get("schema") != "snes01-gate2b/cases-curated@1":
        _die("not curated v2 cases")
    file_hash = {f["name"]: f["sha256"]
                 for f in manifest["frames"]["files"]}
    video = Path(args.video)
    video_sha = hashlib.sha256(video.read_bytes()).hexdigest()
    if video_sha != args.video_sha256:
        _die("video != explicit --video-sha256; refusing")
    frames_dir = Path(args.frames_dir)
    out = Path(args.out)
    for sub in ("tight", "context", "refs", "clips"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy(args.curated, out / "curation_input.json")
    manifest_pairs = []
    sheet_rows = []
    for case in curated["cases"]:
        pid = case["id"]
        entry = {"id": pid, "name": case["name"],
                 "curation": case["curation"],
                 "context_note": case.get("context_note", ""),
                 "images": {}}
        row_tight, row_ctx = [], []
        for role in ("source", "dest"):
            spec = case[role]
            frame_path = frames_dir / spec["frame"]
            digest = g2a.sha256_file(frame_path)
            if digest != file_hash.get(spec["frame"]):
                _die(f"{pid}: {spec['frame']} != manifest hash")
            image = cv2.imread(str(frame_path))
            height, width = image.shape[:2]
            tight = g2a.cut_crop(image, spec["bbox_xywh"])
            tight_name = f"{pid}_{role}_tight.png"
            cv2.imwrite(str(out / "tight" / tight_name), tight)
            ctx_box = widen(spec["bbox_xywh"], CONTEXT_SCALE, width,
                            height)
            context = g2a.cut_crop(image, ctx_box)
            ctx_name = f"{pid}_{role}_context.jpg"
            cv2.imwrite(str(out / "context" / ctx_name), context,
                        [cv2.IMWRITE_JPEG_QUALITY, 92])
            ref = cv2.resize(image, (540, 960),
                             interpolation=cv2.INTER_AREA)
            x, y, w, h = (int(v / 4) for v in spec["bbox_xywh"])
            cv2.rectangle(ref, (x, y), (x + w, y + h), (0, 255, 255), 2)
            cv2.putText(ref, pid, (x, max(20, y - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            ref_name = f"{pid}_{role}_ref.jpg"
            cv2.imwrite(str(out / "refs" / ref_name), ref,
                        [cv2.IMWRITE_JPEG_QUALITY, 88])
            entry["images"][role] = {
                "frame": spec["frame"], "t_s": spec["t_s"],
                "frame_sha256": digest,
                "bbox_xywh": spec["bbox_xywh"],
                "tight": {"file": f"tight/{tight_name}",
                          "sha256": g2a.sha256_file(
                              out / "tight" / tight_name)},
                "context": {"file": f"context/{ctx_name}",
                            "box_xywh": ctx_box,
                            "sha256": g2a.sha256_file(
                                out / "context" / ctx_name)},
                "ref": {"file": f"refs/{ref_name}",
                        "sha256": g2a.sha256_file(
                            out / "refs" / ref_name)},
            }
        tight_a = cv2.imread(
            str(out / "tight" / f"{pid}_source_tight.png"))
        tight_b = cv2.imread(
            str(out / "tight" / f"{pid}_dest_tight.png"))
        for image in (tight_a, tight_b):
            ph, pw = image.shape[:2]
            scale = 420 / max(1, ph)
            row_tight.append(cv2.resize(
                image, (max(1, int(pw * scale)), 420)))
        panel = np.full((420, 560, 3), 20, np.uint8)
        cv2.putText(panel, pid, (10, 36), cv2.FONT_HERSHEY_SIMPLEX,
                    1.0, (0, 255, 255), 2)
        cv2.putText(panel, case["name"][:30], (10, 72),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        src, dest = case["source"], case["dest"]
        cv2.putText(
            panel, f"A {src['frame']} t={src['t_s']}s", (10, 112),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1)
        cv2.putText(
            panel, f"B {dest['frame']} t={dest['t_s']}s", (10, 142),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1)
        cv2.putText(panel, "type proposal: see manifest", (10, 182),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1)
        cv2.putText(panel, "verdict: ___", (10, 232),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        cv2.putText(panel, "identity_basis: ___", (10, 272),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        cv2.putText(panel, "evidence_type: ___", (10, 312),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        row_tight.append(panel)
        sheet_rows.append(np.hstack(row_tight))
        for role in ("source", "dest"):
            for name, hh in (
                    (f"{pid}_{role}_context.jpg", 300),
                    (f"{pid}_{role}_ref.jpg", 300)):
                folder = "context" if "context" in name else "refs"
                image = cv2.imread(str(out / folder / name))
                ph, pw = image.shape[:2]
                scale = hh / max(1, ph)
                row_ctx.append(cv2.resize(
                    image, (max(1, int(pw * scale)), hh)))
        widths = [img.shape[1] for img in row_ctx]
        canvas = np.full((300, sum(widths), 3), 20, np.uint8)
        offset = 0
        for image, width in zip(row_ctx, widths):
            canvas[:, offset:offset + width] = image
            offset += width
        sheet_rows.append(canvas)
        if pid in CLIPS:
            entry["clip"] = cut_clip(
                video, *CLIPS[pid], out / "clips" / f"{pid}_ctx.mp4")
        manifest_pairs.append(entry)
    widths = [row.shape[1] for row in sheet_rows]
    canvas_w = max(widths)
    fixed = []
    for row in sheet_rows:
        base = np.full((row.shape[0], canvas_w, 3), 20, np.uint8)
        base[:, :row.shape[1]] = row
        fixed.append(base)
    cv2.imwrite(str(out / "annotate_sheet_v2.jpg"), np.vstack(fixed),
                [cv2.IMWRITE_JPEG_QUALITY, 88])
    (out / "candidate_manifest.json").write_text(json.dumps(
        {"schema": SCHEMA, "video_sha256": video_sha,
         "video_hash_note": "Verified at build time against explicit "
                            "--video-sha256. NOTE: differs from the "
                            "18:10 source_manifest hash (container bytes "
                            "changed, size/mtime/metadata identical); "
                            "decoded pixels proven identical (see Gate "
                            "2B.1 report). Manifest re-issue is the repo "
                            "owner's decision.",
         "manifest_sha256": hashlib.sha256(
             Path(args.manifest).read_bytes()).hexdigest(),
         "scored_pairs": [p["id"] for p in manifest_pairs],
         "pairs": [
             {"id": p["id"], "name": p["name"],
              "curation": p["curation"],
              "src": {"frame": p["images"]["source"]["frame"],
                      "t_s": p["images"]["source"]["t_s"],
                      "bbox_xywh": p["images"]["source"]["bbox_xywh"],
                      "file": p["images"]["source"]["tight"]["file"],
                      "crop_sha256": p["images"]["source"]["tight"][
                          "sha256"]},
              "dest": {"frame": p["images"]["dest"]["frame"],
                       "t_s": p["images"]["dest"]["t_s"],
                       "bbox_xywh": p["images"]["dest"]["bbox_xywh"],
                       "file": p["images"]["dest"]["tight"]["file"],
                       "crop_sha256": p["images"]["dest"]["tight"][
                           "sha256"]},
              "clip": p.get("clip")}
             for p in manifest_pairs]}, indent=2), encoding="utf-8")
    key_id = f"gate2b-v2-{uuid.uuid4().hex[:8]}"
    (out / "answerkey_template.json").write_text(json.dumps(
        {"schema": KEY_SCHEMA, "annotation_set_id": key_id,
         "status": "template",
         "instructions": "Judge each pair from the supplied pixels + "
                         "context + refs (+ clips). same_physical_"
                         "instance only when the component itself is "
                         "visibly identifiable in BOTH crops; "
                         "different_physical_instance when demonstrably "
                         "different; ambiguous when same-instance is "
                         "unjustified; unverifiable when the evidence "
                         "cannot support any call. Confirm evidence_type "
                         "(visual-reidentification vs event-origin). "
                         "Set status sealed + annotator + date.",
         "annotator": "", "annotation_date": "",
         "verdicts": [
             {"pair_id": p["id"], "verdict": "", "identity_basis": "",
              "evidence_type": "", "supporting_frames": [],
              "notes": ""}
             for p in manifest_pairs]}, indent=2), encoding="utf-8")
    guide = """# Gate 2B.1 v2 annotation guide

Package: 4 human-curated diagnostic pairs (V2-P01 D-pad, V2-P02 rear
shell, V2-P03 PCB cable-connected, V2-P04 face-button group).
Each pair: tight A/B crops, 2.2x context crops, outlined full-frame
refs; P02/P03 add +-2 s evidence clips (stream-copy mp4).

Verdicts: `same_physical_instance` (component visibly identifiable in
BOTH crops), `different_physical_instance` (demonstrably different),
`ambiguous` (same-instance unjustified), `unverifiable` (evidence
cannot support any call). Two identical-looking buttons are NOT
automatically the same instance; a class label is NOT identity proof.

Also confirm `evidence_type` per pair: `visual-reidentification`
(component visible in both) vs `event-origin` (source proves a change
location only). List `supporting_frames` (frame ids/timestamps) behind
each call. Fill `answerkey_template.json`, rename to
`gate2b_v2_answerkey.sealed.json`, set status sealed.

Curation notes (selection context, NOT truth): see
`curation_input.json`. Do not treat proposed types as verdicts.
"""
    (out / "ANNOTATION_GUIDE.md").write_text(guide, encoding="utf-8")
    print(json.dumps({"pairs": len(manifest_pairs), "key_id": key_id,
                      "out": str(out)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
