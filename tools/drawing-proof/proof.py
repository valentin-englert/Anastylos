"""Single-part drawing proof: spec JSON → rectified photo, masks, photo overlay, technical SVG, report.

    python tools/drawing-proof/proof.py --spec tools/drawing-proof/specs/front_shell_p3_f300.json --out C:\\tmp\\anastylos-drawing-proof\\run

Every manual input is in the spec (``manual``) and repeated in the report. Outputs stay outside the repo.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np

import draw
import quality
import segment
import source

INK, FAINT, UNKNOWN = (40, 40, 40), (150, 150, 150), (30, 120, 220)


def _poly_mm(rf: source.RectifiedFrame, polygon_px: np.ndarray) -> np.ndarray:
    return rf.px_to_mm(polygon_px)


def build(spec: dict, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    timings, manual = {}, spec.get("manual", {})
    began = time.perf_counter()
    video = Path(spec["video"])
    frame, rf = source.rectify(video, spec.get("frame_index"), tuple(spec["region_mm"]), spec["px_per_mm"], spec.get("video_sha256"))
    timings["rectify_s"] = time.perf_counter() - began
    image, ppm = rf.image, rf.px_per_mm

    t = time.perf_counter()
    seed, seed_meta = segment.threshold_mask(image, spec["threshold"]["close_px"])
    shadow = segment.shadow_candidates(image, seed, spec["shadow"])
    mask, gc_meta = segment.grabcut_mask(image, seed & ~shadow, spec["grabcut"]["band_px"], spec["grabcut"]["iterations"], background=shadow)
    mask = segment.apply_corrections(mask, manual.get("outline_corrections", {}), rf.mm_to_px)
    timings["outline_mask_s"] = time.perf_counter() - t

    dense = segment.boundary(mask)
    outline_px, outline_dev = draw.simplify(dense, ppm, spec["outline_tolerance_mm"])
    fits = {"threshold_only": segment.edge_fit(image, segment.boundary(seed), **spec["edge_fit"]),
            "final_dense": segment.edge_fit(image, dense, **spec["edge_fit"]),
            "final_simplified": segment.edge_fit(image, outline_px, **spec["edge_fit"])}
    checked = quality.gate(image, mask, seed, shadow, fits["final_dense"]["share_within_2px"])
    source_sha = spec.get("video_sha256") or source.sha256_file(video)
    gap = quality.capture_gap(checked["flags"], spec.get("binding", {"session": "drawing-proof", "target": {"kind": "drawing_element", "id": spec.get("target_id", "part")}, "revision": spec.get("revision", "r1"), "hashes": {}}),
                              source_sha, f"gap-{spec.get('target_id', 'part')}")
    if spec.get("reference_mask"):
        reference = cv2.imread(spec["reference_mask"], cv2.IMREAD_GRAYSCALE) > 0
        checked["reference"] = quality.reference_comparison(mask, reference, ppm, spec["reference_tolerance_mm"])

    t = time.perf_counter()
    regions = [r for r in draw.tone_regions(image, mask, spec["tones"], ppm) if r["tone"] == "light"]
    for box in manual.get("detail_boxes_mm", []):
        regions.append(_boxed_region(image, rf, box, spec))
    timings["details_s"] = time.perf_counter() - t

    geometry = {
        "outline_mm": _poly_mm(rf, outline_px).round(2).tolist(),
        "regions": [{"tone": r["tone"], "source": r.get("source", "auto_tone"), "name": r.get("name"),
                     "polygon_mm": _poly_mm(rf, r["polygon_px"]).round(2).tolist(),
                     "holes_mm": [_poly_mm(rf, h).round(2).tolist() for h in r.get("holes_px", [])],
                     "max_dev_mm": round(r["max_dev_mm"], 3)} for r in regions],
    }
    _write_images(out, frame, rf, mask, shadow, outline_px, regions)
    svg = render_svg(geometry, spec)
    (out / "drawing.svg").write_text(svg, encoding="utf-8")
    cv2.imwrite(str(out / "drawing_preview.png"), render_preview(geometry, spec))
    timings["total_s"] = time.perf_counter() - began
    report = {
        "schema": "anastylos-drawing-proof@1", "spec": spec, "source": rf.provenance,
        "outline": {"points": len(outline_px), "dense_points": len(dense), "tolerance_mm": spec["outline_tolerance_mm"],
                    "max_deviation_mm": round(outline_dev, 3), "area_mm2": round(float(mask.sum()) / ppm ** 2, 1),
                    "shadow_rejected_mm2": round(float(shadow.sum()) / ppm ** 2, 1), "edge_fit_px": fits},
        "segmentation": [seed_meta, gc_meta], "regions": [{k: v for k, v in g.items() if k != "polygon_mm"} | {"points": len(g["polygon_mm"])} for g in geometry["regions"]],
        "manual_steps": manual, "timings_s": {k: round(v, 2) for k, v in timings.items()},
        "geometry_classes": spec.get("geometry_classes"), "unknown": spec.get("unknown"),
        "quality": checked, "status": checked["status"],
    }
    if gap is not None:
        (out / "capture_gap.json").write_text(json.dumps(gap, indent=1), encoding="utf-8")
    (out / "geometry.json").write_text(json.dumps(geometry), encoding="utf-8")
    (out / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    return report


def _boxed_region(image: np.ndarray, rf: source.RectifiedFrame, box: dict, spec: dict) -> dict:
    (x0, y0), (x1, y1) = np.round(rf.mm_to_px(np.array(box["rect_mm"], float).reshape(2, 2))).astype(int)
    labels = np.zeros(image.shape[:2], np.uint8)
    bgd, fgd = np.zeros((1, 65)), np.zeros((1, 65))
    cv2.grabCut(image, labels, (x0, y0, x1 - x0, y1 - y0), bgd, fgd, spec["grabcut"]["iterations"], cv2.GC_INIT_WITH_RECT)
    region = segment.fill_holes(segment._largest_component((labels == cv2.GC_FGD) | (labels == cv2.GC_PR_FGD)))
    polygon, deviation = draw.simplify(segment.boundary(region), rf.px_per_mm, spec["tones"]["tolerance_mm"])
    return {"tone": box["tone"], "source": "manual_box_grabcut", "name": box["name"], "polygon_px": polygon, "max_dev_mm": deviation}


def _write_images(out, frame, rf, mask, shadow, outline_px, regions) -> None:
    cv2.imwrite(str(out / "frame_full.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
    cv2.imwrite(str(out / "rectified.png"), rf.image)
    cv2.imwrite(str(out / "mask.png"), mask.astype(np.uint8) * 255)
    overlay = rf.image.copy()
    overlay[shadow] = (0.55 * overlay[shadow] + np.array([0, 0, 110])).astype(np.uint8)
    cv2.polylines(overlay, [np.round(outline_px).astype(np.int32)], True, (0, 170, 0), 2, cv2.LINE_AA)
    for region in regions:
        cv2.polylines(overlay, [np.round(region["polygon_px"]).astype(np.int32)], True, (200, 0, 200), 2, cv2.LINE_AA)
        for hole in region.get("holes_px", []):
            cv2.polylines(overlay, [np.round(hole).astype(np.int32)], True, (0, 190, 255), 2, cv2.LINE_AA)
    cv2.imwrite(str(out / "overlay.png"), overlay)


def _layout(geometry: dict, spec: dict) -> dict:
    """Disjoint boxes (mm on the drawing sheet): title, part view, legend; one uniform scale."""
    outline = np.array(geometry["outline_mm"])
    lo, hi = outline.min(0), outline.max(0)
    margin, title_h, legend_h = 6.0, 18.0, 30.0
    width = (hi[0] - lo[0]) + 2 * margin
    return {"lo": lo, "hi": hi, "margin": margin, "title_h": title_h, "legend_h": legend_h,
            "page": (width, title_h + (hi[1] - lo[1]) + 2 * margin + legend_h),
            "part_origin": (margin - lo[0], title_h + margin - lo[1])}


def _path(points, origin) -> str:
    return "M " + " L ".join(f"{x + origin[0]:.2f},{y + origin[1]:.2f}" for x, y in points) + " Z"


def render_svg(geometry: dict, spec: dict) -> str:
    lay = _layout(geometry, spec)
    w, h = lay["page"]
    o = lay["part_origin"]
    legend_y = h - lay["legend_h"] + 6
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.1f}mm" height="{h:.1f}mm" viewBox="0 0 {w:.2f} {h:.2f}" font-family="Helvetica, Arial, sans-serif">',
             f'<rect width="{w:.2f}" height="{h:.2f}" fill="#ffffff"/>',
             f'<text x="{lay["margin"]}" y="9" font-size="4.2" font-weight="bold">{spec["title"]}</text>',
             f'<text x="{lay["margin"]}" y="15" font-size="2.4" fill="#555">{spec["subtitle"]}</text>',
             '<g id="observed_outline">',
             f'<path data-line-role="outline" d="{_path(geometry["outline_mm"], o)}" fill="#e9eaec" stroke="#222" stroke-width="0.45" stroke-linejoin="round"/>',
             '</g><g id="observed_regions">']
    for region in geometry["regions"]:
        fill = "#fbfbf8" if region["tone"] == "light" else "#3a3b3d"
        dash = ' stroke-dasharray="2 1"' if region["source"] == "manual_box_grabcut" else ""
        parts.append(f'<path data-line-role="edge" data-source="{region["source"]}" d="{_path(region["polygon_mm"], o)}" fill="{fill}" stroke="#222" stroke-width="0.3"{dash}/>')
        for hole in region["holes_mm"]:
            parts.append(f'<path data-line-role="detail" d="{_path(hole, o)}" fill="#3a3b3d" stroke="#222" stroke-width="0.2"/>')
    parts.append("</g>")
    # Scale bar: 20 mm in sheet-plane millimetres.
    sx, sy = lay["margin"], legend_y + 1
    parts.append(f'<g id="scale"><line x1="{sx}" y1="{sy}" x2="{sx + 20}" y2="{sy}" stroke="#222" stroke-width="0.4"/>'
                 f'<line x1="{sx}" y1="{sy - 1}" x2="{sx}" y2="{sy + 1}" stroke="#222" stroke-width="0.4"/>'
                 f'<line x1="{sx + 20}" y1="{sy - 1}" x2="{sx + 20}" y2="{sy + 1}" stroke="#222" stroke-width="0.4"/>'
                 f'<text x="{sx + 22}" y="{sy + 1}" font-size="2.2">20 mm (sheet plane)</text></g>')
    for i, line in enumerate(spec["legend"]):
        parts.append(f'<text x="{lay["margin"]}" y="{legend_y + 7 + i * 3.6:.1f}" font-size="2.2" fill="#333">{line}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def _dashed(canvas: np.ndarray, polygon: np.ndarray, colour, dash_px: int) -> None:
    """Dashed closed polyline: dash and gap of ``dash_px`` along the arc length."""
    closed = np.vstack([polygon, polygon[:1]]).astype(float)
    for a, b in zip(closed[:-1], closed[1:]):
        length = float(np.hypot(*(b - a)))
        for start in np.arange(0, length, 2 * dash_px):
            p = a + (b - a) * (start / max(length, 1e-9))
            q = a + (b - a) * (min(start + dash_px, length) / max(length, 1e-9))
            cv2.line(canvas, tuple(np.round(p).astype(int)), tuple(np.round(q).astype(int)), colour, 2, cv2.LINE_AA)


def render_preview(geometry: dict, spec: dict, px_per_mm: float = 8.0) -> np.ndarray:
    """Raster of the same geometry and layout as the SVG (no SVG rasteriser in the stdlib/venv)."""
    lay = _layout(geometry, spec)
    w, h = lay["page"]
    canvas = np.full((int(h * px_per_mm), int(w * px_per_mm), 3), 255, np.uint8)
    o = np.array(lay["part_origin"])

    def px(points):
        return [np.round((np.array(points) + o) * px_per_mm).astype(np.int32)]

    cv2.fillPoly(canvas, px(geometry["outline_mm"]), (236, 234, 233))
    cv2.polylines(canvas, px(geometry["outline_mm"]), True, INK, 3, cv2.LINE_AA)
    for region in geometry["regions"]:
        cv2.fillPoly(canvas, px(region["polygon_mm"]), (248, 251, 251) if region["tone"] == "light" else (61, 59, 58))
        if region["source"] == "manual_box_grabcut":
            _dashed(canvas, px(region["polygon_mm"])[0], (255, 255, 255), dash_px=10)
        else:
            cv2.polylines(canvas, px(region["polygon_mm"]), True, INK, 2, cv2.LINE_AA)
        for hole in region["holes_mm"]:
            cv2.fillPoly(canvas, px(hole), (61, 59, 58))
    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(canvas, spec["title"], (int(lay["margin"] * px_per_mm), int(9 * px_per_mm)), font, 1.1, INK, 2, cv2.LINE_AA)
    legend_y = h - lay["legend_h"] + 6
    for i, line in enumerate([spec["subtitle"], *spec["legend"]]):
        cv2.putText(canvas, line, (int(lay["margin"] * px_per_mm), int((legend_y + 4 + i * 3.6) * px_per_mm)), font, 0.55, (60, 60, 60), 1, cv2.LINE_AA)
    return canvas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    report = build(json.loads(args.spec.read_text(encoding="utf-8")), args.out)
    print(json.dumps({"status": report["status"], "flags": report["quality"]["flags"], "measures": report["quality"]["measures"], "timings_s": report["timings_s"]}, indent=1))


if __name__ == "__main__":
    main()
