"""Automatic source-site discovery: an anonymous grid over the device, then removal regions from grid events.

``grid`` finds the device in a static frame as the largest non-paper blob
inside the sheet's work area (generic calibration only) and covers it with
anonymous sites ``G-<row>-<col>``; the result is a custody-site-inventory@1
with basis "automatic grid" and declared start occupancy, for observer v1.1.
``cluster`` merges ``site_changed`` events of neighbouring grid sites with
overlapping time windows into discovered source regions ``S-<n>`` and writes
an events file with one removal per region, so the unchanged custody linker
can propose links. No device names, part counts or coordinates are inputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

PX_PER_MM = 4.0
PARAMS = {"paper_min_value": 150, "paper_max_saturation": 40, "closing_mm": 3.0,
          "pitch_mm": 8.0, "site_mm": 5.0, "min_on_mask": 0.8}


def _sheet(kit: dict, sheet_id: str) -> dict:
    return next(sheet for sheet in kit["sheets"] if sheet["sheet_id"] == sheet_id)


def device_mask(frame_bgr: np.ndarray, image_to_mm: np.ndarray, work: list[float]) -> tuple[np.ndarray, list[float]]:
    """Largest non-paper component inside the work area, in a 4 px/mm grid starting at the work-area corner."""
    x0, y0, w, h = work
    to_px = np.array([[PX_PER_MM, 0, -x0 * PX_PER_MM], [0, PX_PER_MM, -y0 * PX_PER_MM], [0, 0, 1.0]]) @ image_to_mm
    size = (int(w * PX_PER_MM), int(h * PX_PER_MM))
    rect = cv2.warpPerspective(frame_bgr, to_px, size)
    hsv = cv2.cvtColor(rect, cv2.COLOR_BGR2HSV)
    paper = (hsv[..., 2] >= PARAMS["paper_min_value"]) & (hsv[..., 1] <= PARAMS["paper_max_saturation"])
    mask = (~paper).astype(np.uint8)
    k = max(1, int(PARAMS["closing_mm"] * PX_PER_MM))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    if count < 2:
        raise SystemExit("no device found in the work area")
    largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return labels == largest, [x0, y0]


def grid_sites(mask: np.ndarray, origin_mm: list[float]) -> list[dict]:
    pitch, size = PARAMS["pitch_mm"], PARAMS["site_mm"]
    rows, cols = int(mask.shape[0] / PX_PER_MM / pitch), int(mask.shape[1] / PX_PER_MM / pitch)
    sites = []
    for r in range(rows):
        for c in range(cols):
            x, y = c * pitch + (pitch - size) / 2, r * pitch + (pitch - size) / 2
            box = mask[int(y * PX_PER_MM):int((y + size) * PX_PER_MM), int(x * PX_PER_MM):int((x + size) * PX_PER_MM)]
            if box.size and box.mean() >= PARAMS["min_on_mask"]:
                sites.append({"id": f"G-{r:02d}-{c:02d}", "rect_mm": [round(origin_mm[0] + x, 2), round(origin_mm[1] + y, 2), size, size],
                              "initially": "occupied"})
    return sites


def _cell(site_id: str) -> tuple[int, int]:
    _, r, c = site_id.split("-")
    return int(r), int(c)


def cluster(events_doc: dict, sites: dict[str, list[float]]) -> list[dict]:
    """Union of site_changed events that are 8-neighbours with overlapping time windows."""
    changed = [e for e in events_doc["events"] if e["zone_kind"] == "site" and e["kind"] == "site_changed"]
    parent = list(range(len(changed)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(changed):
        for j in range(i + 1, len(changed)):
            b = changed[j]
            (ra, ca), (rb, cb) = _cell(a["zone"]), _cell(b["zone"])
            near = max(abs(ra - rb), abs(ca - cb)) <= 1
            overlap = a["t_before_s"] < b["t_after_s"] and b["t_before_s"] < a["t_after_s"]
            if near and overlap:
                parent[find(i)] = find(j)
    groups: dict[int, list[dict]] = {}
    for i, event in enumerate(changed):
        groups.setdefault(find(i), []).append(event)
    regions = []
    for members in sorted(groups.values(), key=lambda g: min(e["t_before_s"] for e in g)):
        rects = [sites[e["zone"]] for e in members]
        centres = [(r[0] + r[2] / 2, r[1] + r[3] / 2) for r in rects]
        regions.append({
            "id": f"S-{len(regions) + 1}",
            "grid_sites": sorted(e["zone"] for e in members),
            "events": sorted(e["id"] for e in members),
            "centroid_mm": [round(float(np.mean([p[0] for p in centres])), 2), round(float(np.mean([p[1] for p in centres])), 2)],
            "t_before_s": min(e["t_before_s"] for e in members),
            "t_after_s": max(e["t_after_s"] for e in members),
        })
    return regions


def region_events(events_doc: dict, regions: list[dict]) -> dict:
    """Events doc for the linker: grid site events replaced by one removal per discovered region."""
    kept = [e for e in events_doc["events"] if e["zone_kind"] != "site"]
    for region in regions:
        kept.append({"id": f"{region['id']}", "zone": region["id"], "zone_kind": "site", "kind": "site_changed",
                     "t_before_s": region["t_before_s"], "t_after_s": region["t_after_s"]})
    zones = [z for z in events_doc["zones"] if z["kind"] != "site"]
    zones += [{"zone": r["id"], "kind": "site", "status": "observing"} for r in regions]
    return {**events_doc, "events": kept, "zones": zones}


def _write(path: Path, document: dict) -> str:
    data = (json.dumps(document, indent=2) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    grid = sub.add_parser("grid")
    grid.add_argument("--video", required=True, type=Path)
    grid.add_argument("--registration-from", required=True, type=Path, help="events.json whose registration H to use")
    grid.add_argument("--kit", required=True, type=Path)
    grid.add_argument("--at-s", type=float, default=1.0, help="static time for the device mask")
    grid.add_argument("--out", required=True, type=Path)
    clus = sub.add_parser("cluster")
    clus.add_argument("--events", required=True, type=Path)
    clus.add_argument("--inventory", required=True, type=Path)
    clus.add_argument("--out", required=True, type=Path, help="folder for regions.json and region-events.json")
    args = parser.parse_args()

    if args.command == "grid":
        registration = json.loads(args.registration_from.read_text(encoding="utf-8"))["registration"]["report"]
        kit = json.loads(args.kit.read_text(encoding="utf-8"))
        view = registration["view"]
        work = next(z["rect_frame_mm"] for z in _sheet(kit, view)["zones"] if z["type"] == "work_area")
        capture = cv2.VideoCapture(str(args.video))
        capture.set(cv2.CAP_PROP_POS_MSEC, args.at_s * 1000)
        ok, frame = capture.read()
        if not ok:
            raise SystemExit(f"cannot read {args.video} at {args.at_s} s")
        mask, origin = device_mask(frame, np.array(registration["H_image_to_frame_mm"]), work)
        sites = grid_sites(mask, origin)
        inventory = {"schema": "custody-site-inventory@1", "view": view, "inventory_t_s": 0.0,
                     "basis": {"kind": "automatic grid", "by": "tools/discovery/discover.py", "params": PARAMS,
                               "frame_t_s": args.at_s, "operator_accepted": False},
                     "coordinate_note": "anonymous grid over the largest non-paper blob in the work area",
                     "sites": sites}
        print(f"{len(sites)} grid sites; sha256 {_write(args.out, inventory)}")
        cv2.imwrite(str(args.out.with_suffix('.mask.png')), (mask * 255).astype(np.uint8))
    else:
        events_doc = json.loads(args.events.read_text(encoding="utf-8"))
        sites = {s["id"]: s["rect_mm"] for s in json.loads(args.inventory.read_text(encoding="utf-8"))["sites"]}
        regions = cluster(events_doc, sites)
        print(f"{len(regions)} regions; regions.json sha256 {_write(args.out / 'regions.json', {'regions': regions})}")
        print(f"region-events.json sha256 {_write(args.out / 'region-events.json', region_events(events_doc, regions))}")
        for region in regions:
            print(region["id"], region["centroid_mm"], region["t_before_s"], region["t_after_s"], len(region["grid_sites"]))


if __name__ == "__main__":
    main()
