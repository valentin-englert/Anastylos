"""Tile skim frames into labeled contact sheets (cv2 only).

Usage: python tile.py <glob_dir> <label_prefix> <out_jpg> [cols rows]
Label bar under each frame: "<prefix><idx> @ ~<t>s". Skim frames are
2 s apart starting at video PTS ~0 (ffmpeg fps=1/2 picks nearest frame
to k*2 s); labels are approximate and NOT evidence timestamps.
"""
import glob
import os
import sys

import cv2
import numpy as np

_, srcdir, prefix, out, *rest = sys.argv
cols, rows = (int(rest[0]), int(rest[1])) if len(rest) >= 2 else (6, 5)
t0, step = (float(rest[2]), float(rest[3])) if len(rest) >= 4 else (0.0, 2.0)
files = sorted(glob.glob(os.path.join(srcdir, "*.jpg")))
per = cols * rows
base, ext = os.path.splitext(out)
for s in range((len(files) + per - 1) // per):
    chunk = files[s*per:(s+1)*per]
    imgs = [cv2.imread(f) for f in chunk]
    h, w = imgs[0].shape[:2]
    bar = 22
    tiles = []
    for k, im in enumerate(chunk):
        idx = s * per + k
        tile = np.full((h + bar, w, 3), 24, np.uint8)
        tile[:h] = imgs[k]
        cv2.putText(tile, f"{prefix}{idx} ~{t0+step*idx:g}s", (4, h + 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
        tiles.append(tile)
    while len(tiles) < per:
        tiles.append(np.full((h + bar, w, 3), 12, np.uint8))
    grid = np.vstack([np.hstack(tiles[r*cols:(r+1)*cols])
                      for r in range(rows)])
    p = f"{base}-{s+1}of{(len(files)+per-1)//per}{ext}"
    cv2.imwrite(p, grid, [cv2.IMWRITE_JPEG_QUALITY, 82])
    print(p, grid.shape)
