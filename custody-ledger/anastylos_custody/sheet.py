"""Fixed-camera sheet registration, drift compensation, and rectification.

One millimetre homography per session, taken from the first frame that passes
the unchanged change-detection registration gate. A phone on a stand still
creeps (measured on SNES-01: marker corners drift up to ~8 px at 4K over
3.5 min), so ``DriftTracker`` re-fits a pixel-to-pixel homography from the
registration-time marker corners to the current ones and the rectifier folds
it in. Fits with too few inlier corners or a large residual are rejected and
counted; the last good fit stays in use.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from anastylos_diff import engine


@dataclass
class SheetRegistration:
    frame_index: int
    t_s: float
    homography_image_to_mm: np.ndarray
    marker_corners_px: dict[int, np.ndarray]
    report: dict


class SheetReader:
    """Marker detection and gate-checked registration for one view."""

    def __init__(self, harness: dict, kit: dict, view: str):
        self.harness = harness
        self.kit = engine.Kit(kit, harness["registration"]["dictionary"])
        self.detector = engine.make_detector(harness)
        self.view = view
        if view not in self.kit.views:
            raise SystemExit(f"view {view!r} is not in the calibration kit")

    def markers(self, image: np.ndarray) -> dict:
        by_view, _unknown = engine.detect_markers(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), self.detector, self.kit)
        return by_view

    def register(self, image: np.ndarray) -> tuple[dict, np.ndarray | None, dict]:
        by_view = self.markers(image)
        report, homography = engine.register_view(by_view, self.view, self.kit, self.harness)
        corners = {key: value for key, value in by_view.get(self.view, {}).items() if key != "_duplicates"}
        return report, homography, corners

    def view_corners(self, image: np.ndarray) -> dict[int, np.ndarray]:
        return {key: value for key, value in self.markers(image).get(self.view, {}).items() if key != "_duplicates"}

    def zones(self) -> list[dict]:
        return self.kit.views[self.view]["zones"]

    def page_rect_mm(self) -> list[float]:
        return self.kit.views[self.view]["page_rect_mm"]


def find_registration(reader: SheetReader, samples, search_window_s: float) -> SheetRegistration:
    attempts = []
    for sample in samples:
        if sample.t_s > search_window_s:
            break
        report, homography, corners = reader.register(sample.image)
        if homography is not None:
            report["attempts_before"] = attempts
            return SheetRegistration(sample.frame_index, sample.t_s, homography, corners, report)
        attempts.append({"frame_index": sample.frame_index, "reasons": report["reasons"]})
    raise SystemExit(f"no frame in the first {search_window_s} s passes the registration gate: {attempts[-3:]}")


class DriftTracker:
    """Pixel homography from registration-time marker corners to the current frame's."""

    def __init__(self, reader: SheetReader, reference_corners: dict[int, np.ndarray], min_markers: int, max_residual_px: float):
        self.reader = reader
        self.reference = reference_corners
        self.min_markers = min_markers
        self.max_residual_px = max_residual_px
        self.reference_to_current = np.eye(3)
        self.fits: list[dict] = []

    def update(self, t_s: float, image: np.ndarray) -> None:
        found = self.reader.view_corners(image)
        shared = sorted(marker for marker in found if marker in self.reference)
        record = {"t_s": round(t_s, 3), "markers": len(shared), "accepted": False}
        self.fits.append(record)
        if len(shared) < self.min_markers:
            return
        source = np.concatenate([self.reference[marker] for marker in shared]).astype(np.float64)
        target = np.concatenate([found[marker] for marker in shared]).astype(np.float64)
        homography, mask = cv2.findHomography(source, target, cv2.RANSAC, 3.0)
        if homography is None:
            return
        inliers = mask.ravel().astype(bool)
        projected = cv2.perspectiveTransform(source.reshape(-1, 1, 2), homography).reshape(-1, 2)
        residual = np.linalg.norm(projected - target, axis=1)[inliers]
        drift = np.linalg.norm(projected - source, axis=1)
        record.update({"inlier_corners": int(inliers.sum()), "residual_rms_px": round(float(np.sqrt(np.mean(residual ** 2))), 3),
                       "max_drift_px": round(float(drift.max()), 2)})
        if inliers.sum() >= 4 * self.min_markers and record["residual_rms_px"] <= self.max_residual_px:
            self.reference_to_current = homography
            record["accepted"] = True

    def report(self) -> dict:
        accepted = [fit for fit in self.fits if fit["accepted"]]
        return {
            "fits": len(self.fits),
            "accepted": len(accepted),
            "max_drift_px": max((fit["max_drift_px"] for fit in accepted), default=None),
            "max_residual_rms_px": max((fit["residual_rms_px"] for fit in accepted), default=None),
            "rejected_t_s": [fit["t_s"] for fit in self.fits if not fit["accepted"]],
        }


class Rectifier:
    """Warp camera frames into the sheet's millimetre frame at a working scale."""

    def __init__(self, homography_image_to_mm: np.ndarray, page_rect_mm: list[float], px_per_mm: float, margin_mm: float):
        self.ppm = float(px_per_mm)
        self.x0 = page_rect_mm[0] - margin_mm
        self.y0 = page_rect_mm[1] - margin_mm
        self.size = (int(round((page_rect_mm[2] + 2 * margin_mm) * self.ppm)), int(round((page_rect_mm[3] + 2 * margin_mm) * self.ppm)))
        to_px = np.array([[self.ppm, 0, -self.x0 * self.ppm], [0, self.ppm, -self.y0 * self.ppm], [0, 0, 1.0]])
        self._to_rectified = to_px @ homography_image_to_mm
        self._half_res = np.diag([2.0, 2.0, 1.0])

    def rectify(self, image: np.ndarray, reference_to_current: np.ndarray) -> np.ndarray:
        warp = self._to_rectified @ np.linalg.inv(reference_to_current) @ self._half_res
        return cv2.warpPerspective(cv2.pyrDown(image), warp, self.size, flags=cv2.INTER_LINEAR)

    def visible(self, image_shape: tuple[int, ...], reference_to_current: np.ndarray) -> np.ndarray:
        """Bool mask of rectified pixels that the camera image actually covers."""
        warp = self._to_rectified @ np.linalg.inv(reference_to_current) @ self._half_res
        half = np.full(((image_shape[0] + 1) // 2, (image_shape[1] + 1) // 2), 255, np.uint8)
        return cv2.warpPerspective(half, warp, self.size, flags=cv2.INTER_NEAREST) > 0

    def rect_to_px(self, rect_mm: list[float]) -> tuple[int, int, int, int]:
        """Half-open pixel box (x0, y0, x1, y1) of a frame-mm rectangle, clipped to the image."""
        x, y, w, h = rect_mm
        x0 = int(round((x - self.x0) * self.ppm))
        y0 = int(round((y - self.y0) * self.ppm))
        x1 = int(round((x + w - self.x0) * self.ppm))
        y1 = int(round((y + h - self.y0) * self.ppm))
        width, height = self.size
        return max(0, x0), max(0, y0), min(width, x1), min(height, y1)
