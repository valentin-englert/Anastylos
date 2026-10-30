"""Classify stills by board, using cal01_joint.py's own detectors and gates (read-only)."""
import glob, json, os, shutil, sys
sys.path.insert(0, r"C:\Users\HP\Documents\Anastylos-integration-spatial\calibration")
import cv2
from anastylos import boards
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
ccfg = dict(cfg["charuco"]); cboard = boards.charuco_board({"charuco": ccfg})
cdet = cv2.aruco.ArucoDetector(boards.dictionary(ccfg["dictionary"]), boards.detector_parameters(cfg))
sheet = next(s for s in cfg["sheets"] if s["sheet_id"] == "surface_a3")
known = {m["id"] for m in sheet["markers"]}
adet = cv2.aruco.ArucoDetector(boards.dictionary(sheet["markers"][0]["dictionary"]), boards.detector_parameters(cfg))
out = sys.argv[3]
for d in ("charuco", "a3"): os.makedirs(os.path.join(out, d), exist_ok=True)
counts = {"charuco": 0, "a3": 0, "none": 0}
for p in sorted(glob.glob(sys.argv[2])):
    im = cv2.imread(p, cv2.IMREAD_GRAYSCALE | cv2.IMREAD_IGNORE_ORIENTATION)
    c, ids, _ = cdet.detectMarkers(im); nc = 0
    if ids is not None:
        _r, cc, cids = cv2.aruco.interpolateCornersCharuco(c, ids, im, cboard); nc = 0 if cids is None else len(cids)
    c2, ids2, _ = adet.detectMarkers(im)
    na = 0 if ids2 is None else len({int(i) for i in ids2.flatten()} & known)
    kind = "charuco" if nc >= 10 else ("a3" if na >= 3 else "none")
    counts[kind] += 1
    if kind != "none": shutil.copy(p, os.path.join(out, kind, os.path.basename(p)))
print(counts)
