"""Adaptive source discovery, pass 1: change proposals and a source registry (no inventory needed).

The device is found as the largest non-paper region in the work area of
the first quiet sample. On every sample the device is compared with a
hand-free reference. A pixel is a candidate when it differs from the
reference, has been free of motion for the quiet hold, and does not belong
to an *occluder*: a connected change-or-motion component that reaches
outside the device mask (hands and arms enter from outside). This test
uses no colour. A candidate that stays a candidate for the confirm hold
becomes confirmed; confirmed pixels of one sample form proposals
(connected components above the minimum area). The reference then adopts
those pixels, so a later return to the same place is a new change.

Proposals observed in the same interval and close relative to their size merge into one source
``SRC-<n>`` with mask geometry in sheet-frame mm, confidence and
provenance. If a large share of the device changes at once, the device
pose epoch ends and discovery stops: later changes are unknown, never
guessed. Pass 2 is the unchanged observer run with the registry as an
inventory.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

REGISTRY_SCHEMA = "custody-source-registry@1"
PROPOSALS_VERSION = "0.1.0"
DEFAULTS = {
    "paper_min_value": 150, "paper_max_saturation": 40, "mask_closing_mm": 3.0,
    "quiet_hold_s": 0.6, "confirm_hold_s": 1.0, "min_area_mm2": 8.0,
    "occluder_min_outside_mm2": 4.0, "pose_break_fraction": 0.3, "merge_gap_mm": 1.0,
    "same_interval_s": 0.25, "proximity_ratio": 0.5,
}


def device_mask(rectified_bgr: np.ndarray, work_px: tuple[int, int, int, int], ppm: float, params: dict) -> np.ndarray:
    """Largest non-paper component inside the work area (sheet-shaped bool mask)."""
    hsv = cv2.cvtColor(rectified_bgr, cv2.COLOR_BGR2HSV)
    paper = (hsv[..., 2] >= params["paper_min_value"]) & (hsv[..., 1] <= params["paper_max_saturation"])
    work = np.zeros(paper.shape, bool)
    x0, y0, x1, y1 = work_px
    work[y0:y1, x0:x1] = True
    k = max(1, int(params["mask_closing_mm"] * ppm))
    mask = cv2.morphologyEx((~paper & work).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    if count < 2:
        return np.zeros(paper.shape, bool)
    return labels == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))


@dataclass
class ProposalTracker:
    mask: np.ndarray
    reference: np.ndarray
    ppm: float
    change_threshold: float
    params: dict
    proposals: list[dict] = field(default_factory=list)
    pose_epoch_end_s: float | None = None
    _last_motion: np.ndarray | None = None
    _candidate_since: np.ndarray | None = None
    _last_match: np.ndarray | None = None

    def __post_init__(self):
        shape = self.reference.shape
        self._last_motion = np.full(shape, -np.inf, np.float32)
        self._candidate_since = np.full(shape, np.nan, np.float32)
        self._last_match = np.zeros(shape, np.float32)

    def update(self, t_s: float, gray: np.ndarray, motion: np.ndarray) -> None:
        if self.pose_epoch_end_s is not None:
            return
        p = self.params
        self._last_motion[motion] = t_s
        quiet = (t_s - self._last_motion) >= p["quiet_hold_s"]
        differs = np.abs(gray - self.reference) > self.change_threshold
        if (differs & self.mask & quiet).sum() > p["pose_break_fraction"] * self.mask.sum():
            self.pose_epoch_end_s = t_s
            return
        occluded = self._occluders(differs | ~quiet)
        matches = ~differs & quiet & self.mask
        self._last_match[matches] = t_s
        candidate = differs & quiet & self.mask & ~occluded
        self._candidate_since[~candidate] = np.nan
        start = candidate & np.isnan(self._candidate_since)
        self._candidate_since[start] = t_s
        confirmed = candidate & (t_s - np.nan_to_num(self._candidate_since, nan=t_s) >= p["confirm_hold_s"])
        if confirmed.any():
            self._emit(confirmed, t_s, gray)

    def _occluders(self, busy: np.ndarray) -> np.ndarray:
        count, labels, stats, _ = cv2.connectedComponentsWithStats(busy.astype(np.uint8))
        if count < 2:
            return np.zeros(busy.shape, bool)
        outside = np.bincount(labels[busy & ~self.mask].ravel(), minlength=count)
        reaching = np.flatnonzero(outside >= self.params["occluder_min_outside_mm2"] * self.ppm ** 2)
        reaching = reaching[reaching > 0]
        return np.isin(labels, reaching)

    def _emit(self, confirmed: np.ndarray, t_s: float, gray: np.ndarray) -> None:
        count, labels, stats, _ = cv2.connectedComponentsWithStats(confirmed.astype(np.uint8))
        min_px = self.params["min_area_mm2"] * self.ppm ** 2
        for label in range(1, count):
            if stats[label, cv2.CC_STAT_AREA] < min_px:
                continue
            pixels = labels == label
            self.proposals.append({
                "mask": pixels,
                "t_before_s": round(float(self._last_match[pixels].max()), 3),
                "t_after_s": round(float(np.nanmin(self._candidate_since[pixels])), 3),
                "t_confirmed_s": round(t_s, 3),
                "contrast": round(float(np.abs(gray - self.reference)[pixels].mean()), 2),
            })
            self.reference[pixels] = gray[pixels]
            self._candidate_since[pixels] = np.nan


def mask_gap_mm(a: np.ndarray, b: np.ndarray, ppm: float) -> float:
    """Smallest distance between two masks in mm (0 when they touch or overlap)."""
    if (a & b).any():
        return 0.0
    distance = cv2.distanceTransform((~a).astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    return max(0.0, float(distance[b].min()) - 1.0) / ppm  # centre distance minus one pixel = empty pixels between


def merge_into_sources(proposals: list[dict], ppm: float, params: dict) -> list[dict]:
    """Spatiotemporal clustering of proposals into source candidates.

    Two proposals join when (1) their change was observed in the same interval
    (last matching sample within ``same_interval_s`` and windows overlapping)
    and (2) the gap between their masks is at most the larger of
    ``merge_gap_mm`` and ``proximity_ratio`` times the larger part's size
    (square root of its area). Same time alone never merges: distant
    components stay separate sources, and so do neighbours that changed in
    different intervals. Returns groups with the clustering evidence.
    """
    parent = list(range(len(proposals)))
    joins: list[dict] = []

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(proposals):
        for j in range(i + 1, len(proposals)):
            b = proposals[j]
            same_interval = (abs(a["t_before_s"] - b["t_before_s"]) <= params["same_interval_s"]
                             and a["t_before_s"] < b["t_confirmed_s"] and b["t_before_s"] < a["t_confirmed_s"])
            if not same_interval:
                continue
            size_mm = max(float(a["mask"].sum()), float(b["mask"].sum())) ** 0.5 / ppm
            limit = max(params["merge_gap_mm"], params["proximity_ratio"] * size_mm)
            gap = mask_gap_mm(a["mask"], b["mask"], ppm)
            if gap <= limit:
                parent[find(i)] = find(j)
                joins.append({"a": i, "b": j, "gap_mm": round(gap, 2), "limit_mm": round(limit, 2)})
    groups: dict[int, list[int]] = {}
    for i in range(len(proposals)):
        groups.setdefault(find(i), []).append(i)
    result = []
    for members in sorted(groups.values(), key=lambda g: min(proposals[i]["t_before_s"] for i in g)):
        result.append({"members": [proposals[i] for i in members],
                       "joins": [j for j in joins if j["a"] in members]})
    return result


def source_record(index: int, group: dict, to_mm, ppm: float) -> dict:
    members = group["members"]
    mask = np.zeros_like(members[0]["mask"])
    for member in members:
        mask |= member["mask"]
    ys, xs = np.nonzero(mask)
    x0, y0 = to_mm(xs.min(), ys.min())
    x1, y1 = to_mm(xs.max() + 1, ys.max() + 1)
    cx, cy = to_mm(xs.mean(), ys.mean())
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    outline = [[round(v, 2) for v in to_mm(*point[0])] for point in max(contours, key=cv2.contourArea)]
    area = float(mask.sum()) / ppm ** 2
    return {
        "id": f"SRC-{index}",
        "version": 1,
        "supersedes": None,
        "geometry": {"frame": "sheet mm (device at rest, pose epoch 1)", "bbox_mm": [round(x0, 2), round(y0, 2), round(x1 - x0, 2), round(y1 - y0, 2)],
                     "centroid_mm": [round(cx, 2), round(cy, 2)], "outline_mm": outline, "area_mm2": round(area, 1)},
        "first_change": {"t_before_s": min(m["t_before_s"] for m in members), "t_after_s": max(m["t_confirmed_s"] for m in members)},
        "proposals": len(members),
        "submasks": [_submask(member, to_mm, ppm) for member in members],
        "clustering": {"rule": "same observed interval and mask gap <= max(merge_gap_mm, proximity_ratio * sqrt(larger area))",
                       "joins": group["joins"]},
        "confidence": {"area_mm2": round(area, 1), "mean_contrast": round(float(np.mean([m["contrast"] for m in members])), 2)},
        "status": "proposal (unconfirmed)",
    }


def discover_video(video, custody: dict, harness: dict, kit: dict, provenance: dict, params: dict) -> tuple[dict, dict]:
    """Run pass 1 over one fixed-camera video; return (registry, inventory for pass 2)."""
    from anastylos_custody import footage
    from anastylos_custody.sheet import DriftTracker, Rectifier, SheetReader, find_registration
    from anastylos_custody.signals import SignalTracker

    step = int(custody["sampling"]["step_frames"])
    reader = SheetReader(harness, kit, custody["view"])
    registration = find_registration(reader, footage.sample_frames(video, step), float(custody["session_registration"]["search_window_s"]))
    rectifier = Rectifier(registration.homography_image_to_mm, reader.page_rect_mm(), custody["rectified"]["px_per_mm"], custody["rectified"]["margin_mm"])
    work = next(zone["rect_frame_mm"] for zone in reader.zones() if zone["type"] == "work_area")
    spec = custody["session_registration"]
    drift = DriftTracker(reader, registration.marker_corners_px, int(spec["drift_min_markers"]), float(spec["drift_max_residual_px"]))
    signals = SignalTracker(custody["signal"], custody["skin"], rectifier.ppm)
    tracker, quiet_since, next_drift, mask_t = None, None, 0.0, None
    for sample in footage.sample_frames(video, step):
        if sample.t_s >= next_drift:
            drift.update(sample.t_s, sample.image)
            next_drift = sample.t_s + float(spec["drift_update_every_s"])
        rectified = rectifier.rectify(sample.image, drift.reference_to_current)
        frame = signals.update(sample.t_s, rectified)
        if tracker is None:
            moving = frame.motion.mean() > 0.0005 or sample.t_s == 0.0
            quiet_since = None if moving else (quiet_since if quiet_since is not None else sample.t_s)
            if quiet_since is not None and sample.t_s - quiet_since >= params["quiet_hold_s"]:
                mask = device_mask(rectified, rectifier.rect_to_px(work), rectifier.ppm, params)
                tracker = ProposalTracker(mask, frame.gray.copy(), rectifier.ppm, float(custody["signal"]["change_threshold"]), params)
                mask_t = sample.t_s
            continue
        tracker.update(sample.t_s, frame.gray, frame.motion)
    if tracker is None:
        raise SystemExit("no quiet sample to find the device")

    def to_mm(x, y):
        return float(x) / rectifier.ppm + rectifier.x0, float(y) / rectifier.ppm + rectifier.y0

    groups = merge_into_sources(tracker.proposals, rectifier.ppm, params)
    sources = [source_record(i, group, to_mm, rectifier.ppm) for i, group in enumerate(groups, start=1)]
    registry = {
        "schema": REGISTRY_SCHEMA,
        "proposals_version": PROPOSALS_VERSION,
        "params": params,
        "provenance": {**provenance, "video": {**footage.describe(video), "sha256": footage.sha256_file(video)}},
        "device": {"mask_from_t_s": mask_t, "mask_area_mm2": round(float(tracker.mask.sum()) / rectifier.ppm ** 2, 1),
                   "pose_epoch_end_s": tracker.pose_epoch_end_s},
        "proposal_count": len(tracker.proposals),
        "sources": sources,
        "unknowns": ([f"device pose epoch ended at {tracker.pose_epoch_end_s} s; later changes were not proposed"]
                     if tracker.pose_epoch_end_s is not None else []),
    }
    inventory = {
        "schema": "custody-site-inventory@1",
        "view": custody["view"],
        "inventory_t_s": 0.0,
        "basis": {"kind": "adaptive proposal", "by": f"custody discover (proposals {PROPOSALS_VERSION})", "operator_accepted": False},
        "coordinate_note": "bounding boxes of discovered change masks grown by 1 mm; valid for the device pose of epoch 1",
        "sites": [{"id": s["id"], "rect_mm": [round(s["geometry"]["bbox_mm"][0] - 1, 2), round(s["geometry"]["bbox_mm"][1] - 1, 2),
                                              round(s["geometry"]["bbox_mm"][2] + 2, 2), round(s["geometry"]["bbox_mm"][3] + 2, 2)],
                   "initially": "occupied"} for s in sources],
    }
    return registry, inventory


def _submask(member: dict, to_mm, ppm: float) -> dict:
    ys, xs = np.nonzero(member["mask"])
    x0, y0 = to_mm(xs.min(), ys.min())
    x1, y1 = to_mm(xs.max() + 1, ys.max() + 1)
    cx, cy = to_mm(xs.mean(), ys.mean())
    return {"bbox_mm": [round(x0, 2), round(y0, 2), round(x1 - x0, 2), round(y1 - y0, 2)],
            "centroid_mm": [round(cx, 2), round(cy, 2)], "area_mm2": round(float(member["mask"].sum()) / ppm ** 2, 1),
            "t_before_s": member["t_before_s"], "t_confirmed_s": member["t_confirmed_s"], "contrast": member["contrast"]}
