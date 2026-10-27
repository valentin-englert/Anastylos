"""Cable path extraction: mask, skeletonize, trace, type. OpenCV/NumPy.

Centerlines come from Guo-Hall thinning of a background-relative
cable mask; branches, junctions, crossings, endpoints, and widths
derive from the skeleton graph. `segmenter` is an adapter seam: a
callable taking BGR and returning a 0/255 cable mask, so a future
segmentation provider (SAM, local vision models) can plug in.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

MIN_CROP_SIDE = 24
MIN_COMPONENT_FRACTION = 0.003
MIN_BRANCH_PX = 12
MIN_CABLE_PX = 30


def default_segmenter(image_bgr: np.ndarray) -> np.ndarray:
    """Cable-pixel mask from local background distance plus color."""
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    background = cv2.medianBlur(gray, 101)
    diff = cv2.absdiff(gray, background)
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    colored = (hsv[:, :, 1] > 70) & (hsv[:, :, 2] > 60)
    global_dark = gray.astype(float) < float(np.median(gray)) - 70
    usable = (gray >= 25) & (gray <= 245)
    mask = (((diff > 30) | global_dark | colored) & usable).astype(np.uint8)
    mask = mask * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    opened = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    return cv2.morphologyEx(opened, cv2.MORPH_CLOSE, kernel)


def _compact_blobs(mask: np.ndarray, gray: np.ndarray) -> list[dict]:
    """Large solid survivors of a wide opening: anchors, clips, objects."""
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    opened = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(opened, 8)
    total = mask.size
    blobs = []
    for index in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[index])
        frac = area / total
        if not 0.004 <= frac <= 0.09:
            continue
        cells = (labels == index).astype(np.uint8) * 255
        contours, _ = cv2.findContours(cells, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        contour = max(contours, key=cv2.contourArea)
        hull = cv2.convexHull(contour)
        solidity = cv2.contourArea(contour) / max(cv2.contourArea(hull), 1)
        if solidity < 0.85:
            continue
        if max(w / max(h, 1), h / max(w, 1)) > 3.0:
            continue
        blobs.append({"bbox_xywh": [x, y, w, h],
                      "centroid_xy": [round(x + w / 2, 1),
                                      round(y + h / 2, 1)],
                      "area_px": area,
                      "tone": round(float(np.median(gray[cells > 0])), 1)})
    blobs.sort(key=lambda item: (item["bbox_xywh"][1], item["bbox_xywh"][0]))
    return blobs




def _simplify(path: list) -> list[list[float]]:
    contour = np.array(path, np.int32).reshape((-1, 1, 2))
    approx = cv2.approxPolyDP(contour, 1.5, False)
    return [[round(float(p[0][0]), 1), round(float(p[0][1]), 1)]
            for p in approx]


def _tangent(path: list, at_start: bool) -> list[float]:
    sample = path[:8] if at_start else path[-8:]
    if len(sample) < 2:
        return [1.0, 0.0]
    first, last = sample[0], sample[-1]
    dx, dy = last[0] - first[0], last[1] - first[1]
    if at_start:
        dx, dy = -dx, -dy
    norm = math.hypot(dx, dy) or 1.0
    return [round(dx / norm, 3), round(dy / norm, 3)]


def _branch_width(dist: np.ndarray, path: list) -> tuple[float, float]:
    samples = []
    for x, y in path[::3]:
        if 0 <= y < dist.shape[0] and 0 <= x < dist.shape[1]:
            samples.append(float(dist[y, x]) * 2)
    if not samples:
        return 0.0, 0.0
    median = float(np.median(samples))
    cv = float(np.std(samples) / max(median, 1e-9))
    return round(median, 1), round(cv, 3)


def _elongated(cells: np.ndarray) -> bool:
    contours, _ = cv2.findContours(cells, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return False
    contour = max(contours, key=cv2.contourArea)
    (_, _), (w, h), _ = cv2.minAreaRect(contour)
    aspect = max(w, h) / max(min(w, h), 1e-9)
    hull = cv2.convexHull(contour)
    solidity = cv2.contourArea(contour) / max(cv2.contourArea(hull), 1)
    return aspect >= 2.0 or solidity < 0.80














def _neighbors8(thin: np.ndarray, point) -> list:
    x, y = point
    height, width = thin.shape
    found = []
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            nx, ny = x + dx, y + dy
            if 0 <= nx < width and 0 <= ny < height and thin[ny, nx] > 0:
                found.append((nx, ny))
    return found




def _prune(branches: list, touches: list):
    """Drop fragments; retouch bookkeeping to survivors."""
    keep = []
    for index, branch in enumerate(branches):
        tips = sum(1 for end in branch["ends"] if end["kind"] == "tip")
        floor = MIN_BRANCH_PX if tips else 20
        if len(branch["pixels"]) >= floor:
            keep.append(index)
    mapping = {old: new for new, old in enumerate(keep)}
    survivors = [branches[i] for i in keep]
    kept_touches = [dict(item, branch=mapping[item["branch"]],
                         other=mapping[item["other"]])
                    for item in touches
                    if item["branch"] in mapping
                    and item["other"] in mapping]
    return survivors, kept_touches


def _drop_interior(branches: list, touches: list, zones: list):
    """Drop knot-interior fragments: no free end, mostly inside a complex.

    Leftover blob-web links between jumped runs are not cable arms;
    genuine stems always keep one free tip outside the complex.
    """
    if not zones:
        return branches, touches
    keep = []
    for index, branch in enumerate(branches):
        tips = sum(1 for end in branch["ends"] if end["kind"] == "tip")
        inside = sum(1 for point in branch["pixels"][::2]
                     if _in_zone(point, zones) is not None)
        total = max(len(branch["pixels"][::2]), 1)
        if tips == 0 and inside / total >= 0.8:
            continue
        keep.append(index)
    mapping = {old: new for new, old in enumerate(keep)}
    survivors = [branches[i] for i in keep]
    kept_touches = [dict(item, branch=mapping[item["branch"]],
                         other=mapping[item["other"]])
                    for item in touches
                    if item["branch"] in mapping
                    and item["other"] in mapping]
    return survivors, kept_touches










def extract_cables(image_bgr: np.ndarray, mask: np.ndarray,
                   gray: np.ndarray, blobs: list[dict]
                   ) -> tuple[list, list, list]:
    """Trace cable records plus junction/crossing records from a mask."""
    carve = mask.copy()
    for blob in blobs:
        x, y, w, h = blob["bbox_xywh"]
        cv2.rectangle(carve, (x - 3, y - 3), (x + w + 3, y + h + 3), 0, -1)
    count, labels = cv2.connectedComponents(carve, 8)
    cables, features, excluded = [], [], []
    dist_all = cv2.distanceTransform((mask > 0).astype(np.uint8),
                                     cv2.DIST_L2, 3)
    for label in range(1, count):
        cells = (labels == label).astype(np.uint8) * 255
        ys, xs = np.nonzero(cells)
        bbox = [int(xs.min()), int(ys.min()),
                int(xs.max() - xs.min() + 1),
                int(ys.max() - ys.min() + 1)]
        if int((cells > 0).sum()) / mask.size < MIN_COMPONENT_FRACTION:
            continue
        if not _elongated(cells):
            excluded.append({"bbox_xywh": bbox,
                             "reason": "compact non-cable object"})
            continue
        thin = cv2.ximgproc.thinning(
            cells, thinningType=cv2.ximgproc.THINNING_GUOHALL)
        if int((thin > 0).sum()) < MIN_CABLE_PX:
            continue
        zones = _blob_zones(thin)
        traced, touches = _trace_runs(thin, zones)
        kept, touches = _prune(traced, touches)
        kept, touches = _drop_interior(kept, touches, zones)
        if not kept:
            continue
        touches = _gather_touches(kept, touches)
        sites = _group_sites(kept, touches, zones)
        cable_branches = []
        for branch in kept:
            path = branch["pixels"]
            width, width_cv = _branch_width(dist_all, path)
            ends = list(branch["ends"])
            for position in range(2):
                if ends[position]["kind"] != "tip":
                    continue
                tip = path[0] if position == 0 else path[-1]
                near = any(_in_zone((tip[0] + dx, tip[1] + dy), zones)
                           is not None
                           for dx in range(-2, 3) for dy in range(-2, 3))
                if near:
                    ends[position] = {"xy": ends[position]["xy"],
                                      "kind": "junction"}
            cable_branches.append(
                {"polyline_xy": _simplify(path),
                 "length_px": round(len(path), 1),
                 "width_px": width, "width_cv": width_cv,
                 "start_tangent_xy": _tangent(path, True),
                 "end_tangent_xy": _tangent(path, False),
                 "ends": ends,
                 "interpolated_px": branch["interpolated_px"]})
        cable = {"branches": cable_branches, "bbox_xywh": bbox,
                 "_zones": zones}
        cables.append((cable, _classify_sites(kept, sites)))
    cables.sort(key=lambda item: (item[0]["bbox_xywh"][1],
                                  item[0]["bbox_xywh"][0]))
    numbered, features = [], []
    for index, (cable, sites) in enumerate(cables, 1):
        numbered.append(cable)
        for site in sites:
            features.append({**site, "cable": index})
    return numbered, features, excluded


def _blob_zones(thin: np.ndarray) -> list[dict]:
    """Dense junction complexes: merged blobs a walk jumps straight over.

    Sparse corners (curve spurs) stay below the density floor and are
    never zoned; clean T joins have a single junction pixel and are
    handled zone-free by ray topology in _classify_sites.
    """
    skel = thin > 0
    kernel = np.ones((3, 3), np.float32)
    counts = cv2.filter2D(skel.astype(np.float32), -1, kernel) - 1
    junct = (skel & (counts >= 3)).astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13))
    grown = cv2.dilate(junct, kernel)
    count, labels = cv2.connectedComponents(grown, 8)
    kept = np.zeros_like(grown)
    for label in range(1, count):
        cells = labels == label
        if int(((junct > 0) & cells).sum()) >= 6:
            kept[cells] = 255
    bridge = cv2.dilate(kept, cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (17, 17)))
    count, labels = cv2.connectedComponents(bridge, 8)
    zones = []
    for label in range(1, count):
        cells = labels == label
        ys, xs = np.nonzero(cells & (kept > 0))
        if not len(xs):
            continue
        zones.append({"centroid_xy": [float(xs.mean()),
                                      float(ys.mean())],
                      "mask": (cells > 0)})
    zones.sort(key=lambda item: (item["centroid_xy"][1],
                                 item["centroid_xy"][0]))
    return zones


def _in_zone(point, zones) -> int | None:
    x, y = int(point[0]), int(point[1])
    for index, zone in enumerate(zones):
        mask = zone["mask"]
        if 0 <= y < mask.shape[0] and 0 <= x < mask.shape[1] \
                and mask[y, x]:
            return index
    return None


def _trace_runs(thin: np.ndarray, zones: list):
    """Greedy straightest walks; primaries jump straight over blob zones.

    Returns branch dicts with pixel paths, typed ends (tip, touch, or
    loop), interpolated counts, plus walk-stop touch records.
    Deterministic.
    """
    skel = thin > 0
    kernel = np.ones((3, 3), np.float32)
    counts = cv2.filter2D(skel.astype(np.float32), -1, kernel) - 1
    ends = sorted((x, y) for y, x in np.argwhere(skel & (counts == 1)))
    owner: dict[tuple[int, int], int] = {}
    branches: list[dict] = []
    touches: list[dict] = []

    def jump(pos, direction, own):
        """Straight ray across the whole complex; consume, flag the rest."""
        pixels, interpolated, outside = [], 0, 0
        x, y = float(pos[0]), float(pos[1])
        for _ in range(150):
            x += direction[0]
            y += direction[1]
            point = (int(round(x)), int(round(y)))
            if not (0 <= point[1] < thin.shape[0]
                    and 0 <= point[0] < thin.shape[1]):
                return pixels, interpolated, pixels[-1] if pixels else pos
            if _in_zone(point, zones) is None:
                outside += 1
                if skel[point[1], point[0]] or outside > 12:
                    return pixels, interpolated, point
                pixels.append(point)
                interpolated += 1
                continue
            pixels.append(point)
            if skel[point[1], point[0]] and point not in owner:
                owner[point] = own
            else:
                interpolated += 1
        return pixels, interpolated, pixels[-1] if pixels else pos

    def walk(start, primary):
        pixels = [start]
        owner[start] = len(branches)
        direction, interpolated = None, 0
        while len(pixels) <= skel.size:
            options = [q for q in _neighbors8(thin, pixels[-1])
                       if q not in owner]
            if not options:
                others = [q for q in _neighbors8(thin, pixels[-1])
                          if owner.get(q, len(branches)) != len(branches)]
                if others:
                    return pixels, interpolated, "touch", others[0]
                return pixels, interpolated, "tip", None
            if direction is None:
                nxt = sorted(options)[0]
            else:
                scored = []
                for cand in options:
                    dx = cand[0] - pixels[-1][0]
                    dy = cand[1] - pixels[-1][1]
                    norm = math.hypot(dx, dy) or 1.0
                    dot = (dx * direction[0] + dy * direction[1]) / norm
                    scored.append((-dot, cand[0], cand[1], cand))
                nxt = sorted(scored)[0][3]
            dx, dy = nxt[0] - pixels[-1][0], nxt[1] - pixels[-1][1]
            norm = math.hypot(dx, dy) or 1.0
            direction = (dx / norm, dy / norm)
            if primary and _in_zone(nxt, zones) is not None \
                    and _in_zone(pixels[-1], zones) is None:
                base = pixels[max(0, len(pixels) - 30)]
                ax = pixels[-1][0] - base[0]
                ay = pixels[-1][1] - base[1]
                anorm = math.hypot(ax, ay)
                axis = (ax / anorm, ay / anorm) if anorm >= 8.0 \
                    else direction
                jumped, extra, exit_point = jump(pixels[-1], axis,
                                                 len(branches))
                pixels.extend(jumped)
                interpolated += extra
                if _in_zone(exit_point, zones) is None:
                    pixels.append(exit_point)
                    if skel[exit_point[1], exit_point[0]] \
                            and exit_point not in owner:
                        owner[exit_point] = len(branches)
                continue
            pixels.append(nxt)
            owner[nxt] = len(branches)
        return pixels, interpolated, "tip", None

    def start_kind(pixel):
        others = [q for q in _neighbors8(thin, pixel) if q in owner]
        return "touch" if others else "tip"

    for point in ends:
        if point in owner:
            continue
        pixels, extra, end_kind, other = walk(point, True)
        index = len(branches)
        branches.append({"pixels": pixels, "interpolated_px": extra,
                         "ends": [{"xy": list(point), "kind": "tip"},
                                  {"xy": list(pixels[-1]), "kind": end_kind}]})
        if end_kind == "touch":
            touches.append({"branch": index, "end": 1,
                            "pixel": pixels[-1], "other": owner[other],
                            "other_pixel": other})
    height, width = skel.shape
    for y in range(height):
        for x in range(width):
            if not skel[y, x] or (x, y) in owner:
                continue
            pixels, extra, end_kind, other = walk((x, y), False)
            index = len(branches)
            first = start_kind((x, y))
            branches.append({"pixels": pixels, "interpolated_px": extra,
                             "ends": [{"xy": list((x, y)), "kind": first},
                                      {"xy": list(pixels[-1]),
                                       "kind": end_kind}]})
            if end_kind == "touch":
                touches.append({"branch": index, "end": 1,
                                "pixel": pixels[-1], "other": owner[other],
                                "other_pixel": other})
    return branches, touches


def _nearest_pair(first: list, second: list, limit: float):
    """Closest sampled pixel pair under limit, else None. Deterministic."""
    if not first or not second:
        return None
    sample_a = np.array(first[::4], dtype=float)
    sample_b = np.array(second[::4], dtype=float)
    gaps = np.abs(sample_a[:, None, :] - sample_b[None, :, :]).sum(axis=2)
    flat = int(np.argmin(gaps))
    if float(gaps.flat[flat]) >= limit:
        return None
    i, j = divmod(flat, gaps.shape[1])
    return first[i * 4], second[j * 4]


def _gather_touches(branches: list, touches: list) -> list[dict]:
    """Walk-stop touches plus true nearest-pair proximity touches."""
    found = list(touches)
    seen = {(item["branch"], item["other"]) for item in touches}
    for i in range(len(branches)):
        for j in range(i + 1, len(branches)):
            if (i, j) in seen or (j, i) in seen:
                continue
            first, second = branches[i]["pixels"], branches[j]["pixels"]
            pair = _nearest_pair(first, second, 6.0)
            if pair is None:
                continue
            found.append({"branch": i, "end": 0, "pixel": pair[0],
                          "other": j, "other_pixel": pair[1],
                          "proximity": True})
            seen.add((i, j))
    for item in found:
        item.setdefault("proximity", False)
    return found


def _near_complex(point, zones, radius: float) -> int | None:
    for index, zone in enumerate(zones):
        cx, cy = zone["centroid_xy"]
        if math.hypot(point[0] - cx, point[1] - cy) <= radius:
            return index
    return None


def _group_sites(branches: list, touches: list, zones: list) -> list[dict]:
    """One site per junction complex; stray touches grouped by nearness."""
    sites = []
    for index, zone in enumerate(zones):
        cx, cy = zone["centroid_xy"]
        members = [i for i, branch in enumerate(branches)
                   if min(math.hypot(px - cx, py - cy)
                          for px, py in branch["pixels"][::4]) < 10.0]
        if len(members) >= 2:
            sites.append({"xy": [cx, cy], "members": sorted(members),
                          "zone": index})
    strays = [item for item in touches
              if _near_complex(item["pixel"], zones, 25.0) is None
              and _near_complex(item["other_pixel"], zones, 25.0) is None]
    clusters: list[list[dict]] = []
    for touch in strays:
        point = touch["pixel"]
        for cluster in clusters:
            anchor = cluster[0]["pixel"]
            if abs(point[0] - anchor[0]) + abs(point[1] - anchor[1]) < 30.0:
                cluster.append(touch)
                break
        else:
            clusters.append([touch])
    for cluster in clusters:
        members = sorted({item["branch"] for item in cluster}
                         | {item["other"] for item in cluster})
        xs = [item["pixel"][0] for item in cluster]
        ys = [item["pixel"][1] for item in cluster]
        sites.append({"xy": [sum(xs) / len(xs), sum(ys) / len(ys)],
                      "members": members, "zone": None})
    sites.sort(key=lambda item: (item["xy"][1], item["xy"][0]))
    return sites


def _site_rays(branch_pixels: list, site_xy) -> list[float]:
    """Arm directions where the walk passes nearest the site (degrees)."""
    anchor = min(range(0, len(branch_pixels), 2),
                 key=lambda i: abs(branch_pixels[i][0] - site_xy[0])
                 + abs(branch_pixels[i][1] - site_xy[1]))
    ax, ay = branch_pixels[anchor]
    rays = []
    for end in (0, len(branch_pixels) - 1):
        step = -20 if end == 0 else 20
        index = anchor + step
        index = max(0, min(len(branch_pixels) - 1, index))
        if abs(index - anchor) < 4:
            continue
        if end == 0 and anchor < 4:
            continue
        if end == len(branch_pixels) - 1 \
                and anchor > len(branch_pixels) - 5:
            continue
        px, py = branch_pixels[index]
        if math.hypot(px - ax, py - ay) >= 4.0:
            rays.append(math.degrees(math.atan2(py - ay, px - ax)))
    return rays


def _cluster_arms(rays: list[float], window: float = 35.0) -> list[float]:
    """Merge near-parallel rays into one arm each. Deterministic."""
    arms: list[list[float]] = []
    for ray in sorted(rays):
        for arm in arms:
            if min(abs(ray - other) % 360.0 for other in arm) < window \
                    or min(abs(ray - other - 360.0) % 360.0
                           for other in arm) < window \
                    or min(abs(ray - other + 360.0) % 360.0
                           for other in arm) < window:
                arm.append(ray)
                break
        else:
            arms.append([ray])
    merged = []
    for arm in arms:
        vectors = [(math.cos(math.radians(a)), math.sin(math.radians(a)))
                   for a in arm]
        sx = sum(v[0] for v in vectors)
        sy = sum(v[1] for v in vectors)
        merged.append(math.degrees(math.atan2(sy, sx)))
    return merged


def _pair_rays(rays: list[float]) -> tuple[int, list[float], list[float]]:
    """Greedy anti-parallel (>155 deg) pairing; pairs, stems, paired."""
    candidates = []
    for i in range(len(rays)):
        for j in range(i + 1, len(rays)):
            spread = abs(rays[i] - rays[j]) % 360.0
            spread = min(spread, 360.0 - spread)
            if spread > 155.0:
                candidates.append((spread, i, j))
    candidates.sort(reverse=True)
    used: set[int] = set()
    pairs = 0
    for _, i, j in candidates:
        if i not in used and j not in used:
            used.update((i, j))
            pairs += 1
    stems = [ray for index, ray in enumerate(rays) if index not in used]
    paired = [ray for index, ray in enumerate(rays) if index in used]
    return pairs, stems, paired


def _classify_sites(branches: list, sites: list) -> list[dict]:
    """Crossing, tap, branch, or overlap from ray topology at each site.

    Counts straight-through arm pairs: two pairs is a crossing, one
    pair plus one off-axis stem is a tap, no pair is a branch. Works
    with or without a junction complex. 2D only: no electrical,
    identity, or depth claims.
    """
    found = []
    for site in sites:
        rays = []
        for member in site["members"]:
            rays.extend(_site_rays(branches[member]["pixels"], site["xy"]))
        if len(rays) < 3:
            continue
        arms = _cluster_arms(rays)
        if len(arms) < 3:
            continue
        pairs, stems, paired = _pair_rays(arms)
        if pairs >= 2:
            kind = "crossing"
        elif pairs == 1 and len(stems) == 1:
            spread = abs(stems[0] - paired[0]) % 180.0
            kind = "tap" if min(spread, 180.0 - spread) > 25.0 else "overlap"
        elif pairs == 0:
            kind = "branch"
        elif pairs == 1:
            kind = "branch" if len(stems) >= 2 else "overlap"
        else:
            continue
        found.append({"kind": kind, "xy": [round(site["xy"][0], 1),
                                           round(site["xy"][1], 1)],
                      "degree": len(arms),
                      "branches": sorted(site["members"]),
                      "note": ("2D crossing proves no electrical connection, "
                               "shared identity, or depth order")
                      if kind == "crossing" else None})
    return found


def _light_blobs(mask: np.ndarray, gray: np.ndarray, bg_med: float) -> list[dict]:
    """Bright compact regions over cable runs: clip candidates."""
    grown = cv2.dilate((mask > 0).astype(np.uint8), np.ones((5, 5), np.uint8))
    bright = ((gray.astype(float) > bg_med + 25) & (gray <= 245)
              & (grown > 0)).astype(np.uint8) * 255
    count, labels, stats, _ = cv2.connectedComponentsWithStats(bright, 8)
    total = mask.size
    found = []
    for index in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[index])
        if not 0.002 <= area / total <= 0.05:
            continue
        cells = (labels == index).astype(np.uint8) * 255
        contours, _ = cv2.findContours(cells, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        contour = max(contours, key=cv2.contourArea)
        hull = cv2.convexHull(contour)
        if cv2.contourArea(contour) / max(cv2.contourArea(hull), 1) < 0.6:
            continue
        found.append({"bbox_xywh": [x, y, w, h],
                      "centroid_xy": [round(x + w / 2, 1),
                                      round(y + h / 2, 1)],
                      "area_px": area,
                      "tone": round(float(np.median(gray[cells > 0])), 1)})
    return found


def _near_blob(point, blobs: list[dict], radius: float) -> dict | None:
    for blob in blobs:
        cx, cy = blob["centroid_xy"]
        if math.hypot(point[0] - cx, point[1] - cy) <= radius:
            return blob
    return None


def collect_endpoints(cables: list, blobs: list[dict],
                      shape) -> list[dict]:
    """Branch ends at skeleton endpoints, typed by surroundings."""
    height, width = shape[:2]
    found = []
    for cable_index, cable in enumerate(cables, 1):
        for branch_index, branch in enumerate(cable["branches"]):
            poly = branch["polyline_xy"]
            for position, end in enumerate(branch["ends"]):
                if end["kind"] != "tip":
                    continue
                at_start = position == 0
                point = poly[0] if at_start else poly[-1]
                tangent = branch["start_tangent_xy"] if at_start \
                    else branch["end_tangent_xy"]
                if min(point[0], point[1], width - 1 - point[0],
                       height - 1 - point[1]) <= 6:
                    kind = "frame_edge"
                elif _near_blob(point, blobs, 22.0):
                    kind = "connector_proximity"
                else:
                    kind = "free"
                found.append({"xy": point, "tangent_xy": tangent,
                              "cable": cable_index,
                              "branch": branch_index, "type": kind,
                              "width_px": branch["width_px"],
                              "interrupted": False})
    found.sort(key=lambda item: (item["xy"][1], item["xy"][0]))
    for index, item in enumerate(found, 1):
        item["endpoint_id"] = f"e{index}"
    return found


def _facing(first: dict, second: dict) -> float | None:
    dx = second["xy"][0] - first["xy"][0]
    dy = second["xy"][1] - first["xy"][1]
    gap = math.hypot(dx, dy)
    if not 8.0 <= gap <= 120.0:
        return None
    ux, uy = dx / gap, dy / gap
    first_t, second_t = first["tangent_xy"], second["tangent_xy"]
    aim = (first_t[0] * ux + first_t[1] * uy
           + second_t[0] * -ux + second_t[1] * -uy) / 2
    return aim if aim > 0.80 else None


def propose_continuations(endpoints: list[dict]) -> list[dict]:
    """Uncertain links across gaps. Never asserted as same cable."""
    scored = []
    for i in range(len(endpoints)):
        for j in range(i + 1, len(endpoints)):
            first, second = endpoints[i], endpoints[j]
            if first["cable"] == second["cable"]:
                continue
            aim = _facing(first, second)
            if aim is None:
                continue
            ratio = max(first["width_px"], second["width_px"]) / max(
                min(first["width_px"], second["width_px"]), 1e-9)
            if ratio > 1.6:
                continue
            gap = math.hypot(second["xy"][0] - first["xy"][0],
                             second["xy"][1] - first["xy"][1])
            scored.append((gap, first, second, aim))
    scored.sort(key=lambda item: item[0])
    used: set[str] = set()
    hypotheses = []
    for gap, first, second, aim in scored:
        if first["endpoint_id"] in used or second["endpoint_id"] in used:
            continue
        used.add(first["endpoint_id"])
        used.add(second["endpoint_id"])
        first["interrupted"] = True
        second["interrupted"] = True
        hypotheses.append(
            {"endpoints": [first["endpoint_id"], second["endpoint_id"]],
             "gap_px": round(gap, 1), "confidence": 0.35,
             "evidence": {"facing": round(aim, 3)},
             "note": "uncertain continuation across an interruption; "
                     "segments stay disconnected"})
    for index, item in enumerate(hypotheses, 1):
        item["hypothesis_id"] = f"h{index}"
    return hypotheses


def _on_mask_fraction(segment: dict, grown: np.ndarray) -> float:
    p1, p2 = segment["p1"], segment["p2"]
    length = max(int(math.hypot(p2[0] - p1[0], p2[1] - p1[1])), 1)
    hits, total = 0, 0
    for step in range(length + 1):
        x = int(p1[0] + (p2[0] - p1[0]) * step / length)
        y = int(p1[1] + (p2[1] - p1[1]) * step / length)
        if 0 <= y < grown.shape[0] and 0 <= x < grown.shape[1]:
            total += 1
            hits += grown[y, x] > 0
    return hits / max(total, 1)


def detect_channels(gray: np.ndarray, cables: list,
                    mask: np.ndarray) -> list[dict]:
    """Long parallel line pairs flanking a cable: channel candidates."""
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 20, 60)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, 80,
                            minLineLength=120, maxLineGap=12)
    if lines is None:
        return []
    grown = cv2.dilate((mask > 0).astype(np.uint8), np.ones((3, 3), np.uint8))
    segments = []
    for line in lines[:, 0]:
        x1, y1, x2, y2 = (float(v) for v in line)
        angle = math.degrees(math.atan2(y2 - y1, x2 - x1)) % 180.0
        candidate = {"p1": [x1, y1], "p2": [x2, y2], "angle": angle}
        if _on_mask_fraction(candidate, grown) > 0.20:
            continue
        segments.append(candidate)
    found = []
    for i in range(len(segments)):
        for j in range(i + 1, len(segments)):
            first, second = segments[i], segments[j]
            spread = abs(first["angle"] - second["angle"])
            spread = min(spread, 180.0 - spread)
            if spread > 8.0:
                continue
            mid = [(first["p1"][0] + first["p2"][0]) / 2,
                   (first["p1"][1] + first["p2"][1]) / 2]
            other = [(second["p1"][0] + second["p2"][0]) / 2,
                     (second["p1"][1] + second["p2"][1]) / 2]
            separation = math.hypot(mid[0] - other[0], mid[1] - other[1])
            if not 50.0 <= separation <= 150.0:
                continue
            for index, cable in enumerate(cables, 1):
                x, y, w, h = cable["bbox_xywh"]
                center = [x + w / 2, y + h / 2]
                if min(math.hypot(center[0] - mid[0], center[1] - mid[1]),
                       math.hypot(center[0] - other[0],
                                  center[1] - other[1])) > separation:
                    continue
                if any(item["cable"] == index for item in found):
                    continue
                found.append({"rails": [first, second], "cable": index,
                              "separation_px": round(separation, 1),
                              "confidence": 0.55,
                              "note": "parallel rails flanking a cable"})
    return found


def _merge_nearby(blobs: list[dict], gap: float = 15.0) -> list[dict]:
    """Fuse blob fragments (split by slots or seams) into one object."""
    groups: list[list[dict]] = []
    for blob in blobs:
        x, y, w, h = blob["bbox_xywh"]
        placed = False
        for group in groups:
            gx, gy, gw, gh = group[0]["bbox_xywh"]
            for other in group[1:]:
                ox, oy, ow, oh = other["bbox_xywh"]
                gx, gy = min(gx, ox), min(gy, oy)
                gw = max(gx + gw, ox + ow) - gx
                gh = max(gy + gh, oy + oh) - gy
            if x < gx + gw + gap and gx < x + w + gap \
                    and y < gy + gh + gap and gy < y + h + gap:
                group.append(blob)
                placed = True
                break
        if not placed:
            groups.append([blob])
    merged = []
    for group in groups:
        boxes = [item["bbox_xywh"] for item in group]
        x0 = min(box[0] for box in boxes)
        y0 = min(box[1] for box in boxes)
        x1 = max(box[0] + box[2] for box in boxes)
        y1 = max(box[1] + box[3] for box in boxes)
        area = sum(item["area_px"] for item in group)
        tone = sum(item["tone"] * item["area_px"] for item in group) / area
        merged.append({"bbox_xywh": [x0, y0, x1 - x0, y1 - y0],
                       "centroid_xy": [round((x0 + x1) / 2, 1),
                                       round((y0 + y1) / 2, 1)],
                       "area_px": area, "tone": round(tone, 1)})
    merged.sort(key=lambda item: (item["bbox_xywh"][1], item["bbox_xywh"][0]))
    return merged


def _bbox_gap(point, bbox) -> float:
    x, y, w, h = bbox
    dx = max(x - point[0], 0, point[0] - (x + w))
    dy = max(y - point[1], 0, point[1] - (y + h))
    return math.hypot(dx, dy)


def classify_blobs(blobs: list[dict], cables: list[dict], total: int,
                   endpoints: list[dict]) -> tuple[list, list, list]:
    """Split compact blobs into clips, anchors, and background objects."""
    clips, anchors, background = [], [], []
    for index, blob in enumerate(_merge_nearby(blobs), 1):
        blob["blob_id"] = f"b{index}"
        x, y, w, h = blob["bbox_xywh"]
        near_cables = sum(1 for cable in cables
                          if _bbox_gap(blob["centroid_xy"],
                                       cable["bbox_xywh"]) < 20.0)
        mid_run = any(x > cx and y > cy and x + w < cx + cw
                      and y + h < cy + ch
                      for cx, cy, cw, ch in
                      (cable["bbox_xywh"] for cable in cables))
        near_end = any(_bbox_gap(point, blob["bbox_xywh"]) < 30.0
                       for point in (item["xy"] for item in endpoints))
        if (mid_run or near_cables >= 2) \
                and blob["area_px"] / max(total, 1) < 0.025:
            blob["role"] = "clip"
            clips.append(blob)
        elif near_end:
            blob["role"] = "anchor_candidate"
            anchors.append(blob)
        else:
            blob["role"] = "background_object"
            background.append(blob)
    return clips, anchors, background


def _cable_quality(branch: dict, contrast: float) -> dict:
    return {"contrast": round(contrast, 1),
            "width_cv": branch["width_cv"],
            "length_px": branch["length_px"]}


def _jsonable(value):
    """Recursively convert numpy scalars to plain Python types."""
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    return value


def analyze(image: np.ndarray, roi: tuple[int, int, int, int] | None = None,
            frame_metadata: dict | None = None, segmenter=None) -> dict:
    """Extract visible cable paths from one crop. JSON-serializable."""
    from anastylos_cable.geometry import convert_cables
    height, width = image.shape[:2]
    x, y, w, h = roi or (0, 0, width, height)
    if not (0 <= x < width and 0 <= y < height and w > 0 and h > 0
            and x + w <= width and y + h <= height):
        raise ValueError(f"ROI {(x, y, w, h)} is outside {width}x{height}")
    convention = ("source-frame pixels; origin top-left; +x right; +y "
                  "down; polylines ordered along each visible segment")
    if min(h, w) < MIN_CROP_SIDE:
        return {"status": "unavailable",
                "reason": f"crop {w}x{h} below {MIN_CROP_SIDE}px minimum",
                "coordinate_convention": convention,
                "cables": [], "endpoints": [], "features": [],
                "hypotheses": [], "occluded_regions": [], "clips": [],
                "anchors": [], "channels": [], "excluded": [],
                "quality": None,
                "geometry_mm": None, "calibration_error": None,
                "explanation": "crop too small to assess"}
    crop = image[y:y + h, x:x + w]
    image_bgr = crop if crop.ndim == 3 else cv2.cvtColor(crop,
                                                        cv2.COLOR_GRAY2BGR)
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    segment = segmenter or default_segmenter
    mask = segment(image_bgr)
    if mask is None:
        return {"status": "unavailable",
                "reason": "segmentation provider returned no observation",
                "coordinate_convention": convention,
                "cables": [], "endpoints": [], "features": [],
                "hypotheses": [], "occluded_regions": [], "clips": [],
                "anchors": [], "channels": [], "excluded": [],
                "quality": None,
                "geometry_mm": None, "calibration_error": None,
                "explanation": "segmentation provider unavailable"}
    binary = (mask > 0).astype(np.uint8) * 255
    bg_first = float(np.median(gray))
    blobs = _compact_blobs(binary, gray)
    blobs += [item for item in _light_blobs(binary, gray, bg_first)
              if all(abs(item["centroid_xy"][0] - other["centroid_xy"][0])
                     + abs(item["centroid_xy"][1] - other["centroid_xy"][1])
                     > 10 for other in blobs)]
    cables, features, excluded = extract_cables(image_bgr, mask, gray, blobs)
    endpoints = collect_endpoints(cables, blobs, gray.shape)
    hypotheses = propose_continuations(endpoints)
    channels = detect_channels(gray, cables, mask)
    clips, anchors, background = classify_blobs(blobs, cables, gray.size,
                                                endpoints)
    for item in background:
        excluded.append({"bbox_xywh": item["bbox_xywh"],
                         "reason": "background object near no cable"})
    bg_med = float(np.median(gray))
    binary = (mask > 0).astype(np.uint8)
    records = []
    for index, cable in enumerate(cables, 1):
        cx, cy, cw, ch = cable["bbox_xywh"]
        window = binary[cy:cy + ch, cx:cx + cw]
        tones = gray[cy:cy + ch, cx:cx + cw][window > 0]
        contrast = abs(float(np.median(tones)) - bg_med) \
            if tones.size else 0.0
        branches = []
        for branch in cable["branches"]:
            shifted = [[round(px + x, 1), round(py + y, 1)]
                       for px, py in branch["polyline_xy"]]
            branches.append(
                {key: branch[key] for key in
                 ("length_px", "width_px", "width_cv", "start_tangent_xy",
                  "end_tangent_xy", "ends",
                  "interpolated_px")} | {"polyline_xy": shifted})
        records.append(
            {"cable_id": f"c{index}",
             "bbox_xywh": [cable["bbox_xywh"][0] + x,
                           cable["bbox_xywh"][1] + y,
                           cable["bbox_xywh"][2], cable["bbox_xywh"][3]],
             "branches": branches,
             "quality": _cable_quality(
                 cable["branches"][0] if cable["branches"] else
                 {"width_cv": 0.0, "length_px": 0.0}, contrast),
             "note": "visible segment only; identity ends at this frame"})
    for cable in records:
        for branch in cable["branches"]:
            for end in branch["ends"]:
                end["xy"] = [round(end["xy"][0] + x, 1),
                             round(end["xy"][1] + y, 1)]
    for item in endpoints:
        item["xy"] = [round(item["xy"][0] + x, 1),
                      round(item["xy"][1] + y, 1)]
    for item in features:
        item["xy"] = [round(item["xy"][0] + x, 1),
                      round(item["xy"][1] + y, 1)]
    occluded = []
    for item in hypotheses:
        pair = [next(e for e in endpoints if e["endpoint_id"] == ref)
                for ref in item["endpoints"]]
        xs = [e["xy"][0] for e in pair]
        ys = [e["xy"][1] for e in pair]
        pad = 12.0
        occluded.append({"bbox_xywh": [round(min(xs) - pad, 1),
                                       round(min(ys) - pad, 1),
                                       round(max(xs) - min(xs) + 2 * pad, 1),
                                       round(max(ys) - min(ys) + 2 * pad, 1)],
                         "hypothesis_ref": item["hypothesis_id"]})
    for index, item in enumerate(features, 1):
        item["feature_id"] = f"f{index}"
    blur = cv2.Laplacian(gray, cv2.CV_64F)
    quality = {"glare_fraction": round(float((gray > 245).mean()), 3),
               "shadow_fraction": round(float((gray < 25).mean()), 3),
               "blur_variance": round(float(blur.var()), 1),
               "cables": len(records)}
    if not records:
        status, reason = "unknown", "no cable-like paths found"
    elif all(item["quality"]["contrast"] < 25 for item in records):
        status, reason = "ambiguous", "only weak low-contrast paths found"
    else:
        status, reason = "ok", None
    geometry_mm, calibration_error = convert_cables(records, frame_metadata)
    return _jsonable(
        {"status": status, "reason": reason,
         "coordinate_convention": convention, "cables": records,
         "endpoints": endpoints, "features": features,
         "hypotheses": hypotheses, "occluded_regions": occluded,
         "clips": clips, "anchors": anchors, "channels": channels,
         "excluded": excluded,
         "quality": quality, "geometry_mm": geometry_mm,
         "calibration_error": calibration_error,
         "image_size": [w, h],
         "explanation": f"{len(records)} visible cable(s), "
                        f"{len(hypotheses)} continuation hypotheses"})
