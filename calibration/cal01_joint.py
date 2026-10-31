"""CAL-01 joint two-board calibration (ChArUco + surface_a3).

Why joint: the 150 mm ChArUco board never reaches past r~0.5-0.6 of the
frame at working distance, but overhead operation puts A3 markers at
r~0.7 where the extrapolated distortion errs by ~20 px (measured). The
396 mm A3 board constrains exactly those radii. Both boards carry
operator-verified scale (ChArUco: caliper 30/24; A3: caliper 42 plus
Talmeter diagonal 481.0 vs nominal 480.98).

Method: per-view object/image points from either board feed one
cv2.calibrateCamera (Brown-Conrady 5: k1,k2,p1,p2,k3 — the schema's
camera_model; no rational escalation without a contract change).
Deterministic split over the COMBINED sorted file list (index % 3 == 2
held out) so both boards land in fit and held-out sets. Held-out poses
via SQPNP; pooled + per-view RMS reported, never gated here.
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
    parser = argparse.ArgumentParser(description="CAL-01 joint calibration")
    parser.add_argument("--charuco", required=True, help="glob(s), comma-separated")
    parser.add_argument("--aruco", required=True, help="glob(s), comma-separated")
    parser.add_argument("--aruco-board", default="surface_a3")
    parser.add_argument("--config", required=True)
    parser.add_argument("--square-scale", type=float, default=1.0)
    parser.add_argument("--scale-source", default="")
    parser.add_argument("--camera-id", required=True)
    parser.add_argument("--sensor-mode", required=True)
    parser.add_argument("--focus-diopters", type=float, default=None)
    parser.add_argument("--profile-id", default=None)
    parser.add_argument("--min-charuco-corners", type=int, default=10)
    parser.add_argument("--min-aruco-markers", type=int, default=3)
    parser.add_argument("--min-fit-views", type=int, default=15)
    parser.add_argument("--min-heldout", type=int, default=5)
    parser.add_argument("--out", required=True)
    parser.add_argument("--diag", required=True)
    args = parser.parse_args()

    cfg = json.load(open(args.config, encoding="utf-8"))
    ccfg = dict(cfg["charuco"])
    ccfg["square_length_mm"] = float(ccfg["square_length_mm"]) * args.square_scale
    ccfg["marker_length_mm"] = float(ccfg["marker_length_mm"]) * args.square_scale
    cboard = boards.charuco_board({"charuco": ccfg})
    cdet = cv2.aruco.ArucoDetector(boards.dictionary(ccfg["dictionary"]),
                                   boards.detector_parameters(cfg))
    cobj_all = cboard.getChessboardCorners()

    sheet = next(s for s in cfg["sheets"] if s["sheet_id"] == args.aruco_board)
    frame_markers = {m["id"]: m["corners_frame_mm"] for m in sheet["markers"]}
    adict = boards.dictionary(sheet["markers"][0]["dictionary"])
    adet = cv2.aruco.ArucoDetector(adict, boards.detector_parameters(cfg))

    jobs = ([(p, "charuco") for pattern in args.charuco.split(",")
             for p in glob.glob(pattern.strip())]
            + [(p, "aruco") for pattern in args.aruco.split(",")
               for p in glob.glob(pattern.strip())])
    if not jobs:
        print("CAL01_JOINT: no images matched", file=sys.stderr)
        return 2
    views, image_size = [], None
    for path, kind in sorted(jobs):
        image = cv2.imread(path, cv2.IMREAD_GRAYSCALE | cv2.IMREAD_IGNORE_ORIENTATION)
        if image is None:
            print(f"CAL01_JOINT: unreadable {path}", file=sys.stderr)
            return 2
        if image_size is None:
            image_size = (image.shape[1], image.shape[0])
        elif (image.shape[1], image.shape[0]) != image_size:
            print(f"CAL01_JOINT: mixed geometry, {path} differs", file=sys.stderr)
            return 2
        if kind == "charuco":
            corners, ids, _ = cdet.detectMarkers(image)
            if ids is None:
                views.append({"path": path, "board": kind, "usable": False,
                              "reason": "no-markers"})
                continue
            _ret, ccor, cids = cv2.aruco.interpolateCornersCharuco(
                corners, ids, image, cboard)
            if cids is None or len(cids) < args.min_charuco_corners:
                count = 0 if cids is None else len(cids)
                views.append({"path": path, "board": kind, "usable": False,
                              "reason": f"corners-{count}"})
                continue
            flat = cids.flatten().tolist()
            obj = np.array([cobj_all[i] for i in flat], dtype=np.float64)
            img = ccor.reshape(-1, 2).astype(np.float64)
        else:
            corners, ids, _ = adet.detectMarkers(image)
            if ids is None:
                views.append({"path": path, "board": kind, "usable": False,
                              "reason": "no-markers"})
                continue
            by_id = {int(m): corners[di][0]
                     for di, m in enumerate(ids.flatten().tolist())}
            known = [m for m in sorted(by_id) if m in frame_markers]
            if len(known) < args.min_aruco_markers:
                views.append({"path": path, "board": kind, "usable": False,
                              "reason": f"markers-{len(known)}"})
                continue
            obj, img = [], []
            for mid in known:
                for c in range(4):
                    quad = frame_markers[mid]
                    obj.append([quad[c][0], quad[c][1], 0.0])
                    img.append(by_id[mid][c].tolist())
            obj = np.array(obj, dtype=np.float64)
            img = np.array(img, dtype=np.float64)
        views.append({"path": path, "board": kind, "usable": True,
                      "sha256": sha256_file(path), "points": len(obj),
                      "obj": obj, "img": img})
    usable = [v for v in views if v["usable"]]
    fit = [v for i, v in enumerate(usable) if i % 3 != 2]
    heldout = [v for i, v in enumerate(usable) if i % 3 == 2]
    if len(fit) < args.min_fit_views:
        print(f"CAL01_JOINT: fit views {len(fit)} below min", file=sys.stderr)
        return 2
    if len(heldout) < args.min_heldout:
        print(f"CAL01_JOINT: held-out {len(heldout)} below min", file=sys.stderr)
        return 2

    obj_pts = [v["obj"].astype(np.float32) for v in fit]
    img_pts = [v["img"].reshape(-1, 1, 2).astype(np.float32) for v in fit]
    rms, camera_matrix, dist_coeffs, rvecs, tvecs = cv2.calibrateCamera(
        obj_pts, img_pts, image_size, None, None)
    dist = dist_coeffs.flatten().tolist()[:5]
    if len(dist) != 5:
        print("CAL01_JOINT: expected 5 distortion coeffs", file=sys.stderr)
        return 2

    def asset_id(sha: str) -> str:
        return str(uuid.uuid5(ASSET_NAMESPACE, sha))

    per_view, all_fit = {}, []
    for view, rvec, tvec in zip(fit, rvecs, tvecs):
        projected, _ = cv2.projectPoints(view["obj"], rvec, tvec,
                                         camera_matrix, dist_coeffs)
        per_view[asset_id(view["sha256"])] = rms_px(view["img"], projected)
        all_fit.append(view["img"])
    heldout_rms, heldout_ids = {}, []
    for view in heldout:
        ok, rvec, tvec = cv2.solvePnP(view["obj"], view["img"], camera_matrix,
                                      dist_coeffs, flags=cv2.SOLVEPNP_SQPNP)
        if not ok:
            print(f"CAL01_JOINT: held-out solvePnP failed {view['path']}",
                  file=sys.stderr)
            return 2
        projected, _ = cv2.projectPoints(view["obj"], rvec, tvec,
                                         camera_matrix, dist_coeffs)
        aid = asset_id(view["sha256"])
        heldout_rms[aid] = rms_px(view["img"], projected)
        heldout_ids.append(aid)
    pooled_heldout = float(np.sqrt(np.mean(np.square(list(heldout_rms.values())))))
    stacked = np.vstack(all_fit).astype(np.float32)
    hull = cv2.convexHull(stacked)
    edge_coverage = float(cv2.contourArea(hull) / (image_size[0] * image_size[1]))

    view_set = {v["path"]: "fit" for v in fit}
    view_set.update({v["path"]: "heldout" for v in heldout})
    fit_ids = [asset_id(v["sha256"]) for v in fit]
    all_ids = fit_ids + heldout_ids
    per_view_all = dict(per_view)
    per_view_all.update(heldout_rms)
    script_sha = sha256_file(os.path.abspath(__file__))
    thresholds = (
        f"min_fit_views={args.min_fit_views};min_heldout={args.min_heldout};"
        f"min_charuco={args.min_charuco_corners};min_aruco={args.min_aruco_markers};"
        f"split=index_mod3;square_scale={args.square_scale};joint=charuco+{args.aruco_board}"
    )
    threshold_sha = hashlib.sha256(
        (script_sha + thresholds).encode("utf-8")).hexdigest()
    created = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")
    k = camera_matrix.flatten().tolist()
    profile = {
        "schema": "anastylos-camera-calibration@1",
        "profile_id": args.profile_id or str(uuid.uuid4()),
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
        "board_ref": {"board_frame_id": "joint-charuco-a3",
                      "layout_role": f"joint:charuco+{args.aruco_board}"},
        "algorithm": "opencv-joint-calibrate",
        "algorithm_version": f"cv2-{cv2.__version__}",
        "build_sha": "see-diagnostics",
        "created_at": created,
        "source_id": str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"anastylos-cal01-source:{args.camera_id}:{args.sensor_mode}")),
        "evidence_class": "physical",
        "metric_validity": "unknown",
        "limitations": [
            "CAL-01 joint two-board profile. metric_validity stays unknown "
            "until the independent scale check passes."
        ],
        "calibration_image_assets": [
            {"asset_id": asset_id(v["sha256"]), "sha256": v["sha256"]}
            for v in usable
        ],
        "validation": {
            "calibration_view_ids": all_ids,
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
        "opencv_fit_rms": float(rms),
        "dropped": [{k2: v2 for k2, v2 in v.items() if k2 in ("path", "board", "reason")}
                    for v in views if not v["usable"]],
        "K": k,
        "distortion": dist,
        "fit_rms_overall": float(np.sqrt(np.mean(np.square(list(per_view.values()))))),
        "held_out_rms_px": pooled_heldout,
        "edge_coverage": edge_coverage,
        "views": [
            {"path": v["path"], "board": v["board"], "sha256": v.get("sha256"),
             "points": v.get("points"),
             "set": view_set.get(v["path"], "dropped")}
            for v in views
        ],
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(profile, handle, indent=2)
    with open(args.diag, "w", encoding="utf-8") as handle:
        json.dump(diag, handle, indent=2)
    boards_used = {v["board"] for v in fit}
    print(f"fit={len(fit)} heldout={len(heldout)} "
          f"fit_rms={diag['fit_rms_overall']:.4f}px "
          f"heldout_rms={pooled_heldout:.4f}px edge={edge_coverage:.3f} "
          f"boards={sorted(boards_used)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
