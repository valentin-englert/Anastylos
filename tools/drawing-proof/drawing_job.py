"""Background drawing job for one requested part: candidates → Muse frame choice → Muse box → SAM → checks → SVG.

    python tools/drawing-proof/drawing_job.py start --spec job.json --job <dir>   # returns at once
    python tools/drawing-proof/drawing_job.py status --job <dir>                 # progress, with dead/timeout detection

Job spec: video, part, scope (part | assembly | object), view_side, attachments ("included: ..." / "excluded: ..."),
region_mm, px_per_mm, stride_s, candidates, t_range (optional), phase_events (optional custody events.json for
the temporal disassembly context).

Final states: failed | NEEDS_CAPTURE | NEEDS_CONFIRMATION | NEEDS_REVIEW | drawn_unconfirmed. No state releases
a drawing: binding needs the operator (``binding.json`` written by the session UI); memory is never touched.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import threading
import time
from pathlib import Path

import cv2

import draw
import frames
from job_control import HEARTBEAT_S, STAGE_TIMEOUT_S, STAGES, start, status
import muse_sam
import proof
import quality
import segment
import source

STATES = ["single_part", "assembly", "assembled_object", "unclear"]
SCOPE_STATE = {"part": "single_part", "assembly": "assembly", "object": "assembled_object"}
CHOICE_SCHEMA = {"type": "object", "additionalProperties": False,
                 "required": ["tile", "usable", "state", "view_side", "attachments_seen", "reason", "uncertainties"],
                 "properties": {"tile": {"type": "integer"}, "usable": {"type": "boolean"}, "state": {"type": "string", "enum": STATES},
                                "view_side": {"type": "string", "maxLength": 60}, "attachments_seen": {"type": "string", "maxLength": 120},
                                "reason": {"type": "string", "maxLength": 300}, "uncertainties": {"type": "string", "maxLength": 300}}}
CHOICE_PROMPT = ("Numbered tiles: top-down photos of a calibration sheet during a disassembly, taken at the times listed: {times}. "
                 "Requested: the {part}; scope {scope} (single_part = the part alone, separated from the rest; assembly = a group of "
                 "joined parts; assembled_object = the whole object before disassembly); view side {view_side}; attachments {attachments}. "
                 "Pick the tile that best shows exactly that: whole outline visible, no hands, nothing on top, sharp. "
                 "For the picked tile, state what it actually shows (state), the side you see, which cables or attachments you see, "
                 "and what you cannot tell from this image (uncertainties). If from the top you cannot tell a separated part from the "
                 "assembled object, use state unclear. Set usable false if no tile shows the requested item without hands.")


# ---------------------------------------------------------------- process liveness and progress

class Progress:
    def __init__(self, job: Path):
        self.path, self.lock = job / "progress.json", threading.Lock()
        self.doc = {"state": "running", "stage": None, "done": 0, "total": len(STAGES), "stage_s": {}, "pid": os.getpid(),
                    "started_at": time.time(), "stage_started_at": None, "heartbeat_at": time.time(), "timeouts_s": STAGE_TIMEOUT_S}
        self.flush()
        self._stop = threading.Event()
        threading.Thread(target=self._beat, daemon=True).start()

    def _beat(self) -> None:
        while not self._stop.wait(HEARTBEAT_S):
            self.update()

    def flush(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.doc, indent=1, default=str), encoding="utf-8")
        os.replace(tmp, self.path)

    def update(self, **fields) -> None:
        with self.lock:
            self.doc.update(fields, heartbeat_at=time.time(), elapsed_s=round(time.time() - self.doc["started_at"], 2))
            self.flush()

    def stage(self, name: str):
        progress = self

        class _Stage:
            def __enter__(self):
                self.t = time.perf_counter()
                progress.update(stage=name, stage_started_at=time.time())

            def __exit__(self, *exc):
                progress.doc["stage_s"][name] = round(time.perf_counter() - self.t, 2)
                progress.update(done=len(progress.doc["stage_s"]))
        return _Stage()

    def finish(self, state: str, **fields) -> None:
        self._stop.set()
        self.update(state=state, stage=None, **fields)


# ---------------------------------------------------------------- decision rules (deterministic)

def first_removal_s(events_path: str | None) -> float | None:
    if not events_path or not Path(events_path).exists():
        return None
    events = json.loads(Path(events_path).read_text(encoding="utf-8")).get("events", [])
    times = [e["t_s"] for e in events if e.get("kind") == "site_changed"]
    return min(times) if times else None


def frame_decision(spec: dict, choice: dict, t_s: float, removal_s: float | None) -> tuple[str, list[str]]:
    """Muse's description against the request and the temporal context; doubt asks the operator."""
    reasons = []
    wanted = SCOPE_STATE[spec["scope"]]
    if choice["state"] == "unclear":
        reasons.append("Muse cannot tell the state from this image")
    elif choice["state"] != wanted:
        reasons.append(f"Muse sees {choice['state']}, requested {wanted}")
    if removal_s is not None and spec["scope"] != "object" and t_s < removal_s:
        reasons.append(f"frame at {t_s:.1f} s is before the first observed removal of this phase ({removal_s:.1f} s): "
                       "the object may still be assembled")
    return ("NEEDS_CONFIRMATION" if reasons else "ok"), reasons


def final_state(fully_visible: bool, check_flags: list[str], decision: str) -> str:
    if not fully_visible or {"BOX_AT_IMAGE_EDGE", "MASK_AT_IMAGE_EDGE"} & set(check_flags):
        return "NEEDS_CAPTURE"
    if check_flags:
        return "NEEDS_REVIEW"
    return "NEEDS_CONFIRMATION" if decision == "NEEDS_CONFIRMATION" else "drawn_unconfirmed"


# ---------------------------------------------------------------- job

def _candidate_tiles(spec: dict, video: Path, chosen: list, cache_dir: Path) -> tuple[list, list, list]:
    """Rectified candidate tiles, cached next to the sample index by frame and region."""
    tiles, kept, skipped = [], [], []
    for c in chosen:
        cached = cache_dir / f"tile-{c.frame_index}-{'_'.join(str(v) for v in spec['region_mm'])}-{spec['px_per_mm']}.png"
        if cached.exists():
            tiles.append(cv2.imread(str(cached)))
            kept.append(c)
            continue
        try:
            _, rf = source.rectify(video, c.frame_index, tuple(spec["region_mm"]), spec["px_per_mm"])
        except source.SourceError as exc:  # this frame only; the others still count
            skipped.append({"frame": c.frame_index, "reason": str(exc)[:200]})
            continue
        cv2.imwrite(str(cached), rf.image)
        tiles.append(rf.image)
        kept.append(c)
    return tiles, kept, skipped


def _muse_choice(sheet, chosen: list, spec: dict, key: str) -> tuple[dict, dict, str]:
    ok, jpg = cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])
    times = ", ".join(f"{i} = {c.t_s:.1f} s" for i, c in enumerate(chosen, 1))
    prompt = CHOICE_PROMPT.format(times=times, part=spec["part"], scope=spec["scope"], view_side=spec["view_side"],
                                  attachments=spec["attachments"])
    payload = {"model": muse_sam.FROZEN["muse_model"], "reasoning": {"effort": muse_sam.FROZEN["reasoning_effort"]},
               "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt},
                          {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(jpg.tobytes()).decode()}]}],
               "text": {"format": {"type": "json_schema", "name": "frame_choice", "strict": True, "schema": CHOICE_SCHEMA}}}
    data, meta = muse_sam.client.post_responses_once(payload, key, timeout_s=300)
    choice = json.loads(next(c.get("text") for o in data.get("output", []) if o.get("type") == "message" for c in o.get("content", [])))
    return choice, meta, hashlib.sha256(prompt.encode()).hexdigest()


def run(spec: dict, job: Path) -> dict:
    progress = Progress(job)
    usage = {"muse_calls": 0, "sam_calls": 0}
    try:
        video = Path(spec["video"])
        with progress.stage("sample_frames"):
            sampled, cache = frames.cached_sample(video, spec["stride_s"], tuple(spec["t_range"]) if spec.get("t_range") else None)
        progress.update(cache=cache)
        with progress.stage("select_candidates"):
            chosen = frames.select(sampled, spec["candidates"])
        with progress.stage("rectify_candidates"):
            tiles, chosen, skipped = _candidate_tiles(spec, video, chosen, Path(cache["path"]).parent)
            if not tiles:
                raise source.SourceError(f"no candidate frame registered: {skipped}")
            sheet = frames.contact_sheet(tiles)
            cv2.imwrite(str(job / "contact_sheet.jpg"), sheet)
        key = muse_sam.client.load_key()
        with progress.stage("muse_choose_frame"):
            usage["muse_calls"] += 1
            choice, meta, prompt_sha = _muse_choice(sheet, chosen, spec, key)
        base = {"spec": spec, "choice": choice, "skipped_candidates": skipped, "usage": usage, "prompt_sha256": prompt_sha,
                "candidates": [{"frame": c.frame_index, "t_s": c.t_s, "sharpness": round(c.sharpness, 1), "motion": round(c.motion, 2),
                                "skin": round(c.skin, 4)} for c in chosen]}
        if not choice["usable"] or not 1 <= choice["tile"] <= len(chosen):
            result = {**base, "status": "NEEDS_CAPTURE", "reasons": ["Muse found no usable tile"], "capture_gap_kind": "outer_boundary"}
            progress.finish("done", result=result)
            return result
        picked = chosen[choice["tile"] - 1]
        decision, decision_reasons = frame_decision(spec, choice, picked.t_s, first_removal_s(spec.get("phase_events")))
        _, rf = source.rectify(video, picked.frame_index, tuple(spec["region_mm"]), spec["px_per_mm"])
        image_path = job / "chosen_rectified.png"
        cv2.imwrite(str(image_path), rf.image)
        with progress.stage("muse_box"):
            usage["muse_calls"] += 1
            box = muse_sam.muse_box(rf.image, spec["part"], key)
        base.update(frame={"index": picked.frame_index, "t_s": picked.t_s}, box=box)
        if not box["valid"]:
            result = {**base, "status": "NEEDS_CAPTURE", "reasons": ["Muse returned no valid box"]}
            progress.finish("done", result=result)
            return result
        with progress.stage("sam"):
            usage["sam_calls"] += 1
            mask, sam_info = muse_sam.sam_mask(image_path, box["box_full"], rf.image.shape[:2], job / "sam", job / "sam_ledger.jsonl")
            if mask is None:
                raise RuntimeError(f"SAM call failed: {sam_info}")
        with progress.stage("checks_and_svg"):
            outer, openings = muse_sam.outer_and_openings(mask)
            checks = quality.plausibility(outer, box["box_full"], sam_info.get("sam_box"))
            reasons = list(decision_reasons)
            if not box["answer"]["fully_visible"]:
                reasons.append("Muse reports the outline is cut off or hidden (a model statement, not a proof)")
            reasons += [f"check {f}" for f in checks["flags"]]
            cv2.imwrite(str(job / "overlay.jpg"), muse_sam.overlay(rf.image, outer, openings, box["box_full"]))
            ppm = rf.px_per_mm
            outline, deviation = draw.simplify(segment.boundary(outer), ppm, 0.2)
            openings_mm = [rf.px_to_mm(draw.simplify(o, ppm, 0.2)[0]).round(2).tolist() for o in openings]
            svg_spec = {"title": f"{spec['part']} ({spec['scope']}) - outline, unconfirmed",
                        "subtitle": f"{video.name} frame {picked.frame_index} ({picked.t_s} s); attachments {spec['attachments']}",
                        "legend": [f"Outline: SAM mask boundary, max deviation {deviation:.2f} mm, sheet plane; not part dimensions.",
                                   f"Opening candidates: {len(openings)}, unconfirmed, hidden layer 'opening_candidates'.",
                                   "Inner details: not extracted. Frame identity, view side and orientation need operator confirmation."]}
            (job / "drawing.svg").write_text(render_layered_svg(rf.px_to_mm(outline).round(2).tolist(), openings_mm, svg_spec), encoding="utf-8")
        result = {**base, "status": final_state(box["answer"]["fully_visible"], checks["flags"], decision), "reasons": reasons,
                  "checks": checks, "sam": sam_info, "outline_points": len(outline), "opening_candidates": len(openings),
                  "muse_latency_s": {"choose": meta["latency_s"], "box": box["latency_s"]}}
        progress.finish("done", result=result)
        return result
    except Exception as exc:  # every failure ends visibly; no silent stop
        progress.finish("failed", error=f"{type(exc).__name__}: {str(exc)[:300]}", usage=usage)
        raise


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
        print(json.dumps(status(args.job), indent=1))
    elif args.command == "start":
        print(json.dumps(start(args.spec, args.job)))
    else:
        args.job.mkdir(parents=True, exist_ok=True)
        print(json.dumps(run(json.loads(args.spec.read_text(encoding="utf-8")), args.job), indent=1, default=str))


if __name__ == "__main__":
    main()
