"""Tile paired stills side-by-side with exact frame/PTS labels.

Usage: python pair_tile.py <pairs.json> <out_jpg> [cols rows width]
Each tile: CAM1 | CAM2 normalized to equal height, label bar with
exact frame indices + PTS + residual. Review scale via width (default
1440 px output width; use 2880 for deliverable sheets).
"""
import json
import os
import sys

import cv2
import numpy as np

pairs_path, out = sys.argv[1], sys.argv[2]
cols, rows = (int(sys.argv[3]), int(sys.argv[4])) if len(sys.argv) > 4 else (2, 4)
W = int(sys.argv[5]) if len(sys.argv) > 5 else 1440
d = os.path.dirname(pairs_path)
spec = json.load(open(pairs_path))
pairs = spec["pairs"]
per = cols * rows
H_TILE = 640
tiles = []
for pr in pairs:
    a = cv2.imread(os.path.join(d, pr["cam1"]["file"]))
    b = cv2.imread(os.path.join(d, pr["cam2"]["file"]))
    ha, wa = a.shape[:2]
    hb, wb = b.shape[:2]
    # normalize both to H_TILE height
    a = cv2.resize(a, (int(wa * H_TILE / ha), H_TILE))
    b = cv2.resize(b, (int(wb * H_TILE / hb), H_TILE))
    tile = np.hstack([a, b])
    bar = np.full((44, tile.shape[1], 3), 24, np.uint8)
    tag = ("C1 f%(f1)d @%(p1).3fs | C2 f%(f2)d @%(p2).3fs | r=%(r).1fms"
           % {"f1": pr["cam1"]["frame"], "p1": pr["cam1"]["pts"],
              "f2": pr["cam2"]["frame"], "p2": pr["cam2"]["pts"],
              "r": pr["residual_ms"]})
    if not pr["sync_ok"]:
        tag += " MISMATCH"
        bar[:] = (0, 0, 160)
    cv2.putText(bar, tag, (6, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.62,
                (255, 255, 255), 1)
    tiles.append(np.vstack([tile, bar]))
while len(tiles) < ((len(tiles) + per - 1) // per) * per:
    tiles.append(np.full_like(tiles[0], 12))
base, ext = os.path.splitext(out)
ns = (len(tiles) + per - 1) // per
for s in range(ns):
    chunk = tiles[s*per:(s+1)*per]
    grid = np.vstack([np.hstack(chunk[r*cols:(r+1)*cols])
                      for r in range(rows)])
    sc = W / grid.shape[1]
    grid = cv2.resize(grid, (W, int(grid.shape[0] * sc)))
    p = f"{base}-{s+1}of{ns}{ext}" if ns > 1 else out
    cv2.imwrite(p, grid, [cv2.IMWRITE_JPEG_QUALITY, 80])
    print(p, grid.shape)
