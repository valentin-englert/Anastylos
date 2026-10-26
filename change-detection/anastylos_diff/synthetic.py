"""Synthetic before/after pairs. Planar scenes only — not a camera test."""

from __future__ import annotations

import csv
from pathlib import Path

import cv2
import numpy as np

from anastylos_diff.engine import Kit, make_detector, write_image

PPM = 6.0


def render_sheet(kit: dict, sheet: dict, ppm: float = PPM) -> np.ndarray:
    width = int(round(sheet["page"]["width_mm"] * ppm))
    height = int(round(sheet["page"]["height_mm"] * ppm))
    image = np.full((height, width), 255, np.uint8)
    origin = kit["frames"][sheet["frame"]]["origin_on_page_mm"]
    for zone in sheet.get("zones", []):
        x, y, w, h = zone["rect_frame_mm"]
        p0 = (int(round((x + origin[0]) * ppm)), int(round((y + origin[1]) * ppm)))
        p1 = (int(round((x + origin[0] + w) * ppm)), int(round((y + origin[1] + h) * ppm)))
        cv2.rectangle(image, p0, p1, 90, 2)
    for bar in sheet.get("scale_bars", []):
        x, y = bar["start_page_mm"]
        length, thick = bar["length_mm"], bar["thickness_mm"]
        w, h = (length, thick) if bar["orientation"] == "horizontal" else (thick, length)
        cv2.rectangle(
            image,
            (int(round(x * ppm)), int(round(y * ppm))),
            (int(round((x + w) * ppm)), int(round((y + h) * ppm))),
            0,
            -1,
        )
    for marker in sheet.get("markers", []):
        modules = int(kit["dictionaries"][marker["dictionary"]]["modules_per_side"])
        side = int(round(float(marker["size_mm"]) * ppm))
        if side % modules != 0:
            raise RuntimeError(f"ppm {ppm} does not land marker {marker['id']} on whole modules")
        bitmap = cv2.aruco.generateImageMarker(
            cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, marker["dictionary"])),
            int(marker["id"]),
            modules,
            borderBits=1,
        )
        bitmap = cv2.resize(bitmap, (side, side), interpolation=cv2.INTER_NEAREST)
        x = int(round(marker["top_left_page_mm"][0] * ppm))
        y = int(round(marker["top_left_page_mm"][1] * ppm))
        image[y:y + side, x:x + side] = bitmap
    return image


def _window(view: dict):
    return next(zone["rect_frame_mm"] for zone in view["zones"] if zone["type"] == "controller_window")


def screw_positions(view: dict) -> list[tuple[float, float]]:
    x, y, w, h = _window(view)
    cx, cy = x + w / 2, y + h / 2
    return [
        (cx - 55, cy - 16),
        (cx - 18, cy - 18),
        (cx + 18, cy - 18),
        (cx + 55, cy - 16),
        (cx - 36, cy + 16),
        (cx + 36, cy + 16),
    ]


def screw_bbox(view: dict, index: int, radius: float = 2.4) -> list[float]:
    sx, sy = screw_positions(view)[index]
    return [round(sx - radius, 2), round(sy - radius, 2), round(2 * radius, 2), round(2 * radius, 2)]


def _page_px(origin, x_mm: float, y_mm: float, ppm: float) -> tuple[int, int]:
    return int(round((x_mm + origin[0]) * ppm)), int(round((y_mm + origin[1]) * ppm))


def paint_controller(page: np.ndarray, view: dict, removed: tuple[int, ...] = (), extras: tuple = ()) -> np.ndarray:
    image = page.copy()
    origin = view["origin_mm"]
    x, y, w, h = _window(view)
    p0 = _page_px(origin, x, y, PPM)
    p1 = _page_px(origin, x + w, y + h, PPM)
    cv2.rectangle(image, p0, p1, 156, -1)
    patch = image[p0[1]:p1[1], p0[0]:p1[0]].astype(np.float32)
    noise = np.random.default_rng(5).normal(0, 1, patch.shape).astype(np.float32)
    noise = cv2.GaussianBlur(noise, (0, 0), 7) * 5
    image[p0[1]:p1[1], p0[0]:p1[0]] = np.clip(patch + noise, 0, 255).astype(np.uint8)
    for index, (sx, sy) in enumerate(screw_positions(view)):
        if index in removed:
            continue
        center = _page_px(origin, sx, sy, PPM)
        cv2.circle(image, center, int(round(2.4 * PPM)), 40, -1, cv2.LINE_AA)
        cv2.line(image, _page_px(origin, sx - 1.2, sy, PPM), _page_px(origin, sx + 1.2, sy, PPM), 170, 2, cv2.LINE_AA)
    for rect in extras:
        ex, ey, ew, eh = rect
        cv2.rectangle(image, _page_px(origin, ex, ey, PPM), _page_px(origin, ex + ew, ey + eh, PPM), 55, -1)
    return image


def _board_crop(page: np.ndarray, view: dict) -> np.ndarray:
    outline = view["board_mm"]
    origin = view["origin_mm"]
    x0, y0 = _page_px(origin, outline[0], outline[1], PPM)
    x1, y1 = _page_px(origin, outline[0] + outline[2], outline[1] + outline[3], PPM)
    return page[y0:y1, x0:x1]


def compose_fixture(surface: np.ndarray, fixture_page: np.ndarray, surface_view: dict, fixture_view: dict, pose: tuple[float, float, float]) -> np.ndarray:
    board = _board_crop(fixture_page, fixture_view)
    work = next(zone["rect_frame_mm"] for zone in surface_view["zones"] if zone["type"] == "work_area")
    origin = surface_view["origin_mm"]
    cx = (work[0] + work[2] / 2 + origin[0] + pose[0]) * PPM
    cy = (work[1] + work[3] / 2 + origin[1] + pose[1]) * PPM
    matrix = cv2.getRotationMatrix2D((board.shape[1] / 2, board.shape[0] / 2), pose[2], 1.0)
    matrix[0, 2] += cx - board.shape[1] / 2
    matrix[1, 2] += cy - board.shape[0] / 2
    warped = cv2.warpAffine(board, matrix, (surface.shape[1], surface.shape[0]), flags=cv2.INTER_LINEAR, borderValue=255)
    mask = cv2.warpAffine(np.full(board.shape, 255, np.uint8), matrix, (surface.shape[1], surface.shape[0]), flags=cv2.INTER_LINEAR)
    alpha = mask.astype(np.float32) / 255.0
    return np.clip(surface.astype(np.float32) * (1 - alpha) + warped.astype(np.float32) * alpha, 0, 255).astype(np.uint8)


def project(scene: np.ndarray, quad: np.ndarray, rng: np.random.Generator, size: tuple[int, int], light=(1.0, 0.0), gradient=0.0, vignette=0.0, noise=2.0, blur=0.7, jitter_px=0.0) -> np.ndarray:
    height, width = scene.shape
    src = np.float32([[0, 0], [width, 0], [width, height], [0, height]])
    dst = quad.astype(np.float32)
    if jitter_px:
        dst = dst + rng.uniform(-jitter_px, jitter_px, dst.shape).astype(np.float32)
    homography = cv2.getPerspectiveTransform(src, dst)
    image = cv2.warpPerspective(scene, homography, size, flags=cv2.INTER_LINEAR, borderValue=100).astype(np.float32)
    gain, offset = light
    image = image * gain + offset
    yy, xx = np.mgrid[0:size[1], 0:size[0]].astype(np.float32)
    if gradient:
        image += gradient * (xx / size[0] - 0.5) * 2
    if vignette:
        radius = ((xx - size[0] / 2) ** 2 + (yy - size[1] / 2) ** 2) / ((size[0] / 2) ** 2 + (size[1] / 2) ** 2)
        image *= 1 - vignette * radius
    if blur:
        image = cv2.GaussianBlur(image, (0, 0), blur)
    image += rng.normal(0, noise, image.shape)
    gray = np.clip(image, 0, 255).astype(np.uint8)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def make_dataset(folder: Path, kit_doc: dict, model: Kit, harness: dict, seed: int = 20261026) -> list[dict]:
    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    sheets = {sheet["sheet_id"]: sheet for sheet in kit_doc["sheets"]}
    pages = {name: render_sheet(kit_doc, sheet) for name, sheet in sheets.items() if name != "charuco_a4"}
    size = (1700, 1200)
    detector = make_detector(harness)
    rows = []
    lights = [
        ((1.0, 0.0), 0.0, 0.0),
        ((0.86, 6.0), 0.0, 0.0),
        ((1.12, -8.0), 0.0, 0.0),
        ((1.0, 4.0), 14.0, 0.0),
        ((0.94, 5.0), -10.0, 0.12),
        ((1.06, -6.0), 8.0, 0.18),
    ]

    def quad():
        base = np.float32([[100, 170], [1580, 130], [1630, 1060], [80, 1020]])
        return base + rng.uniform(-18, 18, base.shape).astype(np.float32)

    def pose():
        return (float(rng.uniform(-4, 4)), float(rng.uniform(-3, 3)), float(rng.uniform(-2, 2)))

    def save(pair_id, before, after, expected, boxes=(), view="", assert_=1, notes=""):
        write_image(folder / f"{pair_id}_before.jpg", before, 93)
        write_image(folder / f"{pair_id}_after.jpg", after, 93)
        rows.append({
            "pair_id": pair_id,
            "expected": expected,
            "expected_bbox_mm": ";".join(",".join(f"{value:g}" for value in box) for box in boxes),
            "view": view,
            "assert": assert_,
            "notes": notes,
        })

    def fixture_scene(view_name, pose_xy, removed=(), extras=()):
        painted = paint_controller(pages[model.views[view_name]["sheet"]["sheet_id"]], model.views[view_name], removed, extras)
        return compose_fixture(pages["surface_a3"], painted, model.views["surface_a3"], model.views[view_name], pose_xy)

    for index in range(4):
        view_name = "fixture_front" if index % 2 == 0 else "fixture_back"
        scene = fixture_scene(view_name, pose())
        camera = quad()
        light = lights[index % len(lights)]
        before = project(scene, camera, rng, size, noise=2.0, blur=0.7)
        after = project(scene, camera, rng, size, jitter_px=1.0, light=light[0], gradient=light[1], vignette=light[2], noise=2.0, blur=0.7)
        save(f"nochange_{index + 1:02d}", before, after, "none", view=view_name, notes="SYNTHETIC unchanged fixture, lighting varies")

    surface = pages["surface_a3"]
    camera = quad()
    before = project(surface, camera, rng, size, noise=1.5, blur=0.6)
    after = project(surface, camera, rng, size, jitter_px=0.6, light=(0.9, 8), gradient=12, noise=1.5, blur=0.6)
    save("nochange_surface", before, after, "none", view="surface_a3", notes="SYNTHETIC unchanged A3 surface")

    for index in range(3):
        view_name = "fixture_back" if index == 1 else "fixture_front"
        placed = pose()
        camera = quad()
        light = lights[(index + 2) % len(lights)]
        before = project(fixture_scene(view_name, placed), camera, rng, size)
        after = project(fixture_scene(view_name, placed, removed=(index,)), camera, rng, size, jitter_px=1.0, light=light[0], gradient=light[1], vignette=light[2])
        save(f"single_{index + 1:02d}", before, after, "single", [screw_bbox(model.views[view_name], index)], view=view_name, notes=f"SYNTHETIC screw {index} removed")

    combos = [((0, 3), ()), ((1,), ((0, 0, 8, 8),))]
    for index, (removed, _parts) in enumerate(combos):
        view_name = "fixture_front" if index == 0 else "fixture_back"
        win = _window(model.views[view_name])
        extras = tuple((win[0] + 8, win[1] + win[3] - 18, 8, 8) for _ in _parts)
        placed = pose()
        camera = quad()
        before = project(fixture_scene(view_name, placed), camera, rng, size)
        after = project(fixture_scene(view_name, placed, removed=removed, extras=extras), camera, rng, size, jitter_px=1.0, light=(1.04, -4))
        boxes = [screw_bbox(model.views[view_name], i) for i in removed] + [list(rect) for rect in extras]
        save(f"multiple_{index + 1:02d}", before, after, "multiple", boxes, view=view_name, notes=f"SYNTHETIC screws {removed} extras {len(extras)}")

    tray = next(zone["rect_frame_mm"] for zone in model.views["surface_a3"]["zones"] if zone["type"] == "tray_cell" and zone["number"] == 1)
    square = [tray[0] + 7, tray[1] + 7, 10, 10]
    origin = model.views["surface_a3"]["origin_mm"]
    changed = surface.copy()
    p0 = _page_px(origin, square[0], square[1], PPM)
    p1 = _page_px(origin, square[0] + square[2], square[1] + square[3], PPM)
    cv2.rectangle(changed, p0, p1, 30, -1)
    camera = quad()
    before = project(surface, camera, rng, size, noise=1.5, blur=0.6)
    after = project(changed, camera, rng, size, jitter_px=0.6, light=(1.08, -5), noise=1.5, blur=0.6)
    save("surface_single", before, after, "single", [square], view="surface_a3", notes="SYNTHETIC object in tray 1")

    view_name = "fixture_front"
    placed = (0.0, 0.0, 1.0)
    camera = quad()
    scene = fixture_scene(view_name, placed)
    before = project(scene, camera, rng, size, noise=1.5, blur=0.6)
    after = project(scene, camera, rng, size, jitter_px=0.8, noise=1.5, blur=0.6)
    after = _cover(after, {20, 21, 22, 24}, detector)
    save("regfail_occluded", before, after, "invalid", notes="SYNTHETIC four fixture markers covered")

    blurred = cv2.GaussianBlur(project(scene, camera, rng, size, jitter_px=0.8), (0, 0), 9)
    save("regfail_defocus", before, blurred, "invalid", notes="SYNTHETIC heavy defocus")

    back_marker = cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_5X5_100), 30, 7, borderBits=1)
    side = int(round(21 * PPM))
    back_marker = cv2.resize(back_marker, (side, side), interpolation=cv2.INTER_NEAREST)
    stamped = scene.copy()
    stamped[int(200 * PPM):int(200 * PPM) + side, int(348 * PPM):int(348 * PPM) + side] = back_marker
    ambiguous = project(stamped, camera, rng, size, jitter_px=0.8, noise=1.5, blur=0.6)
    save("regfail_ambiguous", before, ambiguous, "invalid", notes="SYNTHETIC front board plus marker 30")

    camera = quad()
    front = project(fixture_scene("fixture_front", (1, 0, -1)), camera, rng, size, noise=1.5)
    back = project(fixture_scene("fixture_back", (1, 0, -1)), camera, rng, size, jitter_px=0.8, noise=1.5)
    save("viewmismatch", front, back, "view_mismatch", notes="SYNTHETIC front then back")

    camera = quad()
    front_scene = fixture_scene("fixture_front", (0, 0, 0.5))
    before = project(front_scene, camera, rng, size, noise=1.5)
    after = project(front_scene, camera, rng, size, jitter_px=0.8, noise=1.5)
    save("viewconfirm_fail", before, after, "invalid", view="fixture_back", notes="SYNTHETIC manifest says back, images are front")

    camera = quad()
    clean = fixture_scene("fixture_front", (0, 0, 0))
    before = project(clean, camera, rng, size, noise=1.2, blur=0.5)
    after = project(clean, camera, rng, size, jitter_px=0.8, noise=1.2, blur=0.5)
    yy, xx = np.mgrid[0:size[1], 0:size[0]]
    factor = (1 - 0.35 * np.exp(-((xx - 850) ** 2 + (yy - 600) ** 2) / (2 * 160 ** 2)))[..., None]
    shadow = np.clip(after.astype(np.float32) * factor, 0, 255).astype(np.uint8)
    save("stress_shadow", before, shadow, "none", view="fixture_front", assert_=0, notes="SYNTHETIC stress, not scored: local shadow")

    with (folder / "manifest.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["pair_id", "expected", "expected_bbox_mm", "view", "assert", "notes"])
        writer.writeheader()
        writer.writerows(rows)
    return rows


def _cover(image: np.ndarray, ids: set[int], detector) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    corners, found, _rejected = detector.detectMarkers(gray)
    if found is None:
        return image
    out = image.copy()
    for quad, marker_id in zip(corners, found.ravel().tolist()):
        if int(marker_id) not in ids:
            continue
        quad = quad.reshape(4, 2)
        center = quad.mean(axis=0)
        grown = center + (quad - center) * 1.85
        cv2.fillConvexPoly(out, np.round(grown).astype(np.int32), (175, 175, 175))
    return out
