"""Draw calibration sheets from a config dict. Marker bits come from OpenCV."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from anastylos.boards import charuco_mask, marker_mask
from anastylos.geometry import num, page_rect, scale_bar_geometry
from anastylos.layout import dumps

SVG_NS = "http://www.w3.org/2000/svg"
PREVIEW_DPI = 72

ZONE_STYLE = {
    "work_area": {"width": 0.30, "dash": "3 1.6"},
    "tray_cell": {"width": 0.35, "dash": None},
    "fixture_board_outline": {"width": 0.40, "dash": "4 2"},
    "controller_window": {"width": 0.30, "dash": "1.5 1.2"},
}


def fmt(value: float) -> str:
    return str(num(value))


def render_svg(cfg: dict, sheet: dict) -> str:
    page = sheet["page"]
    width, height = page["width_mm"], page["height_mm"]
    origin = cfg["frames"][sheet["frame"]]["origin_on_page_mm"]
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="{SVG_NS}" width="{fmt(width)}mm" height="{fmt(height)}mm" '
            f'viewBox="0 0 {fmt(width)} {fmt(height)}" '
            f'data-sheet-id="{sheet["sheet_id"]}" data-frame="{sheet["frame"]}">'
        ),
        f"<title>{_esc(_title(sheet))}</title>",
        f'<rect id="page" x="0" y="0" width="{fmt(width)}" height="{fmt(height)}" fill="#ffffff"/>',
    ]
    if sheet.get("zones"):
        lines.append('<g id="zones" fill="none" stroke="#000000">')
        for zone in sheet["zones"]:
            lines.append(_zone_svg(zone, origin))
        lines.append("</g>")
    if sheet.get("markers"):
        lines.append('<g id="markers" fill="#000000" shape-rendering="crispEdges">')
        for marker in sheet["markers"]:
            lines.extend(_marker_svg(cfg, marker))
        lines.append("</g>")
    if sheet.get("charuco"):
        lines.append('<g id="charuco" fill="#000000" shape-rendering="crispEdges">')
        lines.extend(_charuco_svg(cfg))
        lines.append("</g>")
    for spec in sheet.get("scale_bars", []):
        lines.extend(_scale_svg(spec))
    if sheet.get("labels"):
        lines.append('<g id="labels" fill="#000000">')
        for label in sheet["labels"]:
            lines.append(_text_svg(label))
        lines.append("</g>")
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def generate(cfg: dict, out_dir: Path, *, quiet: bool = False) -> dict:
    out_dir = Path(out_dir)
    print_dir = out_dir / "print"
    preview_dir = out_dir / "preview"
    print_dir.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)
    config_path = out_dir / "calibration-config.json"
    config_path.write_text(dumps(cfg), encoding="utf-8", newline="\n")

    cairo, cairo_error = _load_cairo()
    written = {"config": config_path, "svg": [], "pdf": [], "png": [], "pdf_skipped": cairo_error}
    for sheet in cfg["sheets"]:
        svg = render_svg(cfg, sheet)
        svg_path = print_dir / f"{sheet['file_stem']}.svg"
        svg_path.write_text(svg, encoding="utf-8", newline="\n")
        written["svg"].append(svg_path)
        if cairo is None:
            if not quiet:
                print(f"[generate] {sheet['sheet_id']}: {svg_path.name} (PDF skipped, Cairo missing)")
            continue
        pdf_path = print_dir / f"{sheet['file_stem']}.pdf"
        png_path = preview_dir / f"{sheet['file_stem']}_preview.png"
        try:
            cairo.svg2pdf(bytestring=svg.encode("utf-8"), write_to=str(pdf_path))
            cairo.svg2png(bytestring=svg.encode("utf-8"), write_to=str(png_path), dpi=PREVIEW_DPI, background_color="white")
        except OSError as exc:
            cairo = None
            written["pdf_skipped"] = f"{type(exc).__name__}: {exc}"
            if not quiet:
                print(f"[generate] Cairo failed ({exc}). Remaining PDFs skipped.")
                print(f"[generate] {sheet['sheet_id']}: {svg_path.name}")
            continue
        written["pdf"].append(pdf_path)
        written["png"].append(png_path)
        if not quiet:
            print(f"[generate] {sheet['sheet_id']}: {svg_path.name}, {pdf_path.name}, {png_path.name}")
    if written["pdf_skipped"] and not quiet:
        print(f"[generate] PDF/PNG skipped: {written['pdf_skipped']}")
        print("[generate] On Windows, install a libcairo-2.dll that cairocffi can load, then run generate again.")
    return written


def _title(sheet: dict) -> str:
    names = {
        "surface_a3": "ANASTYLOS A3 surface",
        "surface_a4": "ANASTYLOS A4 surface",
        "fixture_front": "ANASTYLOS fixture front",
        "fixture_back": "ANASTYLOS fixture back",
        "charuco_a4": "ANASTYLOS ChArUco",
    }
    return names[sheet["sheet_id"]]


def _zone_svg(zone: dict, origin: list[float]) -> str:
    x, y, w, h = page_rect(zone["rect_frame_mm"], origin)
    style = ZONE_STYLE[zone["type"]]
    dash = f' stroke-dasharray="{style["dash"]}"' if style["dash"] else ""
    number = zone.get("number", "")
    ident = f'{zone["type"]}-{number}' if number != "" else zone["type"]
    return (
        f'<rect id="zone-{ident}" data-zone="{zone["type"]}" '
        f'x="{fmt(x)}" y="{fmt(y)}" width="{fmt(w)}" height="{fmt(h)}" '
        f'stroke-width="{fmt(style["width"])}"{dash}/>'
    )


def _marker_svg(cfg: dict, marker: dict) -> list[str]:
    bits = int(cfg["dictionaries"][marker["dictionary"]]["marker_bits"])
    modules = bits + 2 * int(cfg["dictionaries"][marker["dictionary"]]["border_bits"])
    mask = marker_mask(marker["dictionary"], marker["id"], modules)
    unit = float(marker["size_mm"]) / modules
    if abs(unit - float(marker["module_mm"])) > 1e-9:
        raise RuntimeError(f"marker {marker['id']} module size does not match its dictionary")
    x0, y0 = marker["top_left_page_mm"]
    header = (
        f'<g id="marker-{marker["id"]}" data-id="{marker["id"]}" data-dictionary="{marker["dictionary"]}" '
        f'data-x="{fmt(x0)}" data-y="{fmt(y0)}" data-size="{fmt(marker["size_mm"])}" data-module="{fmt(unit)}">'
    )
    return [header, *_black_rects(mask, x0, y0, unit), "</g>"]


def _charuco_svg(cfg: dict) -> list[str]:
    spec = cfg["charuco"]
    mask, unit = charuco_mask(cfg)
    x0, y0 = spec["origin_on_page_mm"]
    rows, cols = mask.shape
    header = (
        f'<g id="charuco-board" data-dictionary="{spec["dictionary"]}" data-x="{fmt(x0)}" data-y="{fmt(y0)}" '
        f'data-module="{fmt(unit)}" data-cols="{cols}" data-rows="{rows}">'
    )
    return [header, *_black_rects(mask, x0, y0, unit), "</g>"]


def _black_rects(mask, x0: float, y0: float, unit: float) -> list[str]:
    rects = []
    height, width = mask.shape
    for row in range(height):
        col = 0
        while col < width:
            if mask[row, col]:
                start = col
                while col < width and mask[row, col]:
                    col += 1
                rects.append(
                    f'<rect x="{fmt(x0 + start * unit)}" y="{fmt(y0 + row * unit)}" '
                    f'width="{fmt((col - start) * unit)}" height="{fmt(unit)}"/>'
                )
            else:
                col += 1
    return rects


def _scale_svg(spec: dict) -> list[str]:
    bar, ticks, labels = scale_bar_geometry(spec)
    lines = [
        (
            f'<g id="scale-{spec["name"]}" fill="#000000" shape-rendering="crispEdges" '
            f'data-scale="{spec["orientation"]}" data-length-mm="{fmt(spec["length_mm"])}">'
        ),
        f'<rect class="bar" x="{fmt(bar[0])}" y="{fmt(bar[1])}" width="{fmt(bar[2])}" height="{fmt(bar[3])}"/>',
    ]
    for tick in ticks:
        lines.append(f'<rect class="tick" x="{fmt(tick[0])}" y="{fmt(tick[1])}" width="{fmt(tick[2])}" height="{fmt(tick[3])}"/>')
    lines.append("</g>")
    for label in labels:
        lines.append(_text_svg(label))
    return lines


def _text_svg(label: dict) -> str:
    anchor = label.get("anchor", "start")
    weight = label.get("weight", "normal")
    return (
        f'<text x="{fmt(label["x"])}" y="{fmt(label["y"])}" '
        f'font-family="DejaVu Sans, Liberation Sans, Arial, Helvetica, sans-serif" '
        f'font-size="{fmt(label["size_mm"])}" font-weight="{weight}" text-anchor="{anchor}" '
        f'fill="#000000">{_esc(label["text"])}</text>'
    )


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _load_cairo():
    try:
        import cairosvg
    except Exception as exc:  # ImportError or OSError when libcairo is missing
        return None, f"{type(exc).__name__}: {exc}"
    return cairosvg, None


def parse_svg(path: Path) -> ET.Element:
    return ET.parse(path).getroot()
