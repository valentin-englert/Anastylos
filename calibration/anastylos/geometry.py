"""Pure geometry helpers. Units are millimetres. No marker positions live here."""

from __future__ import annotations


def num(value: float) -> float | int:
    rounded = round(float(value), 4)
    if abs(rounded - round(rounded)) < 1e-9:
        return int(round(rounded))
    return rounded


def page_rect(rect_frame_mm: list[float], origin_on_page_mm: list[float]) -> tuple[float, float, float, float]:
    x, y, w, h = rect_frame_mm
    ox, oy = origin_on_page_mm
    return (x + ox, y + oy, w, h)


def text_width_mm(text: str, size_mm: float) -> float:
    """Conservative width estimate for DejaVu Sans / Arial, used for clearance checks."""
    if text.isupper() and text.isalpha():
        factor = 0.74
    else:
        factor = 0.62
    return factor * size_mm * len(text)


def text_bbox(label: dict) -> tuple[float, float, float, float]:
    """Axis-aligned box for a horizontal SVG text label. `y` is the baseline."""
    size = float(label["size_mm"])
    width = text_width_mm(label["text"], size)
    height = size
    anchor = label.get("anchor", "start")
    if anchor == "middle":
        left = float(label["x"]) - width / 2
    elif anchor == "end":
        left = float(label["x"]) - width
    else:
        left = float(label["x"])
    top = float(label["y"]) - 0.80 * height
    return (left, top, width, height)


def scale_bar_geometry(spec: dict) -> tuple[tuple[float, float, float, float], list[tuple[float, float, float, float]], list[dict]]:
    """Bar, ticks, and end labels for one 100 mm check line.

    The bar itself is exactly `length_mm` long. End ticks stay inside that span.
    """
    x, y = (float(v) for v in spec["start_page_mm"])
    length = float(spec["length_mm"])
    thickness = float(spec["thickness_mm"])
    tick_width = float(spec["tick_width_mm"])
    side = int(spec["tick_side"])
    horizontal = spec["orientation"] == "horizontal"
    bar = (x, y, length, thickness) if horizontal else (x, y, thickness, length)

    ticks: list[tuple[float, float, float, float]] = []
    steps = int(round(length / float(spec["minor_every_mm"])))
    for i in range(steps + 1):
        position = i * float(spec["minor_every_mm"])
        major = abs(position / float(spec["major_every_mm"]) - round(position / float(spec["major_every_mm"]))) < 1e-6
        tick_len = float(spec["major_len_mm"] if major else spec["minor_len_mm"])
        along = min(max(position - tick_width / 2, 0.0), length - tick_width)
        if horizontal:
            tick_y = y + thickness if side > 0 else y - tick_len
            ticks.append((x + along, tick_y, tick_width, tick_len))
        else:
            tick_x = x + thickness if side > 0 else x - tick_len
            ticks.append((tick_x, y + along, tick_len, tick_width))

    end = 2.3
    labels: list[dict] = []
    if horizontal:
        if side > 0:
            baseline = y + thickness + float(spec["major_len_mm"]) + 0.55 + 0.78 * end
            title_y = y - 0.85
        else:
            baseline = y - float(spec["major_len_mm"]) - 0.55
            title_y = y + thickness + 0.70 + 0.78 * 2.5
        labels.append(_label("0", x, baseline, end, "middle"))
        labels.append(_label("100", x + length, baseline, end, "middle"))
        labels.append(_label("100 mm", x + length / 2, title_y, 2.5, "middle", "bold"))
    else:
        if side > 0:
            label_x = x + thickness + float(spec["major_len_mm"]) + 0.8
            anchor = "start"
        else:
            label_x = x - float(spec["major_len_mm"]) - 0.7
            anchor = "end"
        labels.append(_label("0", label_x, y + 0.32 * end, end, anchor))
        labels.append(_label("100", label_x, y + length + 0.15 * end, end, anchor))
    return bar, ticks, labels


def _label(text: str, x: float, y: float, size: float, anchor: str, weight: str = "normal") -> dict:
    return {
        "text": text,
        "x": num(x),
        "y": num(y),
        "size_mm": size,
        "anchor": anchor,
        "weight": weight,
    }
