"""Colour-free hand presence (requirement H1): where arms and hands are, sample by sample.

A hand is not found by skin colour. The tracker keeps a hand-free reference
of the whole sheet, refreshed whenever the scene has been still for the
quiet hold. A pixel is *busy* when it moved recently or differs from that
reference in any colour channel (no colour model: only "looks different
from the hand-free sheet", so a pale hand on white paper counts as well as a
dark one). A busy component that reaches the border of the watched area is
an arm with its hand (arms enter from outside the sheet, or from outside the
camera's view where the sheet edge is not in the picture); busy components
that stay inside are parts or other changes, never hands. A resting hand
stays a hand as long as its arm reaches the border; a static skin-coloured
part is never one. Zones collect *visits*: intervals in which the hand mask
covers them.
"""

from __future__ import annotations

import cv2
import numpy as np

HANDS_VERSION = "0.2.0"
DEFAULTS = {"quiet_hold_s": 0.6, "stale_after_s": 10.0, "scene_quiet_motion_fraction": 0.0005, "change_threshold": 30.0,
            "motion_memory_s": 0.6, "border_px": 3, "min_component_px": 50, "min_zone_overlap_px": 20}


class HandTracker:
    def __init__(self, shape: tuple[int, int], watched: tuple[int, int, int, int], params: dict | None = None):
        self.params = {**DEFAULTS, **(params or {})}
        self.watched = watched  # x0, y0, x1, y1 in rectified px: arms enter across this rectangle's border
        self.reference: np.ndarray | None = None
        self._last_motion = np.full(shape, -np.inf, np.float32)
        self._quiet_since: float | None = None

    def update(self, t_s: float, appearance: np.ndarray, motion: np.ndarray, visible: np.ndarray | None = None) -> np.ndarray:
        """Hand mask of this sample (bool, sheet shaped); ``appearance`` is grey (H, W) or channels (H, W, C).

        ``visible`` marks the pixels the camera sees; its edge counts as a border arms can enter across.
        """
        p = self.params
        self._last_motion[motion] = t_s
        still = motion.mean() <= p["scene_quiet_motion_fraction"]
        self._quiet_since = None if not still else (self._quiet_since if self._quiet_since is not None else t_s)
        if self.reference is None:
            self.reference = appearance.copy()
            return np.zeros(appearance.shape[:2], bool)
        hand = self._hand_mask(t_s, appearance, visible)
        quiet_for = 0.0 if self._quiet_since is None else t_s - self._quiet_since
        # Refresh the hand-free reference only in a still scene without a hand; a resting hand keeps
        # its arm at the border and is not absorbed, unless the scene stays still for stale_after_s.
        if (quiet_for >= p["quiet_hold_s"] and not hand.any()) or quiet_for >= p["stale_after_s"]:
            self.reference = appearance.copy()
        return hand

    def _hand_mask(self, t_s: float, appearance: np.ndarray, visible: np.ndarray | None) -> np.ndarray:
        p = self.params
        difference = np.abs(appearance - self.reference)
        if difference.ndim == 3:
            difference = difference.max(axis=2)
        busy = ((t_s - self._last_motion) < p["motion_memory_s"]) | (difference > p["change_threshold"])
        x0, y0, x1, y1 = self.watched
        inside = np.zeros(busy.shape, bool)
        inside[y0:y1, x0:x1] = True
        busy &= inside
        count, labels, stats, _ = cv2.connectedComponentsWithStats(busy.astype(np.uint8))
        b = p["border_px"]
        frame = np.zeros(busy.shape, bool)
        frame[y0:y1, x0:x1] = True
        frame[y0 + b:y1 - b, x0 + b:x1 - b] = False
        if visible is not None:
            unseen = cv2.dilate((~visible).astype(np.uint8), np.ones((2 * b + 1, 2 * b + 1), np.uint8)) > 0
            frame |= unseen & visible & inside
        touching = np.unique(labels[frame & busy])
        hands = [k for k in touching if k > 0 and stats[k, cv2.CC_STAT_AREA] >= p["min_component_px"]]
        return np.isin(labels, hands)


class VisitLog:
    """Intervals in which the hand mask covers each zone's box."""

    def __init__(self, boxes: dict[str, tuple[int, int, int, int]], min_overlap_px: int):
        self.boxes, self.min_overlap = boxes, min_overlap_px
        self.visits: dict[str, list[list[float]]] = {zone: [] for zone in boxes}
        self._open: dict[str, float] = {}
        self._last_t: float | None = None

    def update(self, t_s: float, hand: np.ndarray) -> None:
        for zone, (x0, y0, x1, y1) in self.boxes.items():
            covered = int(hand[y0:y1, x0:x1].sum()) >= self.min_overlap
            if covered and zone not in self._open:
                self._open[zone] = t_s
            elif not covered and zone in self._open:
                self.visits[zone].append([round(self._open.pop(zone), 3), round(self._last_t, 3)])
        self._last_t = t_s

    def close(self) -> dict[str, list[list[float]]]:
        for zone, start in list(self._open.items()):
            self.visits[zone].append([round(start, 3), round(self._last_t, 3)])
        self._open.clear()
        return {zone: spans for zone, spans in self.visits.items() if spans}
