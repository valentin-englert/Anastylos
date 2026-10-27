"""Gate 1: sparse removal-event detection + source/destination correspondence.

Offline perception experiment on the real SNES-01 1fps frames. Detects
persistent visual changes between stable frames, proposes disappearance
(assembly area) and appearance (workspace) boxes, and ranks sparse
correspondences from appearance + temporal evidence.

INPUT ALLOWLIST (experimental separation): this script opens ONLY the
paths given via --frames-dir (frame_*.jpg), --manifest
(source_manifest.json: technical metadata + per-frame hashes), and
writes ONLY to --out. It never reads event_candidates.json,
stable_states.json, review.md, or any ground-truth file. Timestamps,
labels, boxes, or verdicts from prior analysis are not detection
inputs.

All thresholds below are fixed a priori (documented rationale, no
tuning against known event windows). Scores are uncalibrated ranking
signals, not probabilities. Nothing becomes confirmed: outputs are
candidates with explicit uncertainty.

Usage (needs OpenCV + NumPy, e.g. the shared calibration venv):

    python gate1_run.py --frames-dir C:\\ANASTYLOS\\data\\snes-01\\frames-1fps ^
        --manifest C:\\ANASTYLOS\\data\\snes-01\\benchmark-v1\\source_manifest.json ^
        --out C:\\ANASTYLOS\\data\\snes-01\\gate1-exp1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import typing
from pathlib import Path

try:
    import cv2
    import numpy as np
except ImportError as exc:  # pragma: no cover
    sys.exit(f"gate1_run needs OpenCV + NumPy: {exc}")

SCHEMA = "snes01-gate1/candidates@1"
MANIFEST_SCHEMA = "snes01-benchmark/manifest@1"
MAPPING_RULE = "frame_N corresponds to video time t=(N-1) seconds"

# Work at half resolution for differencing; boxes are scaled back to
# full 2160x3840 source pixels for all outputs.
DOWNSCALE = 2

# Assembly ROI in FULL-res px [x, y, w, h]: the dashed work rectangle
# read off frame_0001 (t=0) only. Everything outside it is workspace
# (tray cells, bench). Controller parts originate inside; deposits
# appear outside. Fixed before the run; not derived from event labels.
ASSEMBLY_ROI = [350, 1680, 1080, 1500]

# Stable frame: consecutive change below the 25th percentile on BOTH
# sides. Relative (label-free) definition of "quiet" for this video.
STABLE_PERCENTILE = 25
# A transition needs >= 2 changing frames between stable frames, so a
# single-frame glitch never becomes an event.
MIN_TRANSITION_FRAMES = 2
# Connected-component gates on the half-res diff mask: min area and
# min mean gray-level change. Reject JPEG shimmer, keep part-sized
# blobs (a D-pad is ~100x100 full px = 2500 half px; 400 is lenient).
MIN_COMPONENT_PX = 400
MIN_COMPONENT_MEAN = 15.0
# Persistence: fraction of a component's pixels still changed one
# frame after the after-frame (mask dilated for robustness). Kills
# transient hand pass-throughs while keeping real deposits.
PERSIST_DILATE_PX = 15
PERSIST_FRACTION = 0.30
# Hand mask: classic fixed YCrCb skin ranges. A component with skin
# covering most of its crop in before OR after frame is excluded from
# correspondence (occluded), never force-matched.
SKIN_CR = (133, 173)
SKIN_CB = (77, 127)
OCCLUDE_FRACTION = 0.50
# Pose check (ORB homography, assembly ROI, before->after): too few
# matches or too few inliers means rotation/view change; boxes stay in
# their own frame coordinates and no cross-frame transfer is attempted.
ORB_FEATURES = 1000
POSE_MIN_MATCHES = 10
POSE_MIN_INLIER_RATIO = 0.30
# Correspondence score = 0.4*color + 0.4*keypoints + 0.2*size.
# Operating point 0.45 is an uncalibrated midpoint; report
# abstentions, do not lower it to manufacture matches.
W_COLOR, W_KP, W_SIZE = 0.4, 0.4, 0.2
ABSTAIN_SCORE = 0.45


def _die(message: str) -> "typing.NoReturn":
    print(f"gate1: ERROR: {message}", file=sys.stderr)
    sys.exit(2)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest(path: Path) -> dict:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _die(f"cannot read manifest {path}: {exc}")
    if manifest.get("schema") != MANIFEST_SCHEMA:
        _die(f"manifest schema must be {MANIFEST_SCHEMA!r}")
    frames = manifest.get("frames") or {}
    if frames.get("mapping_rule") != MAPPING_RULE:
        _die("manifest mapping_rule is not the pinned t=N-1 rule")
    files = frames.get("files")
    if not isinstance(files, list) or not files:
        _die("manifest has no frames.files inventory")
    return manifest


def load_frames(frames_dir: Path, manifest: dict):
    """Verify + decode every inventoried frame. Fail closed."""
    expected = manifest["frames"]["files"]
    names, hashes, images = [], [], []
    for entry in expected:
        path = frames_dir / entry["name"]
        if not path.is_file():
            _die(f"missing frame {entry['name']}")
        data = path.read_bytes()
        if len(data) != entry.get("bytes"):
            _die(f"{entry['name']}: byte size != manifest")
        digest = hashlib.sha256(data).hexdigest()
        if digest != entry.get("sha256"):
            _die(f"{entry['name']}: SHA-256 != manifest")
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            _die(f"{entry['name']}: cannot decode as image")
        height, width = image.shape[:2]
        if width != entry.get("width") or height != entry.get("height"):
            _die(f"{entry['name']}: dimensions != manifest")
        names.append(entry["name"])
        hashes.append(digest)
        images.append(image)
    return names, hashes, images


def frame_time(index: int) -> int:
    """0-based frame index -> video seconds under the pinned rule."""
    return index


def to_half(image):
    height, width = image.shape[:2]
    return cv2.resize(image, (width // DOWNSCALE, height // DOWNSCALE),
                      interpolation=cv2.INTER_AREA)


def gray_half(image):
    return cv2.GaussianBlur(
        cv2.cvtColor(to_half(image), cv2.COLOR_BGR2GRAY), (5, 5), 0)


def consecutive_change(grays) -> list:
    """Mean absdiff between consecutive frames (full-frame, hands in)."""
    return [float(np.mean(cv2.absdiff(grays[i], grays[i + 1])))
            for i in range(len(grays) - 1)]


def stable_frames(change: list) -> list:
    """Frames quiet on both sides. change[i] sits between i and i+1."""
    if not change:
        return []
    level = float(np.percentile(change, STABLE_PERCENTILE))
    stable = []
    for i in range(len(change) + 1):
        before = change[i - 1] if i > 0 else 0.0
        after = change[i] if i < len(change) else 0.0
        if before <= level and after <= level:
            stable.append(i)
    return stable, level


def find_transitions(stable: list, count: int) -> list:
    """Maximal non-stable runs of >= MIN_TRANSITION_FRAMES between
    stable frames. Returns (before_idx, after_idx) index pairs."""
    stable_set = set(stable)
    out = []
    i = 0
    while i < count:
        if i in stable_set:
            i += 1
            continue
        run_start = i
        while i < count and i not in stable_set:
            i += 1
        run_end = i - 1
        before = run_start - 1
        after = i if i < count else None
        if (after is not None and before >= 0
                and run_end - run_start + 1 >= MIN_TRANSITION_FRAMES):
            out.append((before, after))
    return out


def skin_mask(bgr_half) -> np.ndarray:
    ycc = cv2.cvtColor(bgr_half, cv2.COLOR_BGR2YCrCb)
    _, cr, cb = cv2.split(ycc)
    return np.logical_and(
        np.logical_and(cr >= SKIN_CR[0], cr <= SKIN_CR[1]),
        np.logical_and(cb >= SKIN_CB[0], cb <= SKIN_CB[1])).astype(np.uint8)


def change_components(before_gray, after_gray):
    """Otsu-thresholded diff mask -> boxes [x,y,w,h] in half px."""
    diff = cv2.absdiff(before_gray, after_gray)
    _, mask = cv2.threshold(diff, 0, 255,
                            cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        mask, connectivity=8)
    boxes = []
    for label in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[label])
        if area < MIN_COMPONENT_PX:
            continue
        patch = diff[y:y + h, x:x + w]
        if float(np.mean(patch)) < MIN_COMPONENT_MEAN:
            continue
        boxes.append([x, y, w, h])
    boxes.sort(key=lambda b: (b[1], b[0]))
    return boxes, mask


def upscale_box(box) -> list:
    return [int(v * DOWNSCALE) for v in box]


def centroid(box) -> list:
    return [box[0] + box[2] / 2.0, box[1] + box[3] / 2.0]


def point_in_box(point, box) -> bool:
    return (box[0] <= point[0] <= box[0] + box[2]
            and box[1] <= point[1] <= box[1] + box[3])


def clip_box(box, width: int, height: int) -> list:
    x = max(0, min(box[0], width - 1))
    y = max(0, min(box[1], height - 1))
    w = max(1, min(box[2], width - x))
    h = max(1, min(box[3], height - y))
    return [x, y, w, h]


def crop(image, box):
    x, y, w, h = box
    return image[y:y + h, x:x + w]


def skin_fraction(bgr_half, box_half) -> float:
    x, y, w, h = box_half
    height, width = bgr_half.shape[:2]
    box = clip_box([x, y, w, h], width, height)
    mask = skin_mask(bgr_half)
    patch = mask[box[1]:box[1] + box[3], box[0]:box[0] + box[2]]
    return float(np.mean(patch)) if patch.size else 0.0


def persistent(box_half, later_mask) -> tuple[bool, float]:
    """Is the changed component still changed one frame later?"""
    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (PERSIST_DILATE_PX, PERSIST_DILATE_PX))
    dilated = cv2.dilate(later_mask, kernel)
    x, y, w, h = box_half
    patch = dilated[y:y + h, x:x + w]
    frac = float(np.mean(patch > 0)) if patch.size else 0.0
    return frac >= PERSIST_FRACTION, round(frac, 3)


def pose_check(before_bgr, after_bgr) -> dict:
    """ORB homography inside the assembly ROI. Failure means a pose
    epoch boundary: rotation or view change, no cross-frame transfer."""
    roi = [v // DOWNSCALE for v in ASSEMBLY_ROI]
    height, width = before_bgr.shape[:2]
    roi = clip_box(roi, width, height)
    gray_b = cv2.cvtColor(crop(before_bgr, roi), cv2.COLOR_BGR2GRAY)
    gray_a = cv2.cvtColor(crop(after_bgr, roi), cv2.COLOR_BGR2GRAY)
    orb = cv2.ORB_create(nfeatures=ORB_FEATURES)
    kp_b, des_b = orb.detectAndCompute(gray_b, None)
    kp_a, des_a = orb.detectAndCompute(gray_a, None)
    if des_b is None or des_a is None:
        return {"matches": 0, "inliers": 0, "inlier_ratio": 0.0,
                "pose_change": True, "note": "no descriptors"}
    matches = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(des_b, des_a, k=2)
    good = [m for m, n in (pair for pair in matches if len(pair) == 2)
            if m.distance < 0.75 * n.distance]
    if len(good) < POSE_MIN_MATCHES:
        return {"matches": len(good), "inliers": 0, "inlier_ratio": 0.0,
                "pose_change": True, "note": "too few matches"}
    src = np.float32([kp_b[m.queryIdx].pt for m in good])
    dst = np.float32([kp_a[m.trainIdx].pt for m in good])
    _, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    inliers = int(mask.sum()) if mask is not None else 0
    ratio = inliers / len(good)
    changed = ratio < POSE_MIN_INLIER_RATIO
    return {"matches": len(good), "inliers": inliers,
            "inlier_ratio": round(ratio, 3), "pose_change": changed,
            "note": "rotation/view change; boxes stay in own-frame "
                    "coordinates" if changed else "views compatible"}


def _non_skin_mask(bgr) -> np.ndarray:
    ycc = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)
    _, cr, cb = cv2.split(ycc)
    skin = np.logical_and(
        np.logical_and(cr >= SKIN_CR[0], cr <= SKIN_CR[1]),
        np.logical_and(cb >= SKIN_CB[0], cb <= SKIN_CB[1]))
    return np.where(skin, 0, 255).astype(np.uint8)


def color_similarity(crop_a, crop_b) -> float:
    """HSV histogram correlation over non-skin pixels, [0, 1]."""
    if min(crop_a.shape[:2]) < 5 or min(crop_b.shape[:2]) < 5:
        return 0.0
    ha = cv2.calcHist([cv2.cvtColor(crop_a, cv2.COLOR_BGR2HSV)], [0, 1],
                      _non_skin_mask(crop_a), [30, 32], [0, 180, 0, 256])
    hb = cv2.calcHist([cv2.cvtColor(crop_b, cv2.COLOR_BGR2HSV)], [0, 1],
                      _non_skin_mask(crop_b), [30, 32], [0, 180, 0, 256])
    cv2.normalize(ha, ha)
    cv2.normalize(hb, hb)
    return float(max(0.0, cv2.compareHist(ha, hb, cv2.HISTCMP_CORREL)))


def orb_match_fraction(crop_a, crop_b) -> float:
    """Ratio-tested ORB matches / query keypoints, in [0, 1].
    Guards tiny crops."""
    if min(crop_a.shape[:2]) < 30 or min(crop_b.shape[:2]) < 30:
        return 0.0
    orb = cv2.ORB_create(nfeatures=500)
    gray_a = cv2.cvtColor(crop_a, cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(crop_b, cv2.COLOR_BGR2GRAY)
    kp_a, des_a = orb.detectAndCompute(gray_a, None)
    kp_b, des_b = orb.detectAndCompute(gray_b, None)
    if des_a is None or des_b is None:
        return 0.0
    matches = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(des_a, des_b, k=2)
    good = sum(1 for m, n in (pair for pair in matches if len(pair) == 2)
               if m.distance < 0.75 * n.distance)
    return good / max(1, len(kp_a))


def pair_score(src_crop, dest_crop) -> dict:
    color = color_similarity(src_crop, dest_crop)
    keypoints = orb_match_fraction(src_crop, dest_crop)
    area_a = max(1, src_crop.shape[0] * src_crop.shape[1])
    area_b = max(1, dest_crop.shape[0] * dest_crop.shape[1])
    size = min(area_a, area_b) / max(area_a, area_b)
    total = W_COLOR * color + W_KP * keypoints + W_SIZE * size
    return {"score": round(total, 3), "color": round(color, 3),
            "keypoints": round(keypoints, 3), "size": round(size, 3)}


def rank_correspondences(sources, dests, before_bgr, after_bgr) -> list:
    """Score every source x destination crop pair. Source crops come
    from the BEFORE frame (part in situ), dest crops from AFTER."""
    ranked = []
    for src in sources:
        for dest in dests:
            feats = pair_score(crop(before_bgr, src["box_half"]),
                               crop(after_bgr, dest["box_half"]))
            ranked.append({"src_region": src["region_id"],
                           "dest_region": dest["region_id"],
                           **feats})
    ranked.sort(key=lambda r: (-r["score"], r["src_region"],
                               r["dest_region"]))
    return ranked


def analyze_transition(event_id, before_idx, after_idx, names, grays,
                       halves) -> dict:
    before_name, after_name = names[before_idx], names[after_idx]
    boxes, _ = change_components(grays[before_idx], grays[after_idx])
    pose = pose_check(halves[before_idx], halves[after_idx])
    hand_before = round(float(np.mean(skin_mask(halves[before_idx]))), 4)
    hand_after = round(float(np.mean(skin_mask(halves[after_idx]))), 4)
    height, width = halves[before_idx].shape[:2]
    full_h, full_w = height * DOWNSCALE, width * DOWNSCALE
    # Later mask for the persistence test (one frame past after).
    later_mask = None
    if after_idx + 1 < len(grays):
        diff = cv2.absdiff(grays[before_idx], grays[after_idx + 1])
        _, later_mask = cv2.threshold(
            diff, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    regions = []
    for num, box in enumerate(boxes):
        full = upscale_box(box)
        full = clip_box(full, full_w, full_h)
        role = ("disappearance"
                if point_in_box(centroid(full), ASSEMBLY_ROI)
                else "appearance")
        skin_b = skin_fraction(halves[before_idx], box)
        skin_a = skin_fraction(halves[after_idx], box)
        occluded = max(skin_b, skin_a) > OCCLUDE_FRACTION
        if later_mask is None:
            persist, persist_frac = False, 0.0
            persist_note = "unknown: after-frame is the last frame"
        else:
            persist, persist_frac = persistent(box, later_mask)
            persist_note = ("change still visible one frame later"
                            if persist else
                            "transient: gone one frame later")
        regions.append({
            "region_id": f"{event_id}-r{num}",
            "frame": after_name,
            "bbox_xywh": full,
            "role": role,
            "role_basis": "centroid inside assembly ROI"
                          if role == "disappearance" else
                          "centroid outside assembly ROI (workspace)",
            "skin_fraction_before": round(skin_b, 3),
            "skin_fraction_after": round(skin_a, 3),
            "occluded": occluded,
            "persistent": persist,
            "persistence_fraction": persist_frac,
            "persistence_note": persist_note,
            "box_half": box,
        })
    keep = [r for r in regions if r["persistent"] and not r["occluded"]]
    sources = [r for r in keep if r["role"] == "disappearance"]
    dests = [r for r in keep if r["role"] == "appearance"]
    ranked = rank_correspondences(sources, dests, halves[before_idx],
                                  halves[after_idx])
    if not sources or not dests:
        abstain_reason = ("no persistent non-occluded source regions"
                          if not sources else
                          "no persistent non-occluded dest regions")
        top1 = None
    elif not ranked or ranked[0]["score"] < ABSTAIN_SCORE:
        best = ranked[0]["score"] if ranked else 0.0
        abstain_reason = (f"best pair score {best} below operating "
                          f"point {ABSTAIN_SCORE}")
        top1 = None
    else:
        abstain_reason = None
        first = ranked[0]
        top1 = {**first,
                "src_frame": before_name, "dest_frame": after_name}
    for region in regions:
        del region["box_half"]
    return {
        "id": event_id,
        "before_frame": before_name, "after_frame": after_name,
        "before_t": frame_time(before_idx),
        "after_t": frame_time(after_idx),
        "pose": pose,
        "hand_fraction_before": hand_before,
        "hand_fraction_after": hand_after,
        "n_components": len(boxes),
        "persistent_regions": [r for r in regions if r["persistent"]],
        "transient_regions": [r for r in regions if not r["persistent"]],
        "ranked_pairs": ranked,
        "correspondence_top1": top1,
        "abstain_reason": abstain_reason,
    }


def render_sheet(events, names, images, out_path: Path):
    """One row per event: before/after thumbs with proposed boxes."""
    rows = []
    for event in events:
        before = to_half(images[names.index(event["before_frame"])])
        after = to_half(images[names.index(event["after_frame"])])
        for label, canvas, frame_key in (
                ("BEFORE", before, "before_frame"),
                ("AFTER", after, "after_frame")):
            for region in (event["persistent_regions"]
                           + event["transient_regions"]):
                if region["frame"] != event[frame_key] and label == "BEFORE":
                    continue
                if region["frame"] != event["after_frame"] \
                        and label == "AFTER":
                    continue
                box = [v // DOWNSCALE for v in region["bbox_xywh"]]
                x, y, w, h = box
                if not region["persistent"]:
                    color, thick = (0, 0, 255), 1
                elif region["occluded"]:
                    color, thick = (0, 165, 255), 2
                elif region["role"] == "disappearance":
                    color, thick = (0, 255, 0), 2
                else:
                    color, thick = (255, 0, 0), 2
                cv2.rectangle(canvas, (x, y), (x + w, y + h), color, thick)
                cv2.putText(canvas, region["region_id"], (x, max(12, y - 4)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
            cv2.putText(canvas, f"{label} {event[frame_key.lower()]}",
                        (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (255, 255, 255), 2)
        rows.append(np.hstack([before, after]))
    sheet = np.vstack(rows) if rows else np.zeros((64, 64, 3), np.uint8)
    cv2.imwrite(str(out_path), sheet,
                [cv2.IMWRITE_JPEG_QUALITY, 88])


def render_log(document: dict) -> str:
    lines = ["# Gate 1 uncertainty and failure-case log",
             "",
             "Autonomous proposals only; nothing here is confirmed.",
             ""]
    params = document["params"]
    lines.append(f"- stable percentile: {params['stable_percentile']}, "
                 f"level used: {document['stable_level']:.2f}")
    lines.append(f"- abstain operating point: "
                 f"{params['abstain_score']} (uncalibrated)")
    lines.append(f"- pose epochs: {len(document['pose_epochs'])}")
    lines.append("")
    for event in document["events"]:
        top = event["correspondence_top1"]
        lines.append(f"## {event['id']}: t={event['before_t']}s -> "
                     f"t={event['after_t']}s "
                     f"({event['before_frame']} -> {event['after_frame']})")
        lines.append(f"- pose_change={event['pose']['pose_change']} "
                     f"(matches={event['pose']['matches']}, "
                     f"inlier_ratio={event['pose']['inlier_ratio']})")
        lines.append(f"- hand fraction before/after: "
                     f"{event['hand_fraction_before']}/"
                     f"{event['hand_fraction_after']}")
        lines.append(f"- components: {event['n_components']}, persistent: "
                     f"{len(event['persistent_regions'])}, transient: "
                     f"{len(event['transient_regions'])}")
        if top is None:
            lines.append(f"- ABSTAIN: {event['abstain_reason']}")
        else:
            lines.append(f"- top1 {top['src_region']} -> "
                         f"{top['dest_region']} score={top['score']} "
                         f"(color={top['color']}, kp={top['keypoints']}, "
                         f"size={top['size']}) -- uncalibrated")
        lines.append("")
    lines.append("## Known failure modes (a priori)")
    lines.append("")
    lines.append("- Opposite-face changes (e.g. the PCB flip): both faces "
                 "are genuine but look different, so appearance "
                 "correspondence is expected to abstain, not to match.")
    lines.append("- Indistinguishable small parts (screws, membranes): "
                 "no per-instance identity is attempted; group-level or "
                 "abstention outcomes are the honest result.")
    lines.append("- 1 fps sampling cannot order sub-second handling; fast "
                 "in-hand moves between frames are invisible.")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Gate 1 sparse correspondence experiment.")
    parser.add_argument("--frames-dir", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    manifest_path = Path(args.manifest)
    manifest = load_manifest(manifest_path)
    names, hashes, images = load_frames(Path(args.frames_dir), manifest)
    grays = [gray_half(image) for image in images]
    halves = [to_half(image) for image in images]
    change = consecutive_change(grays)
    stable, level = stable_frames(change)
    transitions = find_transitions(stable, len(names))
    events = []
    transient_only = 0
    for num, (before_idx, after_idx) in enumerate(transitions, 1):
        event = analyze_transition(f"E{num}", before_idx, after_idx,
                                   names, grays, halves)
        if event["persistent_regions"]:
            events.append(event)
        else:
            transient_only += 1
    # Pose epochs: maximal runs unbroken by a pose_change transition.
    epochs, start = [], 0
    for event in events:
        if event["pose"]["pose_change"]:
            epochs.append({"start_frame": names[start],
                           "end_frame": event["before_frame"]})
            start = names.index(event["after_frame"])
    epochs.append({"start_frame": names[start], "end_frame": names[-1]})
    document = {
        "schema": SCHEMA,
        "inputs": {
            "manifest": str(manifest_path),
            "manifest_sha256": sha256_file(manifest_path),
            "frames_dir": args.frames_dir,
            "n_frames": len(names),
            "frame_sha256": {name: digest
                             for name, digest in zip(names, hashes)},
        },
        "params": {
            "assembly_roi_xywh": ASSEMBLY_ROI,
            "stable_percentile": STABLE_PERCENTILE,
            "min_transition_frames": MIN_TRANSITION_FRAMES,
            "min_component_px_halfres": MIN_COMPONENT_PX,
            "persist_fraction": PERSIST_FRACTION,
            "occlude_fraction": OCCLUDE_FRACTION,
            "pose_min_matches": POSE_MIN_MATCHES,
            "pose_min_inlier_ratio": POSE_MIN_INLIER_RATIO,
            "weights": {"color": W_COLOR, "keypoints": W_KP,
                        "size": W_SIZE},
            "abstain_score": ABSTAIN_SCORE,
        },
        "stable_level": level,
        "n_stable_frames": len(stable),
        "n_transitions": len(transitions),
        "n_transient_only": transient_only,
        "pose_epochs": epochs,
        "events": events,
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "candidates.json").write_text(
        json.dumps(document, indent=2), encoding="utf-8")
    render_sheet(events, names, images, out / "contact_sheet_gate1.jpg")
    (out / "uncertainty_log.md").write_text(render_log(document),
                                            encoding="utf-8")
    print(json.dumps({"events": len(events),
                      "transient_only": transient_only,
                      "epochs": len(epochs),
                      "abstentions": sum(
                          1 for e in events
                          if e["correspondence_top1"] is None)},
                     indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())