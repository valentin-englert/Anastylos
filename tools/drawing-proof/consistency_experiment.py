"""CONS-1: run-to-run consistency of Muse for the frame choice and the part box (plan specs/plan_cons1.json).

    python tools/drawing-proof/consistency_experiment.py --out C:\\tmp\\anastylos-drawing-proof\\cons1 [--prepare-only]

Reuses drawing_job._muse_choice (frozen, effort max) and muse_sam.muse_box (r4.2, effort high) unchanged. The run is
sequential, capped at 40 Muse calls including retries, and happens once: an existing log in --out stops it.
``--prepare-only`` rebuilds and checks the B1 contact sheets without any API call.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

import drawing_job
import effort_experiment
import frames
import muse_sam

HERE = Path(__file__).resolve().parent
B1_SPECS = HERE / "specs" / "batch_b1"
B1_RUNS = Path(r"C:\tmp\anastylos-drawing-proof\batch-b1")
JOBS = ["J1-back-shell", "J2-pcb-cable", "J3-front-shell"]
BOX_IMAGES = list(effort_experiment.PARTS)
FRAME_REPEATS, BOX_REPEATS = 5, 3
HARD_CAP = 40
RETRY_PAUSE_S = 5
MAX_SHEET_DIFF = 3.0  # mean absolute pixel difference to the saved B1 sheet (JPEG re-encode level)
BACK_WORDS, FRONT_WORDS = ("back", "rear", "bottom", "underside", "inside", "inner"), ("front", "top", "outer")


class BudgetExhausted(Exception):
    pass


# ---------------------------------------------------------------- pure analysis (pre-registered definitions)

def view_class(view_side: str) -> str:
    text = view_side.lower()
    back, front = any(w in text for w in BACK_WORDS), any(w in text for w in FRONT_WORDS)
    return "mixed" if back and front else "back" if back else "front" if front else "other"


def gate_outcome(spec: dict, choice: dict, chosen: list) -> tuple[str, int | None]:
    """What the pipeline does with the answer: NEEDS_CAPTURE, or frame_decision on the picked tile."""
    if not choice["usable"] or not 1 <= choice["tile"] <= len(chosen):
        return "NEEDS_CAPTURE", None
    return drawing_job.frame_decision(spec, choice, chosen[choice["tile"] - 1].t_s, None)[0], choice["tile"]


def modal(values: list) -> tuple[object, int, dict]:
    """(mode, its count, all counts); ``None`` for no value. Ties keep the first-seen value as mode."""
    counts = Counter(json.dumps(v) for v in values)
    if not counts:
        return None, 0, {}
    top, count = counts.most_common(1)[0]
    return json.loads(top), count, dict(counts)


def frame_level(tuple_count: int, state_count: int) -> str:
    if tuple_count == FRAME_REPEATS and state_count == FRAME_REPEATS:
        return "L0"
    if tuple_count >= 4 and state_count >= 4:
        return "L1"
    return "L2"


def pairwise_ious(boxes: list[list[int]]) -> list[float]:
    return [effort_experiment.iou(boxes[i], boxes[j]) for i in range(len(boxes)) for j in range(i + 1, len(boxes))]


def box_level(valid_boxes: list[list[int]], fully_visible: list[bool]) -> str:
    ious = pairwise_ious(valid_boxes)
    identical_visibility = len(set(fully_visible)) <= 1
    if len(valid_boxes) == BOX_REPEATS and identical_visibility and min(ious) >= 0.90:
        return "L0"
    if len(valid_boxes) >= 2 and identical_visibility and min(ious) >= 0.75:
        return "L1"
    return "L2"


def worst(levels: list[str]) -> str:
    return max(levels) if levels else "L2"


def summarise_frame_job(rows: list[dict]) -> dict:
    done = [r for r in rows if r["status"] == "ok"]
    _, t_count, t_counts = modal([[r["gate_outcome"], r["tile"]] for r in done])
    _, s_count, s_counts = modal([r["choice"]["state"] for r in done])
    _, g_count, g_counts = modal([r["gate_outcome"] for r in done])
    _, tile_count, tile_counts = modal([r["tile"] for r in done])
    _, v_count, v_counts = modal([r["view_class"] for r in done])
    return {"completed": f"{len(done)}/{FRAME_REPEATS}",
            "decision_tuple": {"modal": f"{t_count}/{FRAME_REPEATS}", "counts": t_counts},
            "gate_outcome": {"modal": f"{g_count}/{FRAME_REPEATS}", "counts": g_counts},
            "tile": {"modal": f"{tile_count}/{FRAME_REPEATS}", "counts": tile_counts},
            "state": {"modal": f"{s_count}/{FRAME_REPEATS}", "counts": s_counts},
            "view_class": {"modal": f"{v_count}/{FRAME_REPEATS}", "counts": v_counts},
            "usable": dict(Counter(str(r["choice"]["usable"]) for r in done)),
            "median_latency_s": statistics.median(r["latency_s"] for r in done) if done else None,
            "level": frame_level(t_count, s_count)}


def summarise_box_image(rows: list[dict]) -> dict:
    valid = [r for r in rows if r["status"] == "ok" and r["valid"]]
    boxes = [r["box"] for r in valid]
    ious = pairwise_ious(boxes) if len(boxes) >= 2 else []
    spread = [max(b[i] for b in boxes) - min(b[i] for b in boxes) for i in range(4)] if boxes else None
    return {"completed": f"{sum(r['status'] == 'ok' for r in rows)}/{BOX_REPEATS}", "valid": f"{len(valid)}/{BOX_REPEATS}",
            "fully_visible": [r["fully_visible"] for r in valid],
            "iou_min": round(min(ious), 4) if ious else None, "iou_median": round(statistics.median(ious), 4) if ious else None,
            "edge_spread_px": spread,
            "median_latency_s": statistics.median(r["latency_s"] for r in rows if r["status"] == "ok") if any(r["status"] == "ok" for r in rows) else None,
            "level": box_level(boxes, [r["fully_visible"] for r in valid]) if len(boxes) >= 2 else "L2"}


# ---------------------------------------------------------------- inputs

def build_sheet(job: str) -> dict:
    """B1 contact sheet rebuilt from the candidate and tile caches; aborts instead of decoding video."""
    spec = json.loads((B1_SPECS / f"{job}.json").read_text(encoding="utf-8"))
    b1 = json.loads((B1_RUNS / job / "progress.json").read_text(encoding="utf-8"))["result"]["candidates"]
    video = Path(spec["video"])
    sampled, cache = frames.cached_sample(video, spec["stride_s"], tuple(spec["t_range"]))
    if not cache["hit"]:
        raise RuntimeError(f"{job}: candidate index not cached ({cache['path']})")
    wanted = [c["frame"] for c in b1]
    chosen = [c for c in frames.select(sampled, spec["candidates"]) if c.frame_index in wanted]
    if [c.frame_index for c in chosen] != wanted:
        raise RuntimeError(f"{job}: candidates {[c.frame_index for c in chosen]} differ from B1 {wanted}")
    cache_dir = Path(cache["path"]).parent
    missing = [c.frame_index for c in chosen if not (cache_dir / f"tile-{c.frame_index}-{'_'.join(str(v) for v in spec['region_mm'])}-{spec['px_per_mm']}.png").exists()]
    if missing:
        raise RuntimeError(f"{job}: tiles not cached for frames {missing}; refusing to decode video")
    tiles, chosen, skipped = drawing_job._candidate_tiles(spec, video, chosen, cache_dir)
    sheet = frames.contact_sheet(tiles)
    saved = cv2.imread(str(B1_RUNS / job / "contact_sheet.jpg"))
    if saved is None or saved.shape != sheet.shape:
        raise RuntimeError(f"{job}: sheet shape {sheet.shape} differs from saved B1 sheet {None if saved is None else saved.shape}")
    diff = float(np.mean(cv2.absdiff(sheet, saved)))
    if diff > MAX_SHEET_DIFF or skipped:
        raise RuntimeError(f"{job}: sheet differs from saved B1 sheet (mean abs diff {diff:.2f}, skipped {skipped})")
    return {"job": job, "spec": spec, "sheet": sheet, "chosen": chosen, "sheet_mean_abs_diff": round(diff, 3),
            "tiles": [{"tile": i, "frame": c.frame_index, "t_s": c.t_s} for i, c in enumerate(chosen, 1)]}


# ---------------------------------------------------------------- calls

class Runner:
    def __init__(self, key: str, log_path: Path):
        self.key, self.log_path, self.used = key, log_path, 0

    def scrub(self, text: str) -> str:
        return text.replace(self.key, "***")

    def log(self, **fields) -> None:
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(self.scrub(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), **fields}, default=str)) + "\n")

    def call(self, decision: str, unit: str, repeat: int, fn) -> tuple[object | None, str, list[dict]]:
        """fn() once, plus at most one retry after an error; returns (result, status, attempts)."""
        attempts = []
        for attempt in (1, 2):
            if self.used >= HARD_CAP:
                self.log(decision=decision, unit=unit, repeat=repeat, event="not_run", reason="hard cap reached")
                return None, "not_run", attempts
            self.used += 1
            began = time.perf_counter()
            try:
                result = fn()
            except Exception as exc:  # recorded; one retry at most
                record = {"attempt": attempt, "error": self.scrub(f"{type(exc).__name__}: {str(exc)[:160]}"),
                          "wall_s": round(time.perf_counter() - began, 2)}
                attempts.append(record)
                self.log(decision=decision, unit=unit, repeat=repeat, **record)
                if attempt == 1:
                    time.sleep(RETRY_PAUSE_S)
                continue
            attempts.append({"attempt": attempt, "wall_s": round(time.perf_counter() - began, 2)})
            return result, "ok", attempts
        return None, "failed", attempts


def frame_row(runner: Runner, prepared: dict, repeat: int) -> dict:
    def fn():
        choice, meta, prompt_sha = drawing_job._muse_choice(prepared["sheet"], prepared["chosen"], prepared["spec"], runner.key)
        if set(choice) != set(drawing_job.CHOICE_SCHEMA["required"]) or choice["state"] not in drawing_job.STATES or not isinstance(choice["tile"], int):
            raise ValueError("answer violates CHOICE_SCHEMA")
        return choice, meta, prompt_sha
    result, status, attempts = runner.call("frame_choice", prepared["job"], repeat, fn)
    row = {"decision": "frame_choice", "unit": prepared["job"], "repeat": repeat, "status": status, "attempts": attempts}
    if result:
        choice, meta, prompt_sha = result
        outcome, tile = gate_outcome(prepared["spec"], choice, prepared["chosen"])
        row.update(choice=choice, latency_s=meta["latency_s"], prompt_sha256=prompt_sha, gate_outcome=outcome, tile=tile,
                   view_class=view_class(choice["view_side"]))
    runner.log(**{k: v for k, v in row.items() if k != "attempts"})
    return row


def box_row(runner: Runner, image: np.ndarray, name: str, repeat: int) -> dict:
    result, status, attempts = runner.call("part_box", name, repeat, lambda: muse_sam.muse_box(image, effort_experiment.PARTS[name], runner.key))
    row = {"decision": "part_box", "unit": name, "repeat": repeat, "status": status, "attempts": attempts}
    if result:
        usage = result.get("usage") or {}
        row.update(valid=result["valid"], box=result["box_full"], fully_visible=result["answer"]["fully_visible"], found=result["answer"]["found"],
                   latency_s=result["latency_s"], effort_returned=result["effort_returned"], output_tokens=usage.get("output_tokens"),
                   reasoning_tokens=(usage.get("output_tokens_details") or {}).get("reasoning_tokens"), reason=result["answer"]["reason"])
    runner.log(**{k: v for k, v in row.items() if k != "attempts"})
    return row


def box_overlay(image: np.ndarray, rows: list[dict]) -> np.ndarray:
    view = image.copy()
    thickness = max(2, round(max(image.shape[:2]) / 400))
    for row, colour in zip(rows, ((255, 160, 0), (0, 200, 0), (0, 0, 230))):
        if row.get("valid"):
            cv2.rectangle(view, tuple(row["box"][:2]), tuple(row["box"][2:]), colour, thickness)
    scale = min(1.0, 1200 / max(view.shape[:2]))
    return cv2.resize(view, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)


# ---------------------------------------------------------------- run

def run(out: Path, prepare_only: bool) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    prepared = {job: build_sheet(job) for job in JOBS}
    images = {name: cv2.imread(str(effort_experiment.rectified(name))) for name in BOX_IMAGES}
    if any(img is None for img in images.values()):
        raise RuntimeError("a box image is missing")
    for job, p in prepared.items():
        cv2.imwrite(str(out / f"sheet_{job}.jpg"), p["sheet"], [cv2.IMWRITE_JPEG_QUALITY, 88])
        print(json.dumps({"job": job, "tiles": p["tiles"], "sheet_mean_abs_diff": p["sheet_mean_abs_diff"]}), flush=True)
    if prepare_only:
        return {"prepared": {job: p["tiles"] for job, p in prepared.items()}}
    log_path = out / "cons1.log"
    if log_path.exists():
        raise SystemExit(f"{log_path} exists: CONS-1 runs once (plan run_once)")
    runner = Runner(muse_sam.client.load_key(), log_path)
    runner.log(event="start", plan="specs/plan_cons1.json", hard_cap=HARD_CAP)
    frame_rows = [frame_row(runner, prepared[job], repeat) for repeat in range(1, FRAME_REPEATS + 1) for job in JOBS]
    box_rows = [box_row(runner, images[name], name, repeat) for repeat in range(1, BOX_REPEATS + 1) for name in BOX_IMAGES]
    summary = {
        "frame_choice": {job: summarise_frame_job([r for r in frame_rows if r["unit"] == job]) for job in JOBS},
        "part_box": {name: summarise_box_image([r for r in box_rows if r["unit"] == name]) for name in BOX_IMAGES}}
    summary["frame_choice_verdict"] = worst([s["level"] for s in summary["frame_choice"].values()])
    summary["part_box_verdict"] = worst([s["level"] for s in summary["part_box"].values()])
    for name in BOX_IMAGES:
        cv2.imwrite(str(out / f"overlay_{name}.jpg"), box_overlay(images[name], [r for r in box_rows if r["unit"] == name]))
    calls = [a for r in frame_rows + box_rows for a in r["attempts"]]
    usage = {"muse_calls": runner.used, "planned": len(frame_rows) + len(box_rows), "retries": sum(a["attempt"] == 2 for a in calls),
             "failed_calls": sum(r["status"] != "ok" for r in frame_rows + box_rows), "cap": HARD_CAP}
    result = {"plan": "specs/plan_cons1.json", "usage": usage, "sheets": {job: {"tiles": p["tiles"], "sheet_mean_abs_diff": p["sheet_mean_abs_diff"]}
                                                                       for job, p in prepared.items()},
              "frame_rows": frame_rows, "box_rows": box_rows, "summary": summary}
    (out / "cons1.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    runner.log(event="done", **usage)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--prepare-only", action="store_true", help="rebuild and check the sheets; no API call")
    args = parser.parse_args()
    result = run(args.out, args.prepare_only)
    print(json.dumps(result.get("summary") or result, indent=1, default=str))


if __name__ == "__main__":
    main()
