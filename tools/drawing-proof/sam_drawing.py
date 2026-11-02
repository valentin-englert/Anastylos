"""Technical SVG from a SAM mask on a sheet-rectified image: outer outline and openings as separate geometry.

    python tools/drawing-proof/sam_drawing.py --run <proof run dir with rectified.png + report.json> --sam <dir with */evidence.json> --out <dir>

The outline and the openings are reported separately: a correct silhouette is not a complete drawing.
Openings are holes in SAM's part mask (>= 0.05 % of the outer area); nothing inside the part is invented.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

import draw
import muse_sam
import proof
import segment


def load_sam_mask(sam_dir: Path, shape: tuple[int, int]) -> np.ndarray:
    evidence = next(sam_dir.glob("**/evidence.json"))
    record = max(json.loads(evidence.read_text(encoding="utf-8"))["records"], key=lambda r: r.get("confidence") or 0)
    raw = cv2.imread(str(evidence.parent / record["mask"]["png"]), cv2.IMREAD_GRAYSCALE)
    b = record["box"]
    full = np.zeros(shape, np.uint8)
    full[b["top"]:b["bottom"], b["left"]:b["right"]] = cv2.resize(raw, (b["right"] - b["left"], b["bottom"] - b["top"]),
                                                                   interpolation=cv2.INTER_NEAREST)[:shape[0] - b["top"], :shape[1] - b["left"]]
    return full > 127


def build(run: Path, sam_dir: Path, out: Path, title: str) -> dict:
    report = json.loads((run / "report.json").read_text(encoding="utf-8"))
    ppm = report["source"]["px_per_mm"]
    x0, y0 = report["source"]["region_mm"][:2]
    image = cv2.imread(str(run / "rectified.png"))
    outer, openings = muse_sam.outer_and_openings(load_sam_mask(sam_dir, image.shape[:2]))
    to_mm = lambda px: (np.asarray(px) / ppm + (x0, y0)).round(2).tolist()  # noqa: E731
    outline, deviation = draw.simplify(segment.boundary(outer), ppm, 0.2)
    simplified = [draw.simplify(o, ppm, 0.2)[0] for o in openings]
    geometry = {"outline_mm": to_mm(outline),
                "regions": [{"tone": "opening", "source": "sam_mask_hole", "name": None, "polygon_mm": to_mm(o), "holes_mm": [], "max_dev_mm": 0.2}
                            for o in simplified]}
    spec = {"title": title, "subtitle": f"{report['source'].get('video', '')} frame {report['source'].get('frame_index')}; outline and openings from the SAM mask",
            "legend": [f"Outline: SAM mask boundary, max deviation {deviation:.2f} mm (sheet plane). Openings: {len(openings)} holes in the mask.",
                       "Not drawn: inner ribs, bosses, heights, hidden sides. Identity and orientation unconfirmed."]}
    out.mkdir(parents=True, exist_ok=True)
    (out / "drawing.svg").write_text(proof.render_svg(geometry, spec), encoding="utf-8")
    cv2.imwrite(str(out / "drawing_preview.png"), proof.render_preview(geometry, spec))
    cv2.imwrite(str(out / "overlay.png"), muse_sam.overlay(image, outer, openings, None))
    result = {"outline_points": len(outline), "outline_max_dev_mm": round(deviation, 3), "openings": len(openings),
              "opening_areas_mm2": sorted(round(cv2.contourArea(o.astype(np.float32)) / ppm ** 2, 1) for o in openings)}
    (out / "sam_drawing.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--sam", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--title", default="Part outline from SAM (drawing proof)")
    args = parser.parse_args()
    print(json.dumps(build(args.run, args.sam, args.out, args.title)))


if __name__ == "__main__":
    main()
