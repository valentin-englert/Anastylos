"""Checks for the ANASTYLOS calibration sheets.

Run from the calibration directory:

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import math
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

import cv2
import numpy as np

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))

from anastylos.boards import charuco_board, charuco_mask, detector_parameters, dictionary, marker_mask
from anastylos.geometry import scale_bar_geometry, text_bbox
from anastylos.layout import build_config, dumps
from anastylos.render import SVG_NS, generate, render_svg

NS = f"{{{SVG_NS}}}"
DPI = 200
MM_PER_INCH = 25.4
PT_PER_MM = 72.0 / MM_PER_INCH

VISIBLE_TEXT = {"FRONT", "BACK", "0", "100", "100 mm", *{str(n) for n in range(1, 13)}}

MARKER_TOP_LEFT = {
    "surface_a4": {0: (12, 12), 1: (250, 12), 2: (250, 163), 3: (12, 163), 4: (12, 87.5)},
    "surface_a3": {
        10: (12, 12), 11: (366, 12), 12: (366, 243), 13: (12, 243),
        14: (189, 12), 15: (189, 243), 16: (12, 127.5),
    },
    "fixture_front": {20: (37.5, 28), 21: (238.5, 28), 22: (238.5, 149), 23: (37.5, 149), 24: (138, 28), 25: (138, 149)},
    "fixture_back": {30: (37.5, 28), 31: (238.5, 28), 32: (238.5, 149), 33: (37.5, 149), 34: (138, 28), 35: (138, 149)},
}
MARKER_SIZE = {"surface_a4": 35, "surface_a3": 42, "fixture_front": 21, "fixture_back": 21}


def _tag(element: ET.Element) -> str:
    return element.tag.split("}")[-1]


def _overlaps(a, b) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def _stroke_bands(x, y, w, h, stroke: float):
    half = stroke / 2
    return [
        (x - half, y - half, w + stroke, stroke),
        (x - half, y + h - half, w + stroke, stroke),
        (x - half, y - half, stroke, h + stroke),
        (x + w - half, y - half, stroke, h + stroke),
    ]


def _rect(element: ET.Element):
    return tuple(float(element.get(key)) for key in ("x", "y", "width", "height"))


def _owner(element: ET.Element) -> str:
    node = element
    while node is not None:
        if node.get("data-id") is not None:
            return f"marker-{node.get('data-id')}"
        if node.get("id") == "charuco-board":
            return "charuco"
        if node.get("data-scale") is not None:
            return f"scale-{node.get('data-scale')}"
        node = node.getparent() if hasattr(node, "getparent") else None
    return ""


def _walk_owner(element: ET.Element, owner: str, sink: list):
    current = owner
    if element.get("data-id") is not None:
        current = f"marker-{element.get('data-id')}"
    elif element.get("id") == "charuco-board":
        current = "charuco"
    elif element.get("data-scale") is not None:
        current = f"scale-{element.get('data-scale')}"
    if _tag(element) == "rect" and element.get("id") != "page":
        box = _rect(element)
        if element.get("data-zone"):
            for band in _stroke_bands(*box, float(element.get("stroke-width"))):
                sink.append((f"zone-{element.get('id')}", band))
        else:
            sink.append((current, box))
    if _tag(element) == "text":
        label = {
            "text": element.text or "",
            "x": float(element.get("x")),
            "y": float(element.get("y")),
            "size_mm": float(element.get("font-size")),
            "anchor": element.get("text-anchor") or "start",
        }
        sink.append((f"text:{label['text']}:{label['x']:.2f}:{label['y']:.2f}", text_bbox(label)))
    for child in list(element):
        _walk_owner(child, current, sink)


def _ink(root: ET.Element):
    sink = []
    _walk_owner(root, "", sink)
    return sink


def _pdf_mediabox_mm(path: Path) -> tuple[float, float]:
    data = path.read_bytes()
    blobs = [data]
    for stream in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        try:
            blobs.append(zlib.decompress(stream.group(1)))
        except zlib.error:
            continue
    match = re.search(rb"/MediaBox\s*\[\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s*\]", b"\n".join(blobs))
    if match is None:
        raise AssertionError(f"no MediaBox in {path.name}")
    x0, y0, x1, y1 = (float(v) for v in match.groups())
    return (x1 - x0) / PT_PER_MM, (y1 - y0) / PT_PER_MM


def _render_svg(svg: str, dpi: int) -> np.ndarray:
    import cairosvg

    png = cairosvg.svg2png(bytestring=svg.encode("utf-8"), dpi=dpi, background_color="white")
    image = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise AssertionError("cairosvg png did not decode")
    return image


def _render_pdf(path: Path, dpi: int, directory: Path) -> np.ndarray:
    stem = directory / path.stem
    subprocess.run(
        ["pdftoppm", "-r", str(dpi), "-gray", "-png", "-singlefile", str(path), str(stem)],
        check=True,
        capture_output=True,
    )
    image = cv2.imread(str(stem) + ".png", cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise AssertionError(f"pdftoppm produced no image for {path.name}")
    return image


def _measure_bar(image: np.ndarray, bar, orientation: str, dpi: int) -> float:
    scale = dpi / MM_PER_INCH
    x, y, w, h = bar
    if orientation == "horizontal":
        row = int(round((y + h / 2) * scale))
        c0 = int(math.floor((x - 1.5) * scale))
        c1 = int(math.ceil((x + w + 1.5) * scale))
        line = image[row, c0:c1].astype(np.float64)
    else:
        col = int(round((x + w / 2) * scale))
        r0 = int(math.floor((y - 1.5) * scale))
        r1 = int(math.ceil((y + h + 1.5) * scale))
        line = image[r0:r1, col].astype(np.float64)
    return float((255.0 - line).clip(0, 255).sum() / 255.0 / scale)


def _mask_from_rects(group: ET.Element, x0: float, y0: float, unit: float, shape: tuple[int, int]) -> np.ndarray:
    mask = np.zeros(shape, np.uint8)
    for element in group.iter(f"{NS}rect"):
        x, y, w, _h = _rect(element)
        col = int(round((x - x0) / unit))
        row = int(round((y - y0) / unit))
        count = int(round(w / unit))
        mask[row, col:col + count] = 1
    return mask


class CalibrationKitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = build_config()
        cls.by_id = {sheet["sheet_id"]: sheet for sheet in cls.cfg["sheets"]}
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name)
        generate(cls.cfg, cls.out, quiet=True)
        cls.svg = {sheet["sheet_id"]: (cls.out / "print" / f"{sheet['file_stem']}.svg").read_text(encoding="utf-8") for sheet in cls.cfg["sheets"]}
        cls.roots = {sid: ET.fromstring(text) for sid, text in cls.svg.items()}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_config_is_deterministic(self):
        self.assertEqual(dumps(build_config()), dumps(build_config()))
        self.assertEqual((self.out / "calibration-config.json").read_text(encoding="utf-8"), dumps(self.cfg))

    def test_locked_marker_ids_and_positions(self):
        for sheet_id, expected in MARKER_TOP_LEFT.items():
            sheet = self.by_id[sheet_id]
            found = {marker["id"]: tuple(marker["top_left_page_mm"]) for marker in sheet["markers"]}
            self.assertEqual(found, expected)
            self.assertTrue(all(marker["size_mm"] == MARKER_SIZE[sheet_id] for marker in sheet["markers"]))
            self.assertTrue(all(marker["dictionary"] == "DICT_5X5_100" for marker in sheet["markers"]))
        self.assertEqual([zone["number"] for zone in self.by_id["surface_a3"]["zones"] if zone["type"] == "tray_cell"], list(range(1, 13)))
        self.assertEqual([zone["number"] for zone in self.by_id["surface_a4"]["zones"] if zone["type"] == "tray_cell"], list(range(1, 5)))

    def test_frames_origins_and_separate_front_back(self):
        for sheet_id, marker_id in (("surface_a4", 0), ("surface_a3", 10), ("fixture_front", 20), ("fixture_back", 30)):
            sheet = self.by_id[sheet_id]
            frame = self.cfg["frames"][sheet["frame"]]
            marker = next(item for item in sheet["markers"] if item["id"] == marker_id)
            self.assertEqual(frame["origin_on_page_mm"], marker["top_left_page_mm"])
            self.assertEqual(marker["corners_frame_mm"][0], [0, 0])
            origin = frame["origin_on_page_mm"]
            for item in sheet["markers"]:
                x, y = item["top_left_page_mm"]
                size = item["size_mm"]
                expected = [[x - origin[0], y - origin[1]], [x - origin[0] + size, y - origin[1]],
                            [x - origin[0] + size, y - origin[1] + size], [x - origin[0], y - origin[1] + size]]
                self.assertEqual(item["corners_frame_mm"], [[int(v) if float(v).is_integer() else v for v in point] for point in expected])
        front = {m["id"] for m in self.by_id["fixture_front"]["markers"]}
        back = {m["id"] for m in self.by_id["fixture_back"]["markers"]}
        a3 = {m["id"] for m in self.by_id["surface_a3"]["markers"]}
        a4 = {m["id"] for m in self.by_id["surface_a4"]["markers"]}
        self.assertEqual(front, set(range(20, 26)))
        self.assertEqual(back, set(range(30, 36)))
        self.assertFalse(front & back)
        self.assertFalse(a3 & a4)
        self.assertFalse(front & a3 or front & a4 or back & a3 or back & a4)
        self.assertEqual(len({*front, *back, *a3, *a4}), 24)

    def test_id_ranges_and_reserved_block(self):
        used = {}
        for sheet in self.cfg["sheets"]:
            for marker in sheet["markers"]:
                key = (marker["dictionary"], marker["id"])
                self.assertNotIn(key, used)
                used[key] = sheet["sheet_id"]
        for marker_id in self.cfg["charuco"]["marker_ids"]:
            key = (self.cfg["charuco"]["dictionary"], marker_id)
            self.assertNotIn(key, used)
            used[key] = "charuco_a4"
        for block in self.cfg["id_ranges"]:
            for marker_id in block["used"]:
                self.assertTrue(block["first"] <= marker_id <= block["last"])
                self.assertEqual(used[(block["dictionary"], marker_id)] is not None, True)
            if block["role"] == "reserved":
                self.assertEqual(block["used"], [])
                self.assertEqual((block["first"], block["last"]), (40, 99))
        declared = {(block["dictionary"], marker_id) for block in self.cfg["id_ranges"] for marker_id in block["used"]}
        self.assertEqual(declared, set(used))
        self.assertTrue(all(marker_id < 40 for marker_id in (key[1] for key in used if key[0] == "DICT_5X5_100")))

    def test_controller_and_fixture_geometry(self):
        self.assertIsNone(self.cfg["controller"]["dimensions_mm"])
        self.assertIsNone(self.cfg["controller"]["height_mm"])
        self.assertEqual(self.cfg["controller"]["board_mm"], [230, 150])
        self.assertEqual(self.cfg["controller"]["window_mm"], [172, 92])
        for side in ("fixture_front", "fixture_back"):
            zones = {zone["type"]: zone["rect_frame_mm"] for zone in self.by_id[side]["zones"]}
            self.assertEqual(zones["fixture_board_outline"], [-4, -4, 230, 150])
            self.assertEqual(zones["controller_window"], [25, 25, 172, 92])
        a3_work = next(z["rect_frame_mm"] for z in self.by_id["surface_a3"]["zones"] if z["type"] == "work_area")
        a4_work = next(z["rect_frame_mm"] for z in self.by_id["surface_a4"]["zones"] if z["type"] == "work_area")
        self.assertGreaterEqual(a3_work[2], 230)
        self.assertGreaterEqual(a3_work[3], 150)
        self.assertLess(a4_work[2], 230)
        self.assertLess(a4_work[3], 150)
        self.assertEqual(self.cfg["measured_print_scale"], {sid: None for sid in self.by_id})

    def test_charuco_spec_and_opencv_board(self):
        spec = self.cfg["charuco"]
        self.assertEqual(spec["dictionary"], "DICT_6X6_250")
        self.assertEqual((spec["squares_x"], spec["squares_y"]), (5, 7))
        self.assertEqual(spec["square_length_mm"], 30)
        self.assertEqual(spec["marker_length_mm"], 24)
        self.assertEqual(spec["module_mm"], 3)
        self.assertIs(spec["legacy_pattern"], False)
        self.assertEqual(spec["marker_ids"], list(range(17)))
        self.assertEqual(spec["board_size_mm"], [150, 210])
        self.assertEqual(spec["origin_on_page_mm"], [30, 22])
        self.assertEqual(spec["top_left_square"], "black")
        board = charuco_board(self.cfg)
        self.assertFalse(board.getLegacyPattern())
        corners = board.getChessboardCorners().reshape(-1, 3)
        self.assertEqual(len(corners), 24)
        for index, point in enumerate(corners):
            self.assertAlmostEqual(point[0], ((index % 4) + 1) * 30, places=4)
            self.assertAlmostEqual(point[1], ((index // 4) + 1) * 30, places=4)
            self.assertEqual(point[2], 0)
        mask, unit = charuco_mask(self.cfg)
        self.assertEqual(unit, 3)
        self.assertTrue(mask[:10, :10].all())

    def test_page_sizes_and_svg_matches_opencv(self):
        expected_pages = {
            "surface_a3": (420, 297),
            "surface_a4": (297, 210),
            "fixture_front": (297, 210),
            "fixture_back": (297, 210),
            "charuco_a4": (210, 297),
        }
        for sheet in self.cfg["sheets"]:
            root = self.roots[sheet["sheet_id"]]
            width, height = expected_pages[sheet["sheet_id"]]
            self.assertEqual(root.get("width"), f"{width}mm")
            self.assertEqual(root.get("height"), f"{height}mm")
            self.assertEqual([float(v) for v in root.get("viewBox").split()], [0, 0, width, height])
            for marker in sheet["markers"]:
                group = root.find(f".//{NS}g[@id='marker-{marker['id']}']")
                self.assertIsNotNone(group)
                modules = int(self.cfg["dictionaries"][marker["dictionary"]]["modules_per_side"])
                unit = float(marker["module_mm"])
                got = _mask_from_rects(group, marker["top_left_page_mm"][0], marker["top_left_page_mm"][1], unit, (modules, modules))
                fresh = marker_mask(marker["dictionary"], marker["id"], modules)
                high = cv2.aruco.generateImageMarker(dictionary(marker["dictionary"]), marker["id"], modules * 8, borderBits=1)
                sampled = (high[4::8, 4::8] == 0).astype(np.uint8)
                self.assertEqual(int((got != fresh).sum()), 0)
                self.assertEqual(int((got != sampled).sum()), 0)
            if sheet.get("charuco"):
                group = root.find(f".//{NS}g[@id='charuco-board']")
                rows, cols = int(group.get("data-rows")), int(group.get("data-cols"))
                unit = float(group.get("data-module"))
                got = _mask_from_rects(group, float(group.get("data-x")), float(group.get("data-y")), unit, (rows, cols))
                fresh, _unit = charuco_mask(self.cfg)
                board = charuco_board(self.cfg)
                high = board.generateImage((cols * 8, rows * 8), marginSize=0, borderBits=1)
                sampled = (high[4::8, 4::8] == 0).astype(np.uint8)
                self.assertTrue(np.array_equal(got, fresh))
                self.assertTrue(np.array_equal(got, sampled))

    def test_pdf_page_sizes(self):
        for sheet in self.cfg["sheets"]:
            pdf = self.out / "print" / f"{sheet['file_stem']}.pdf"
            self.assertTrue(pdf.exists(), pdf.name)
            width, height = _pdf_mediabox_mm(pdf)
            self.assertAlmostEqual(width, sheet["page"]["width_mm"], delta=0.05)
            self.assertAlmostEqual(height, sheet["page"]["height_mm"], delta=0.05)
            if shutil.which("pdfinfo"):
                info = subprocess.run(["pdfinfo", str(pdf)], check=True, capture_output=True, text=True).stdout
                match = re.search(r"Page size:\s+([\d.]+) x ([\d.]+) pts", info)
                pages = re.search(r"Pages:\s+(\d+)", info)
                self.assertIsNotNone(match)
                self.assertEqual(int(pages.group(1)), 1)
                self.assertAlmostEqual(float(match.group(1)) / PT_PER_MM, sheet["page"]["width_mm"], delta=0.05)
                self.assertAlmostEqual(float(match.group(2)) / PT_PER_MM, sheet["page"]["height_mm"], delta=0.05)

    def test_check_lines_are_100_mm(self):
        with tempfile.TemporaryDirectory() as directory:
            raster_dir = Path(directory)
            for sheet in self.cfg["sheets"]:
                root = self.roots[sheet["sheet_id"]]
                orientations = set()
                for spec in sheet["scale_bars"]:
                    bar, _ticks, _labels = scale_bar_geometry(spec)
                    group = root.find(f".//{NS}g[@id='scale-{spec['name']}']")
                    drawn = _rect(group.find(f"{NS}rect[@class='bar']"))
                    self.assertEqual(drawn, tuple(float(v) for v in bar))
                    along = drawn[2] if spec["orientation"] == "horizontal" else drawn[3]
                    self.assertEqual(along, 100)
                    orientations.add(spec["orientation"])
                self.assertEqual(orientations, {"horizontal", "vertical"})
                svg_image = _render_svg(self.svg[sheet["sheet_id"]], DPI)
                pdf_image = _render_pdf(self.out / "print" / f"{sheet['file_stem']}.pdf", DPI, raster_dir)
                for spec in sheet["scale_bars"]:
                    bar, _ticks, _labels = scale_bar_geometry(spec)
                    for label, image in (("svg", svg_image), ("pdf", pdf_image)):
                        measured = _measure_bar(image, bar, spec["orientation"], DPI)
                        self.assertAlmostEqual(
                            measured, 100, delta=0.2,
                            msg=f"{sheet['sheet_id']} {spec['name']} {label} measured {measured:.3f} mm",
                        )

    def test_markers_detected_from_fresh_renders(self):
        params = detector_parameters(self.cfg)
        scale = DPI / MM_PER_INCH
        with tempfile.TemporaryDirectory() as directory:
            raster_dir = Path(directory)
            for sheet in self.cfg["sheets"]:
                images = {
                    "svg": _render_svg(self.svg[sheet["sheet_id"]], DPI),
                    "pdf": _render_pdf(self.out / "print" / f"{sheet['file_stem']}.pdf", DPI, raster_dir),
                }
                expected = {marker["id"]: marker for marker in sheet["markers"]}
                for source, image in images.items():
                    found = {}
                    for dict_name in self.cfg["dictionaries"]:
                        corners, ids, _rejected = cv2.aruco.ArucoDetector(dictionary(dict_name), params).detectMarkers(image)
                        found[dict_name] = [] if ids is None else sorted(int(v) for v in ids.ravel())
                        if dict_name == "DICT_5X5_100" and expected:
                            self._assert_registration_geometry(sheet, expected, corners, ids, scale, source)
                    if sheet.get("charuco"):
                        self.assertEqual(found["DICT_5X5_100"], [])
                        self.assertEqual(found["DICT_6X6_250"], list(range(17)))
                        self._assert_charuco_geometry(image, scale, source)
                    else:
                        self.assertEqual(found["DICT_5X5_100"], sorted(expected))
                        self.assertEqual(found["DICT_6X6_250"], [])

    def _assert_registration_geometry(self, sheet, expected, corners, ids, scale, source):
        self.assertIsNotNone(ids)
        frames = set()
        id_to_frame = {marker["id"]: item["frame"] for item in self.cfg["sheets"] for marker in item["markers"]}
        for quad, marker_id in zip(corners, ids.ravel().tolist()):
            marker = expected[int(marker_id)]
            quad = quad.reshape(4, 2)
            center_mm = np.array(marker["top_left_page_mm"], dtype=float) + marker["size_mm"] / 2
            center_err = np.linalg.norm(quad.mean(axis=0) / scale - center_mm)
            sides = [np.linalg.norm(quad[(k + 1) % 4] - quad[k]) / scale for k in range(4)]
            self.assertLess(center_err, 0.25, f"{sheet['sheet_id']} id {marker_id} {source} center {center_err:.3f} mm")
            self.assertLess(abs(float(np.mean(sides)) - marker["size_mm"]), 0.2)
            frames.add(id_to_frame[int(marker_id)])
        self.assertEqual(frames, {sheet["frame"]})

    def _assert_charuco_geometry(self, image, scale, source):
        spec = self.cfg["charuco"]
        board = charuco_board(self.cfg)
        detector = cv2.aruco.CharucoDetector(board)
        detector.setDetectorParameters(detector_parameters(self.cfg))
        corners, ids, _mk, _mk_ids = detector.detectBoard(image)
        self.assertIsNotNone(ids)
        self.assertEqual(len(ids), (spec["squares_x"] - 1) * (spec["squares_y"] - 1))
        obj = board.getChessboardCorners()[ids.ravel()][:, :2]
        detected = corners.reshape(-1, 2)
        expected = (obj + np.array(spec["origin_on_page_mm"])) * scale
        error_px = min(
            np.linalg.norm(detected - expected, axis=1).max(),
            np.linalg.norm(detected - (expected - 0.5), axis=1).max(),
        )
        self.assertLess(error_px / scale, 0.25, f"charuco {source} corner error {error_px / scale:.3f} mm")
        points = {int(i): p for i, p in zip(ids.ravel(), detected)}
        nx = spec["squares_x"] - 1
        distances = []
        for index, point in points.items():
            if index % nx < nx - 1 and index + 1 in points:
                distances.append(np.linalg.norm(points[index + 1] - point))
            if index + nx in points:
                distances.append(np.linalg.norm(points[index + nx] - point))
        self.assertAlmostEqual(float(np.mean(distances)) / scale, spec["square_length_mm"], delta=0.15)

    def test_margins_quiet_zones_and_visible_text(self):
        margin = self.cfg["print"]["safe_margin_mm"]
        for sheet in self.cfg["sheets"]:
            root = self.roots[sheet["sheet_id"]]
            width, height = sheet["page"]["width_mm"], sheet["page"]["height_mm"]
            ink = _ink(root)
            texts = [element.text for element in root.iter(f"{NS}text")]
            self.assertTrue(texts or sheet["sheet_id"] == "charuco_a4")
            for text in texts:
                self.assertIn(text, VISIBLE_TEXT)
            for _owner, box in ink:
                self.assertGreaterEqual(box[0], margin - 0.05, f"{sheet['sheet_id']} {_owner} left {box}")
                self.assertGreaterEqual(box[1], margin - 0.05, f"{sheet['sheet_id']} {_owner} top {box}")
                self.assertLessEqual(box[0] + box[2], width - margin + 0.05, f"{sheet['sheet_id']} {_owner} right {box}")
                self.assertLessEqual(box[1] + box[3], height - margin + 0.05, f"{sheet['sheet_id']} {_owner} bottom {box}")
            protected = []
            for marker in sheet["markers"]:
                x, y = marker["top_left_page_mm"]
                protected.append((f"marker-{marker['id']}", (x, y, marker["size_mm"], marker["size_mm"]), marker["quiet_zone_mm"]))
            if sheet.get("charuco"):
                x, y = self.cfg["charuco"]["origin_on_page_mm"]
                w, h = self.cfg["charuco"]["board_size_mm"]
                protected.append(("charuco", (x, y, w, h), self.cfg["charuco"]["module_mm"]))
            for owner, box, quiet in protected:
                grown = (box[0] - quiet, box[1] - quiet, box[2] + 2 * quiet, box[3] + 2 * quiet)
                for other, other_box in ink:
                    if other == owner:
                        continue
                    self.assertFalse(_overlaps(grown, other_box), f"{sheet['sheet_id']} {owner} quiet zone hits {other}")

    def test_committed_files_match_generator(self):
        config_path = KIT / "calibration-config.json"
        self.assertEqual(config_path.read_text(encoding="utf-8"), dumps(self.cfg))
        for sheet in self.cfg["sheets"]:
            committed = (KIT / "print" / f"{sheet['file_stem']}.svg").read_text(encoding="utf-8")
            self.assertEqual(committed, render_svg(self.cfg, sheet))
            self.assertEqual(committed, self.svg[sheet["sheet_id"]])
            preview = KIT / "preview" / f"{sheet['file_stem']}_preview.png"
            pdf = KIT / "print" / f"{sheet['file_stem']}.pdf"
            self.assertTrue(preview.exists())
            self.assertTrue(pdf.exists())
            fresh_w, fresh_h = _pdf_mediabox_mm(self.out / "print" / f"{sheet['file_stem']}.pdf")
            committed_w, committed_h = _pdf_mediabox_mm(pdf)
            self.assertAlmostEqual(fresh_w, committed_w, delta=0.02)
            self.assertAlmostEqual(fresh_h, committed_h, delta=0.02)

    def test_blank_page_detects_nothing(self):
        params = detector_parameters(self.cfg)
        blank = np.full((600, 800), 255, np.uint8)
        for dict_name in self.cfg["dictionaries"]:
            _corners, ids, _rejected = cv2.aruco.ArucoDetector(dictionary(dict_name), params).detectMarkers(blank)
            self.assertIsNone(ids)
        board = charuco_board(self.cfg)
        _corners, ids, _mk, _mk_ids = cv2.aruco.CharucoDetector(board).detectBoard(blank)
        self.assertTrue(ids is None or len(ids) == 0)


if __name__ == "__main__":
    unittest.main()
