"""Background drawing job: frame pre-selection → Muse frame choice → Muse box → SAM → layered SVG.

    python tools/drawing-proof/drawing_job.py start --spec job.json --job <dir>   # returns at once
    python tools/drawing-proof/drawing_job.py status --job <dir>                 # progress.json

The job writes ``progress.json`` after every stage (state, stage, done/total, stage seconds), so a dialog can
stay usable and poll. Segmentation is the frozen r4 tool (muse_sam.py); frame choice is a separate variant.
Job spec: video, part, scope (object | assembly | part), attachments (e.g. "cable excluded"), region_mm,
px_per_mm, stride_s, candidates, t_range (optional).
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

import draw
import frames
import muse_sam
import proof
import segment
import source

STAGES = ["sample_frames", "select_candidates", "rectify_candidates", "muse_choose_frame", "muse_box", "sam", "svg"]
CHOICE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["tile", "usable", "reason"],
                 "properties": {"tile": {"type": "integer"}, "usable": {"type": "boolean"}, "reason": {"type": "string", "maxLength": 300}}}
CHOICE_PROMPT = ("The numbered tiles are top-down photos of a calibration sheet at different times. Which tile shows the {part} "
                 "best for an outline drawing: whole part visible, no hands, no other part on top of it, sharp? Answer with its number. "
                 "Set usable false if no tile shows the whole part without hands.")


class Progress:
    def __init__(self, job: Path):
        self.path, self.began, self.timings = job / "progress.json", time.perf_counter(), {}
        self.write("running", None)

    def write(self, state: str, stage: str | None, **extra) -> None:
        done = len(self.timings)
        self.path.write_text(json.dumps({"state": state, "stage": stage, "done": done, "total": len(STAGES),
                                         "stage_s": self.timings, "elapsed_s": round(time.perf_counter() - self.began, 2), **extra}, indent=1),
                             encoding="utf-8")

    def stage(self, name: str):
        progress = self

        class _Stage:
            def __enter__(self):
                self.t = time.perf_counter()
                progress.write("running", name)

            def __exit__(self, *exc):
                progress.timings[name] = round(time.perf_counter() - self.t, 2)
                progress.write("running", name)
        return _Stage()


def render_layered_svg(outline_mm, openings_mm, spec: dict) -> str:
    """Outline visible; opening candidates in their own hidden, labelled group; inner details as an empty group."""
    geometry = {"outline_mm": outline_mm, "regions": []}
    svg = proof.render_svg(geometry, {"title": spec["title"], "subtitle": spec["subtitle"], "legend": spec["legend"]})
    o = proof._layout(geometry, spec)["part_origin"]
    candidates = "".join(f'<path data-line-role="detail" data-status="unconfirmed" d="{proof._path(p, o)}" fill="none" '
                         f'stroke="#c0392b" stroke-width="0.3" stroke-dasharray="1 1"/>' for p in openings_mm)
    layers = (f'<g id="opening_candidates" data-status="unconfirmed" display="none">{candidates}</g>'
              '<g id="inner_details" data-status="not_extracted"></g>')
    return svg.replace("</svg>", layers + "</svg>")


def run(spec: dict, job: Path) -> dict:
    progress = Progress(job)
    usage = {"muse_calls": 0, "sam_calls": 0, "failed_calls": 0}
    try:
        video = Path(spec["video"])
        with progress.stage("sample_frames"):
            sampled = frames.sample(video, spec["stride_s"], tuple(spec["t_range"]) if spec.get("t_range") else None)
        with progress.stage("select_candidates"):
            chosen = frames.select(sampled, spec["candidates"])
        with progress.stage("rectify_candidates"):
            tiles, rectified, skipped = [], [], []
            for c in list(chosen):
                try:
                    _, rf = source.rectify(video, c.frame_index, tuple(spec["region_mm"]), spec["px_per_mm"])
                except SystemExit as exc:  # the sheet reader exits on a rejected registration: skip that frame
                    skipped.append({"frame": c.frame_index, "reason": str(exc)[:200]})
                    chosen.remove(c)
                    continue
                rectified.append(rf)
                tiles.append(rf.image)
            if not tiles:
                raise RuntimeError(f"no candidate frame registered: {skipped}")
            sheet = frames.contact_sheet(tiles)
            cv2.imwrite(str(job / "contact_sheet.jpg"), sheet)
        key = muse_sam.client.load_key()
        with progress.stage("muse_choose_frame"):
            ok, jpg = cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])
            payload = {"model": muse_sam.FROZEN["muse_model"], "reasoning": {"effort": muse_sam.FROZEN["reasoning_effort"]},
                       "input": [{"role": "user", "content": [{"type": "input_text", "text": CHOICE_PROMPT.format(part=spec["part"])},
                                  {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(jpg.tobytes()).decode()}]}],
                       "text": {"format": {"type": "json_schema", "name": "frame_choice", "strict": True, "schema": CHOICE_SCHEMA}}}
            usage["muse_calls"] += 1
            data, meta = muse_sam.client.post_responses_once(payload, key, timeout_s=300)
            text = next(c.get("text") for o in data.get("output", []) if o.get("type") == "message" for c in o.get("content", []))
            choice = json.loads(text)
        if not choice["usable"] or not 1 <= choice["tile"] <= len(chosen):
            result = {"status": "NEEDS_CAPTURE", "reason": "Muse found no usable tile", "choice": choice}
            progress.write("done", None, result=result)
            return result
        rf, picked = rectified[choice["tile"] - 1], chosen[choice["tile"] - 1]
        image_path = job / "chosen_rectified.png"
        cv2.imwrite(str(image_path), rf.image)
        with progress.stage("muse_box"):
            usage["muse_calls"] += 1
            box = muse_sam.muse_box(rf.image, spec["part"], key)
        if not box["valid"]:
            result = {"status": "MODEL_BOX_INVALID", "box": box}
            progress.write("done", None, result=result)
            return result
        if not box["answer"]["fully_visible"]:  # Muse itself says the part is cut off: ask for a capture, draw nothing
            result = {"status": "NEEDS_CAPTURE", "reason": box["answer"]["reason"], "frame": {"index": picked.frame_index, "t_s": picked.t_s},
                      "capture_gap_kind": "outer_boundary", "usage": usage}
            progress.write("done", None, result=result)
            return result
        with progress.stage("sam"):
            usage["sam_calls"] += 1
            mask, sam_info = muse_sam.sam_mask(image_path, box["box_full"], rf.image.shape[:2], job / "sam", job / "sam_ledger.jsonl")
        if mask is None:
            usage["failed_calls"] += 1
            result = {"status": "PROVIDER_ERROR", "sam": sam_info}
            progress.write("done", None, result=result)
            return result
        with progress.stage("svg"):
            outer, openings = muse_sam.outer_and_openings(mask)
            ppm = rf.px_per_mm
            outline, deviation = draw.simplify(segment.boundary(outer), ppm, 0.2)
            openings_mm = [rf.px_to_mm(draw.simplify(o, ppm, 0.2)[0]).round(2).tolist() for o in openings]
            svg_spec = {"title": f"{spec['part']} - {spec['scope']} outline (demonstrator)",
                        "subtitle": f"{video.name} frame {picked.frame_index} ({picked.t_s} s); attachments: {spec['attachments']}",
                        "legend": [f"Outline: SAM mask boundary, max deviation {deviation:.2f} mm, sheet plane; not part dimensions.",
                                   f"Opening candidates: {len(openings)}, unconfirmed, hidden layer 'opening_candidates'.",
                                   "Inner details: not extracted (empty layer). Frame identity (which physical part), view side and orientation unconfirmed."]}
            (job / "drawing.svg").write_text(render_layered_svg(rf.px_to_mm(outline).round(2).tolist(), openings_mm, svg_spec), encoding="utf-8")
            cv2.imwrite(str(job / "overlay.jpg"), muse_sam.overlay(rf.image, outer, openings, box["box_full"]))
        result = {"status": "drawn_unconfirmed", "needs": ["operator confirms frame identity (capture-gap kind frame_identity)"], "scope": spec["scope"], "attachments": spec["attachments"],
                  "frame": {"index": picked.frame_index, "t_s": picked.t_s}, "choice": choice, "box": box["answer"],
                  "outline_points": len(outline), "opening_candidates": len(openings), "sam": sam_info,
                  "muse_latency_s": {"choose": meta["latency_s"], "box": box["latency_s"]},
                  "skipped_candidates": skipped, "candidates": [{"frame": c.frame_index, "t_s": c.t_s, "sharpness": round(c.sharpness, 1), "motion": round(c.motion, 2),
                                  "skin": round(c.skin, 4)} for c in chosen], "usage": usage}
        progress.write("done", None, result=result)
        return result
    except (Exception, SystemExit) as exc:  # the dialog must see a failure, never a silent stop
        usage["failed_calls"] += 1
        progress.write("failed", None, error=f"{type(exc).__name__}: {str(exc)[:300]}", usage=usage)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("start", "run"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--spec", required=True, type=Path)
        cmd.add_argument("--job", required=True, type=Path)
    sub.add_parser("status").add_argument("--job", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "status":
        print((args.job / "progress.json").read_text(encoding="utf-8"))
        return
    args.job.mkdir(parents=True, exist_ok=True)
    if args.command == "start":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen([sys.executable, "-B", __file__, "run", "--spec", str(args.spec), "--job", str(args.job)],
                         stdout=open(args.job / "job.log", "w"), stderr=subprocess.STDOUT, creationflags=flags)
        print(json.dumps({"job": str(args.job), "state": "started"}))
        return
    print(json.dumps(run(json.loads(args.spec.read_text(encoding="utf-8")), args.job), indent=1))


if __name__ == "__main__":
    main()
