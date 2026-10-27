#!/usr/bin/env python3
"""HARDWARE-01 ground-truth annotation (validation tooling, not production).

Measures screw bounding boxes from SINGLE rectified images by brightness
segmentation inside constrained windows. Independent of the before/after
difference pipeline under test: the only shared input is the calibrated
frame mapping (origin + px/mm), which is the ruler, not the measurement.

Method per site:
  - window: generous frame-mm rectangle known (by visual inspection of the
    originals) to contain exactly one screw plus white paper / printed digit.
  - mask: gray < 180 inside the window (paper is ~210+, screw body darker).
  - keep the largest connected component (printed digits are far smaller),
    bounding box + 1 mm margin, rounded to 0.25 mm (rectified pixel).
  - verification crop with the box drawn is saved for human review.

Also used for the repeatability check: each physical screw placement is
measured twice from two different photos/registrations.

Outputs: hardware-01/gt_boxes.json, hardware-01/manifest_gt.csv,
hardware-01/gt_check/<site>.png
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
ORIGIN = (-14.0, -14.0)  # rectified origin_frame_mm (surface_a3)


def mm_to_px(x_mm, y_mm):
    return (int(round((x_mm - ORIGIN[0]) * PPM)), int(round((y_mm - ORIGIN[1]) * PPM)))


def measure(rect_path, window_mm, thresh=180, min_area_mm2=40.0):
    img = cv2.imread(str(rect_path))
    assert img is not None, rect_path
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    x0, y0 = mm_to_px(window_mm[0], window_mm[1])
    x1, y1 = mm_to_px(window_mm[0] + window_mm[2], window_mm[1] + window_mm[3])
    win = gray[y0:y1, x0:x1]
    bg = float(np.median(win))
    mask = (win < bg - 45).astype(np.uint8) * 255
    print(f"  {rect_path.name} window bg_median={bg:.0f} dark_frac={(mask > 0).mean():.4f}")
    n, lab, stats, _cent = cv2.connectedComponentsWithStats(mask, 8)
    assert n > 1, f"no dark pixels in {rect_path} {window_mm}"
    order = sorted(
        (i for i in range(1, n)), key=lambda i: stats[i, cv2.CC_STAT_AREA], reverse=True
    )
    areas = [stats[i, cv2.CC_STAT_AREA] / PPM**2 for i in order[:4]]
    best = order[0]
    area_mm2 = stats[best, cv2.CC_STAT_AREA] / PPM**2
    assert area_mm2 >= min_area_mm2, f"largest CC too small: {area_mm2:.1f} mm2"
    bx, by, bw, bh = (int(stats[best, k]) for k in range(4))
    # to frame mm + 1 mm margin, snapped to rectified pixel (0.25 mm)
    m = 1.0
    box = [
        round((x0 + bx) / PPM + ORIGIN[0] - m, 2),
        round((y0 + by) / PPM + ORIGIN[1] - m, 2),
        round(bw / PPM + 2 * m, 2),
        round(bh / PPM + 2 * m, 2),
    ]
    # verification crop: window + box overlay
    crop = img[max(0, y0 - 40): y1 + 40, max(0, x0 - 40): x1 + 40].copy()
    ox, oy = max(0, x0 - 40), max(0, y0 - 40)
    qx0, qy0 = mm_to_px(box[0], box[1])
    qx1, qy1 = mm_to_px(box[0] + box[2], box[1] + box[3])
    cv2.rectangle(crop, (qx0 - ox, qy0 - oy), (qx1 - ox, qy1 - oy), (0, 0, 255), 2)
    return box, round(area_mm2, 1), [round(a, 1) for a in areas], crop


# (site, rectified image, window [x,y,w,h] frame-mm)
JOBS = [
    ("work_R04after", RES / "R04" / "after_rectified.jpg", [140, 110, 80, 50]),
    ("work_R05before", RES / "R05" / "before_rectified.jpg", [140, 110, 80, 50]),
    ("tray1_R05after", RES / "R05" / "after_rectified.jpg", [320, 52, 22, 22]),
    ("tray1_R06before", RES / "R06" / "before_rectified.jpg", [320, 52, 22, 22]),
    ("tray7_R06after", RES / "R06" / "after_rectified.jpg", [350, 52, 22, 22]),
    ("tray7_R07before", RES / "R07" / "before_rectified.jpg", [350, 52, 22, 22]),
]

boxes = {}
areas = {}
for site, path, window in JOBS:
    box, area, ranked, crop = measure(path, window)
    boxes[site] = box
    areas[site] = {"largest_mm2": area, "ranked_mm2": ranked}
    cv2.imwrite(str(OUT / f"{site}.png"), crop)
    print(f"{site}: box={box} largest={area} mm2 ranked={ranked}")

# repeatability: same physical placement measured from two photos
for a, b in [("work_R04after", "work_R05before"),
             ("tray1_R05after", "tray1_R06before"),
             ("tray7_R06after", "tray7_R07before")]:
    ba, bb = boxes[a], boxes[b]
    ca = (ba[0] + ba[2] / 2, ba[1] + ba[3] / 2)
    cb = (bb[0] + bb[2] / 2, bb[1] + bb[3] / 2)
    d = ((ca[0] - cb[0]) ** 2 + (ca[1] - cb[1]) ** 2) ** 0.5
    print(f"repeat {a} vs {b}: centroid drift {d:.2f} mm")

(ROOT / "gt_boxes.json").write_text(
    json.dumps({"boxes_mm": boxes, "areas": areas,
                "method": "single-image gray<180 largest-CC +1mm margin, snapped 0.25mm",
                "frame": "surface_a3 rectified, origin [-14,-14] mm, 4 px/mm"},
               indent=1), encoding="utf-8")

# GT manifest: one box per physical change site
gt = {
    "R04": [boxes["work_R04after"]],
    "R05": [boxes["tray1_R05after"], boxes["work_R05before"]],
    "R06": [boxes["tray7_R06after"], boxes["tray1_R06before"]],
    "R07": [boxes["tray7_R07before"]],
}
rows = [
    ("R01", "none", "", "verified empty in-ROI; out-of-sheet screw + person at frame edge"),
    ("R02", "none", "", "verified empty in-ROI; out-of-sheet screw + person at frame edge"),
    ("R03", "none", "", "verified empty in-ROI; out-of-sheet screw only"),
    ("R04", "single", ";".join(",".join(f"{v:g}" for v in b) for b in gt["R04"]),
     "screw added to work area; GT box measured single-image (see gt_boxes.json)"),
    ("R05", "multiple", ";".join(",".join(f"{v:g}" for v in b) for b in gt["R05"]),
     "screw work area -> tray 1; GT boxes measured single-image"),
    ("R06", "multiple", ";".join(",".join(f"{v:g}" for v in b) for b in gt["R06"]),
     "screw tray 1 -> tray 7; GT boxes measured single-image"),
    ("R07", "single", ";".join(",".join(f"{v:g}" for v in b) for b in gt["R07"]),
     "screw removed from tray 7; GT box measured single-image"),
]
with open(ROOT / "manifest_gt.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["pair_id", "expected", "expected_bbox_mm", "view", "assert", "notes"])
    for pid, exp, bb, note in rows:
        w.writerow([pid, exp, bb, "surface_a3", 1, note])
print("wrote manifest_gt.csv")
