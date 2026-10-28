"""Track a resting device that slides or rotates on the sheet (one rigid body per inventory).

``DevicePose`` keeps a reference image of the device region and a Euclidean
warp from reference to current coordinates. Re-registration is ECC
(coarse-to-fine) from the last warp, and when that fails a SIFT + RANSAC
similarity seed followed by ECC. A fit is only accepted when the masked
correlation is high and the motion is plausible for a device that was
nudged, not flipped; callers still verify each site's anchor ring and end
that site's pose epoch when it does not match.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class AlignmentLimits:
    max_shift_px: float
    max_rotation_deg: float
    min_correlation: float


class DevicePose:
    """Reference image of the device region and the current reference-to-current warp."""

    def __init__(self, region: tuple[int, int, int, int], sites_mask: np.ndarray, limits: AlignmentLimits):
        self.region = region
        self.static_mask = ~sites_mask  # region-shaped: device surface without the sites themselves
        self.limits = limits
        self.reference: np.ndarray | None = None
        self.reference_valid: np.ndarray | None = None
        self.reference_t_s: float | None = None
        self.warp = np.eye(2, 3, dtype=np.float32)
        self.realignments: list[dict] = []
        self._aligned_at: float | None = None
        self._aligned: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None
        self._attempted_at: float | None = None
        self._attempt_accepted = False

    def initialize(self, t_s: float, gray: np.ndarray, motion: np.ndarray, fresh_skin: np.ndarray) -> None:
        """Take the reference; pixels covered by hands or motion at this moment are never used for alignment."""
        busy = cv2.dilate((self._crop(motion) | self._crop(fresh_skin)).astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
        self.reference = self._crop(gray).copy()
        self.reference_valid = ~busy
        self.reference_t_s = t_s

    def aligned_layers(self, t_s: float, gray: np.ndarray, motion: np.ndarray, fresh_skin: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Device region of gray, motion and fresh skin in reference coordinates (cached per sample)."""
        if self._aligned_at != t_s:
            self._aligned = (
                warp_into_reference(self._crop(gray), self.warp),
                _warp_mask(self._crop(motion), self.warp),
                _warp_mask(self._crop(fresh_skin), self.warp),
            )
            self._aligned_at = t_s
        return self._aligned

    def realign(self, t_s: float, gray: np.ndarray, fresh_skin: np.ndarray) -> bool:
        """Re-register once per sample; True when the warp was updated."""
        if self._attempted_at == t_s:
            return self._attempt_accepted
        self._attempted_at = t_s
        hands = cv2.dilate(self._crop(fresh_skin).astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
        mask = self.static_mask & self.reference_valid & ~hands
        warp, report = align(self.reference, self._crop(gray), mask, self.warp, self.limits, feature_mask=mask)
        self._attempt_accepted = warp is not None
        self.realignments.append({"t_s": round(t_s, 3), "accepted": self._attempt_accepted, **report})
        if warp is not None:
            self.warp = warp
            self._aligned_at = None
        return self._attempt_accepted

    def _crop(self, image: np.ndarray) -> np.ndarray:
        x0, y0, x1, y1 = self.region
        return image[y0:y1, x0:x1]


def _warp_mask(mask: np.ndarray, warp: np.ndarray) -> np.ndarray:
    height, width = mask.shape
    return cv2.warpAffine(mask.astype(np.uint8), warp, (width, height), flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP) > 0


def warp_into_reference(current: np.ndarray, warp: np.ndarray) -> np.ndarray:
    height, width = current.shape
    return cv2.warpAffine(current, warp, (width, height), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REPLICATE)


def align(reference: np.ndarray, current: np.ndarray, mask: np.ndarray, initial: np.ndarray, limits: AlignmentLimits,
          feature_mask: np.ndarray | None = None) -> tuple[np.ndarray | None, dict]:
    """Return (2x3 warp mapping reference coords to current coords, report), or (None, report).

    ECC from ``initial`` first; when that fails and ``feature_mask`` is given,
    a SIFT + RANSAC similarity fit seeds a second ECC attempt.
    """
    warp, report = _ecc(reference, current, mask, initial, limits)
    if warp is not None or feature_mask is None:
        return warp, report
    seed, seed_report = _feature_seed(reference, current, feature_mask)
    report = {"ecc_from_previous": report, "feature_seed": seed_report}
    if seed is None:
        return None, report
    warp, refined = _ecc(reference, current, mask, seed, limits)
    report["ecc_from_seed"] = refined
    return warp, report


def _feature_seed(reference: np.ndarray, current: np.ndarray, feature_mask: np.ndarray) -> tuple[np.ndarray | None, dict]:
    sift = cv2.SIFT_create()
    ref_u8, cur_u8 = (np.clip(image, 0, 255).astype(np.uint8) for image in (reference, current))
    ref_points, ref_desc = sift.detectAndCompute(ref_u8, feature_mask.astype(np.uint8) * 255)
    cur_points, cur_desc = sift.detectAndCompute(cur_u8, None)
    report = {"reference_keypoints": len(ref_points), "current_keypoints": len(cur_points)}
    if ref_desc is None or cur_desc is None or len(ref_points) < 6 or len(cur_points) < 6:
        return None, report
    pairs = cv2.BFMatcher(cv2.NORM_L2).knnMatch(ref_desc, cur_desc, k=2)
    good = [first for first, second in (pair for pair in pairs if len(pair) == 2) if first.distance < 0.75 * second.distance]
    report["ratio_matches"] = len(good)
    if len(good) < 6:
        return None, report
    source = np.float32([ref_points[match.queryIdx].pt for match in good])
    target = np.float32([cur_points[match.trainIdx].pt for match in good])
    similarity, inliers = cv2.estimateAffinePartial2D(source, target, method=cv2.RANSAC, ransacReprojThreshold=2.0)
    report["ransac_inliers"] = 0 if inliers is None else int(inliers.sum())
    if similarity is None or report["ransac_inliers"] < 6:
        return None, report
    scale = math.hypot(float(similarity[0, 0]), float(similarity[1, 0]))
    report["scale"] = round(scale, 4)
    rigid = similarity.astype(np.float32)
    rigid[:, :2] /= scale  # Euclidean seed: the sheet scale is fixed
    return rigid, report


def _ecc(reference: np.ndarray, current: np.ndarray, mask: np.ndarray, initial: np.ndarray, limits: AlignmentLimits) -> tuple[np.ndarray | None, dict]:
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-5)
    mask_u8 = mask.astype(np.uint8) * 255
    warp = initial.astype(np.float32).copy()
    report: dict = {}
    try:
        coarse = warp.copy()
        coarse[:, 2] /= 2.0
        small_reference, small_current = cv2.pyrDown(reference), cv2.pyrDown(current)
        small_mask = cv2.resize(mask_u8, (small_reference.shape[1], small_reference.shape[0]), interpolation=cv2.INTER_NEAREST)
        _, coarse = cv2.findTransformECC(small_reference, small_current, coarse, cv2.MOTION_EUCLIDEAN, criteria, small_mask, 5)
        warp = coarse.copy()
        warp[:, 2] *= 2.0
        correlation, warp = cv2.findTransformECC(reference, current, warp, cv2.MOTION_EUCLIDEAN, criteria, mask_u8, 5)
    except cv2.error as exc:
        report["failure"] = str(exc).splitlines()[-1][:120]
        return None, report
    shift = math.hypot(float(warp[0, 2]), float(warp[1, 2]))
    rotation = math.degrees(math.atan2(float(warp[1, 0]), float(warp[0, 0])))
    report.update({"correlation": round(float(correlation), 4), "shift_px": round(shift, 2), "rotation_deg": round(rotation, 2)})
    if correlation < limits.min_correlation or shift > limits.max_shift_px or abs(rotation) > limits.max_rotation_deg:
        report["failure"] = "outside limits"
        return None, report
    return warp, report
