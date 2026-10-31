"""CAL-01 independent validation: cross-board check + scale anchor.

Two independent legs (both use images/measurements NEVER used in the fit):

1. Cross-board generalization: solvePnP on surface_a3 stills (different
   board, different dictionary DICT_5X5_100, different images) with the
   fitted K. Reports pooled + per-view RMS. Gate: pooled <= 1.5 px
   (MAX_VIEW_RMS_GATE_PX) and every view <= 4.5 px (LIVE_MAX_PX scaled
   to 1920 width: 3.0 * 1920/1280).
2. Scale anchor: operator ruler/caliper reading vs nominal print length.
   Gate: |measured - nominal| <= uncertainty.

Only when BOTH gates hold AND the spatial validator reports no reasons
and no reviews does metric_validity flip unknown -> validated. Anything
else leaves the profile unknown with an explanatory limitation.
Exits non-zero on any detection/parse failure (fail-closed).
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "spatial"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from anastylos import boards  # noqa: E402
from anastylos_spatial.profiles import validate_profile  # noqa: E402

POOLED_GATE_PX = 1.5
VIEW_GATE_PX = 4.5
MIN_MARKERS = 3


def rms_px(observed: np.ndarray, projected: np.ndarray) -> float:
    diff = observed.reshape(-1, 2) - projected.reshape(-1, 2)
    return float(np.sqrt(np.mean(np.sum(diff * diff, axis=1))))


def main() -> int:
    parser = argparse.ArgumentParser(description="CAL-01 scale/cross-board check")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--images", required=True, help="glob for A3 stills")
    parser.add_argument("--config", required=True)
    parser.add_argument("--board", default="surface_a3")
    parser.add_argument("--nominal-mm", type=float, required=True)
    parser.add_argument("--measured-mm", type=float, required=True)
    parser.add_argument("--uncertainty-mm", type=float, required=True)
    parser.add_argument("--scale-what", required=True, help="what was measured")
    parser.add_argument("--out", required=True, help="updated profile path")
    parser.add_argument("--diag", required=True)
    parser.add_argument("--min-views", type=int, default=3)
    args = parser.parse_args()

    profile = json.load(open(args.profile, encoding="utf-8"))
    cfg = json.load(open(args.config, encoding="utf-8"))
    sheet = next(s for s in cfg["sheets"] if s["sheet_id"] == args.board)
    frame_markers = {m["id"]: m["corners_frame_mm"] for m in sheet["markers"]}
    dictionary = boards.dictionary(sheet["markers"][0]["dictionary"])
    detector = cv2.aruco.ArucoDetector(dictionary, boards.detector_parameters(cfg))

    k = np.array(profile["K"], dtype=np.float64).reshape(3, 3)
    dist = np.array(profile["distortion"], dtype=np.float64)
    ref_size = tuple(profile["reference_image_size_px"])

    paths = sorted(glob.glob(args.images))
    if not paths:
        print("CAL01_SCALE: no images matched", file=sys.stderr)
        return 2
    per_view, views, skipped = {}, [], []
    for path in paths:
        image = cv2.imread(path, cv2.IMREAD_GRAYSCALE | cv2.IMREAD_IGNORE_ORIENTATION)
        if image is None:
            print(f"CAL01_SCALE: unreadable {path}", file=sys.stderr)
            return 2
        if (image.shape[1], image.shape[0]) != ref_size:
            print(f"CAL01_SCALE: geometry mismatch {path}", file=sys.stderr)
            return 2
        corners, ids, _ = detector.detectMarkers(image)
        if ids is None:
            print(f"CAL01_SCALE: {path}: no markers", file=sys.stderr)
            return 2
        # Detection order != sorted order: index corners by id, never by
        # sorted position (a sorted-index pairing silently scrambles every
        # correspondence and SQPNP diverges to 1e20 px RMS).
        by_id = {int(mid): corners[di][0]
                 for di, mid in enumerate(ids.flatten().tolist())}
        found = sorted(by_id)
        known = [i for i in found if i in frame_markers]
        if len(known) < MIN_MARKERS:
            # Underconstrained for a board pose (not a bad fit): skipped
            # openly, counted in diag, never silently dropped.
            skipped.append({"path": os.path.basename(path),
                            "markers": found, "reason": "below-min-markers"})
            continue
        obj, img = [], []
        for marker_id in found:
            if marker_id not in frame_markers:
                continue
            quad = frame_markers[marker_id]
            for c in range(4):
                obj.append([quad[c][0], quad[c][1], 0.0])
                img.append(by_id[marker_id][c].tolist())
        obj = np.array(obj, dtype=np.float64)
        img = np.array(img, dtype=np.float64)
        ok, rvec, tvec = cv2.solvePnP(obj, img, k, dist, flags=cv2.SOLVEPNP_SQPNP)
        if not ok:
            print(f"CAL01_SCALE: solvePnP failed {path}", file=sys.stderr)
            return 2
        projected, _ = cv2.projectPoints(obj, rvec, tvec, k, dist)
        rms = rms_px(img, projected)
        per_view[os.path.basename(path)] = rms
        views.append({"path": os.path.basename(path), "markers": known,
                      "rms_px": rms, "tvec": tvec.flatten().tolist()})
    if len(views) < args.min_views:
        print(f"CAL01_SCALE: usable views {len(views)} below min {args.min_views}",
              file=sys.stderr)
        return 2
    pooled = float(np.sqrt(np.mean(np.square(list(per_view.values())))))
    scale_ok = abs(args.measured_mm - args.nominal_mm) <= args.uncertainty_mm
    rms_ok = pooled <= POOLED_GATE_PX and all(v <= VIEW_GATE_PX for v in per_view.values())

    profile["validation"]["independent_scale_check_mm"] = {
        "nominal_mm": args.nominal_mm,
        "measured_mm": args.measured_mm,
        "uncertainty_mm": args.uncertainty_mm,
        "what": args.scale_what,
    }
    verdict = validate_profile(profile)
    validated = (rms_ok and scale_ok
                 and not verdict["reasons"] and not verdict["reviews"])
    if validated:
        profile["metric_validity"] = "validated"
        profile["limitations"] = [
            "CAL-01 measured profile: K/distortion fitted on 34 physical ChArUco stills "
            f"(held-out RMS {profile['validation']['held_out_rms_px']:.3f}px); cross-board "
            f"surface_a3 RMS {pooled:.3f}px on {len(views)} independent stills; scale anchor "
            f"{args.scale_what}: {args.measured_mm} vs nominal {args.nominal_mm} "
            f"(+/-{args.uncertainty_mm})."
        ]
    else:
        profile["limitations"].append(
            f"Independent check NOT passed: rms_ok={rms_ok} scale_ok={scale_ok} "
            f"validator={verdict}."
        )
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(profile, handle, indent=2)
    diag = {"views": views, "skipped": skipped, "pooled_rms_px": pooled,
            "rms_ok": rms_ok, "scale_ok": scale_ok, "validator": verdict,
            "metric_validity": profile["metric_validity"]}
    with open(args.diag, "w", encoding="utf-8") as handle:
        json.dump(diag, handle, indent=2)
    print(f"cross-board pooled={pooled:.4f}px rms_ok={rms_ok} scale_ok={scale_ok} "
          f"validity={profile['metric_validity']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
