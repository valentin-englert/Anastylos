"""Controller schematic sheet: assembled + exploded from one part model.

Stdlib-only. Reads a controller-schematic-v1 package (three main assemblies
with schematic profiles, evidence provenance, assembled placement and
explosion offsets). Emits one landscape A3 SVG. Explosion factor 0
reconstructs the assembled schematic. Not calibrated millimetres; not a
second memory.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from xml.sax.saxutils import escape

SCHEMA = "controller-schematic-v1"
INK = "#202020"
INK_LIGHT = "#666666"
ESTIMATED = "#8a5a00"


def _f(value: float) -> str:
    return f"{float(value):.3f}"


def _text(value) -> str:
    # Keep SVG text ASCII-safe for common rasterizers (Pango/ImageMagick).
    raw = "" if value is None else str(value)
    raw = (raw.replace("\u2014", "-").replace("\u2013", "-")
           .replace("\u00b7", "|").replace("\u2192", "->")
           .replace("\u2026", "..."))
    return escape(raw)


def _sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def load_package(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != SCHEMA:
        raise ValueError(f"unsupported schema_version {data.get('schema_version')!r}")
    if not isinstance(data.get("parts"), list) or len(data["parts"]) < 1:
        raise ValueError("package needs a non-empty parts list")
    return data


def verify_evidence(package: dict) -> list[dict]:
    """Re-hash cited extract files when present on disk."""
    reports = []
    evidence = package.get("evidence") or {}
    for key, row in sorted(evidence.items()):
        extract = row.get("extract")
        cited = row.get("extract_sha256")
        entry = {
            "evidence_key": key,
            "extract": extract,
            "cited_sha256": cited,
            "current_sha256": None,
            "status": "missing",
        }
        if not extract:
            entry["status"] = "no_extract_path"
            reports.append(entry)
            continue
        path = Path(extract)
        current = _sha256_file(path)
        entry["current_sha256"] = current
        if current is None:
            entry["status"] = "unreadable"
        elif cited and current.lower() == str(cited).lower():
            entry["status"] = "verified"
        elif cited:
            entry["status"] = "mismatch"
        else:
            entry["status"] = "unverified"
        reports.append(entry)
    return reports


def _transform_points(points, assembled: dict, explode: dict, factor: float,
                      origin: tuple[float, float]):
    ox, oy = origin
    ax = float(assembled.get("x", 0.0))
    ay = float(assembled.get("y", 0.0))
    ex = float(explode.get("x", 0.0)) * factor
    ey = float(explode.get("y", 0.0)) * factor
    rot = math.radians(float(assembled.get("rotation_deg", 0.0)))
    cos_r, sin_r = math.cos(rot), math.sin(rot)
    out = []
    for x, y in points:
        rx = x * cos_r - y * sin_r
        ry = x * sin_r + y * cos_r
        out.append((ox + ax + ex + rx, oy + ay + ey + ry))
    return out


def _anchor(part: dict, factor: float, origin: tuple[float, float]) -> tuple[float, float]:
    size = part.get("size_mm") or [0.0, 0.0]
    cx, cy = float(size[0]) / 2.0, float(size[1]) / 2.0
    pts = _transform_points(
        [[cx, cy]], part["assembled"], part["explode_offset_mm"], factor, origin)
    return pts[0]


def _polyline(points, **attrs) -> str:
    if len(points) < 2:
        return ""
    pairs = " ".join(f"{_f(x)},{_f(y)}" for x, y in points)
    extra = "".join(f' {k}="{_text(v)}"' for k, v in attrs.items())
    return f'<polyline points="{pairs}" fill="none"{extra}/>'


def _status_stroke(status: str) -> tuple[str, str]:
    status = status or ""
    if ("partial" in status or "estimated" in status
            or "schematic_from_partial" in status):
        return ESTIMATED, "0.25 0.35"
    return INK, ""


def _scaled_pose(pose: dict, scale: float) -> dict:
    return {
        "x": float(pose.get("x", 0.0)) * scale,
        "y": float(pose.get("y", 0.0)) * scale,
        "rotation_deg": float(pose.get("rotation_deg", 0.0)),
    }


def _draw_part(part: dict, factor: float, origin: tuple[float, float],
               scale: float = 1.0) -> list[str]:
    """Draw one part into sheet millimetres (already scaled by caller via origin/scale)."""
    pieces: list[str] = []
    assembled = _scaled_pose(part["assembled"], scale)
    explode = {
        "x": float(part["explode_offset_mm"].get("x", 0.0)) * scale,
        "y": float(part["explode_offset_mm"].get("y", 0.0)) * scale,
    }
    # Scale profiles into view by treating coordinates as already mm; caller
    # sets origin. Optional uniform scale applied to profile before transform.
    profile = [[float(x) * scale, float(y) * scale] for x, y in part["profile_mm"]]
    placed = _transform_points(profile, assembled, explode, factor, origin)
    stroke, dash = _status_stroke(part.get("geometry_status") or "")
    dash_attr = {"stroke-dasharray": dash} if dash else {}
    pid = part["id"]
    pieces.append(
        f'<g id="{_text(pid)}" data-part-id="{_text(pid)}" '
        f'data-item-no="{part.get("item_no")}" '
        f'data-geometry-status="{_text(part.get("geometry_status"))}" '
        f'data-assignment-status="{_text(part.get("assignment_status"))}">')
    pieces.append(_polyline(
        placed, stroke=stroke, **{"stroke-width": "0.45", "stroke-linejoin": "round",
                                  "stroke-linecap": "round", "data-line-role": "outline"},
        **dash_attr))
    # Inner marks (membranes / holes) — schematic only
    for mark in part.get("inner_marks") or []:
        cx, cy = float(mark["cx"]) * scale, float(mark["cy"]) * scale
        r = float(mark.get("r", 4)) * scale
        pts = _transform_points(
            [[cx - r, cy], [cx, cy - r], [cx + r, cy], [cx, cy + r], [cx - r, cy]],
            assembled, explode, factor, origin)
        pieces.append(_polyline(pts, stroke=INK_LIGHT, **{"stroke-width": "0.22"}))
    for mark in part.get("hole_marks") or []:
        cx, cy = float(mark["cx"]) * scale, float(mark["cy"]) * scale
        r = 1.6 * scale
        circle_pts = []
        for i in range(12):
            a = 2 * math.pi * i / 12
            circle_pts.append([cx + r * math.cos(a), cy + r * math.sin(a)])
        circle_pts.append(circle_pts[0])
        placed_h = _transform_points(circle_pts, assembled, explode, factor, origin)
        pieces.append(_polyline(placed_h, stroke=INK_LIGHT, **{"stroke-width": "0.2"}))
    cable = part.get("cable_mm")
    if cable and len(cable) >= 2:
        cpts = [[float(p[0]) * scale, float(p[1]) * scale] for p in cable]
        placed_c = _transform_points(cpts, assembled, explode, factor, origin)
        pieces.append(_polyline(
            placed_c, stroke=INK, **{"stroke-width": "0.7", "stroke-linecap": "round",
                                     "data-line-role": "cable"}))
        pieces.append(
            f'<text x="{_f(placed_c[-1][0] - 2)}" y="{_f(placed_c[-1][1] - 2)}" '
            f'font-size="2.2" fill="{INK_LIGHT}">cable (partial)</text>')
    pieces.append("</g>")
    return pieces


def _view_box(parts: list[dict], factor: float, scale: float) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for part in parts:
        profile = [[float(x) * scale, float(y) * scale] for x, y in part["profile_mm"]]
        assembled = _scaled_pose(part["assembled"], scale)
        explode = {
            "x": float(part["explode_offset_mm"].get("x", 0.0)) * scale,
            "y": float(part["explode_offset_mm"].get("y", 0.0)) * scale,
        }
        placed = _transform_points(profile, assembled, explode, factor, (0.0, 0.0))
        for x, y in placed:
            xs.append(x)
            ys.append(y)
        cable = part.get("cable_mm")
        if cable:
            cpts = [[float(p[0]) * scale, float(p[1]) * scale] for p in cable]
            for x, y in _transform_points(cpts, assembled, explode, factor, (0.0, 0.0)):
                xs.append(x)
                ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


def render_schematic_svg(package: dict, *, explode_factor: float = 1.0) -> str:
    """Render assembled inset + exploded main view on landscape A3."""
    parts = package["parts"]
    sheet_w = float((package.get("sheet") or {}).get("width_mm", 420.0))
    sheet_h = float((package.get("sheet") or {}).get("height_mm", 297.0))
    evidence_reports = verify_evidence(package)

    # Layout rectangles (mm)
    margin = 12.0
    title_h = 18.0
    legend_h = 28.0
    # Dominant exploded view on the right/center; assembled inset top-left
    inset_w, inset_h = 120.0, 95.0
    main_x0 = margin + 8.0
    main_y0 = margin + title_h + 8.0
    main_w = sheet_w - 2 * margin - 8.0
    main_h = sheet_h - margin - legend_h - title_h - 20.0

    # Fit exploded geometry into main area
    scale = 1.15
    minx, miny, maxx, maxy = _view_box(parts, explode_factor, scale)
    gw, gh = max(maxx - minx, 1.0), max(maxy - miny, 1.0)
    fit = min((main_w - 40.0) / gw, (main_h - 30.0) / gh)
    scale *= fit
    minx, miny, maxx, maxy = _view_box(parts, explode_factor, scale)
    origin_main = (
        main_x0 + 28.0 - minx,
        main_y0 + 22.0 - miny + 8.0,
    )

    # Assembled inset
    amin_x, amin_y, amax_x, amax_y = _view_box(parts, 0.0, 1.0)
    agw, agh = max(amax_x - amin_x, 1.0), max(amax_y - amin_y, 1.0)
    inset_scale = min((inset_w - 16.0) / agw, (inset_h - 24.0) / agh)
    inset_origin = (
        margin + 10.0 - amin_x * inset_scale,
        margin + title_h + 28.0 - amin_y * inset_scale,
    )

    body: list[str] = []
    body.append(
        f'<rect width="{_f(sheet_w)}" height="{_f(sheet_h)}" fill="white"/>')
    body.append(
        f'<text id="drawing-title" x="{_f(margin)}" y="{_f(margin + 8)}" '
        f'font-size="5" font-weight="bold" fill="{INK}">'
        f'{_text(package.get("title") or "Controller schematic")}</text>')
    body.append(
        f'<text x="{_f(margin)}" y="{_f(margin + 14)}" font-size="2.5" fill="{INK_LIGHT}">'
        f'status: {_text(package.get("status"))} - draft until operator confirms '
        f'assignments and common orientation; schematic, not to scale</text>')

    # Assembled inset
    body.append(
        f'<g id="view-assembled" data-view="assembled" data-explode-factor="0">')
    body.append(
        f'<rect x="{_f(margin)}" y="{_f(margin + title_h + 4)}" '
        f'width="{_f(inset_w)}" height="{_f(inset_h)}" fill="none" stroke="{INK}" '
        f'stroke-width="0.25"/>')
    body.append(
        f'<text x="{_f(margin + 3)}" y="{_f(margin + title_h + 10)}" '
        f'font-size="3" font-weight="bold" fill="{INK}">Assembled schematic</text>')
    for part in parts:
        body.extend(_draw_part(part, 0.0, inset_origin, scale=inset_scale))
    body.append("</g>")

    # Exploded main
    body.append(
        f'<g id="view-exploded" data-view="exploded" '
        f'data-explode-factor="{_f(explode_factor)}">')
    body.append(
        f'<text x="{_f(main_x0)}" y="{_f(main_y0)}" font-size="3.5" '
        f'font-weight="bold" fill="{INK}">Exploded schematic</text>')
    body.append(
        f'<text x="{_f(main_x0)}" y="{_f(main_y0 + 5)}" font-size="2.2" fill="{INK_LIGHT}">'
        f'long axis +X (controller length); explosion toward top of page '
        f'(separates assemblies); factor={_f(explode_factor)}</text>')
    for part in parts:
        body.extend(_draw_part(part, explode_factor, origin_main, scale=scale))

    # Balloons + leaders to anchors (exploded view)
    for part in sorted(parts, key=lambda p: p.get("item_no", 0)):
        size = part.get("size_mm") or [0.0, 0.0]
        cx, cy = float(size[0]) * scale / 2.0, float(size[1]) * scale / 2.0
        ax, ay = _transform_points(
            [[cx, cy]],
            _scaled_pose(part["assembled"], scale),
            {
                "x": float(part["explode_offset_mm"]["x"]) * scale,
                "y": float(part["explode_offset_mm"]["y"]) * scale,
            },
            explode_factor,
            origin_main,
        )[0]
        bx = ax + 38.0
        by = ay - 8.0
        item = part.get("item_no")
        body.append(
            f'<g id="balloon-{_text(part["id"])}" data-for="{_text(part["id"])}">')
        body.append(
            f'<line x1="{_f(ax)}" y1="{_f(ay)}" x2="{_f(bx - 4)}" y2="{_f(by)}" '
            f'stroke="{INK}" stroke-width="0.16" class="leader"/>')
        body.append(
            f'<circle cx="{_f(bx)}" cy="{_f(by)}" r="3.2" fill="white" '
            f'stroke="{INK}" stroke-width="0.35"/>')
        body.append(
            f'<text x="{_f(bx)}" y="{_f(by + 1.1)}" text-anchor="middle" '
            f'font-size="3" font-weight="bold" fill="{INK}">{item}</text>')
        body.append(
            f'<text x="{_f(bx + 5)}" y="{_f(by + 1.1)}" font-size="2.4" fill="{INK}">'
            f'{_text(part.get("label"))}</text>')
        body.append("</g>")
    body.append("</g>")

    # Screw group callout (optional, no hole assignment)
    screw = package.get("screw_group")
    if screw:
        body.append(
            f'<g id="screw-group-{_text(screw.get("id"))}">'
            f'<text x="{_f(sheet_w - margin - 110)}" y="{_f(main_y0 + 8)}" '
            f'font-size="2.4" fill="{INK}">[{_text(screw.get("id"))}] '
            f'{_text(screw.get("label"))}</text>'
            f'<text x="{_f(sheet_w - margin - 110)}" y="{_f(main_y0 + 12)}" '
            f'font-size="2.1" fill="{INK_LIGHT}">{_text(screw.get("note"))}</text></g>')

    # Legend
    ly = sheet_h - legend_h + 4
    body.append(f'<g id="legend">')
    body.append(
        f'<text x="{_f(margin)}" y="{_f(ly)}" font-size="2.8" font-weight="bold" '
        f'fill="{INK}">Legend</text>')
    body.append(
        f'<line x1="{_f(margin)}" y1="{_f(ly + 5)}" x2="{_f(margin + 12)}" '
        f'y2="{_f(ly + 5)}" stroke="{INK}" stroke-width="0.45"/>')
    body.append(
        f'<text x="{_f(margin + 14)}" y="{_f(ly + 6)}" font-size="2.2" fill="{INK}">'
        f'observed / schematic from observed</text>')
    body.append(
        f'<line x1="{_f(margin + 70)}" y1="{_f(ly + 5)}" x2="{_f(margin + 82)}" '
        f'y2="{_f(ly + 5)}" stroke="{ESTIMATED}" stroke-width="0.45" '
        f'stroke-dasharray="0.25 0.35"/>')
    body.append(
        f'<text x="{_f(margin + 84)}" y="{_f(ly + 6)}" font-size="2.2" fill="{INK}">'
        f'partial / estimated completion</text>')
    body.append(
        f'<text x="{_f(margin)}" y="{_f(ly + 12)}" font-size="2.1" fill="{INK_LIGHT}">'
        f'Assignments and layer order unconfirmed. Evidence hashes verified at render '
        f'when extract files are present. Full provenance in companion JSON.</text>')
    # Item list compact
    ix = margin + 200
    body.append(
        f'<text x="{_f(ix)}" y="{_f(ly)}" font-size="2.8" font-weight="bold" '
        f'fill="{INK}">Items</text>')
    for i, part in enumerate(sorted(parts, key=lambda p: p.get("item_no", 0))):
        mem = part.get("memory_refs") or {}
        mem_s = mem.get("proposed_event_id") or mem.get("kind") or "?"
        body.append(
            f'<text x="{_f(ix)}" y="{_f(ly + 6 + i * 4)}" font-size="2.2" fill="{INK}">'
            f'{part.get("item_no")}. {_text(part.get("label"))} | {_text(mem_s)} | '
            f'{_text(part.get("geometry_status"))}</text>')
    body.append("</g>")

    # Machine-readable evidence summary (small)
    body.append('<g id="evidence-index" display="none">')
    for report in evidence_reports:
        body.append(
            f'<desc data-evidence-key="{_text(report["evidence_key"])}" '
            f'data-status="{_text(report["status"])}" '
            f'data-sha256="{_text(report.get("current_sha256") or report.get("cited_sha256"))}">'
            f'{_text(report.get("extract"))}</desc>')
    body.append("</g>")

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{_f(sheet_w)}mm" height="{_f(sheet_h)}mm" '
        f'viewBox="0 0 {_f(sheet_w)} {_f(sheet_h)}" '
        f'role="img" aria-labelledby="drawing-title">\n'
        f'<title id="drawing-title">{_text(package.get("title"))}</title>\n'
        f'<desc>Assembled and exploded schematic of three controller assemblies '
        f'from FULL-01 evidence. Schematic, not metrically calibrated.</desc>\n'
        f'<defs><style>'
        f'text{{font-family:Helvetica,Arial,sans-serif}}'
        f'</style></defs>\n'
        + "\n".join(body)
        + "\n</svg>\n"
    )


def build_zero_and_exploded(package: dict) -> tuple[str, str]:
    """Return (assembled-only check svg is same parts, exploded svg)."""
    zero = render_schematic_svg(package, explode_factor=0.0)
    exploded = render_schematic_svg(package, explode_factor=1.0)
    return zero, exploded


def write_schematic(package_path: str, out_dir: str,
                    explode_factor: float = 1.0) -> dict:
    package = load_package(package_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    reports = verify_evidence(package)
    svg = render_schematic_svg(package, explode_factor=explode_factor)
    zero = render_schematic_svg(package, explode_factor=0.0)
    svg_path = out / "controller_schematic.svg"
    zero_path = out / "controller_schematic_assembled.svg"
    svg_path.write_bytes(svg.encode("utf-8"))
    zero_path.write_bytes(zero.encode("utf-8"))
    companion = {
        "schema_version": SCHEMA,
        "package": str(Path(package_path).resolve()),
        "package_sha256": _sha256_file(Path(package_path)),
        "explode_factor": explode_factor,
        "evidence_verification": reports,
        "assumptions": package.get("assumptions"),
        "parts": [
            {
                "id": p["id"],
                "item_no": p.get("item_no"),
                "label": p.get("label"),
                "geometry_status": p.get("geometry_status"),
                "assignment_status": p.get("assignment_status"),
                "memory_refs": p.get("memory_refs"),
                "explode_offset_mm": p.get("explode_offset_mm"),
                "assembled": p.get("assembled"),
            }
            for p in package["parts"]
        ],
        "svg": str(svg_path),
        "svg_sha256": hashlib.sha256(svg.encode("utf-8")).hexdigest(),
        "assembled_svg": str(zero_path),
        "assembled_svg_sha256": hashlib.sha256(zero.encode("utf-8")).hexdigest(),
    }
    companion_path = out / "controller_schematic_provenance.json"
    companion_path.write_bytes(
        (json.dumps(companion, indent=2) + "\n").encode("utf-8"))
    return companion
