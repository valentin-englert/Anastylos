"""Gate 1B: cadence comparison + motion/custody evidence (1/5/10 fps).

Tests whether temporal continuity reveals source-to-destination
transport that 1 fps appearance matching cannot. Same physical
windows (from FROZEN Gate 1 candidates.json -- machine output, never
human labels) analyzed at three cadences with identical criteria.

Evidence levels per window (never auto-confirmed):
  visual_change -> interaction_candidate -> transport_candidate /
  deposit_candidate -> correspondence_proposal. confirmed_identity is
  NEVER assigned. Ambiguity abstains explicitly; competing tracks are
  all preserved.

Frozen a priori (see header constants): window cap 60 s (inert here:
longest Gate 1 window is 58 s), track speed/gap/path/duration gates,
deposit persistence rule, spatial link dilation. Gate 1 thresholds
are reused unchanged via import, never lowered.

Coordinates: display orientation 2160x3840 origin top-left everywhere
in outputs. Analysis at half (x2) and quarter (x4) resolution.

Usage:
    python gate1b_run.py --video <mp4> --candidates <frozen json> ^
        --frames1 <frames-1fps> --frames10 <gate1b/frames10> ^
        --out <gate1b/run1>
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from pathlib import Path

try:
    import cv2
    import numpy as np
except ImportError as exc:  # pragma: no cover
    sys.exit(f"gate1b_run needs OpenCV + NumPy: {exc}")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate1_run as g1

SCHEMA = "snes01-gate1b/motion@1"
HALF = 2          # analysis-half px * 2 = display-full px
QUARTER = 4       # quarter px * 4 = display-full px

CADENCES = (1, 5, 10)
WINDOW_CAP_S = 60  # per-window cap; inert for the Gate 1 windows

# Hand blobs: connected skin components below this quarter-px area are
# noise (a hand at 540x960 is tens of thousands of px).
SKIN_MIN_AREA_Q = 2000
# Greedy tracklet linking: max centroid speed between frames.
TRACK_MAX_SPEED_QPX_S = 300.0
# Bridge gaps (occlusion/blink) up to this many frames; longer gaps
# split the tracklet. Gaps are recorded, never hidden.
TRACK_MAX_GAP = 2
# Interaction: >= this many consecutive frames with the tracklet
# centroid inside the (slightly dilated) assembly ROI.
INTERACT_MIN_FRAMES = 3
INTERACT_DILATE_QPX = 20
# Transport: starts inside, ends outside, moves enough, lasts long.
TRANSPORT_MIN_PATH_QPX = 100.0
TRANSPORT_MIN_DURATION_S = 1.0
# Deposit: workspace change pixels still changed (fraction >= this)
# in at least half of the last-quartile frames.
DEPOSIT_PIXEL_FRAC = 0.3
DEPOSIT_FRAME_SHARE = 0.5
# Spatial link: track endpoint/start must fall inside the deposit /
# Gate-1 source box dilated by this (quarter px).
LINK_DILATE_QPX = 40


def _die(message: str):
    print(f"gate1b_run: ERROR: {message}", file=sys.stderr)
    sys.exit(2)


def load_candidates(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _die(f"cannot read candidates {path}: {exc}")
    if doc.get("schema") != g1.SCHEMA:
        _die("candidates schema is not the frozen Gate 1 schema")
    return doc


def select_windows(doc: dict) -> list:
    """All Gate 1 events with persistent regions, longest-first cap
    rule applied deterministically. No human labels consulted."""
    windows = []
    for event in doc.get("events", []):
        if not event.get("persistent_regions"):
            continue
        start, end = event["before_t"], event["after_t"]
        if end - start > WINDOW_CAP_S:
            end = start + WINDOW_CAP_S
        windows.append({
            "id": event["id"],
            "start_t": start,
            "end_t": end,
            "before_frame": event["before_frame"],
            "after_frame": event["after_frame"],
            "gate1_sources": [
                r["bbox_xywh"] for r in event["persistent_regions"]
                if r["role"] == "disappearance"],
        })
    return windows


def load_index(frames10: Path) -> dict:
    path = frames10 / "frames10_index.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _die(f"cannot read frames10 index: {exc}")


def window_frames_10(index: dict, start: float, end: float) -> list:
    """Index entries with start <= pts <= end. Deterministic."""
    return [f for f in index["frames"]
            if start - 1e-6 <= f["pts_time_s"] <= end + 1e-6]


def subsample(entries: list, step: int) -> list:
    return entries[::step]


def window_frames_1(frames1: Path, start: int, end: int) -> list:
    """Existing 1 fps frames with start <= N-1 <= end. Nominal PTS."""
    out = []
    for second in range(start, end + 1):
        name = f"frame_{second + 1:04d}.jpg"
        if (frames1 / name).is_file():
            out.append({"file": name, "pts_time_s": float(second)})
    return out


def read_half(path: Path):
    image = cv2.imread(str(path))
    if image is None:
        _die(f"cannot decode {path}")
    height, width = image.shape[:2]
    if (width, height) == (1080, 1920):
        return image
    if (width, height) == (2160, 3840):
        return cv2.resize(image, (1080, 1920),
                          interpolation=cv2.INTER_AREA)
    _die(f"unexpected frame size {width}x{height}: {path}")


def to_quarter(half):
    """Half (1080x1920) -> quarter of display-full (540x960)."""
    return cv2.resize(half, (540, 960), interpolation=cv2.INTER_AREA)


def hand_blobs(quarter) -> list:
    """Skin connected components: centroid + bbox, quarter px."""
    mask = g1.skin_mask(quarter)
    count, _, stats, centroids = cv2.connectedComponentsWithStats(
        mask, connectivity=8)
    blobs = []
    for label in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[label])
        if area < SKIN_MIN_AREA_Q:
            continue
        blobs.append({"centroid": [float(centroids[label][0]),
                                   float(centroids[label][1])],
                      "bbox": [x, y, w, h], "area": area})
    blobs.sort(key=lambda b: -b["area"])
    return blobs


def link_tracklets(per_frame_blobs: list, dt_s: float) -> list:
    """Greedy nearest-centroid linking with gap bridging. Returns
    tracklets: ordered observations + gap/continuity records."""
    max_step = TRACK_MAX_SPEED_QPX_S * dt_s
    open_tracks, done = [], []
    for frame_pos, blobs in enumerate(per_frame_blobs):
        claimed = set()
        for track in open_tracks:
            best, best_dist = None, None
            for num, blob in enumerate(blobs):
                if num in claimed:
                    continue
                dist = float(np.hypot(
                    blob["centroid"][0] - track["observations"][-1][1][0],
                    blob["centroid"][1] - track["observations"][-1][1][1]))
                gap_mul = frame_pos - track["observations"][-1][0]
                if dist <= max_step * gap_mul and (
                        best_dist is None or dist < best_dist):
                    best, best_dist = num, dist
            if best is None:
                continue
            claimed.add(best)
            gap = frame_pos - track["observations"][-1][0] - 1
            track["gaps"] += gap
            track["observations"].append(
                (frame_pos, blobs[best]["centroid"], blobs[best]["bbox"]))
            track["step_dist"].append(best_dist)
        for num, blob in enumerate(blobs):
            if num not in claimed:
                open_tracks.append(
                    {"observations": [(frame_pos, blob["centroid"],
                                       blob["bbox"])],
                     "gaps": 0, "step_dist": []})
        still_open = []
        for track in open_tracks:
            if frame_pos - track["observations"][-1][0] > TRACK_MAX_GAP:
                done.append(track)
            else:
                still_open.append(track)
        open_tracks = still_open
    done.extend(open_tracks)
    return done


def tracklet_record(track: dict, entries: list) -> dict:
    obs = track["observations"]
    first, last = obs[0][0], obs[-1][0]
    start_t = entries[first]["pts_time_s"]
    end_t = entries[last]["pts_time_s"]
    path_len = round(float(sum(track["step_dist"])), 1)
    return {
        "start_t": start_t, "end_t": end_t,
        "duration_s": round(end_t - start_t, 2),
        "n_obs": len(obs), "gaps": track["gaps"],
        "path_length_qpx": path_len,
        "path": [{"frame": entries[pos]["file"], "pts_time_s": pt,
                  "centroid_qpx": [round(c[0], 1), round(c[1], 1)]}
                 for (pos, c, _), pt in
                 zip(obs, [entries[p]["pts_time_s"] for p, _, _ in obs])],
    }


def _roi_q() -> list:
    return [v // QUARTER for v in g1.ASSEMBLY_ROI]


def _dilate(box, pad: int) -> list:
    return [box[0] - pad, box[1] - pad, box[2] + 2 * pad, box[3] + 2 * pad]


def interaction(track: dict) -> dict:
    """INTERACTION_CANDIDATE if the centroid sits inside the assembly
    ROI for >= INTERACT_MIN_FRAMES consecutive observations."""
    roi = _dilate(_roi_q(), INTERACT_DILATE_QPX)
    run, best = 0, 0
    for node in track["path"]:
        run = run + 1 if g1.point_in_box(node["centroid_qpx"], roi) else 0
        best = max(best, run)
    return {"interaction_candidate": best >= INTERACT_MIN_FRAMES,
            "max_consecutive_roi_frames": best}


def transport(track: dict) -> dict:
    """TRANSPORT_CANDIDATE if the path starts inside, ends outside,
    moves enough, and lasts long enough. Says nothing about what, if
    anything, the hand carried."""
    roi = _roi_q()
    path = track["path"]
    first_q = path[:max(1, len(path) // 4)]
    last_q = path[-max(1, len(path) // 4):]
    starts_in = any(g1.point_in_box(n["centroid_qpx"], roi)
                    for n in first_q)
    ends_out = any(not g1.point_in_box(n["centroid_qpx"], roi)
                   for n in last_q)
    long_enough = track["path_length_qpx"] >= TRANSPORT_MIN_PATH_QPX
    slow_enough = track["duration_s"] >= TRANSPORT_MIN_DURATION_S
    ok = starts_in and ends_out and long_enough and slow_enough
    reasons = []
    if not starts_in:
        reasons.append("never starts inside assembly ROI")
    if not ends_out:
        reasons.append("never leaves assembly ROI")
    if not long_enough:
        reasons.append("path too short")
    if not slow_enough:
        reasons.append("duration under 1 s")
    return {"transport_candidate": ok, "reasons": reasons}


def deposits(halves: list, grays: list) -> list:
    """DEPOSIT_CANDIDATEs: workspace change components (start-vs-end)
    that persist across the last quartile of frames."""
    boxes, _ = g1.change_components(grays[0], grays[-1])
    height, width = grays[0].shape[:2]
    full_w, full_h = width * HALF, height * HALF
    last_q = grays[max(1, 3 * len(grays) // 4):] or grays[-1:]
    out = []
    for num, box in enumerate(boxes):
        full = g1.clip_box([v * HALF for v in box], full_w, full_h)
        if g1.point_in_box(g1.centroid(full), g1.ASSEMBLY_ROI):
            continue
        kept = 0
        for gray in last_q:
            diff = cv2.absdiff(grays[0], gray)
            _, mask = cv2.threshold(
                diff, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            x, y, w, h = box
            frac = float(np.mean(mask[y:y + h, x:x + w] > 0))
            kept += frac >= DEPOSIT_PIXEL_FRAC
        if kept >= DEPOSIT_FRAME_SHARE * len(last_q):
            out.append({"bbox_xywh": full,
                        "persist_share": round(kept / len(last_q), 2)})
    return out


def proposals(tracks: list, deposit_list: list,
              gate1_sources: list) -> tuple[list, str]:
    """CORRESPONDENCE_PROPOSALs: transport endpoint near a deposit +
    start near a frozen Gate 1 source box. Ranked; all kept."""
    ranked = []
    for track in tracks:
        if not track["transport"]["transport_candidate"]:
            continue
        start = track["path"][0]["centroid_qpx"]
        end = track["path"][-1]["centroid_qpx"]
        src_hit = any(
            g1.point_in_box(
                start, _dilate([v // QUARTER for v in box],
                               LINK_DILATE_QPX))
            for box in gate1_sources)
        for dep in deposit_list:
            dep_q = [v // QUARTER for v in dep["bbox_xywh"]]
            if not g1.point_in_box(
                    end, _dilate(dep_q, LINK_DILATE_QPX)):
                continue
            dist = float(np.hypot(
                end[0] - (dep_q[0] + dep_q[2] / 2),
                end[1] - (dep_q[1] + dep_q[3] / 2)))
            ranked.append({
                "track_id": track["id"],
                "deposit_box_xywh": dep["bbox_xywh"],
                "src_gate1_linked": src_hit,
                "endpoint_dist_qpx": round(dist, 1),
                "uncertainty": "hand path only; carried object not "
                               "observed" + ("" if src_hit else
                               "; start outside Gate 1 source boxes"),
            })
    ranked.sort(key=lambda r: (not r["src_gate1_linked"],
                               r["endpoint_dist_qpx"]))
    if not tracks:
        return [], "no hand tracklets: hands absent or unsegmentable"
    if not any(t["transport"]["transport_candidate"] for t in tracks):
        return [], "tracks exist but none leaves the assembly ROI"
    if not deposit_list:
        return [], "transport without persistent deposit"
    if not ranked:
        return [], "transport endpoint far from every deposit"
    return ranked, ""


def analyze_window(entries: list, frame_dir: Path, window: dict,
                   fps: int) -> dict:
    halves = [read_half(frame_dir / e["file"]) for e in entries]
    grays = [cv2.GaussianBlur(cv2.cvtColor(h, cv2.COLOR_BGR2GRAY), (5, 5), 0)
             for h in halves]
    quarters = [to_quarter(h) for h in halves]
    dt_s = 1.0 / fps
    blobs = [hand_blobs(q) for q in quarters]
    raw_tracks = link_tracklets(blobs, dt_s)
    tracks = []
    for num, raw in enumerate(raw_tracks):
        rec = tracklet_record(raw, entries)
        rec["id"] = f"{window['id']}-f{fps}-t{num}"
        rec["interaction"] = interaction(rec)
        rec["transport"] = transport(rec)
        tracks.append(rec)
    deposit_list = deposits(halves, grays) if len(grays) > 1 else []
    ranked, abstain = proposals(tracks, deposit_list,
                               window["gate1_sources"])
    covered = {p["frame"] for t in tracks for p in t["path"]}
    return {
        "fps": fps, "n_frames": len(entries),
        "t_start": entries[0]["pts_time_s"],
        "t_end": entries[-1]["pts_time_s"],
        "n_tracklets": len(tracks),
        "n_interactions": sum(
            1 for t in tracks if t["interaction"]["interaction_candidate"]),
        "n_transports": sum(
            1 for t in tracks if t["transport"]["transport_candidate"]),
        "n_deposits": len(deposit_list),
        "continuity_coverage": round(len(covered) / max(1, len(entries)), 3),
        "tracks": tracks,
        "deposits": deposit_list,
        "proposals": ranked,
        "abstain_reason": abstain if not ranked else "",
    }


def render_detail(windows: dict, frame_dirs: dict, out: Path):
    """10 fps rows: start/mid/end thumbs + deposits + best track."""
    rows = []
    for wid, arms in windows.items():
        arm = arms[10]
        thumbs = []
        picks = [0, len(arm["entries"]) // 2, len(arm["entries"]) - 1]
        for pos in picks:
            entry = arm["entries"][pos]
            canvas = read_half(frame_dirs[10] / entry["file"]).copy()
            for dep in arm["deposits"]:
                x, y, w, h = (v // HALF for v in dep["bbox_xywh"])
                cv2.rectangle(canvas, (x, y), (x + w, y + h),
                              (255, 0, 0), 2)
            for track in arm["tracks"]:
                pts = [(int(n["centroid_qpx"][0] * QUARTER // HALF),
                        int(n["centroid_qpx"][1] * QUARTER // HALF))
                       for n in track["path"]]
                color = (0, 255, 0) if track["transport"][
                    "transport_candidate"] else (128, 128, 128)
                for a, b in zip(pts, pts[1:]):
                    cv2.line(canvas, a, b, color, 2)
            cv2.putText(canvas, f"{wid} {entry['file']} "
                                f"t={entry['pts_time_s']:.1f}s", (8, 24),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            thumbs.append(cv2.resize(canvas, (540, 960)))
        rows.append(np.hstack(thumbs))
    cv2.imwrite(str(out), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 85])


def render_compare(windows: dict, frame_dirs: dict, out: Path):
    """12 rows x 3 cadences: start/end pairs with proposal counts."""
    rows = []
    for wid, arms in windows.items():
        cells = []
        for fps in CADENCES:
            arm = arms[fps]
            pair = []
            for entry in (arm["entries"][0], arm["entries"][-1]):
                canvas = read_half(frame_dirs[fps] / entry["file"])
                canvas = cv2.resize(canvas, (270, 480))
                pair.append(canvas)
            cell = np.hstack(pair)
            cv2.putText(cell, f"{wid} {fps}fps P={len(arm['proposals'])} "
                              f"T={arm['n_transports']}", (6, 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
            cells.append(cell)
        rows.append(np.hstack(cells))
    cv2.imwrite(str(out), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 85])


def render_strips(windows: dict, frame_dirs: dict, out_dir: Path,
                  max_frames: int = 8):
    """One strip per proposal: evenly spaced frames along its track
    with centroid dots + linked deposit box. Deterministic."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for wid, arms in windows.items():
        for fps in CADENCES:
            arm = arms[fps]
            by_id = {t["id"]: t for t in arm["tracks"]}
            for num, prop in enumerate(arm["proposals"]):
                track = by_id[prop["track_id"]]
                path = track["path"]
                step = max(1, len(path) // max_frames)
                picks = path[::step][:max_frames]
                cells = []
                dep = [v // HALF for v in prop["deposit_box_xywh"]]
                for node in picks:
                    canvas = read_half(
                        frame_dirs[fps] / node["frame"]).copy()
                    x, y, w, h = dep
                    cv2.rectangle(canvas, (x, y), (x + w, y + h),
                                  (255, 0, 0), 3)
                    cx = int(node["centroid_qpx"][0] * QUARTER // HALF)
                    cy = int(node["centroid_qpx"][1] * QUARTER // HALF)
                    cv2.circle(canvas, (cx, cy), 10, (0, 255, 0), -1)
                    cv2.putText(
                        canvas,
                        f"{node['frame']} t={node['pts_time_s']:.1f}s",
                        (8, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                        (255, 255, 255), 2)
                    cells.append(cv2.resize(canvas, (270, 480)))
                strip = np.hstack(cells)
                (out_dir / f"{prop['track_id']}-p{num}.jpg").write_bytes(
                    cv2.imencode(".jpg", strip,
                                 [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes())


def write_review_csv(windows: dict, out: Path):
    with open(out, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["window_id", "cadence_fps", "candidate_kind",
                         "candidate_id", "t_start_s", "t_end_s",
                         "path_length_display_px", "gaps", "n_obs",
                         "deposit_box_display_xywh", "src_gate1_linked",
                         "auto_level", "auto_note", "operator_verdict",
                         "operator_note"])
        for wid, arms in windows.items():
            for fps in CADENCES:
                arm = arms[fps]
                for track in arm["tracks"]:
                    if not track["transport"]["transport_candidate"]:
                        continue
                    writer.writerow([
                        wid, fps, "transport", track["id"],
                        track["start_t"], track["end_t"],
                        round(track["path_length_qpx"] * QUARTER, 1),
                        track["gaps"], track["n_obs"], "", "",
                        "transport_candidate",
                        "hand path only; carried object not observed",
                        "", ""])
                for num, prop in enumerate(arm["proposals"]):
                    writer.writerow([
                        wid, fps, "correspondence",
                        f"{prop['track_id']}-p{num}",
                        "", "", "", "", "",
                        prop["deposit_box_xywh"],
                        prop["src_gate1_linked"],
                        "correspondence_proposal", prop["uncertainty"],
                        "", ""])


def summarize(windows: dict, timings: dict) -> dict:
    per_fps = {}
    for fps in CADENCES:
        arms = [arms[fps] for arms in windows.values()]
        abstains: dict[str, int] = {}
        for arm in arms:
            if not arm["proposals"]:
                key = arm["abstain_reason"] or "unknown"
                abstains[key] = abstains.get(key, 0) + 1
        per_fps[str(fps)] = {
            "windows": len(arms),
            "frames_total": sum(a["n_frames"] for a in arms),
            "tracklets": sum(a["n_tracklets"] for a in arms),
            "interactions": sum(a["n_interactions"] for a in arms),
            "transports": sum(a["n_transports"] for a in arms),
            "deposits": sum(a["n_deposits"] for a in arms),
            "proposals": sum(len(a["proposals"]) for a in arms),
            "windows_with_proposal": sum(
                1 for a in arms if a["proposals"]),
            "abstention_rate": round(
                sum(1 for a in arms if not a["proposals"])
                / max(1, len(arms)), 3),
            "abstain_reasons": abstains,
            "mean_continuity_coverage": round(
                sum(a["continuity_coverage"] for a in arms)
                / max(1, len(arms)), 3),
            "compute_s": round(timings.get(fps, 0.0), 1),
        }
    return per_fps


def render_comparison_md(summary: dict) -> str:
    lines = ["# Gate 1B cadence comparison (1 / 5 / 10 fps)",
             "",
             "Same windows, same criteria. Proposals are machine",
             "candidates, never confirmed identities. Correspondence",
             "accuracy is NOT YET EVALUATED (no sealed GT).",
             "",
             "| metric | 1 fps | 5 fps | 10 fps |",
             "|---|---|---|---|"]
    for metric in ("windows", "frames_total", "tracklets",
                   "interactions", "transports", "deposits",
                   "proposals", "windows_with_proposal",
                   "abstention_rate", "mean_continuity_coverage",
                   "compute_s"):
        row = " | ".join(str(summary[str(f)][metric]) for f in CADENCES)
        lines.append(f"| {metric} | {row} |")
    lines += ["", "## Abstain reasons per cadence", ""]
    for fps in CADENCES:
        lines.append(f"### {fps} fps")
        for reason, count in summary[str(fps)][
                "abstain_reasons"].items():
            lines.append(f"- ({count}x) {reason}")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Gate 1B cadence/motion experiment.")
    parser.add_argument("--video", required=True)
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--frames1", required=True)
    parser.add_argument("--frames10", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    doc = load_candidates(Path(args.candidates))
    index = load_index(Path(args.frames10))
    frames1 = Path(args.frames1)
    frame_dirs = {1: frames1, 5: Path(args.frames10), 10: Path(args.frames10)}
    windows_sel = select_windows(doc)
    windows: dict[str, dict] = {}
    timings: dict[int, float] = {}
    for window in windows_sel:
        arms = {}
        full10 = window_frames_10(index, window["start_t"], window["end_t"])
        if not full10:
            _die(f"window {window['id']}: no 10 fps frames in range")
        for fps in CADENCES:
            if fps == 1:
                entries = window_frames_1(
                    frames1, int(window["start_t"]), int(window["end_t"]))
            elif fps == 5:
                entries = subsample(full10, 2)
            else:
                entries = full10
            if len(entries) < 2:
                _die(f"window {window['id']} f{fps}: "
                     f"only {len(entries)} frames")
            begun = time.monotonic()
            arm = analyze_window(entries, frame_dirs[fps], window, fps)
            timings[fps] = timings.get(fps, 0.0) + (
                time.monotonic() - begun)
            arm["entries"] = entries
            arms[fps] = arm
        windows[window["id"]] = arms
    summary = summarize(windows, timings)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    motion = {
        "schema": SCHEMA,
        "video": args.video,
        "video_sha256": hashlib.sha256(
            Path(args.video).read_bytes()).hexdigest(),
        "candidates": args.candidates,
        "candidates_sha256": hashlib.sha256(
            Path(args.candidates).read_bytes()).hexdigest(),
        "frames10_index_sha256": hashlib.sha256(
            (Path(args.frames10)
             / "frames10_index.json").read_bytes()).hexdigest(),
        "params": {
            "cadences_fps": list(CADENCES),
            "window_cap_s": WINDOW_CAP_S,
            "skin_min_area_qpx": SKIN_MIN_AREA_Q,
            "track_max_speed_qpx_s": TRACK_MAX_SPEED_QPX_S,
            "track_max_gap": TRACK_MAX_GAP,
            "interact_min_frames": INTERACT_MIN_FRAMES,
            "transport_min_path_qpx": TRANSPORT_MIN_PATH_QPX,
            "transport_min_duration_s": TRANSPORT_MIN_DURATION_S,
            "deposit_pixel_frac": DEPOSIT_PIXEL_FRAC,
            "deposit_frame_share": DEPOSIT_FRAME_SHARE,
            "link_dilate_qpx": LINK_DILATE_QPX,
        },
        "summary": summary,
        "windows": windows,
    }
    (out / "motion.json").write_text(json.dumps(motion, indent=2),
                                     encoding="utf-8")
    (out / "comparison.json").write_text(json.dumps(summary, indent=2),
                                         encoding="utf-8")
    (out / "comparison.md").write_text(render_comparison_md(summary),
                                       encoding="utf-8")
    render_detail(windows, frame_dirs, out / "sheet_detail_10fps.jpg")
    render_compare(windows, frame_dirs, out / "sheet_compare.jpg")
    render_strips(windows, frame_dirs, out / "strips")
    write_review_csv(windows, out / "review.csv")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

