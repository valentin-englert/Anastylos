"""Per-zone clean-state observation: when is a zone visible, and did its content change?

A zone is *clean* when its context window has been quiet for a hold time and
no hand covers it: a fresh-skin component that overlaps the zone's narrow
guard box and also reaches outside it (fingers attach to a hand). A
skin-coloured part lying inside the zone is not a hand. Only clean samples
are compared with the zone's reference. A content change becomes an event
only after it stays stable while clean for the confirm time. Occluded or
moving time is never read as "unchanged".

Device sites are read in the device's reference pose (``DevicePose``). Each
site also watches an anchor ring on the device around it; when the ring no
longer matches, the device is re-registered, and if the ring still does not
match the site's pose epoch ends and the site is unknown from then on.

Occupancy is always measured as difference from the zone's first reference.
Event names state what that difference means. Without a declaration (v1.0
convention) tray cells start empty and sites start occupied, so a site
change is a removal (``site_changed``) and a cell change is a deposit
(``appeared``). v1.1 accepts a declared start occupancy per zone, so the
same names stay true when a take starts with full cells and empty sites:
cells then report ``vacated`` when a part leaves, sites ``site_filled`` when
a part is placed and ``site_emptied`` when it leaves again.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from anastylos_custody.device_alignment import DevicePose
from anastylos_custody.signals import FrameSignals

OBSERVER_VERSION = "1.5.0"
KEYFRAME_CANDIDATES = 10
Box = tuple[int, int, int, int]


@dataclass(frozen=True)
class ZoneGeometry:
    """Half-open pixel boxes (x0, y0, x1, y1) on the rectified image."""

    content: Box
    guard: Box
    context: Box
    anchor_mask: np.ndarray | None = None  # bool, context-window shaped; None for tray cells


@dataclass(frozen=True)
class ObserverParams:
    change_threshold: float
    quiet_hold_s: float
    max_motion_fraction: float
    intrusion_min_area_px: float
    min_changed_area_px: float
    confirm_hold_s: float
    max_anchor_changed_fraction: float
    init_tolerance_s: float = 2.0
    # What a still-changed anchor ring means after the device re-registration was
    # accepted: "pose_break" (v1.0/v1.1) or "content" (v1.2 option for dense site
    # grids, where a neighbouring part lies inside the ring).
    ring_change_with_verified_pose: str = "pose_break"
    # Tray-cell occupancy: "start_relative" (v1.0-v1.2: difference from the cell's first
    # clean image) or "paper_model" (v1.3 option: non-paper area after removing printed
    # strokes thinner than print_stroke_px, so a part put back slightly differently is
    # still "occupied" and an empty printed cell is "empty").
    cell_occupancy: str = "start_relative"
    print_stroke_px: int = 0
    min_part_area_px: float = 0.0
    # v1.4 paper-model cues: a cell is also occupied when its saturated area (HSV S above
    # min_saturation, same stroke opening) reaches min_part_area_px, or when its edge length
    # reaches min_edge_px (white parts on white paper). 0 disables a cue.
    min_saturation: float = 0.0
    min_edge_px: float = 0.0


@dataclass
class _Snapshot:
    t_s: float
    frame_index: int


@dataclass
class _Layers:
    """Signal planes in one coordinate frame, plus that frame's offset on the rectified image."""

    gray: np.ndarray
    motion: np.ndarray
    fresh_skin: np.ndarray
    offset: tuple[int, int]

    def crop(self, plane: np.ndarray, box: Box) -> np.ndarray:
        return _crop_at(plane, box, self.offset)


@dataclass
class ZoneObserver:
    zone_id: str
    zone_kind: str
    geometry: ZoneGeometry
    params: ObserverParams
    valid_from_s: float = 0.0
    device: DevicePose | None = None
    events: list[dict] = field(default_factory=list)
    status: str = "waiting"  # waiting | observing | epoch_broken | inventory_not_observable
    clean_s: float = 0.0
    not_clean_s: float = 0.0
    trace: list[dict] | None = None  # per-sample diagnostics when not None
    initially_occupied: bool | None = None  # None: v1.0 convention (see module docstring)
    _baseline: np.ndarray | None = None
    _reference: np.ndarray | None = None
    _anchor_reference: np.ndarray | None = None
    _quiet_since: float | None = None
    _last_t: float | None = None
    _last_clean: _Snapshot | None = None
    _clean_run: list[int] = field(default_factory=list)
    _run_broken: bool = True
    _candidate: np.ndarray | None = None
    _candidate_at: _Snapshot | None = None
    _candidate_frames: list[int] = field(default_factory=list)
    _anchor_moved_since: _Snapshot | None = None
    _cues_now: tuple | None = None
    _baseline_cues: tuple | None = None
    _reference_cues: tuple | None = None
    _candidate_cues: tuple | None = None

    def observe(self, signals: FrameSignals, frame_index: int) -> None:
        t = signals.t_s
        dt = 0.0 if self._last_t is None else t - self._last_t
        self._last_t = t
        if t < self._starts_at() or self.status in ("epoch_broken", "inventory_not_observable"):
            return
        if self._reference is None and t - self.valid_from_s > self.params.init_tolerance_s and self.zone_kind == "site":
            self.status = "inventory_not_observable"
            return
        layers = self._layers(signals)
        if not self._is_clean(layers, t):
            self.not_clean_s += dt
            self._run_broken = True
            return
        self.clean_s += dt
        now = _Snapshot(t, frame_index)
        if self._uses_paper_model() and signals.saturation is not None:
            self._cues_now = (_crop(signals.saturation, self.geometry.content), _crop(signals.edges, self.geometry.content))
        context = layers.crop(layers.gray, self.geometry.context)
        if self._reference is None:
            self._initialize(context, now)
            return
        if self.geometry.anchor_mask is not None:
            context = self._verified_device_context(signals, context, now)
            if context is None:
                return
        content = _crop(context, _relative(self.geometry.content, self.geometry.context))
        changed = self._changed_area(content, self._reference)
        if self._uses_paper_model() and self._cues_now is not None and changed < self.params.min_changed_area_px:
            # A part that barely changes the grey image (white or pale on paper) still changes the cues.
            if self._cue_report(content, self._cues_now)["occupied"] != self._cue_report(self._reference, self._reference_cues)["occupied"]:
                changed = int(self.params.min_changed_area_px)
        if changed < self.params.min_changed_area_px:
            self._settle(now)
            return
        self._track_candidate(content, now, changed)

    def summary(self) -> dict:
        summary = {
            "zone": self.zone_id,
            "kind": self.zone_kind,
            "status": self.status,
            "clean_s": round(self.clean_s, 2),
            "not_clean_s": round(self.not_clean_s, 2),
            "events": len(self.events),
        }
        if self.initially_occupied is not None:
            summary["initially"] = "occupied" if self.initially_occupied else "empty"
        if self._uses_paper_model() and self._baseline is not None:
            measured = self._cue_report(self._baseline, self._baseline_cues)["occupied"]
            summary["initially_measured"] = "occupied" if measured else "empty"
            if self.initially_occupied is not None and self.initially_occupied != measured:
                summary["declared_contradicts_measurement"] = True
        return summary

    # -- frames and cleanliness -----------------------------------------------

    def _layers(self, signals: FrameSignals) -> _Layers:
        if self.device is None:
            return _Layers(signals.gray, signals.motion, signals.fresh_skin, (0, 0))
        device = self.device
        if device.reference is None:
            device.initialize(signals.t_s, signals.gray, signals.motion, signals.fresh_skin)
        gray, motion, skin = device.aligned_layers(signals.t_s, signals.gray, signals.motion, signals.fresh_skin)
        return _Layers(gray, motion, skin, device.region[:2])

    def _is_clean(self, layers: _Layers, t: float) -> bool:
        motion = float(layers.crop(layers.motion, self.geometry.context).mean())
        intrusion = _hand_over_zone(layers.crop(layers.fresh_skin, self.geometry.context),
                                    _relative(self.geometry.guard, self.geometry.context), self.params.intrusion_min_area_px)
        if motion > self.params.max_motion_fraction:
            self._quiet_since = None
        elif self._quiet_since is None:
            self._quiet_since = t
        clean = self._quiet_since is not None and not intrusion and t - self._quiet_since >= self.params.quiet_hold_s
        if self.trace is not None:
            self.trace.append({"t_s": round(t, 3), "motion": round(motion, 5), "intrusion": intrusion, "clean": clean})
        if not clean and (intrusion or self._quiet_since is None):
            self._drop_candidate()
        return clean

    # -- state transitions ---------------------------------------------------

    def _starts_at(self) -> float:
        return self.valid_from_s - self.params.init_tolerance_s if self.zone_kind == "site" else self.valid_from_s

    def _initialize(self, context: np.ndarray, now: _Snapshot) -> None:
        content = _crop(context, _relative(self.geometry.content, self.geometry.context))
        self._baseline = content.copy()
        self._reference = content.copy()
        self._baseline_cues = self._reference_cues = self._cues_now
        self._anchor_reference = context.copy()
        self.status = "observing"
        self._settle(now)
        self.events.append({"zone": self.zone_id, "zone_kind": self.zone_kind, "kind": "reference_set", "t_s": round(now.t_s, 3), "frame": now.frame_index})

    def _anchor_changed_fraction(self, context: np.ndarray) -> float:
        diff = np.abs(context - self._anchor_reference) > self.params.change_threshold
        return float(diff[self.geometry.anchor_mask].mean())

    def _verified_device_context(self, signals: FrameSignals, context: np.ndarray, now: _Snapshot) -> np.ndarray | None:
        """The context in the device's reference pose when the anchor ring confirms it, else None."""
        if not self.geometry.anchor_mask.any():
            # No ring of its own (all ring pixels belong to other sites): rely on the device pose,
            # which the other sites keep verified. Before v1.4.1 this was a silent NaN and a pose break.
            return context
        fraction = self._anchor_changed_fraction(context)
        if fraction <= self.params.max_anchor_changed_fraction:
            self._anchor_moved_since = None
            return context
        report = {"anchor_changed_fraction": round(fraction, 4)}
        if self.device.realign(now.t_s, signals.gray, signals.fresh_skin):
            gray, _motion, _skin = self.device.aligned_layers(now.t_s, signals.gray, signals.motion, signals.fresh_skin)
            context = _crop_at(gray, self.geometry.context, self.device.region[:2])
            report["anchor_changed_fraction_after"] = round(self._anchor_changed_fraction(context), 4)
            report["device_realignment"] = self.device.realignments[-1]
            if report["anchor_changed_fraction_after"] <= self.params.max_anchor_changed_fraction:
                self._anchor_moved_since = None
                self.events.append({"zone": self.zone_id, "zone_kind": self.zone_kind, "kind": "device_realigned", "t_s": round(now.t_s, 3), "frame": now.frame_index, **report})
                return context
            if self.params.ring_change_with_verified_pose == "content":
                # The device pose is verified globally, so the ring changed by its own content
                # (e.g. a neighbouring part was removed): adopt the ring and keep observing.
                self._anchor_moved_since = None
                self._anchor_reference = context.copy()
                self.events.append({"zone": self.zone_id, "zone_kind": self.zone_kind, "kind": "anchor_ring_changed", "t_s": round(now.t_s, 3), "frame": now.frame_index, **report})
                return context
        elif self.device.realignments:
            report["device_realignment"] = self.device.realignments[-1]
        self._drop_candidate()
        if self._anchor_moved_since is None:
            self._anchor_moved_since = now
        elif now.t_s - self._anchor_moved_since.t_s >= self.params.confirm_hold_s:
            self.status = "epoch_broken"
            self.events.append(self._event("pose_epoch_break", self._anchor_moved_since, now, report))
        return None

    def _settle(self, now: _Snapshot) -> None:
        self._drop_candidate()
        self._last_clean = now
        run = [] if self._run_broken else self._clean_run
        self._clean_run = (run + [now.frame_index])[-KEYFRAME_CANDIDATES:]
        self._run_broken = False

    def _track_candidate(self, content: np.ndarray, now: _Snapshot, changed: int) -> None:
        if (self._candidate is None or self._changed_area(content, self._candidate) >= self.params.min_changed_area_px
                or self._cue_flip(content)):
            self._candidate = content.copy()
            self._candidate_cues = self._cues_now
            self._candidate_at = now
            self._candidate_frames = [now.frame_index]
            return
        self._candidate_frames = (self._candidate_frames + [now.frame_index])[:KEYFRAME_CANDIDATES]
        if now.t_s - self._candidate_at.t_s < self.params.confirm_hold_s:
            return
        occupancy_before = self._changed_area(self._reference, self._baseline)
        occupancy_after = self._changed_area(self._candidate, self._baseline)
        if self._uses_paper_model():
            occupancy_before, occupancy_after = self._paper_area(self._reference), self._paper_area(self._candidate)
            cues_before = self._cue_report(self._reference, self._reference_cues)
            cues_after = self._cue_report(self._candidate, self._candidate_cues)
        details = {
            "changed_area_px": changed,
            "occupancy_before_px": occupancy_before,
            "occupancy_after_px": occupancy_after,
            "before_keyframe_candidates": list(reversed(self._clean_run)),
            "after_keyframe_candidates": list(self._candidate_frames),
            "t_confirmed_s": round(now.t_s, 3),
        }
        if self.device is not None:
            details["device_warp"] = [[round(float(value), 5) for value in row] for row in self.device.warp]
        if self._uses_paper_model():
            kind = self._paper_kind(cues_before["occupied"], cues_after["occupied"])
            details.update({"occupancy_rule": "paper_model", "cues_before": cues_before, "cues_after": cues_after})
        else:
            kind = self._change_kind(occupancy_before, occupancy_after)
        self.events.append(self._event(kind, self._last_clean, self._candidate_at, details))
        self._reference = self._candidate
        self._reference_cues = self._candidate_cues
        self._run_broken = True
        self._settle(now)

    def _change_kind(self, occupancy_before: int, occupancy_after: int) -> str:
        differs_before = occupancy_before >= self.params.min_changed_area_px
        differs_after = occupancy_after >= self.params.min_changed_area_px
        if self.zone_kind == "site":
            if self.initially_occupied is False:
                return "site_filled" if differs_after else "site_emptied"
            return "site_changed" if differs_after else "site_restored"
        if self.initially_occupied:
            if not differs_before and differs_after:
                return "vacated"
            if differs_before and not differs_after:
                return "appeared"
            return "changed_while_away_from_start"
        if not differs_before and differs_after:
            return "appeared"
        if differs_before and not differs_after:
            return "vacated"
        return "changed_while_occupied"

    def _uses_paper_model(self) -> bool:
        return self.zone_kind == "tray_cell" and self.params.cell_occupancy == "paper_model"

    def _paper_area(self, content: np.ndarray) -> int:
        """Non-paper pixels after removing printed lines and digits (normalized paper is ~128)."""
        nonpaper = (np.abs(content - 128.0) > self.params.change_threshold).astype(np.uint8)
        k = max(1, int(self.params.print_stroke_px))
        return int(cv2.morphologyEx(nonpaper, cv2.MORPH_OPEN, np.ones((k, k), np.uint8)).sum())

    def _cue_report(self, content: np.ndarray, cues: tuple | None) -> dict:
        """Occupancy cues of one clean cell state: grey contrast, saturation, edge length (any cue suffices)."""
        report = {"grey_px": self._paper_area(content)}
        occupied = report["grey_px"] >= self.params.min_part_area_px
        if cues is not None:
            saturation, edges = cues
            k = max(1, int(self.params.print_stroke_px))
            if self.params.min_saturation > 0:
                saturated = (saturation > self.params.min_saturation).astype(np.uint8)
                report["saturated_px"] = int(cv2.morphologyEx(saturated, cv2.MORPH_OPEN, np.ones((k, k), np.uint8)).sum())
                occupied = occupied or report["saturated_px"] >= self.params.min_part_area_px
            if self.params.min_edge_px > 0:
                report["edge_px"] = int(edges.sum())
                occupied = occupied or report["edge_px"] >= self.params.min_edge_px
        report["occupied"] = bool(occupied)
        return report

    def _cue_flip(self, content: np.ndarray) -> bool:
        """True when the cue occupancy of this sample differs from the tracked candidate's (paper model only)."""
        if not self._uses_paper_model() or self._cues_now is None or self._candidate is None:
            return False
        return self._cue_report(content, self._cues_now)["occupied"] != self._cue_report(self._candidate, self._candidate_cues)["occupied"]

    def _paper_kind(self, before: bool, after: bool) -> str:
        if not before and after:
            return "appeared"
        if before and not after:
            return "vacated"
        return "changed_while_occupied" if before else "changed_while_empty"

    def _event(self, kind: str, before: _Snapshot, after: _Snapshot, details: dict) -> dict:
        return {
            "zone": self.zone_id,
            "zone_kind": self.zone_kind,
            "kind": kind,
            "t_before_s": round(before.t_s, 3),
            "t_after_s": round(after.t_s, 3),
            "frame_before": before.frame_index,
            "frame_after": after.frame_index,
            **details,
        }

    def _changed_area(self, a: np.ndarray, b: np.ndarray) -> int:
        return int((np.abs(a - b) > self.params.change_threshold).sum())

    def _drop_candidate(self) -> None:
        self._candidate = None
        self._candidate_at = None
        self._candidate_frames = []


def _crop(image: np.ndarray, box: Box) -> np.ndarray:
    x0, y0, x1, y1 = box
    return image[y0:y1, x0:x1]


def _crop_at(plane: np.ndarray, box: Box, offset: tuple[int, int]) -> np.ndarray:
    """Crop a rectified-image box from a plane whose origin sits at ``offset``."""
    x0, y0, x1, y1 = box
    ox, oy = offset
    return plane[y0 - oy:y1 - oy, x0 - ox:x1 - ox]


def _relative(box: Box, window: Box) -> Box:
    return box[0] - window[0], box[1] - window[1], box[2] - window[0], box[3] - window[1]


def _hand_over_zone(fresh_skin: np.ndarray, guard: Box, min_area_px: float) -> bool:
    """True when a fresh-skin component overlaps the guard box and also extends beyond it."""
    gx0, gy0, gx1, gy1 = guard
    if int(fresh_skin[gy0:gy1, gx0:gx1].sum()) < min_area_px:
        return False
    _count, labels, stats, _ = cv2.connectedComponentsWithStats(fresh_skin.astype(np.uint8), connectivity=8)
    for index in np.unique(labels[gy0:gy1, gx0:gx1]):
        if index == 0:
            continue
        x, y, w, h, _area = stats[index]
        overlap = int((labels[gy0:gy1, gx0:gx1] == index).sum())
        if overlap >= min_area_px and (x < gx0 or y < gy0 or x + w > gx1 or y + h > gy1):
            return True
    return False
