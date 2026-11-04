"""Annotated diagnostic overlays for tray-counter results."""

from __future__ import annotations

from pathlib import Path

import cv2

from anastylos_trays.counter import load_result, mm_to_px

ZONE_COLOR = (255, 200, 0)
DEVICE_COLOR = (200, 0, 200)
EVENT_COLORS = {"addition": (0, 200, 0), "removal": (0, 0, 230), "ambiguous": (0, 200, 230),
                "unknown": (130, 130, 130), "unchanged": (90, 90, 90)}


def annotate_pair(pair_dir: str | Path, answer: dict, config: dict, out_path: str | Path) -> Path | None:
    """Draw zones and candidate boxes on the rectified after-image."""
    pair_dir = Path(pair_dir)
    out_path = Path(out_path)
    result = load_result(pair_dir / "result.json")
    files = result.get("output_files") or {}
    after_path = pair_dir / files.get("after_rectified", "after_rectified.jpg")
    image = cv2.imread(str(after_path))
    if image is None or not isinstance(result.get("rectified"), dict):
        return None
    rectified = result["rectified"]
    zones = [(tray["id"], tray, ZONE_COLOR) for tray in config["trays"]]
    if config["device_area"] is not None:
        zones.append(("device", config["device_area"], DEVICE_COLOR))
    for zone_id, zone, color in zones:
        x, y, w, h = mm_to_px(zone["rect_frame_mm"], rectified)
        cv2.rectangle(image, (x, y), (x + w, y + h), color, 2)
        cv2.putText(image, zone_id, (x + 3, max(15, y - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    verdicts = {tray["tray_id"]: tray for tray in answer["trays"]}
    if answer.get("device"):
        verdicts["device"] = answer["device"]
    for zone_report in verdicts.values():
        for hit in zone_report["candidates"]:
            x, y, w, h = mm_to_px(hit["bbox_mm"], rectified)
            color = EVENT_COLORS.get(hit["event"], (255, 255, 255))
            cv2.rectangle(image, (x, y), (x + w, y + h), color, 2)
            cv2.putText(image, f"{hit['region']}:{hit['event']}", (x + 3, y + h - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), image)
    return out_path
