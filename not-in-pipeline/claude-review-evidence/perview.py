"""Diagnostic only: per-view PnP reprojection RMS + Laplacian sharpness with a fitted profile."""
import glob, json, sys
sys.path.insert(0, r"C:\Users\HP\Documents\Anastylos-integration-spatial\calibration")
import cv2, numpy as np
from anastylos import boards
cfg = json.load(open(sys.argv[1], encoding="utf-8")); prof = json.load(open(sys.argv[2]))
K = np.array(prof["K"]).reshape(3, 3); D = np.array(prof["distortion"])
ccfg = dict(cfg["charuco"]); cb = boards.charuco_board({"charuco": ccfg}); cobj = cb.getChessboardCorners()
cdet = cv2.aruco.ArucoDetector(boards.dictionary(ccfg["dictionary"]), boards.detector_parameters(cfg))
sheet = next(s for s in cfg["sheets"] if s["sheet_id"] == "surface_a3")
fm = {m["id"]: m["corners_frame_mm"] for m in sheet["markers"]}
adet = cv2.aruco.ArucoDetector(boards.dictionary(sheet["markers"][0]["dictionary"]), boards.detector_parameters(cfg))
for kind, pattern in (("charuco", sys.argv[3]), ("a3", sys.argv[4])):
    for p in sorted(glob.glob(pattern)):
        im = cv2.imread(p, cv2.IMREAD_GRAYSCALE | cv2.IMREAD_IGNORE_ORIENTATION)
        sharp = cv2.Laplacian(im, cv2.CV_64F).var()
        if kind == "charuco":
            c, ids, _ = cdet.detectMarkers(im); _r, cc, cids = cv2.aruco.interpolateCornersCharuco(c, ids, im, cb)
            obj = np.array([cobj[i] for i in cids.flatten()], np.float64); img = cc.reshape(-1, 2).astype(np.float64)
        else:
            c, ids, _ = adet.detectMarkers(im); by = {int(m): c[i][0] for i, m in enumerate(ids.flatten())}
            ks = [m for m in sorted(by) if m in fm]
            obj = np.array([[*pt[:2], 0.0] if len(pt) == 2 else pt for m in ks for pt in fm[m]], np.float64)
            img = np.array([pt for m in ks for pt in by[m]], np.float64)
        ok, rv, tv = cv2.solvePnP(obj, img, K, D, flags=cv2.SOLVEPNP_SQPNP)
        pr, _ = cv2.projectPoints(obj, rv, tv, K, D)
        rms = float(np.sqrt(np.mean(np.sum((pr.reshape(-1, 2) - img) ** 2, axis=1))))
        print(f"{kind:7s} rms={rms:5.2f} sharp={sharp:7.1f} dist={np.linalg.norm(tv):6.0f}mm {p[-45:-37]}")
