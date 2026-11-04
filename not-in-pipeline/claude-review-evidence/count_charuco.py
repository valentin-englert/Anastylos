"""Count usable ChArUco views exactly as cal01_joint.py does (read-only)."""
import glob, json, sys
sys.path.insert(0, r"C:\Users\HP\Documents\Anastylos-integration-spatial\calibration")
import cv2
from anastylos import boards
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
ccfg = dict(cfg["charuco"])
cboard = boards.charuco_board({"charuco": ccfg})
cdet = cv2.aruco.ArucoDetector(boards.dictionary(ccfg["dictionary"]), boards.detector_parameters(cfg))
ok = 0
for p in sorted(glob.glob(sys.argv[2])):
    im = cv2.imread(p, cv2.IMREAD_GRAYSCALE | cv2.IMREAD_IGNORE_ORIENTATION)
    c, ids, _ = cdet.detectMarkers(im)
    n = 0
    if ids is not None:
        _r, cc, cids = cv2.aruco.interpolateCornersCharuco(c, ids, im, cboard)
        n = 0 if cids is None else len(cids)
    ok += n >= 10
    print(n, p.split("\\")[-1][:22])
print("usable", ok)
