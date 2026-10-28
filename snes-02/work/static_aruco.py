"""Camera-static check v2: ArUco marker corner displacement between
ST1 (t=4s, empty scene) and ST2 (t=179s) full-res stills, per camera.
Markers are printed on the fixed sheet: corner shifts measure camera
motion (plus sub-pixel detection noise + JPEG/reading noise)."""
import cv2
import numpy as np

aruco = cv2.aruco
d = aruco.getPredefinedDictionary(aruco.DICT_5X5_100)
det = aruco.ArucoDetector(d, aruco.DetectorParameters())

for cam, pre in (("CAM1", "ST1-c1-001.jpg"), ("CAM2", "ST1-c2-001.jpg")):
    ia = cv2.imread(f"snes-02/work/cases/ST1/{pre}", cv2.IMREAD_GRAYSCALE)
    ib = cv2.imread(
        f"snes-02/work/cases/ST2/{pre.replace('ST1', 'ST2')}",
        cv2.IMREAD_GRAYSCALE)
    ca, ida, _ = det.detectMarkers(ia)
    cb, idb, _ = det.detectMarkers(ib)
    da = {int(i[0]): c.reshape(4, 2) for i, c in zip(ida, ca)} if ida is not None else {}
    db = {int(i[0]): c.reshape(4, 2) for i, c in zip(idb, cb)} if idb is not None else {}
    print(f"{cam}: ST1 ids={sorted(da)} ST2 ids={sorted(db)}")
    for i in sorted(set(da) & set(db)):
        shift = np.abs(da[i] - db[i]).max()
        mean = np.abs(da[i] - db[i]).mean()
        print(f"  marker {i}: max-corner-shift={shift:.2f}px "
              f"mean={mean:.2f}px")
