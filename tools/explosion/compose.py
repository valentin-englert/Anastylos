"""Compose the schematic explosion view from the drawing jobs of one session.

    python compose.py propose --session <dir>                 write the layout skeleton from the existing jobs
    python compose.py confirm --session <dir> --job <job> [--view-side S] [--label L] [--flip yes|no]
    python compose.py compose --session <dir> [--out <dir>] [--mode exploded|assembled]

Inputs: ``<session>/drawings/jobs/<job>/`` (drawing.svg, overlay.jpg, rectified image, binding.json, progress.json)
and the operator's layout spec ``<session>/drawings/explosion.json`` (see explosion_layout.py).
Outputs: ``explosion.svg`` and ``manifest.json`` (anastylos-explosion-manifest@1) in ``<session>/drawings/explosion/``.

Only outlines that exist in a drawing are drawn. Their mm are each drawing's own sheet plane and are kept at one
uniform scale; the spacing between layers is layout only (schematic) and implies no height, mounting point or
assembly path. Stdlib only, offline, no timestamps in the outputs: identical inputs give identical bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

sys.path.insert(0, str(Path(__file__).resolve().parent))

import explosion_jobs as jobs  # noqa: E402
import explosion_layout as layout  # noqa: E402

SCHEMA_MANIFEST = "anastylos-explosion-manifest@1"
OUTPUT_DIR = "explosion"
MODES = ("exploded", "assembled")
MARGIN, HEADER_H, LABEL_BAND, LAYER_GAP, LEGEND_W, LEGEND_ROW, FOOTER_H, MIN_CONTENT_W = 10.0, 33.0, 11.0, 22.0, 80.0, 24.0, 46.0, 90.0

SCHEMATIC_NOTE = "EXPLOSION LAYOUT: layer order requires operator confirmation; spacing between layers is layout only."
NO_CLAIM_NOTE = "No height, mounting point or assembly path is implied."
SCALE_NOTE = "Outlines: mm on each drawing's own sheet plane, one uniform scale, not registered to each other; not part dimensions."
ASSEMBLED_NOTE = "Assembled mode only centres the outlines on their bounding boxes; it is not an alignment."

STYLE = """.ax-part{cursor:pointer}.ax-part:focus{outline:none}.ax-legend{cursor:pointer}
.ax-part.ax-selected .ax-outline{stroke:#0f5c8c;stroke-width:1.2}.ax-legend.ax-selected rect.ax-row{fill:#dbe9f5}
svg[data-mode="assembled"] .ax-exploded-only{display:none}"""


@dataclass(frozen=True)
class Composition:
    svg: str
    manifest: dict


def _n(value: float) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _t(value, limit: int | None = None) -> str:
    text = "" if value is None else str(value)
    return escape(text[:limit] if limit else text)  # cut before escaping: never inside an entity


def _a(value) -> str:
    return quoteattr("" if value is None else str(value))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _layer_state(layer: dict, evidence: jobs.JobEvidence) -> tuple[str | None, list[str]]:
    """(reason the layer cannot be drawn, reasons it is drawn but unconfirmed)."""
    if not evidence.exists:
        return "drawing job folder is missing", []
    if evidence.binding_state == "rejected":
        return "operator rejected this drawing", []
    if evidence.outline is None:
        return evidence.outline_error or "no outline", []
    if evidence.state != "done":
        return f"drawing job state is {evidence.state}", []
    if evidence.status not in jobs.DRAWABLE_STATUSES:
        return f"drawing job requires technical review ({evidence.status})", []
    reasons = []
    if evidence.binding_state == "none":
        reasons.append("drawing not bound by operator")
    elif evidence.binding_state == "stale":
        reasons.append("binding no longer matches the drawing")
    elif evidence.binding_state == "unreadable":
        reasons.append("binding.json unreadable")
    if not layer["confirmed_by_operator"]:
        reasons.append("layer, order and view side not confirmed")
    if not layer["view_side"]:
        reasons.append("view side not set")
    return None, reasons


def _place(items: list[dict]) -> None:
    """Fill in the canvas-space centres: exploded stacks the layers top to bottom, assembled centres them together."""
    cursor = HEADER_H
    for item in items:
        _, _, _, h = item["size"]
        item["exploded_center"] = (item["cx"], cursor + LABEL_BAND + h / 2)
        cursor += LABEL_BAND + h + LAYER_GAP
    tallest = max((item["size"][3] for item in items), default=0.0)
    for item in items:  # assembled: one common centre just below the header, so the page does not jump when toggling
        item["assembled_center"] = (item["cx"], HEADER_H + LABEL_BAND + tallest / 2)


def _part_svg(item: dict, mode: str) -> list[str]:
    evidence: jobs.JobEvidence = item["evidence"]
    layer = item["layer"]
    x0, y0, x1, y1 = evidence.outline.bbox
    bcx, bcy = (x0 + x1) / 2, (y0 + y1) / 2
    ex, ey = item["exploded_center"]
    ax, ay = item["assembled_center"]
    translate = {"exploded": (ex - bcx, ey - bcy), "assembled": (ax - bcx, ay - bcy)}
    confirmed = item["status"] == "confirmed"
    fill, stroke, dash = ("#dfe6ee", "#1d2733", "") if confirmed else ("#fff4d6", "#8a5a00", ' stroke-dasharray="2 1.2"')
    flip = f' transform="translate({_n(2 * bcx)} 0) scale(-1 1)"' if layer["flip_horizontal"] else ""
    tag = "confirmed" if confirmed else "UNCONFIRMED: " + "; ".join(item["reasons"])
    title = f"{layer['label']} - {tag}"
    out = [f'<g id={_a("part-" + layer["job"])} class="ax-part" data-job={_a(layer["job"])} data-label={_a(layer["label"])} '
           f'data-status="{item["status"]}" data-binding="{evidence.binding_state}" data-layer="{item["index"]}" '
           f'data-view-side={_a(layer["view_side"] or "")} data-flip="{str(layer["flip_horizontal"]).lower()}" '
           f'data-sha256="{item["drawing_sha256"]}" '
           f'data-exploded-translate="{_n(translate["exploded"][0])},{_n(translate["exploded"][1])}" '
           f'data-assembled-translate="{_n(translate["assembled"][0])},{_n(translate["assembled"][1])}" '
           f'transform="translate({_n(translate[mode][0])} {_n(translate[mode][1])})" tabindex="0" role="button" '
           f'aria-label={_a(title)}>', f"<title>{_t(title)}</title>",
           f'<g class="ax-shape"{flip}>']
    out += [f'<path class="ax-outline" d={_a(d)} fill="{fill}" fill-opacity="0.85" stroke="{stroke}" stroke-width="0.5" '
            f'stroke-linejoin="round"{dash}/>' for d in evidence.outline.paths]
    out.append("</g>")
    label_color = "#1d2733" if confirmed else "#8a5a00"
    out.append(f'<g class="ax-exploded-only"><text x="{_n(bcx)}" y="{_n(y0 - 6.5)}" font-size="4" font-weight="bold" '
               f'text-anchor="middle" fill="#1d2733">{item["index"] + 1}  {_t(layer["label"], 70)}</text>'
               f'<text x="{_n(bcx)}" y="{_n(y0 - 2.5)}" font-size="2.8" text-anchor="middle" fill="{label_color}">'
               f'{_t(tag, 110)}</text></g>')
    out.append("</g>")
    return out


def _legend_svg(items: list[dict], x: float) -> list[str]:
    out = [f'<text x="{_n(x)}" y="{_n(HEADER_H - 4)}" font-size="3.6" font-weight="bold" fill="#1d2733">Layers, top to bottom</text>']
    for row, item in enumerate(items):
        layer = item["layer"]
        y = HEADER_H + row * LEGEND_ROW
        confirmed = item["status"] == "confirmed"
        color = "#1d2733" if confirmed else "#8a5a00"
        row_dash = "" if confirmed else ' stroke-dasharray="1.5 1"'
        out.append(f'<g id={_a("legend-" + layer["job"])} class="ax-legend" data-job={_a(layer["job"])} data-status="{item["status"]}">'
                   f'<rect class="ax-row" x="{_n(x - 2)}" y="{_n(y)}" width="{_n(LEGEND_W - 2)}" height="{_n(LEGEND_ROW - 3)}" '
                   f'fill="#f4f6f9" stroke="{color}" stroke-width="0.3"{row_dash}/>'
                   f'<text x="{_n(x + 1)}" y="{_n(y + 6)}" font-size="3.4" font-weight="bold" fill="{color}">{item["index"] + 1}  {_t(layer["label"], 34)}</text>'
                   f'<text x="{_n(x + 1)}" y="{_n(y + 11)}" font-size="2.6" fill="{color}">{"confirmed" if confirmed else "UNCONFIRMED"}'
                   f' | view side: {_t(layer["view_side"] or "not set")}{" | mirrored" if layer["flip_horizontal"] else ""}</text>'
                   f'<text x="{_n(x + 1)}" y="{_n(y + 15.5)}" font-size="2.4" fill="#44536a">{_t(item["reasons"][0] if item["reasons"] else "drawing bound, layer confirmed", 46)}</text></g>')
    return out


def _file_entry(evidence: jobs.JobEvidence, name: str):
    entry = evidence.files.get(name)
    return dict(entry) if entry else None


def compose(session_dir: Path, spec: dict | None = None, mode: str = "exploded") -> Composition:
    """Compose from the saved layout spec (or from ``spec``, validated, for a preview). Reads only; writes nothing."""
    if mode not in MODES:
        raise layout.LayoutError(f"mode must be one of {MODES}.")
    session_dir = Path(session_dir)
    if spec is None:
        spec_path = layout.layout_path(session_dir)
        if not spec_path.is_file():
            raise layout.LayoutError("There is no layout yet: propose one first.")
        spec_bytes = spec_path.read_bytes()
        spec = layout.validate(json.loads(spec_bytes.decode("utf-8")))
    else:
        spec = layout.validate(spec)
        spec_bytes = json.dumps(spec, sort_keys=True, separators=(",", ":")).encode("utf-8")

    drawn, excluded, warnings = [], [], []
    for index, entry in enumerate(spec["layers"]):
        evidence = jobs.read_job(session_dir, entry["job"])
        blocked, reasons = _layer_state(entry, evidence)
        if blocked:
            excluded.append({"index": index, "job": entry["job"], "label": entry["label"], "reason": blocked})
            continue
        if entry["view_side"] and evidence.view_side and entry["view_side"] != evidence.view_side:
            warnings.append(f"{entry['job']}: layer view side {entry['view_side']!r} differs from the drawing job's {evidence.view_side!r}.")
        x0, y0, x1, y1 = evidence.outline.bbox
        drawn.append({"index": index, "layer": entry, "evidence": evidence, "reasons": reasons,
                      "status": "unconfirmed" if reasons else "confirmed", "size": (x0, y0, x1 - x0, y1 - y0),
                      "drawing_sha256": evidence.files["drawing.svg"]["sha256"]})

    content_w = max([MIN_CONTENT_W] + [item["size"][2] for item in drawn])
    for item in drawn:
        item["cx"] = MARGIN + content_w / 2
    _place(drawn)
    stack_end = HEADER_H + sum(LABEL_BAND + item["size"][3] + LAYER_GAP for item in drawn) - LAYER_GAP if drawn else HEADER_H
    legend_x = MARGIN + content_w + 18
    width = legend_x + LEGEND_W + MARGIN
    height = max(stack_end, HEADER_H + len(drawn) * LEGEND_ROW) + FOOTER_H + MARGIN

    unconfirmed = [item for item in drawn if item["status"] != "confirmed"]
    all_confirmed = bool(drawn) and not unconfirmed and not excluded
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{_n(width)}mm" height="{_n(height)}mm" viewBox="0 0 {_n(width)} {_n(height)}" '
           f'font-family="Helvetica, Arial, sans-serif" data-mode="{mode}" data-schema={_a(SCHEMA_MANIFEST)}>',
           f"<style>{STYLE}</style>", f'<rect width="{_n(width)}" height="{_n(height)}" fill="#ffffff"/>',
           f'<text x="{_n(MARGIN)}" y="10" font-size="6" font-weight="bold" fill="#1d2733">Explosion view - schematic layout</text>',
           f'<text x="{_n(MARGIN)}" y="16.5" font-size="3" fill="#44536a">{_t(SCHEMATIC_NOTE)}</text>',
           f'<text x="{_n(MARGIN)}" y="21" font-size="3" fill="#44536a">{_t(NO_CLAIM_NOTE)}</text>',
           f'<text x="{_n(MARGIN)}" y="26.5" font-size="3.2" font-weight="bold" fill="{"#1e6b3a" if all_confirmed else "#a4262c"}">'
           + (f"All {len(drawn)} layers confirmed by the operator." if all_confirmed else
              f"{len(unconfirmed)} of {len(drawn)} drawn layers UNCONFIRMED (dashed outline)" + (f"; {len(excluded)} not drawn" if excluded else "") + ".")
           + "</text>"]
    if drawn:
        gaps = [(upper["exploded_center"][1] + upper["size"][3] / 2, lower["exploded_center"][1] - lower["size"][3] / 2 - LABEL_BAND)
                for upper, lower in zip(drawn, drawn[1:])]  # the axis is drawn in the gaps only, never across a part or its label
        if gaps:
            lines = "".join(f'<line x1="{_n(drawn[0]["cx"])}" y1="{_n(y1)}" x2="{_n(drawn[0]["cx"])}" y2="{_n(y2)}"/>' for y1, y2 in gaps)
            out.append(f'<g class="ax-exploded-only" stroke="#44536a" stroke-width="0.3" stroke-dasharray="4 1.5 0.8 1.5">{lines}'
                       f'<text x="{_n(drawn[0]["cx"] + 2)}" y="{_n((gaps[0][0] + gaps[0][1]) / 2)}" font-size="2.4" fill="#44536a" stroke="none">'
                       f'explosion axis (schematic)</text></g>')
        for item in reversed(drawn):  # painter's order: the top layer (index 0) is painted last, so it is the click target when assembled
            out += _part_svg(item, mode)
        out += _legend_svg(drawn, legend_x)
    else:
        out.append(f'<text x="{_n(MARGIN)}" y="{_n(HEADER_H + 8)}" font-size="4" fill="#a4262c">No drawable layers: nothing is invented.</text>')
    footer = height - FOOTER_H
    out += [f'<g id="scale"><line x1="{_n(MARGIN)}" y1="{_n(footer + 3)}" x2="{_n(MARGIN + 20)}" y2="{_n(footer + 3)}" stroke="#222" stroke-width="0.4"/>'
            f'<line x1="{_n(MARGIN)}" y1="{_n(footer + 2)}" x2="{_n(MARGIN)}" y2="{_n(footer + 4)}" stroke="#222" stroke-width="0.4"/>'
            f'<line x1="{_n(MARGIN + 20)}" y1="{_n(footer + 2)}" x2="{_n(MARGIN + 20)}" y2="{_n(footer + 4)}" stroke="#222" stroke-width="0.4"/>'
            f'<text x="{_n(MARGIN + 23)}" y="{_n(footer + 4)}" font-size="2.6">20 mm (drawing sheet plane)</text></g>',
            f'<text x="{_n(MARGIN)}" y="{_n(footer + 10)}" font-size="2.6" fill="#333">{_t(SCALE_NOTE)}</text>',
            f'<text x="{_n(MARGIN)}" y="{_n(footer + 14.5)}" font-size="2.6" fill="#333">{_t(ASSEMBLED_NOTE)}</text>']
    for row, skipped in enumerate(excluded[:4]):
        out.append(f'<text x="{_n(MARGIN)}" y="{_n(footer + 20 + row * 4)}" font-size="2.6" fill="#a4262c" data-not-drawn={_a(skipped["job"])}>'
                   f'Not drawn: {_t(skipped["label"], 50)} - {_t(skipped["reason"], 90)}</text>')
    out.append("</svg>\n")
    svg = "\n".join(out)

    manifest = {
        "schema": SCHEMA_MANIFEST, "schematic": True, "mode": mode,
        "notes": [SCHEMATIC_NOTE, NO_CLAIM_NOTE, SCALE_NOTE, ASSEMBLED_NOTE],
        "scale": {"uniform": True, "svg_units_per_mm": 1.0, "basis": "each drawing's own sheet plane; not part dimensions"},
        "canvas": {"width": round(width, 3), "height": round(height, 3)},
        "layout_spec": {"path": f"drawings/{layout.FILE}", "sha256": _sha(spec_bytes), "layer_count": len(spec["layers"])},
        "all_confirmed": all_confirmed,
        "layers": [{
            "index": item["index"], "job": item["layer"]["job"], "label": item["layer"]["label"], "status": item["status"],
            "reasons": item["reasons"], "binding": item["evidence"].binding_state, "view_side": item["layer"]["view_side"],
            "job_view_side": item["evidence"].view_side, "flip_horizontal": item["layer"]["flip_horizontal"],
            "confirmed_by_operator": item["layer"]["confirmed_by_operator"], "part": item["evidence"].part,
            "scope": item["evidence"].scope, "outline_size_mm": [round(item["size"][2], 2), round(item["size"][3], 2)],
            "placement": {"exploded_center": [round(c, 3) for c in item["exploded_center"]],
                          "assembled_center": [round(c, 3) for c in item["assembled_center"]]},
            "inputs": {name: _file_entry(item["evidence"], name)
                       for name in ("drawing.svg", "overlay.jpg", "rectified.png", "binding.json", "progress.json")},
        } for item in drawn],
        "excluded": excluded, "warnings": warnings, "svg_sha256": _sha(svg.encode("utf-8")),
    }
    return Composition(svg, manifest)


def write_outputs(session_dir: Path, out_dir: Path | None = None, mode: str = "exploded") -> dict:
    """Compose and write explosion.svg + manifest.json (outside the job folders); returns the manifest."""
    result = compose(session_dir, mode=mode)
    target = Path(out_dir) if out_dir else Path(session_dir) / "drawings" / OUTPUT_DIR
    target.mkdir(parents=True, exist_ok=True)
    (target / "explosion.svg").write_text(result.svg, encoding="utf-8", newline="\n")
    (target / "manifest.json").write_text(json.dumps(result.manifest, indent=1) + "\n", encoding="utf-8", newline="\n")
    return result.manifest


def _flag(value: str | None) -> bool | None:
    return None if value is None else value.lower() in ("yes", "true", "1")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("propose", "confirm", "compose"):
        command = sub.add_parser(name)
        command.add_argument("--session", required=True, type=Path)
        if name == "confirm":
            command.add_argument("--job", required=True)
            command.add_argument("--view-side")
            command.add_argument("--label")
            command.add_argument("--flip", choices=("yes", "no"))
        if name == "compose":
            command.add_argument("--out", type=Path)
            command.add_argument("--mode", choices=MODES, default="exploded")
    args = parser.parse_args(argv)
    try:
        if args.command == "propose":
            print(json.dumps(layout.propose(args.session), indent=1))
        elif args.command == "confirm":
            print(json.dumps(layout.confirm_layer(args.session, args.job, label=args.label, view_side=args.view_side,
                                                  flip_horizontal=_flag(args.flip)), indent=1))
        else:
            manifest = write_outputs(args.session, args.out, args.mode)
            print(json.dumps({"layers": len(manifest["layers"]), "excluded": manifest["excluded"], "all_confirmed": manifest["all_confirmed"],
                              "svg_sha256": manifest["svg_sha256"]}, indent=1))
    except (layout.LayoutError, jobs.JobError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
