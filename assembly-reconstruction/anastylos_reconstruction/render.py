"""Deterministic observation-review SVG renderer. Standard library only.

Pure function of one Assembly State v1 document: physical mm panels and
the observation stack stay in separate sections, every card keeps its
identity unknown, and missing evidence renders as an explicit
placeholder -- never as an invented part.
"""

from __future__ import annotations

import base64
import math
import os
from xml.sax.saxutils import escape

from anastylos_reconstruction.schema import (
    PHYSICAL_SCALE,
    TECH_LAYOUT,
    validate_state,
)

LINE = 16.0
THUMB_H = 120.0
PALETTE = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]
MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
INK = "#000000"
INK_LIGHT = "#666666"


def _f(value) -> str:
    return f"{float(value):.2f}"


def _text(value) -> str:
    return escape("" if value is None else str(value))


def _is_box(value, count: int) -> bool:
    return (
        isinstance(value, list)
        and len(value) == count
        and all(
            isinstance(item, (int, float))
            and not isinstance(item, bool)
            and math.isfinite(item)
            for item in value
        )
    )


def _href(image: str, svg_path: str | None) -> str:
    """Browser-safe relative reference: forward slashes, no backslashes."""
    if svg_path:
        try:
            image = os.path.relpath(
                image, os.path.dirname(os.path.abspath(svg_path)))
        except (OSError, ValueError):
            pass
    return image.replace(os.sep, "/")


def _pool(state: dict) -> list[dict]:
    """Unique crop images in sorted path order (deterministic ids)."""
    seen: dict[str, dict] = {}
    for component in state["components"]:
        for crop in component.get("crops") or []:
            path = crop.get("image") or ""
            size = crop.get("image_size_px")
            if path and _is_box(size, 2) and path not in seen:
                seen[path] = {"path": path, "size": list(size)}
    items = [seen[key] for key in sorted(seen)]
    for index, item in enumerate(items):
        item["id"] = f"cropimg-{index}"
    return items


def _embed_defs(canvas: _Canvas, pool: list[dict]) -> tuple[dict[str, str], list[str]]:
    """Embed readable jpg/png files once as data URIs. Returns id map +
    notes for files that could not be embedded (rendered as placeholders)."""
    embedded: dict[str, str] = {}
    notes: list[str] = []
    canvas.add("<defs>")
    for item in pool:
        mime = MIME.get(os.path.splitext(item["path"])[1].lower())
        try:
            with open(item["path"], "rb") as handle:
                raw = handle.read()
        except OSError:
            raw = b""
        if not mime or not raw:
            notes.append(f"{item['path']}: not embedded "
                         f"({'unknown type' if raw else 'unreadable'})")
            continue
        blob = base64.b64encode(raw).decode("ascii")
        canvas.add(
            f'<image id="{item["id"]}" href="data:{mime};base64,{blob}" '
            f'xlink:href="data:{mime};base64,{blob}" x="0" y="0" '
            f'width="{item["size"][0]:g}" height="{item["size"][1]:g}"/>')
        embedded[item["path"]] = item["id"]
    canvas.add("</defs>")
    return embedded, notes


class _Canvas:
    def __init__(self) -> None:
        self.parts: list[str] = []

    def add(self, text: str) -> None:
        self.parts.append(text)

    def rect(self, x, y, w, h, **attrs) -> None:
        extra = "".join(f' {key}="{_text(val)}"' for key, val in attrs.items())
        self.parts.append(
            f'<rect x="{_f(x)}" y="{_f(y)}" width="{_f(max(w, 0))}" '
            f'height="{_f(max(h, 0))}"{extra}/>')

    def line(self, x1, y1, x2, y2, **attrs) -> None:
        extra = "".join(f' {key}="{_text(val)}"' for key, val in attrs.items())
        self.parts.append(
            f'<line x1="{_f(x1)}" y1="{_f(y1)}" x2="{_f(x2)}" y2="{_f(y2)}"{extra}/>')

    def text(self, x, y, content, **attrs) -> None:
        extra = "".join(f' {key}="{_text(val)}"' for key, val in attrs.items())
        self.parts.append(
            f'<text x="{_f(x)}" y="{_f(y)}"{extra}>{_text(content)}</text>')

    def clip_view(self, x, y, width, height, clip, ref, img_size,
                  use_embedded: bool) -> None:
        """Nested viewport showing one rectified-image clip."""
        if use_embedded:
            body = f'<use href="#{ref}" xlink:href="#{ref}"/>'
        else:
            body = (f'<image href="{_text(ref)}" x="0" y="0" '
                    f'width="{img_size[0]:g}" height="{img_size[1]:g}" '
                    f'preserveAspectRatio="none"/>')
        self.add(
            f'<svg x="{_f(x)}" y="{_f(y)}" width="{_f(width)}" '
            f'height="{_f(height)}" viewBox="{clip[0]:g} {clip[1]:g} '
            f'{clip[2]:g} {clip[3]:g}">{body}'
            f'<rect x="{clip[0]:g}" y="{clip[1]:g}" width="{clip[2]:g}" '
            f'height="{clip[3]:g}" fill="none" stroke="#333"/></svg>')


def _same_location_peers(state: dict) -> dict[str, list[str]]:
    """Observation ids sharing identical verbatim (view, bbox) coordinates.

    A location match only -- never an identity claim. Declared removals
    without metric boxes are excluded."""
    groups: dict[tuple, list[str]] = {}
    for component in state["components"]:
        physical = component["physical"]
        box = physical.get("bbox_mm")
        if not _is_box(box, 4):
            continue
        key = (physical.get("source_view"), tuple(box))
        groups.setdefault(key, []).append(component["id"])
    peers: dict[str, list[str]] = {}
    for ids in groups.values():
        if len(ids) > 1:
            for ident in ids:
                peers[ident] = [other for other in ids if other != ident]
    return peers


def _underlay_choice(items: list[dict]) -> dict | None:
    """First before_rectified crop (timeline order) with a usable grid."""
    for component in items:
        region = (component["physical"].get("region") or {})
        grid = region.get("rectified") or {}
        ppm = grid.get("px_per_mm")
        size = grid.get("size_px")
        origin = grid.get("origin_frame_mm")
        if not (isinstance(ppm, (int, float)) and ppm > 0
                and math.isfinite(ppm)
                and _is_box(size, 2) and _is_box(origin, 2)):
            continue
        for crop in component.get("crops") or []:
            if crop.get("role") == "before_rectified":
                return {"crop": crop, "ppm": ppm, "size": size,
                        "origin": origin, "component": component["id"],
                        "pair": (component["physical"].get("evidence")
                                 or {}).get("pair_id")}
    return None


def _physical_panels(state: dict, canvas: _Canvas, top: float,
                     svg_path: str | None, embedded: dict[str, str]) -> float:
    """One mm-faithful map per source view over its rectified underlay."""
    views: dict[str, list[dict]] = {}
    for component in state["components"]:
        # Declared removals have no metric frame; keep them off the mm map.
        view = component["physical"].get("source_view")
        if not view:
            continue
        views.setdefault(view, []).append(component)
    y = top
    canvas.text(24, y, "Physical map (frame mm, verbatim — not to be confused "
                       "with the observation stack below)",
                **{"font-size": "14", "font-weight": "bold"})
    y += LINE + 4
    if not views:
        canvas.text(24, y, "no observations", **{"font-size": "12"})
        return y + LINE
    for view in sorted(views):
        items = views[view]
        boxes = [c["physical"]["bbox_mm"] for c in items
                 if _is_box(c["physical"].get("bbox_mm"), 4)]
        trays = []
        for component in items:
            link = component.get("tray")
            tray = (link or {}).get("tray") or {}
            if tray.get("frame") == view and _is_box(tray.get("rect_frame_mm"), 4):
                trays.append(tray)
        underlay = _underlay_choice(items)
        spots = boxes + [t["rect_frame_mm"] for t in trays]
        canvas.text(24, y, f"view {view} — scale {PHYSICAL_SCALE:g} svg-px per mm, "
                           "origin top-left, +x right, +y down",
                    **{"font-size": "12", "font-weight": "bold"})
        y += LINE
        if underlay is not None:
            img = underlay["crop"]
            frame = [underlay["origin"][0], underlay["origin"][1],
                     underlay["size"][0] / underlay["ppm"],
                     underlay["size"][1] / underlay["ppm"]]
            spots = spots + [frame]
        if not spots:
            canvas.text(24, y, "no drawable geometry (missing or degenerate boxes)",
                        **{"font-size": "12"})
            y += LINE + 8
            continue
        min_x = min(b[0] for b in spots)
        min_y = min(b[1] for b in spots)
        max_x = max(b[0] + b[2] for b in spots)
        max_y = max(b[1] + b[3] for b in spots)
        origin_x, origin_y = 24.0, y
        panel_w = (max_x - min_x) * PHYSICAL_SCALE
        panel_h = (max_y - min_y) * PHYSICAL_SCALE

        def place(box):
            return (origin_x + (box[0] - min_x) * PHYSICAL_SCALE,
                    origin_y + 4 + (box[1] - min_y) * PHYSICAL_SCALE,
                    box[2] * PHYSICAL_SCALE, box[3] * PHYSICAL_SCALE)

        if underlay is not None:
            img = underlay["crop"]
            ux, uy, uw, uh = place(frame)
            if img["image"] in embedded:
                iw, ih = img["image_size_px"]
                canvas.add(
                    f'<svg x="{_f(ux)}" y="{_f(uy)}" width="{_f(uw)}" '
                    f'height="{_f(uh)}" viewBox="0 0 {iw:g} {ih:g}">'
                    f'<use href="#{embedded[img["image"]]}" '
                    f'xlink:href="#{embedded[img["image"]]}"/></svg>')
            else:
                canvas.add(
                    f'<image href="{_text(_href(img["image"], svg_path))}" '
                    f'x="{_f(ux)}" y="{_f(uy)}" width="{_f(uw)}" '
                    f'height="{_f(uh)}" preserveAspectRatio="none"/>')
            canvas.text(origin_x, origin_y + panel_h + 8 + LINE,
                        f"background: before_rectified "
                        f"({underlay['pair']}, via {underlay['component']}) — "
                        f"labeled frame, not interpreted",
                        **{"font-size": "10"})
        else:
            canvas.text(origin_x, origin_y + panel_h + 8 + LINE,
                        "no rectified underlay available for this view",
                        **{"font-size": "10"})
        canvas.rect(origin_x, origin_y, panel_w + 200, panel_h + 8,
                    fill="none", stroke="#999999")
        for tray in trays:
            tx, ty, tw, th = place(tray["rect_frame_mm"])
            canvas.rect(tx, ty, tw, th, fill="none", stroke="#666666",
                        **{"stroke-dasharray": "4 3"})
            canvas.text(tx, ty - 4, f"T{tray.get('number')}", **{"font-size": "10"})
        for position, component in enumerate(items):
            color = PALETTE[component["timeline_index"] % len(PALETTE)]
            badge = f"[{component['timeline_index'] + 1}]"
            anchor = origin_x + panel_w + 12
            box = component["physical"]["bbox_mm"]
            if _is_box(box, 4):
                bx, by, bw, bh = place(box)
                canvas.rect(bx, by, bw, bh, fill="none", stroke=color,
                            **{"stroke-width": "2"})
                centroid = component["physical"]["centroid_mm"]
                if _is_box(centroid, 2):
                    canvas.add(
                        f'<circle cx="{_f(origin_x + (centroid[0] - min_x) * PHYSICAL_SCALE)}" '
                        f'cy="{_f(origin_y + 4 + (centroid[1] - min_y) * PHYSICAL_SCALE)}" '
                        f'r="2.5" fill="{color}"/>')
                canvas.rect(bx - 2, by - 18, 34, 15, fill=color)
                canvas.text(bx + 1, by - 6, badge,
                            **{"font-size": "10", "fill": "white",
                               "font-weight": "bold"})
                canvas.text(anchor, origin_y + 16 + position * LINE,
                            f"{badge} {component['id']} {box}",
                            **{"font-size": "10", "fill": color})
            else:
                canvas.text(anchor, origin_y + 16 + position * LINE,
                            f"{badge} {component['id']}: no drawable box",
                            **{"font-size": "10", "fill": color})
            tray = component.get("tray")
            if tray and (tray.get("tray") or {}).get("frame") == view:
                canvas.text(anchor, origin_y + 16 + (len(items) + position) * LINE,
                            f"{badge} -> T{tray['tray'].get('number')} "
                            f"({(tray.get('basis') or {}).get('kind')})",
                            **{"font-size": "10"})
        y = origin_y + panel_h + 8 + (2 * len(items) + 2) * LINE + 8
    return y


def _card_crops(canvas: _Canvas, component: dict, x: float, y: float,
                svg_path: str | None, embedded: dict[str, str]) -> float:
    """Before/after thumbnails beside one card. Returns width consumed."""
    crops = component.get("crops") or []
    if not crops:
        canvas.text(x, y + 12,
                    f"crop unavailable: {component.get('crop_unavailable')}",
                    **{"font-size": "11", "fill": "#7a5200"})
        return 0.0
    cursor = x
    for crop in crops:
        clip = crop.get("clip_px")
        size = crop.get("image_size_px")
        if not (_is_box(clip, 4) and _is_box(size, 2) and clip[3]):
            canvas.text(cursor, y + 12,
                        f"{crop.get('role')}: undrawable (non-finite clip "
                        "or image size)", **{"font-size": "11"})
            cursor += 260
            continue
        if crop["image"] in embedded:
            ref, use_embedded = embedded[crop["image"]], True
        elif os.path.isfile(crop["image"]):
            ref, use_embedded = _href(crop["image"], svg_path), False
        else:
            canvas.text(cursor, y + 12,
                        f"{crop.get('role')}: image unreadable at render time",
                        **{"font-size": "11", "fill": "#7a5200"})
            cursor += 260
            continue
        thumb_w = max(THUMB_H * clip[2] / clip[3], 12.0)
        canvas.clip_view(cursor, y, thumb_w, THUMB_H, clip, ref, size,
                         use_embedded)
        canvas.text(cursor, y + THUMB_H + 12,
                    f"{crop.get('role')} {clip}", **{"font-size": "10"})
        cursor += thumb_w + 16
    return cursor - x


def _observation_stack(state: dict, canvas: _Canvas, top: float,
                       svg_path: str | None, embedded: dict[str, str]) -> float:
    """One card per component at the projected layout coordinates."""
    count = len(state["components"])
    n_obs = sum(1 for c in state["components"] if c.get("kind", "observation") == "observation")
    n_dec = count - n_obs
    canvas.text(24, top, "Component stack (layout-only positions in layout_px "
                         "— no physical meaning; symbol size = 4 px per mm)",
                **{"font-size": "14", "font-weight": "bold"})
    canvas.text(24, top + LINE,
                f"{count} components ({n_obs} observations, {n_dec} declared) — not "
                f"{count} verified parts; no cross-event identity asserted",
                **{"font-size": "11"})
    y0 = top + 2 * LINE + 4
    components = state["components"]
    if not components:
        canvas.text(24, y0, "no components", **{"font-size": "12"})
        return y0 + LINE
    peers = _same_location_peers(state)
    base_x = 96.0
    anchors: dict[str, tuple[float, float]] = {}
    bottom = y0
    for component in components:
        box = component["exploded"]
        color = PALETTE[component["timeline_index"] % len(PALETTE)]
        badge = f"[{component['timeline_index'] + 1}]"
        x, y = base_x + box["x"], y0 + box["y"]
        w, h = max(box["w"], 6.0), max(box["h"], 6.0)
        if box["w"] <= 0 or box["h"] <= 0:
            canvas.text(x, y - 4, "degenerate geometry: symbol not to scale",
                        **{"font-size": "10", "fill": "#a00"})
        canvas.rect(x, y, w, h, fill="#f2f6fa", stroke=color,
                    **{"stroke-width": "2"})
        anchors[component["id"]] = (x, y + h / 2)
        evidence = component["physical"].get("evidence") or {}
        kind = component.get("kind", "observation")
        if kind == "declared_removal":
            declaration = component.get("declaration") or {}
            lines = [
                f"{badge} {component['id']}  kind: declared_removal  identity: unknown",
                f"operator_label: {declaration.get('operator_label')} "
                f"(declaration, not identity)",
                f"source: {declaration.get('source')}  phase: {declaration.get('phase')}",
            ]
        else:
            lines = [
                f"{badge} {component['id']}  kind: observation  identity: unknown",
                f"evidence {evidence.get('pair_id')}/{evidence.get('region')} "
                f"@ {component['physical'].get('source_view')}",
            ]
        tray = component.get("tray")
        if tray:
            basis = tray.get("basis") or {}
            if basis.get("kind") == "human":
                lines.append(f"tray {tray['tray'].get('number')} "
                             f"(operator-confirmed by {basis.get('confirmed_by')})")
            else:
                lines.append(f"tray {tray['tray'].get('number')} (evidence basis)")
        elif component.get("trays"):
            lines.append(f"{len(component['trays'])} tray links (ambiguous)")
        storage = component.get("storage")
        if storage:
            lines.append(f"storage: {storage.get('location')} "
                         f"({(storage.get('basis') or {}).get('kind')})")
        elif component.get("storages"):
            lines.append(f"{len(component['storages'])} storage links (ambiguous)")
        elif kind == "observation" and not tray and not component.get("trays"):
            lines.append("tray: unassigned")
        if component.get("compatible_groups"):
            lines.append(
                "compatible_groups: "
                + ", ".join(g["id"] for g in component["compatible_groups"])
                + " (interchangeable; no per-hole assignment)"
            )
        representation = component.get("representation") or {}
        if representation:
            lines.append(f"representation: {representation.get('kind')} — "
                         f"{(representation.get('note') or '')[:60]}")
        if kind == "observation" and not component["physical"].get("region_resolved"):
            lines.append(f"evidence missing: {component['physical'].get('missing_reason')}")
        if component["id"] in peers:
            lines.append("same observed coordinates as "
                         + ", ".join(peers[component["id"]])
                         + " (location match only, not identity)")
        for offset, line in enumerate(lines):
            canvas.text(x + w + 8, y + 12 + offset * LINE, line,
                        **{"font-size": "11"})
        _card_crops(canvas, component, x + w + 340, y, svg_path, embedded)
        bottom = max(bottom, y + max(h + len(lines) * LINE + 8, THUMB_H + 28))
    edges = sorted(state["relationships"].get("confirmed", []),
                   key=lambda e: (e.get("before"), e.get("after"))) + sorted(
        state["relationships"].get("candidates", []),
        key=lambda e: (e.get("before"), e.get("after")))
    confirmed_pairs = {(e.get("before"), e.get("after"))
                       for e in state["relationships"].get("confirmed", [])}
    for lane, edge in enumerate(edges):
        start = anchors.get(edge.get("before"))
        end = anchors.get(edge.get("after"))
        if not start or not end:
            continue
        lane_x = base_x - 24 - lane * 7
        is_confirmed = (edge.get("before"), edge.get("after")) in confirmed_pairs
        style = {} if is_confirmed else {"stroke-dasharray": "5 4"}
        color = "#0b5394" if is_confirmed else "#777777"
        canvas.line(lane_x, start[1], lane_x, end[1], stroke=color,
                    **{"stroke-width": "2"}, **style)
        canvas.line(start[0] - 2, start[1], lane_x, start[1], stroke=color, **style)
        canvas.line(lane_x, end[1], end[0] - 2, end[1], stroke=color, **style)
        canvas.text(lane_x - 4, (start[1] + end[1]) / 2,
                    "confirmed" if is_confirmed else "candidate",
                    **{"font-size": "9", "fill": color,
                       "transform": f"rotate(-90 {lane_x - 4:.2f} {(start[1] + end[1]) / 2:.2f})"})
    canvas.text(24, bottom + LINE, "solid = operator-confirmed dependency; "
                                   "dashed = unconfirmed candidate",
                **{"font-size": "11"})
    return bottom + 2 * LINE


def _notes_section(state: dict, canvas: _Canvas, top: float) -> float:
    canvas.text(24, top, "Relationships, missing evidence, warnings",
                **{"font-size": "14", "font-weight": "bold"})
    y = top + LINE + 4
    rel = state["relationships"]
    lines = [
        f"graph: {rel.get('source') or 'none'} "
        f"(status {rel.get('status')}; confirmed edges are operator-confirmed "
        "by construction, candidates are unconfirmed proposals)",
    ]
    reassembly = rel.get("reassembly")
    if isinstance(reassembly, dict) and reassembly.get("proposal") is not None:
        lines.append("reassembly proposal: "
                     + ", ".join(reassembly["proposal"])
                     + " — NOT a verified procedure")
        if reassembly.get("unresolved"):
            lines.append("unresolved steps: " + ", ".join(reassembly["unresolved"]))
    for item in state.get("missing_evidence", []):
        lines.append(f"missing evidence: {item.get('observation_id')} — {item.get('reason')}")
    for warning in state.get("warnings", []):
        lines.append(f"warning: {warning}")
    if state.get("superseded"):
        lines.append("superseded (not drawn as live): " + ", ".join(state["superseded"]))
    if len(lines) == 1:
        lines.append("none")
    for line in lines:
        canvas.text(24, y, line, **{"font-size": "11"})
        y += LINE
    return y + 4


def _component_label(component: dict) -> str:
    if component.get("kind") == "declared_removal":
        declaration = component.get("declaration") or {}
        return declaration.get("operator_label") or component["id"]
    return component["id"]


def _storage_text(component: dict) -> str:
    storage = component.get("storage")
    if storage:
        return str(storage.get("location") or "storage unknown")
    if component.get("storages"):
        locs = [str(item.get("location")) for item in component["storages"]]
        return "ambiguous storage: " + "; ".join(locs)
    tray = component.get("tray")
    if tray and tray.get("tray"):
        return f"tray cell {tray['tray'].get('number')}"
    if component.get("trays"):
        return f"{len(component['trays'])} tray links (ambiguous)"
    return "storage unassigned"


def _tech_place(components: list[dict]) -> dict[str, dict]:
    """Deterministic exploded layout slots for the technical sketch panel."""
    # Group by declaration phase when present, else a single lane.
    lanes: dict[str, list[dict]] = {}
    for component in components:
        declaration = component.get("declaration") or {}
        phase = declaration.get("phase") or (
            "observations" if component.get("kind") == "observation"
            else "undeclared-phase")
        lanes.setdefault(phase, []).append(component)
    placements: dict[str, dict] = {}
    lane_keys = sorted(lanes)
    y_cursor = TECH_LAYOUT["origin_y"] - 40.0
    for lane_index, phase in enumerate(lane_keys):
        items = lanes[phase]
        x = TECH_LAYOUT["lane_x0"] + lane_index * TECH_LAYOUT["lane_gap_x"]
        y = y_cursor
        for row, component in enumerate(items):
            placements[component["id"]] = {
                "x": x,
                "y": y,
                "phase": phase,
                "lane": lane_index,
                "row": row,
                "position": component["timeline_index"] + 1,
            }
            y += TECH_LAYOUT["row_gap_y"]
        y_cursor = max(y_cursor + TECH_LAYOUT["phase_gap_y"], y)
    return placements


def _scaled_contour_points(representation: dict, cx: float, cy: float,
                           max_size: float) -> list[tuple[float, float]] | None:
    points = representation.get("points_px") or []
    if len(points) < 3:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span = max(max_x - min_x, max_y - min_y, 1.0)
    scale = max_size / span
    mid_x = (min_x + max_x) / 2.0
    mid_y = (min_y + max_y) / 2.0
    return [
        (cx + (px - mid_x) * scale, cy + (py - mid_y) * scale)
        for px, py in points
    ]


def _draw_polyline(canvas: _Canvas, points: list[tuple[float, float]],
                   **attrs) -> None:
    if len(points) < 2:
        return
    pairs = " ".join(f"{_f(x)},{_f(y)}" for x, y in points)
    extra = "".join(f' {key}="{_text(val)}"' for key, val in attrs.items())
    canvas.add(f'<polyline points="{pairs}" fill="none"{extra}/>')


def _technical_sketch(state: dict, canvas: _Canvas, top: float,
                      svg_path: str | None, embedded: dict[str, str]) -> float:
    """Black-on-white technical explosion: silhouettes, balloons, leaders."""
    components = state.get("components") or []
    canvas.text(24, top, "Technical explosion sketch (layout-only positions — "
                         "not measured assembly vectors)",
                **{"font-size": "14", "font-weight": "bold", "fill": INK})
    canvas.text(24, top + LINE,
                "Position numbers link to event ids. Contours from change-detection "
                "masks; declared parts use labeled SUBSTITUTE cards. Identity stays unknown.",
                **{"font-size": "11", "fill": INK})
    y0 = top + 2 * LINE + 8
    if not components:
        canvas.text(24, y0, "no components", **{"font-size": "12", "fill": INK})
        return y0 + LINE
    placements = _tech_place(components)
    ox, oy = TECH_LAYOUT["origin_x"], TECH_LAYOUT["origin_y"]
    canvas.add(
        f'<g id="assembly-origin" data-role="layout-origin">'
        f'<circle cx="{_f(ox)}" cy="{_f(oy)}" r="4" fill="{INK}"/>'
        f'<line x1="{_f(ox - 28)}" y1="{_f(oy)}" x2="{_f(ox + 28)}" y2="{_f(oy)}" '
        f'stroke="{INK}" stroke-width="1"/>'
        f'<line x1="{_f(ox)}" y1="{_f(oy - 28)}" x2="{_f(ox)}" y2="{_f(oy + 28)}" '
        f'stroke="{INK}" stroke-width="1"/>'
        f'</g>')
    canvas.text(ox - 70, oy + 44, "origin (layout only)",
                **{"font-size": "10", "fill": INK_LIGHT})
    bottom = y0
    # Legend anchors for internal links.
    for component in components:
        place = placements[component["id"]]
        cx = place["x"]
        cy = place["y"]
        position = place["position"]
        representation = component.get("representation") or {}
        kind = component.get("kind", "observation")
        storage = _storage_text(component)
        label = _component_label(component)
        group_note = ""
        groups = component.get("compatible_groups") or []
        if groups:
            group_note = "group " + ", ".join(
                f"{g.get('id')}({g.get('label') or 'unlabeled'})" for g in groups)
        balloon_x = cx + TECH_LAYOUT["contour_max"] / 2 + 28
        balloon_y = cy - TECH_LAYOUT["contour_max"] / 2 - 8
        part_id = f"part-{component['id']}"
        balloon_id = f"balloon-{component['id']}"
        canvas.add(
            f'<g id="{_text(part_id)}" data-event-id="{_text(component["id"])}" '
            f'data-kind="{_text(kind)}" data-timeline-index="{position - 1}" '
            f'data-storage="{_text(storage)}" '
            f'data-representation="{_text(representation.get("kind"))}">')
        # Leader from origin toward part (layout cue only).
        canvas.line(ox, oy, cx, cy, stroke=INK_LIGHT, **{"stroke-width": "0.75",
                    "stroke-dasharray": "2 3"})
        if representation.get("kind") == "mask_contour":
            pts = _scaled_contour_points(
                representation, cx, cy, TECH_LAYOUT["contour_max"])
            if pts:
                _draw_polyline(canvas, pts, stroke=INK, **{"stroke-width": "1.5"})
            else:
                canvas.rect(cx - 30, cy - 20, 60, 40, fill="none", stroke=INK,
                            **{"stroke-dasharray": "4 3"})
                canvas.text(cx - 28, cy, "contour unavailable",
                            **{"font-size": "9", "fill": INK})
        elif representation.get("kind") == "evidence_crop":
            crop = None
            for item in component.get("crops") or []:
                if item.get("image") == representation.get("source_image"):
                    crop = item
                    break
            if crop is None and component.get("crops"):
                crop = component["crops"][0]
            w = TECH_LAYOUT["card_w"]
            h = TECH_LAYOUT["card_h"]
            canvas.rect(cx - w / 2, cy - h / 2, w, h, fill="none", stroke=INK,
                        **{"stroke-dasharray": "5 3", "stroke-width": "1.25"})
            if crop and _is_box(crop.get("clip_px"), 4) and _is_box(
                    crop.get("image_size_px"), 2):
                if crop["image"] in embedded:
                    ref, use_embedded = embedded[crop["image"]], True
                elif os.path.isfile(crop["image"]):
                    ref, use_embedded = _href(crop["image"], svg_path), False
                else:
                    ref, use_embedded = None, False
                if ref is not None:
                    canvas.clip_view(cx - w / 2 + 4, cy - h / 2 + 4, w - 8, h - 20,
                                     crop["clip_px"], ref, crop["image_size_px"],
                                     use_embedded)
            canvas.text(cx - w / 2 + 4, cy + h / 2 - 6, "SUBSTITUTE crop",
                        **{"font-size": "9", "fill": INK})
        else:
            w = TECH_LAYOUT["card_w"]
            h = TECH_LAYOUT["card_h"]
            canvas.rect(cx - w / 2, cy - h / 2, w, h, fill="none", stroke=INK,
                        **{"stroke-dasharray": "6 4", "stroke-width": "1.25"})
            canvas.text(cx - w / 2 + 6, cy - 10, "SUBSTITUTE",
                        **{"font-size": "10", "font-weight": "bold", "fill": INK})
            canvas.text(cx - w / 2 + 6, cy + 6, label[:28],
                        **{"font-size": "10", "fill": INK})
            canvas.text(cx - w / 2 + 6, cy + 22, "no measured silhouette",
                        **{"font-size": "9", "fill": INK_LIGHT})
        # Balloon with internal link target.
        canvas.add(
            f'<a href="#{_text(part_id)}" id="{_text(balloon_id)}">'
            f'<circle cx="{_f(balloon_x)}" cy="{_f(balloon_y)}" '
            f'r="{_f(TECH_LAYOUT["balloon_r"])}" fill="white" stroke="{INK}" '
            f'stroke-width="1.5"/>'
            f'<text x="{_f(balloon_x)}" y="{_f(balloon_y + 4)}" text-anchor="middle" '
            f'font-size="11" font-weight="bold" fill="{INK}">{position}</text></a>')
        canvas.line(cx, cy - 8, balloon_x - TECH_LAYOUT["balloon_r"], balloon_y,
                    stroke=INK, **{"stroke-width": "1"})
        meta_y = cy + TECH_LAYOUT["contour_max"] / 2 + 14
        canvas.text(cx - 40, meta_y, f"{position}. {component['id']}",
                    **{"font-size": "10", "fill": INK})
        canvas.text(cx - 40, meta_y + 12, f"label: {label}" if kind == "declared_removal"
                    else "identity: unknown",
                    **{"font-size": "9", "fill": INK_LIGHT})
        canvas.text(cx - 40, meta_y + 24, f"storage: {storage}",
                    **{"font-size": "9", "fill": INK_LIGHT})
        evid = ""
        if kind == "observation":
            evidence = (component.get("physical") or {}).get("evidence") or {}
            region = (component.get("physical") or {}).get("region") or {}
            sha = (region.get("result_sha256_current")
                   or evidence.get("result_sha256") or "")
            evid = f"evidence {evidence.get('pair_id')}/{evidence.get('region')}"
            if sha:
                evid += f" sha256:{str(sha)[:12]}…"
            if representation.get("source_sha256"):
                evid += f" mask:{representation['source_sha256'][:12]}…"
        else:
            declaration = component.get("declaration") or {}
            evid = (f"declared by {declaration.get('declared_by')} "
                    f"phase {declaration.get('phase')}")
        canvas.text(cx - 40, meta_y + 36, evid[:70],
                    **{"font-size": "8", "fill": INK_LIGHT})
        if group_note:
            canvas.text(cx - 40, meta_y + 48, group_note[:70],
                        **{"font-size": "8", "fill": INK_LIGHT})
        canvas.add("</g>")
        bottom = max(bottom, meta_y + 60)
    # Compact bill of materials with internal references.
    bom_x = 24.0
    bom_y = bottom + LINE
    canvas.text(bom_x, bom_y, "Parts index (event id → storage → representation)",
                **{"font-size": "12", "font-weight": "bold", "fill": INK})
    bom_y += LINE
    for component in components:
        place = placements[component["id"]]
        representation = component.get("representation") or {}
        line = (
            f"[{place['position']}] {component['id']}  "
            f"{_component_label(component)}  |  {_storage_text(component)}  |  "
            f"{representation.get('kind')}"
        )
        canvas.add(
            f'<a href="#part-{_text(component["id"])}">'
            f'<text x="{_f(bom_x)}" y="{_f(bom_y)}" font-size="10" fill="{INK}">'
            f"{_text(line)}</text></a>")
        bom_y += LINE
    canvas.text(bom_x, bom_y + 4,
                "Exploded offsets are layout coordinates only. Removal order "
                "structures the lanes; it does not prove mechanical dependence.",
                **{"font-size": "10", "fill": INK_LIGHT})
    return bom_y + 2 * LINE


def _header(state: dict, canvas: _Canvas) -> float:
    canvas.text(24, 28, "ANASTYLOS technical explosion sketch (Assembly State v1 — proposed)",
                **{"font-size": "16", "font-weight": "bold"})
    provenance = state.get("provenance", {})
    memory_sha = (provenance.get("memory_sha256") or "unreadable")[:12]
    components = state.get("components") or []
    n_obs = sum(1 for c in components if c.get("kind", "observation") == "observation")
    n_dec = len(components) - n_obs
    canvas.text(24, 48, f"status {state.get('status')}"
                        + (f": {state.get('reason')}" if state.get("reason") else ""),
                **{"font-size": "12"})
    canvas.text(24, 64, f"memory {provenance.get('memory')} sha256:{memory_sha}…  "
                        f"components: {len(components)} "
                        f"({n_obs} observations, {n_dec} declared)",
                **{"font-size": "11"})
    canvas.text(24, 80, "Identity stays unknown. Operator labels are declarations. "
                        "Exploded positions are layout-only.",
                **{"font-size": "11"})
    if state.get("status") == "unavailable":
        canvas.text(24, 104, f"unavailable: {state.get('reason')}",
                    **{"font-size": "12", "fill": "#a00"})
        return 120.0
    return 100.0


def _footer(state: dict, canvas: _Canvas, top: float,
            embedded: dict[str, str], embed_notes: list[str]) -> float:
    canvas.text(24, top, "Provenance (SHA-256 over input file bytes, computed at "
                         "projection time):",
                **{"font-size": "12", "font-weight": "bold"})
    y = top + LINE
    provenance = state.get("provenance", {})
    canvas.text(24, y, f"memory: {provenance.get('memory')} "
                       f"{provenance.get('memory_sha256')}",
                **{"font-size": "10"})
    y += LINE
    for path in sorted((provenance.get("result_files") or {})):
        canvas.text(24, y, f"result: {path} "
                           f"{(provenance['result_files'] or {})[path]}",
                    **{"font-size": "10"})
        y += LINE
    rel = state.get("relationships", {})
    if rel.get("source"):
        canvas.text(24, y, f"graph: {rel.get('source')} {rel.get('source_sha256')}",
                    **{"font-size": "10"})
        y += LINE
    if embedded or embed_notes:
        canvas.text(24, y, f"embedded images: {len(embedded)}; "
                           + ("all crops self-contained"
                              if not embed_notes else "; ".join(embed_notes)),
                    **{"font-size": "10"})
        y += LINE
    projector = state.get("projector", {})
    canvas.text(24, y, f"schema {state.get('schema_version')} — "
                       f"{projector.get('name')} {projector.get('version')} "
                       "(deterministic: identical inputs, identical bytes)",
                **{"font-size": "10"})
    return y + LINE


def render_svg(state: dict, svg_path: str | None = None,
               embed_crops: bool = False) -> str:
    """Render one Assembly State v1 document to an SVG string.

    With embed_crops, readable jpg/png evidence images are embedded once
    as data URIs so the SVG is self-contained; otherwise crops are linked
    with browser-safe relative references."""
    validate_state(state)
    canvas = _Canvas()
    components = state.get("components") or []
    placements = _tech_place(components) if components else {}
    lane_count = 1 + max((p["lane"] for p in placements.values()), default=0)
    width = max(1100.0, TECH_LAYOUT["lane_x0"] + lane_count * TECH_LAYOUT["lane_gap_x"] + 80.0)
    pool = _pool(state)
    embedded: dict[str, str] = {}
    embed_notes: list[str] = []
    top = _header(state, canvas)
    if state.get("status") != "unavailable":
        if embed_crops and pool:
            embedded, embed_notes = _embed_defs(canvas, pool)
        top = _technical_sketch(state, canvas, top, svg_path, embedded)
        top = _physical_panels(state, canvas, top + 8, svg_path, embedded)
        top = _observation_stack(state, canvas, top + 8, svg_path, embedded)
        top = _notes_section(state, canvas, top + 8)
    top = _footer(state, canvas, top + 8, embedded, embed_notes)
    body = "\n".join(canvas.parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{_f(width)}" height="{_f(top)}" viewBox="0 0 {_f(width)} {_f(top)}" '
        f'font-family="Helvetica, Arial, sans-serif">\n'
        f'<rect x="0" y="0" width="{_f(width)}" height="{_f(top)}" fill="white"/>\n'
        f"{body}\n</svg>\n"
    )
