"""Diagnostic: per-session ChArUco-only intrinsics (focus consistency check)."""
import glob, json, os, sys
sys.path.insert(0, r"C:\Users\HP\Documents\Anastylos-integration-spatial\calibration")
import cv2, numpy as np
from anastylos import boards
cfg = json.load(open(sys.argv[1], encoding="utf-8")); ccfg = dict(cfg["charuco"])
cb = boards.charuco_board({"charuco": ccfg}); cobj = cb.getChessboardCorners()
det = cv2.aruco.ArucoDetector(boards.dictionary(ccfg["dictionary"]), boards.detector_parameters(cfg))
by = {}
size = None
for p in sorted(glob.glob(os.path.join(sys.argv[2], "*.jpg"))):
    im = cv2.imread(p, cv2.IMREAD_GRAYSCALE | cv2.IMREAD_IGNORE_ORIENTATION)
    size = (im.shape[1], im.shape[0])
    c, ids, _ = det.detectMarkers(im)
    if ids is None: continue
    _r, cc, cids = cv2.aruco.interpolateCornersCharuco(c, ids, im, cb)
    if cids is None or len(cids) < 10: continue
    by.setdefault(os.path.basename(p)[:2], []).append((np.array([cobj[i] for i in cids.flatten()], np.float32), cc.reshape(-1, 2).astype(np.float32)))
print("image size (sensor frame)", size)
for s, v in sorted(by.items()) + [("ALL", [x for vv in by.values() for x in vv])]:
    if len(v) < 6: print(s, "views", len(v), "too few"); continue
    rms, K, D, _, _ = cv2.calibrateCamera([o for o, _ in v], [i for _, i in v], size, None, None)
    print(f"{s}: views={len(v):3d} rms={rms:.3f} fx={K[0,0]:.1f} fy={K[1,1]:.1f} cx={K[0,2]:.1f} cy={K[1,2]:.1f} k1={D[0,0]:.3f}")
