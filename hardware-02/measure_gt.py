#!/usr/bin/env python3
"""HARDWARE-02 ground-truth annotation (validation tooling, not production).

Single-image measurements, independent of the before/after difference
pipeline under test. Shared input is only the calibrated frame mapping.

Sites (frame-mm, surface_a3 rectified, origin [-14,-14], 4 px/mm):
  - tray screw: brass/steel screw lying in tray 1 in photo C.
    Window = tray 1 cell inset 2 mm; mask = gray < window_median - 45
    (paper ~210, screw body darker); largest CC + 1 mm margin.
  - case screw: top-left case screw present in photo D (reinstalled).
    Window = generous zone around the case top-left corner; the screw
    head is mid-bright (brass) between dark case and bright paper, so
    mask = 90 < gray < 190; largest CC in plausible area range
    (5..60 mm2) + 1 mm margin. Falls back to ABORT (no fabricated box)
    if no plausible component is found.
  The SAME physical sites serve all pairs (sheet-fixed frame): the
  mounting hole does not move, so the D-measured case box is valid GT
  for AB/AC/CD alike. AB/AC carry no detection coords (registration
  invalid), so their boxes document intent only and are never scored.

Outputs: gt_boxes.json, manifest_gt.csv, gt_check/*.png (human review).
"""
import csv
import json
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
RES = ROOT / "results"
OUT = ROOT / "gt_check"
OUT.mkdir(exist_ok=True)

PPM = 4.0
ORIGIN = (-14.0, -14.0)


def mm_to_px(x_mm, y_mm):
    return (int(round((x_mm - ORIGIN[0]) * PPM)), int(round((y_mm - ORIGIN[1]) * PPM)))


def box_of(mask, x0, y0, margin_mm=1.0):
    n, _lab, stats, _c = cv2.connectedComponentsWithStats(mask, 8)
    cands = [(stats[i, cv2.CC_STAT_AREA] / PPM**2, i) for i in range(1, n)]
    cands.sort(reverse=True)
    return cands


img_c = cv2.imread(str(RES / "BC" / "after_rectified.jpg"))
gray_c = cv2.cvtColor(img_c, cv2.COLOR_BGR2GRAY)
x0, y0 = mm_to_px(320, 52)
x1, y1 = mm_to_px(342, 74)
win = gray_c[y0:y1, x0:x1]
bg = float(np.median(win))
mask = (win < bg - 45).astype(np.uint8) * 255
cands = box_of(mask, x0, y0)
area, best = cands[0]
assert area >= 20.0, f"tray screw too small: {area}"
n, lab, stats, _c = cv2.connectedComponentsWithStats(mask, 8)
bx, by, bw, bh = (int(stats[best, k]) for k in range(4))
tray_box = [round((x0 + bx) / PPM + ORIGIN[0] - 1.0, 2),
            round((y0 + by) / PPM + ORIGIN[1] - 1.0, 2),
            round(bw / PPM + 2.0, 2), round(bh / PPM + 2.0, 2)]
print("tray:", tray_box, "area=%.1f bg=%.0f" % (area, bg))
crop = img_c[max(0, y0 - 40):y1 + 40, max(0, x0 - 40):x1 + 40].copy()
ox, oy = max(0, x0 - 40), max(0, y0 - 40)
qx0, qy0 = mm_to_px(tray_box[0], tray_box[1])
qx1, qy1 = mm_to_px(tray_box[0] + tray_box[2], tray_box[1] + tray_box[3])
cv2.rectangle(crop, (qx0 - ox, qy0 - oy), (qx1 - ox, qy1 - oy), (0, 0, 255), 2)
cv2.imwrite(str(OUT / "tray_C.png"), crop)

img_d = cv2.imread(str(RES / "CD" / "after_rectified.jpg"))
gray_d = cv2.cvtColor(img_d, cv2.COLOR_BGR2GRAY)
wx0, wy0 = mm_to_px(152, 80)
wx1, wy1 = mm_to_px(166, 93)
win = gray_d[wy0:wy1, wx0:wx1]
mask = ((win > 90) & (win < 190)).astype(np.uint8) * 255
cands = box_of(mask, wx0, wy0)
print("case candidates (area mm2):", [round(a, 1) for a, _ in cands[:5]])
plausible = [(a, i) for a, i in cands if 5.0 <= a <= 60.0]
assert plausible, "no plausible case-screw component; aborting (no fabricated box)"
area, best = plausible[0]
n, lab, stats, _c = cv2.connectedComponentsWithStats(mask, 8)
bx, by, bw, bh = (int(stats[best, k]) for k in range(4))
case_box = [round((wx0 + bx) / PPM + ORIGIN[0] - 1.0, 2),
            round((wy0 + by) / PPM + ORIGIN[1] - 1.0, 2),
            round(bw / PPM + 2.0, 2), round(bh / PPM + 2.0, 2)]
print("case:", case_box, "area=%.1f" % area)
crop = img_d[max(0, wy0 - 40):wy1 + 40, max(0, wx0 - 40):wx1 + 40].copy()
ox, oy = max(0, wx0 - 40), max(0, wy0 - 40)
qx0, qy0 = mm_to_px(case_box[0], case_box[1])
qx1, qy1 = mm_to_px(case_box[0] + case_box[2], case_box[1] + case_box[3])
cv2.rectangle(crop, (qx0 - ox, qy0 - oy), (qx1 - ox, qy1 - oy), (0, 0, 255), 2)
cv2.imwrite(str(OUT / "case_D.png"), crop)

(ROOT / "gt_boxes.json").write_text(json.dumps(
    {"boxes_mm": {"tray_C": tray_box, "case_D": case_box},
     "frame": "surface_a3 rectified, origin [-14,-14] mm, 4 px/mm",
     "note": "sheet-fixed frame: case_D box also valid for AB/AC (same hole)"},
    indent=1), encoding="utf-8")

tb = ",".join(f"{v:g}" for v in tray_box)
cb = ",".join(f"{v:g}" for v in case_box)
rows = [
    ("AB", "single", cb, "top-left case screw removed; GT = mounting site from D (same hole); pair unscored (registration invalid)"),
    ("BC", "single", tb, "screw placed in tray 1"),
    ("AC", "multiple", f"{cb};{tb}", "case removal + tray addition; pair unscored (registration invalid)"),
    ("CD", "multiple", f"{tb};{cb}", "tray removal + case reinstallation"),
]
with open(ROOT / "manifest_gt.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["pair_id", "expected", "expected_bbox_mm", "view", "assert", "notes"])
    for pid, exp, bb, note in rows:
        w.writerow([pid, exp, bb, "surface_a3", 1, note])
print("wrote manifest_gt.csv")
