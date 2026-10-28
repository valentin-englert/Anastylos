"""Camera-static check: phase-correlation shift of never-touched
background strips (top ruler/table band) between ST1 (t=4s) and ST2
(t=179s) full-res stills, per camera. A (0,0) peak with high response
means the camera never moved (single pose epoch)."""
import cv2
import numpy as np

for cam, pre in (("CAM1", "ST1-c1-001.jpg"), ("CAM2", "ST1-c2-001.jpg")):
    a = cv2.imread(f"snes-02/work/cases/ST1/{pre}", cv2.IMREAD_GRAYSCALE)
    b = cv2.imread(
        f"snes-02/work/cases/ST2/{pre.replace('ST1', 'ST2')}",
        cv2.IMREAD_GRAYSCALE)
    assert a.shape == b.shape, (cam, a.shape, b.shape)
    strip_a = a[0:150, :].astype(np.float64)
    strip_b = b[0:150, :].astype(np.float64)
    (dx, dy), resp = cv2.phaseCorrelate(strip_a, strip_b)
    print(f"{cam} {a.shape[1]}x{a.shape[0]}: shift=({dx:+.3f},{dy:+.3f}) "
          f"response={resp:.4f}")
