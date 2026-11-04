"""Annotated diagnostic overlays for cable analyses. OpenCV only.

Draws centerlines, endpoints, junction features, occluded regions,
uncertain hypotheses, clips, anchors and channels over the source crop.
Colors distinguish visible evidence (solid) from hypotheses (dotted).
"""

from __future__ import annotations

import cv2

CABLE_COLORS = [(60, 60, 220), (220, 120, 40), (40, 170, 60),
                (200, 60, 200), (60, 200, 200), (120, 120, 220)]
ENDPOINT_COLORS = {"free": (0, 200, 0), "frame_edge": (0, 180, 230),
                   "connector_proximity": (220, 120, 40),
                   "junction": (150, 150, 150)}
FEATURE_COLORS = {"branch": (0, 140, 255), "tap": (255, 0, 255),
                  "crossing": (0, 0, 255), "overlap": (130, 130, 130)}


def _shift(point, origin):
    return (int(round(point[0] - origin[0])), int(round(point[1] - origin[1])))


def annotate(image, result: dict, origin=(0, 0)):
    """Draw the analysis over a BGR image. Returns a new image."""
    overlay = image.copy()
    for index, cable in enumerate(result.get("cables", [])):
        color = CABLE_COLORS[index % len(CABLE_COLORS)]
        for branch in cable.get("branches", []):
            points = [(_shift(point, origin))
                      for point in branch.get("polyline_xy", [])]
            if len(points) >= 2:
                for i in range(len(points) - 1):
                    cv2.line(overlay, points[i], points[i + 1], color, 2)
            for end in branch.get("ends", []):
                if end.get("kind") == "junction":
                    cv2.circle(overlay, _shift(end["xy"], origin), 4,
                               ENDPOINT_COLORS["junction"], -1)
        box = cable.get("bbox_xywh")
        if box:
            corner = _shift(box[:2], origin)
            cv2.rectangle(overlay, corner,
                          (corner[0] + int(box[2]), corner[1] + int(box[3])),
                          color, 1)
            cv2.putText(overlay, cable.get("cable_id", ""), corner,
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    for endpoint in result.get("endpoints", []):
        color = ENDPOINT_COLORS.get(endpoint.get("type"), (255, 255, 255))
        center = _shift(endpoint["xy"], origin)
        cv2.circle(overlay, center, 5, color, 2)
        cv2.putText(overlay, endpoint.get("endpoint_id", ""), center,
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)
    for feature in result.get("features", []):
        color = FEATURE_COLORS.get(feature.get("kind"), (255, 255, 255))
        center = _shift(feature["xy"], origin)
        cv2.drawMarker(overlay, center, color, cv2.MARKER_TILTED_CROSS, 16, 2)
        cv2.putText(overlay,
                    f"{feature.get('feature_id', '')}:{feature.get('kind', '')}",
                    (center[0] + 10, center[1]),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)
    for region in result.get("occluded_regions", []):
        box = region.get("bbox_xywh", [0, 0, 0, 0])
        corner = _shift(box[:2], origin)
        cv2.rectangle(overlay, corner,
                      (corner[0] + int(box[2]), corner[1] + int(box[3])),
                      (0, 255, 255), 1, cv2.LINE_4)
    by_id = {item.get("endpoint_id"): item
             for item in result.get("endpoints", [])}
    for hypothesis in result.get("hypotheses", []):
        pair = [by_id.get(ref) for ref in hypothesis.get("endpoints", [])]
        if all(item is not None for item in pair):
            cv2.line(overlay, _shift(pair[0]["xy"], origin),
                     _shift(pair[1]["xy"], origin), (0, 255, 255), 1,
                     cv2.LINE_4)
    for key, color in (("clips", (255, 255, 255)),
                       ("anchors", (220, 120, 40))):
        for blob in result.get(key, []):
            box = blob.get("bbox_xywh", [0, 0, 0, 0])
            corner = _shift(box[:2], origin)
            cv2.rectangle(overlay, corner,
                          (corner[0] + int(box[2]), corner[1] + int(box[3])),
                          color, 2)
    for channel in result.get("channels", []):
        cv2.line(overlay, _shift(channel["p1"], origin),
                 _shift(channel["p2"], origin), (255, 255, 0), 1)
    status = f"{result.get('status', '')}: {result.get('explanation', '')}"
    cv2.putText(overlay, status[:100], (8, 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return overlay
