"""Render the rectified sheet at one moment of a take with a millimetre grid (owner tool, PROTO-01-E3B).

The owner reads each true source point (x/y in sheet mm) from this image for the
sealed e3b-actual@1 file. It uses the frozen custody registration and
rectification, so the coordinates are the ones the pipeline uses. Run by the
owner only; the image shows the arrangement and must not be sent to the
Technical Lead before scoring.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "custody-ledger"), str(REPO / "change-detection")]

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from anastylos_custody import footage  # noqa: E402
from anastylos_custody.config import load_custody_config  # noqa: E402
from anastylos_custody.sheet import Rectifier, SheetReader, find_registration  # noqa: E402

GRID_MM, LABEL_MM = 10, 50


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--t", type=float, default=1.0, help="video second to render (the device at rest before step 1)")
    parser.add_argument("--config", type=Path, default=REPO / "custody-ledger" / "custody-config.v15.json")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    custody, harness, kit, _ = load_custody_config(args.config)
    step = int(custody["sampling"]["step_frames"])
    reader = SheetReader(harness, kit, custody["view"])
    registration = find_registration(reader, footage.sample_frames(args.video, step), float(custody["session_registration"]["search_window_s"]))
    rectifier = Rectifier(registration.homography_image_to_mm, reader.page_rect_mm(), custody["rectified"]["px_per_mm"],
                          custody["rectified"]["margin_mm"])
    sample = next(s for s in footage.sample_frames(args.video, step) if s.t_s >= args.t)
    image = rectifier.rectify(sample.image, np.eye(3))  # the fixed camera has not drifted at the start of the take
    height, width = image.shape[:2]
    ppm = rectifier.ppm
    first_x = int(rectifier.x0 // GRID_MM + 1) * GRID_MM
    first_y = int(rectifier.y0 // GRID_MM + 1) * GRID_MM
    for mm in range(first_x, int(rectifier.x0 + width / ppm), GRID_MM):
        x = int(round((mm - rectifier.x0) * ppm))
        cv2.line(image, (x, 0), (x, height - 1), (0, 0, 255) if mm % LABEL_MM == 0 else (0, 160, 255), 1)
        if mm % LABEL_MM == 0:
            cv2.putText(image, str(mm), (x + 2, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
    for mm in range(first_y, int(rectifier.y0 + height / ppm), GRID_MM):
        y = int(round((mm - rectifier.y0) * ppm))
        cv2.line(image, (0, y), (width - 1, y), (0, 0, 255) if mm % LABEL_MM == 0 else (0, 160, 255), 1)
        if mm % LABEL_MM == 0:
            cv2.putText(image, str(mm), (2, y - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
    cv2.imwrite(str(args.out), image)
    print(f"{args.out}: t = {sample.t_s:.2f} s, x {rectifier.x0:.0f}-{rectifier.x0 + width / ppm:.0f} mm, "
          f"y {rectifier.y0:.0f}-{rectifier.y0 + height / ppm:.0f} mm, grid {GRID_MM} mm")


if __name__ == "__main__":
    main()
