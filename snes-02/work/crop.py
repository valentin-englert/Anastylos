"""Crop a still with a labeled pixel grid overlay for auditable
coordinate reading. Grid labels show ORIGINAL display-pixel coords.

Usage: python crop.py <in.jpg> <x0,y0,x1,y1> <out.jpg> [step_px=50] [scale=1.0]
"""
import sys

import cv2

src, roi, out = sys.argv[1], sys.argv[2], sys.argv[3]
step = int(sys.argv[4]) if len(sys.argv) > 4 else 50
scale = float(sys.argv[5]) if len(sys.argv) > 5 else 1.0
x0, y0, x1, y1 = [int(v) for v in roi.split(",")]
img = cv2.imread(src)
crop = img[y0:y1, x0:x1].copy()
h, w = crop.shape[:2]
for gx in range((x0 // step) * step, x1, step):
    cx = gx - x0
    cv2.line(crop, (cx, 0), (cx, h), (0, 200, 255), 1)
    cv2.putText(crop, str(gx), (cx + 2, 14),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 200, 255), 1)
for gy in range((y0 // step) * step, y1, step):
    cy = gy - y0
    cv2.line(crop, (0, cy), (w, cy), (0, 200, 255), 1)
    cv2.putText(crop, str(gy), (2, cy + 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 200, 255), 1)
if scale != 1.0:
    crop = cv2.resize(crop, (int(w * scale), int(h * scale)),
                      interpolation=cv2.INTER_NEAREST)
cv2.imwrite(out, crop, [cv2.IMWRITE_JPEG_QUALITY, 88])
print(out, crop.shape, "roi", roi)
