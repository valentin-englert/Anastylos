"""Sheet geometry for the ANASTYLOS calibration kit.

This module is the only place that states marker positions, tray fields,
the fixture window, and paper sizes. `build_config()` writes the JSON the
rest of the project reads. Coordinates are millimetres, x to the right,
y down.
"""

from __future__ import annotations

from anastylos.geometry import num, page_rect

REGISTRATION = "DICT_5X5_100"
CHARUCO_DICT = "DICT_6X6_250"

A4_W, A4_H = 297.0, 210.0
A3_W, A3_H = 420.0, 297.0

# Fixture board. The window is inset by the corner marker plus 4 mm of white.
BOARD_W, BOARD_H = 230.0, 150.0
BOARD_X, BOARD_Y = 33.5, 24.0
BOARD_INSET = 4.0
FIXTURE_MARKER = 21.0
FIXTURE_MODULE = 3.0


def build_config() -> dict:
    surface_a4 = _surface_a4()
    surface_a3 = _surface_a3()
    fixture_front = _fixture("front", 20)
    fixture_back = _fixture("back", 30)
    charuco_sheet, charuco = _charuco()
    sheets = [surface_a3, surface_a4, fixture_front, fixture_back, charuco_sheet]
    return {
        "name": "ANASTYLOS",
        "units": "mm",
        "coordinate_system": {
            "page": "origin at the top-left corner of the sheet, x right, y down",
            "frame": "origin at marker 0, 10, 20, 30 or the ChArUco board corner; x right, y down, z into the plane",
            "marker_corner_order": ["top_left", "top_right", "bottom_right", "bottom_left"],
            "front_and_back": "fixture_front and fixture_back are separate frames with no fixed transform",
        },
        "opencv_package": "opencv-contrib-python==4.14.0.94",
        "dictionaries": {
            REGISTRATION: {
                "opencv": "cv2.aruco.DICT_5X5_100",
                "marker_bits": 5,
                "border_bits": 1,
                "modules_per_side": 7,
                "role": "registration",
            },
            CHARUCO_DICT: {
                "opencv": "cv2.aruco.DICT_6X6_250",
                "marker_bits": 6,
                "border_bits": 1,
                "modules_per_side": 8,
                "role": "charuco",
            },
        },
        "id_ranges": [
            {"dictionary": REGISTRATION, "first": 0, "last": 9, "role": "surface_a4", "used": [0, 1, 2, 3, 4]},
            {"dictionary": REGISTRATION, "first": 10, "last": 19, "role": "surface_a3", "used": [10, 11, 12, 13, 14, 15, 16]},
            {"dictionary": REGISTRATION, "first": 20, "last": 29, "role": "fixture_front", "used": [20, 21, 22, 23, 24, 25]},
            {"dictionary": REGISTRATION, "first": 30, "last": 39, "role": "fixture_back", "used": [30, 31, 32, 33, 34, 35]},
            {"dictionary": REGISTRATION, "first": 40, "last": 99, "role": "reserved", "used": []},
            {"dictionary": CHARUCO_DICT, "first": 0, "last": 16, "role": "charuco", "used": list(range(17))},
        ],
        "detection": {"corner_refinement": "CORNER_REFINE_SUBPIX"},
        "frames": {sheet["frame"]: _frame(sheet) for sheet in sheets},
        "controller": {
            "dimensions_mm": None,
            "height_mm": None,
            "window_mm": [num(BOARD_W - 2 * (BOARD_INSET + FIXTURE_MARKER + BOARD_INSET)), num(BOARD_H - 2 * (BOARD_INSET + FIXTURE_MARKER + BOARD_INSET))],
            "board_mm": [num(BOARD_W), num(BOARD_H)],
        },
        "print": {
            "safe_margin_mm": 10,
            "scale": "100%",
            "check_line_mm": 100,
            "color": "black",
        },
        "measured_print_scale": {sheet["sheet_id"]: None for sheet in sheets},
        "charuco": charuco,
        "sheets": sheets,
    }


def _frame(sheet: dict) -> dict:
    if sheet["sheet_id"] == "charuco_a4":
        origin_id = None
        origin = sheet["board_origin_page_mm"]
    else:
        origin_marker = min(sheet["markers"], key=lambda marker: marker["id"])
        origin_id = origin_marker["id"]
        origin = origin_marker["top_left_page_mm"]
    return {
        "sheet_id": sheet["sheet_id"],
        "origin_marker_id": origin_id,
        "origin_on_page_mm": origin,
        "axes": {"x": "right", "y": "down", "z": "into_the_plane"},
    }


def _marker(marker_id: int, role: str, size: float, module: float, top_left: tuple[float, float], origin: tuple[float, float]) -> dict:
    x, y = top_left
    ox, oy = origin
    fx, fy = x - ox, y - oy
    corners = [[fx, fy], [fx + size, fy], [fx + size, fy + size], [fx, fy + size]]
    return {
        "id": marker_id,
        "dictionary": REGISTRATION,
        "role": role,
        "size_mm": num(size),
        "module_mm": num(module),
        "quiet_zone_mm": num(module),
        "top_left_page_mm": [num(x), num(y)],
        "corners_frame_mm": [[num(px), num(py)] for px, py in corners],
        "center_frame_mm": [num(fx + size / 2), num(fy + size / 2)],
    }


def _zone(kind: str, rect_page: tuple[float, float, float, float], origin: tuple[float, float], number: int | None = None) -> dict:
    x, y, w, h = rect_page
    ox, oy = origin
    zone = {"type": kind, "rect_frame_mm": [num(x - ox), num(y - oy), num(w), num(h)]}
    if number is not None:
        zone["number"] = number
    return zone


def _label(text: str, x: float, y: float, size: float, anchor: str = "middle", weight: str = "bold") -> dict:
    return {"text": text, "x": num(x), "y": num(y), "size_mm": size, "anchor": anchor, "weight": weight}


def _centered(text: str, rect: tuple[float, float, float, float], size: float) -> dict:
    x, y, w, h = rect
    return _label(text, x + w / 2, y + h / 2 + size * 0.32, size)


def _bar(name: str, orientation: str, x: float, y: float, tick_side: int) -> dict:
    return {
        "name": name,
        "orientation": orientation,
        "start_page_mm": [num(x), num(y)],
        "length_mm": 100,
        "thickness_mm": 0.8,
        "tick_side": tick_side,
        "major_every_mm": 10,
        "minor_every_mm": 5,
        "major_len_mm": 3,
        "minor_len_mm": 1.8,
        "tick_width_mm": 0.25,
    }


def _sheet(sheet_id: str, frame: str, stem: str, page: dict, markers: list, zones: list, bars: list, labels: list, **extra) -> dict:
    data = {
        "sheet_id": sheet_id,
        "frame": frame,
        "file_stem": stem,
        "page": page,
        "markers": markers,
        "zones": zones,
        "scale_bars": bars,
        "labels": labels,
    }
    data.update(extra)
    return data


def _a4_landscape() -> dict:
    return {"format": "A4", "orientation": "landscape", "width_mm": num(A4_W), "height_mm": num(A4_H)}


def _surface_a4() -> dict:
    origin = (12.0, 12.0)
    size, module = 35.0, 5.0
    markers = [
        _marker(0, "corner_tl", size, module, (12.0, 12.0), origin),
        _marker(1, "corner_tr", size, module, (250.0, 12.0), origin),
        _marker(2, "corner_br", size, module, (250.0, 163.0), origin),
        _marker(3, "corner_bl", size, module, (12.0, 163.0), origin),
        _marker(4, "mid_left", size, module, (12.0, 87.5), origin),
    ]
    work = (64.0, 54.0, 180.0, 102.0)
    zones = [_zone("work_area", work, origin)]
    labels = []
    for index in range(4):
        rect = (251.0, 53.0 + index * 26.0, 20.0, 24.0)
        zones.append(_zone("tray_cell", rect, origin, index + 1))
        labels.append(_centered(str(index + 1), rect, 8))
    bars = [_bar("h", "horizontal", 98.5, 183.0, 1), _bar("v", "vertical", 282.0, 55.0, -1)]
    return _sheet("surface_a4", "surface_a4", "anastylos_surface_a4", _a4_landscape(), markers, zones, bars, labels)


def _surface_a3() -> dict:
    origin = (12.0, 12.0)
    size, module = 42.0, 6.0
    markers = [
        _marker(10, "corner_tl", size, module, (12.0, 12.0), origin),
        _marker(11, "corner_tr", size, module, (366.0, 12.0), origin),
        _marker(12, "corner_br", size, module, (366.0, 243.0), origin),
        _marker(13, "corner_bl", size, module, (12.0, 243.0), origin),
        _marker(14, "mid_top", size, module, (189.0, 12.0), origin),
        _marker(15, "mid_bottom", size, module, (189.0, 243.0), origin),
        _marker(16, "mid_left", size, module, (12.0, 127.5), origin),
    ]
    work = (72.0, 64.0, 250.0, 172.0)
    zones = [_zone("work_area", work, origin)]
    labels = []
    number = 1
    for x in (330.0, 360.0):
        for row in range(6):
            rect = (x, 62.0 + row * 29.0, 26.0, 26.0)
            zones.append(_zone("tray_cell", rect, origin, number))
            labels.append(_centered(str(number), rect, 8))
            number += 1
    bars = [_bar("h", "horizontal", 78.0, 272.0, -1), _bar("v", "vertical", 400.0, 98.5, -1)]
    page = {"format": "A3", "orientation": "landscape", "width_mm": num(A3_W), "height_mm": num(A3_H)}
    return _sheet("surface_a3", "surface_a3", "anastylos_surface_a3", page, markers, zones, bars, labels)


def _fixture(side: str, first_id: int) -> dict:
    size, module = FIXTURE_MARKER, FIXTURE_MODULE
    ox = BOARD_X + BOARD_INSET
    oy = BOARD_Y + BOARD_INSET
    origin = (ox, oy)
    right = BOARD_X + BOARD_W - BOARD_INSET - size
    bottom = BOARD_Y + BOARD_H - BOARD_INSET - size
    mid_x = BOARD_X + BOARD_W / 2 - size / 2
    places = [
        ("tl", (ox, oy)),
        ("tr", (right, oy)),
        ("br", (right, bottom)),
        ("bl", (ox, bottom)),
        ("mid_top", (mid_x, oy)),
        ("mid_bottom", (mid_x, bottom)),
    ]
    markers = [
        _marker(first_id + index, f"{side}_{role}", size, module, xy, origin)
        for index, (role, xy) in enumerate(places)
    ]
    gap = BOARD_INSET + size + BOARD_INSET
    window = (BOARD_X + gap, BOARD_Y + gap, BOARD_W - 2 * gap, BOARD_H - 2 * gap)
    board = (BOARD_X, BOARD_Y, BOARD_W, BOARD_H)
    zones = [
        _zone("fixture_board_outline", board, origin),
        _zone("controller_window", window, origin),
    ]
    title = "FRONT" if side == "front" else "BACK"
    labels = [_label(title, A4_W / 2, 17.0, 6)]
    bars = [_bar("h", "horizontal", 98.5, 182.0, 1), _bar("v", "vertical", 21.0, 49.0, -1)]
    frame = f"fixture_{side}"
    return _sheet(frame, frame, f"anastylos_fixture_{side}", _a4_landscape(), markers, zones, bars, labels)


def _charuco() -> tuple[dict, dict]:
    squares_x, squares_y = 5, 7
    square, marker = 30.0, 24.0
    origin = (30.0, 22.0)
    spec = {
        "dictionary": CHARUCO_DICT,
        "squares_x": squares_x,
        "squares_y": squares_y,
        "square_length_mm": num(square),
        "marker_length_mm": num(marker),
        "module_mm": num(marker / 8),
        "legacy_pattern": False,
        "marker_ids": list(range(17)),
        "board_size_mm": [num(squares_x * square), num(squares_y * square)],
        "origin_on_page_mm": [num(origin[0]), num(origin[1])],
        "top_left_square": "black",
    }
    bars = [_bar("h", "horizontal", 55.0, 243.0, 1), _bar("v", "vertical", 17.0, 77.0, 1)]
    page = {"format": "A4", "orientation": "portrait", "width_mm": num(A4_H), "height_mm": num(A4_W)}
    sheet = _sheet(
        "charuco_a4",
        "charuco_board",
        "anastylos_charuco_a4",
        page,
        [],
        [],
        bars,
        [],
        charuco=True,
        board_origin_page_mm=[num(origin[0]), num(origin[1])],
    )
    return sheet, spec


def dumps(cfg: dict) -> str:
    import json

    return json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"


def frame_page_rect(cfg: dict, sheet: dict, rect_frame_mm: list[float]) -> tuple[float, float, float, float]:
    origin = cfg["frames"][sheet["frame"]]["origin_on_page_mm"]
    return page_rect(rect_frame_mm, origin)
