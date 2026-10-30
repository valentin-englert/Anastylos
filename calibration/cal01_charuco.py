"""CAL-01 ChArUco intrinsics calibration → anastylos-camera-calibration@1 profile.

Methodology (fixed for CAL-01, reported in docs/integration/CAL-01.md):
  1. Detect DICT_6X6_250 markers with the config's corner refinement
     (CORNER_REFINE_SUBPIX) and interpolate ChArUco corners.
  2. Deterministic fit/held-out split: sorted filename order, every view
     with index % 3 == 2 is held out, the rest fit the intrinsics. Both
     sets therefore span the whole capture session.
  3. Fit K + 5 distortion coefficients with calibrateCameraCharuco.
  4. Held-out check: per-view SQPNP pose from the fitted K, then RMS of
     the reprojected ChArUco corners. Held-out views never enter the fit.
  5. Emit a profile with metric_validity "unknown". Only the independent
     A3 scale check (cal01_scale_check.py) may flip it to "validated".

Fail-closed: fewer than --min-fit-views usable fit views, fewer than
--min-heldout usable held-out views, or a failed solver exit non-zero
with a reason. No invented pass/fail: RMS values are reported as-is.

Board model is built ONLY from calibration-config.json via
anastylos.boards.charuco_board (legacy_pattern=false, ids 0..16),
with the OPERATOR-MEASURED print scale applied to square/marker length.

Pixels are read in SENSOR frame (IMREAD_IGNORE_ORIENTATION): the live
analysis Y-plane carries no EXIF orientation, so a display-frame fit
would silently misplace the principal point.
"""

from __future__ import annotations

import argparse
import datetime
import glob
import hashlib
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from anastylos import boards  # noqa: E402

ASSET_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "anastylos-cal01-asset")


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rms_px(observed: np.ndarray, projected: np.ndarray) -> float:
    diff = observed.reshape(-1, 2) - projected.reshape(-1, 2)
    return float(np.sqrt(np.mean(np.sum(diff * diff, axis=1))))


def main() -> int:
    parser = argparse.ArgumentParser(description="CAL-01 ChArUco calibration")
    parser.add_argument("--images", required=True,
                        help="glob(s) for calibration JPEGs, comma-separated")
    parser.add_argument("--config", required=True, help="calibration-config.json")
    parser.add_argument("--square-scale", type=float, default=1.0,
                        help="measured print scale (operator measurement / nominal)")
    parser.add_argument("--scale-source", default="",
                        help="provenance note for --square-scale, e.g. operator values")
    parser.add_argument("--camera-id", required=True)
    parser.add_argument("--sensor-mode", required=True,
                        help="e.g. 0-960x720-manual (cameraId-analysisWxH-optics)")
    parser.add_argument("--focus-diopters", type=float, default=None)
    parser.add_argument("--profile-id", default=None, help="default: random uuid4")
    parser.add_argument("--fix-k3", action="store_true",
                        help="fix k3=0 (use when corner coverage is central)")
    parser.add_argument("--zero-dist", action="store_true",
                        help="pure pinhole: fix all distortion at 0")
    parser.add_argument("--min-fit-views", type=int, default=15)
    parser.add_argument("--min-heldout", type=int, default=5)
    parser.add_argument("--min-corners", type=int, default=10)
    parser.add_argument("--out", required=True, help="profile JSON path")
    parser.add_argument("--diag", required=True, help="diagnostics JSON path")
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as handle:
        cfg = json.load(handle)
    cfg["charuco"] = dict(cfg["charuco"])
    cfg["charuco"]["square_length_mm"] = (
        float(cfg["charuco"]["square_length_mm"]) * args.square_scale
    )
    cfg["charuco"]["marker_length_mm"] = (
        float(cfg["charuco"]["marker_length_mm"]) * args.square_scale
    )
    board = boards.charuco_board(cfg)
    detector = cv2.aruco.ArucoDetector(
        boards.dictionary(cfg["charuco"]["dictionary"]),
        boards.detector_parameters(cfg),
    )
    obj_points_all = board.getChessboardCorners()

    paths = sorted(p for pattern in args.images.split(",")
                     for p in glob.glob(pattern.strip()))
    if not paths:
        print("CAL01_CALIB: no images matched", file=sys.stderr)
        return 2
    views = []
    image_size = None
    for path in paths:
        # Sensor frame, NOT display frame: cv2.imread would otherwise apply
        # EXIF orientation (all our stills are Orientation 6). The analysis
        # Y-plane carries no EXIF, so the profile must live in sensor pixels.
        image = cv2.imread(path, cv2.IMREAD_GRAYSCALE | cv2.IMREAD_IGNORE_ORIENTATION)
        if image is None:
            print(f"CAL01_CALIB: unreadable {path}", file=sys.stderr)
            return 2
        if image_size is None:
            image_size = (image.shape[1], image.shape[0])
        elif (image.shape[1], image.shape[0]) != image_size:
            print(f"CAL01_CALIB: mixed geometry, {path} differs", file=sys.stderr)
            return 2
        corners, ids, _ = detector.detectMarkers(image)
        if ids is None or len(ids) == 0:
            views.append({"path": path, "usable": False, "reason": "no-markers"})
            continue
        _ret, charuco_corners, charuco_ids = cv2.aruco.interpolateCornersCharuco(
            corners, ids, image, board
        )
        if charuco_ids is None or len(charuco_ids) < args.min_corners:
            count = 0 if charuco_ids is None else len(charuco_ids)
            views.append({"path": path, "usable": False,
                          "reason": f"corners-{count}-below-min-{args.min_corners}"})
            continue
        flat_ids = charuco_ids.flatten().tolist()
        obj = np.array([obj_points_all[i] for i in flat_ids], dtype=np.float64)
        img = charuco_corners.reshape(-1, 2).astype(np.float64)
        views.append({"path": path, "usable": True, "sha256": sha256_file(path),
                      "corners": len(flat_ids), "markers": len(ids),
                      "obj": obj, "img": img})
    usable = [v for v in views if v["usable"]]
    fit = [v for i, v in enumerate(usable) if i % 3 != 2]
    heldout = [v for i, v in enumerate(usable) if i % 3 == 2]
    if len(fit) < args.min_fit_views:
        print(f"CAL01_CALIB: fit views {len(fit)} below min {args.min_fit_views}",
              file=sys.stderr)
        return 2
    if len(heldout) < args.min_heldout:
        print(f"CAL01_CALIB: held-out views {len(heldout)} below min {args.min_heldout}",
              file=sys.stderr)
        return 2

    # calibrateCameraCharuco needs per-view id lists aligned with obj rows.
    fit_ids = []
    for v in fit:
        ids = np.array(
            [int(np.where((obj_points_all == row).all(axis=1))[0][0]) for row in v["obj"]],
            dtype=np.int32,
        ).reshape(-1, 1)
        fit_ids.append(ids)
    if args.zero_dist:
        flags = (cv2.CALIB_FIX_K1 | cv2.CALIB_FIX_K2 | cv2.CALIB_FIX_K3 |
                 cv2.CALIB_FIX_K4 | cv2.CALIB_FIX_K5 | cv2.CALIB_FIX_K6 |
                 cv2.CALIB_FIX_TANGENT_DIST)
    else:
        flags = cv2.CALIB_FIX_K3 if args.fix_k3 else 0
    retval, camera_matrix, dist_coeffs, rvecs, tvecs = cv2.aruco.calibrateCameraCharuco(
        [v["img"].reshape(-1, 1, 2).astype(np.float32) for v in fit],
        fit_ids,
        board,
        image_size,
        None,
        None,
        flags=flags,
    )
    if not retval:
        print("CAL01_CALIB: solver declined", file=sys.stderr)
        return 2
    dist = dist_coeffs.flatten().tolist()
    if len(dist) != 5:
        print(f"CAL01_CALIB: expected 5 distortion coeffs, got {len(dist)}",
              file=sys.stderr)
        return 2

    def asset_id(sha: str) -> str:
        return str(uuid.uuid5(ASSET_NAMESPACE, sha))

    per_view, all_fit_corners = {}, []
    for view, rvec, tvec in zip(fit, rvecs, tvecs):
        projected, _ = cv2.projectPoints(view["obj"], rvec, tvec, camera_matrix,
                                         dist_coeffs)
        per_view[asset_id(view["sha256"])] = rms_px(view["img"], projected)
        all_fit_corners.append(view["img"])
    heldout_rms, heldout_ids = {}, []
    for view in heldout:
        ok, rvec, tvec = cv2.solvePnP(view["obj"], view["img"], camera_matrix,
                                      dist_coeffs, flags=cv2.SOLVEPNP_SQPNP)
        if not ok:
            print(f"CAL01_CALIB: held-out solvePnP failed for {view['path']}",
                  file=sys.stderr)
            return 2
        projected, _ = cv2.projectPoints(view["obj"], rvec, tvec, camera_matrix,
                                         dist_coeffs)
        aid = asset_id(view["sha256"])
        heldout_rms[aid] = rms_px(view["img"], projected)
        heldout_ids.append(aid)
    pooled_heldout = float(np.sqrt(np.mean(np.square(list(heldout_rms.values())))))
    stacked = np.vstack(all_fit_corners).astype(np.float32)
    hull = cv2.convexHull(stacked)
    edge_coverage = float(cv2.contourArea(hull) / (image_size[0] * image_size[1]))

    view_set = {v["path"]: "fit" for v in fit}
    view_set.update({v["path"]: "heldout" for v in heldout})
    fit_ids_out = [asset_id(v["sha256"]) for v in fit]
    # Validator semantics: calibration_view_ids covers the whole run
    # (fit + held-out); per_view_rms_px must key exactly that set.
    all_ids_out = fit_ids_out + heldout_ids
    per_view_all = dict(per_view)
    per_view_all.update(heldout_rms)
    assets = [{"asset_id": asset_id(v["sha256"]), "sha256": v["sha256"],
               "path": os.path.basename(v["path"])} for v in usable]
    script_sha = sha256_file(os.path.abspath(__file__))
    thresholds = (
        f"min_fit_views={args.min_fit_views};min_heldout={args.min_heldout};"
        f"min_corners={args.min_corners};split=index_mod3;square_scale={args.square_scale};"
        f"fix_k3={args.fix_k3};zero_dist={args.zero_dist}"
    )
    threshold_sha = hashlib.sha256(
        (script_sha + thresholds).encode("utf-8")).hexdigest()
    profile_id = args.profile_id or str(uuid.uuid4())
    created = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")
    k = camera_matrix.flatten().tolist()
    profile = {
        "schema": "anastylos-camera-calibration@1",
        "profile_id": profile_id,
        "camera_id": args.camera_id,
        "sensor_mode": args.sensor_mode,
        "reference_image_size_px": [image_size[0], image_size[1]],
        "reference_pixel_frame_id": f"cam{args.camera_id}-native-{image_size[0]}x{image_size[1]}",
        "K": k,
        "distortion": dist,
        "camera_model": "brown_conrady_5",
        "zoom": 1,
        "focus_distance_diopters": args.focus_diopters,
        "physical_lens_id": None,
        "sensor_crop": None,
        "pixel_convention": "top_left_pixel_center_0_0_x_right_y_down",
        "board_ref": {"board_frame_id": "charuco_a4", "layout_role": "charuco_a4"},
        "algorithm": "opencv-charuco-calibrate",
        "algorithm_version": f"cv2-{cv2.__version__}-fixk3-{args.fix_k3}-zerodist-{args.zero_dist}",
        "build_sha": "see-diagnostics",
        "created_at": created,
        "source_id": str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"anastylos-cal01-source:{args.camera_id}:{args.sensor_mode}")),
        "evidence_class": "physical",
        "metric_validity": "unknown",
        "limitations": [
            "CAL-01 measured profile: K/distortion fitted on physical ChArUco stills. "
            "metric_validity stays unknown until the independent A3 scale check passes."
        ],
        "calibration_image_assets": [
            {"asset_id": a["asset_id"], "sha256": a["sha256"]} for a in assets
        ],
        "validation": {
            "calibration_view_ids": all_ids_out,
            "held_out_view_ids": heldout_ids,
            "held_out_rms_px": pooled_heldout,
            "per_view_rms_px": per_view_all,
            "edge_coverage": edge_coverage,
            "independent_scale_check_mm": None,
            "residual_map_ref": None,
            "threshold_config_sha256": threshold_sha,
        },
    }
    diag = {
        "script_sha256": script_sha,
        "thresholds": thresholds,
        "threshold_config_sha256": threshold_sha,
        "opencv": cv2.__version__,
        "square_scale": args.square_scale,
        "scale_source": args.scale_source,
        "image_size": [image_size[0], image_size[1]],
        "usable_views": len(usable),
        "fit_views": len(fit),
        "heldout_views": len(heldout),
        "dropped": [v for v in views if not v["usable"]],
        "K": k,
        "distortion": dist,
        "fit_rms_overall": float(
            np.sqrt(np.mean(np.square(list(per_view.values()))))),
        "held_out_rms_px": pooled_heldout,
        "edge_coverage": edge_coverage,
        "views": [
            {"path": os.path.basename(v["path"]), "sha256": v.get("sha256"),
             "corners": v.get("corners"), "markers": v.get("markers"),
             "set": view_set.get(v["path"], "dropped")}
            for v in views
        ],
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(profile, handle, indent=2)
    with open(args.diag, "w", encoding="utf-8") as handle:
        json.dump(diag, handle, indent=2)
    print(f"fit={len(fit)} heldout={len(heldout)} "
          f"fit_rms={diag['fit_rms_overall']:.4f}px "
          f"heldout_rms={pooled_heldout:.4f}px edge={edge_coverage:.3f}")
    print(f"profile: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
