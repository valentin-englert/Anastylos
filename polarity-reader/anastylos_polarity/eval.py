"""Labelled-image evaluation for polarity recognition.

Labels file schema:
{"images": [{"file": "crop.png", "marker": "band",
             "family": "diode", "angle_deg": 12.0}]}

`family` is an optional operator hint passed to the pipeline (the
normal workflow: the operator knows the part, the tool finds the
marker). `angle_deg` is the expected source-frame marker direction
(clockwise from +x); entries without it check the marker type only.
Ambiguity is a valid finding here, not an error: `undecided` rows
name whether the labelled marker stayed among the candidates.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2

from anastylos_polarity.annotate import annotate
from anastylos_polarity.vision import analyze

ANGLE_TOLERANCE_DEG = 25.0


def _angle_gap(left: float, right: float) -> float:
    return abs((left - right + 180.0) % 360.0 - 180.0)


def run_eval(labels_path: str | Path, images_dir: str | Path,
             out_dir: str | Path) -> dict:
    """Score readings against ground truth; writes report + overlays."""
    labels_path, images_dir, out_dir = (Path(labels_path),
                                        Path(images_dir), Path(out_dir))
    images = json.loads(labels_path.read_text(encoding="utf-8-sig"))["images"]
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = {"exact": 0, "misses": 0, "false_confident": 0, "undecided": 0}
    details = []
    for entry in images:
        image = cv2.imread(str(images_dir / entry["file"]))
        if image is None:
            raise ValueError(f"cannot read eval image {entry['file']}")
        result = analyze(image, family=entry.get("family"))
        top = result["candidates"][0] if result["candidates"] else None
        marker_ok = top is not None and top["marker_type"] == entry["marker"]
        angle_ok = True
        angle_error = None
        if "angle_deg" in entry and top is not None:
            angle_error = round(_angle_gap(top["orientation_angle_deg"],
                                           float(entry["angle_deg"])), 1)
            angle_ok = angle_error <= ANGLE_TOLERANCE_DEG
        truth_kept = any(item["marker_type"] == entry["marker"]
                         for item in result["candidates"])
        if top is not None and result["status"] == "ok" and marker_ok \
                and angle_ok:
            rows["exact"] += 1
            outcome = "exact"
        elif result["status"] in ("unknown", "unavailable"):
            rows["misses"] += 1
            outcome = "miss"
        elif result["status"] == "ok":
            rows["false_confident"] += 1
            outcome = "false_confident"
        else:
            rows["undecided"] += 1
            outcome = "undecided"
        details.append({"file": entry["file"], "marker": entry["marker"],
                        "top": top["marker_type"] if top else None,
                        "top_angle_deg": top["orientation_angle_deg"]
                        if top else None,
                        "angle_error_deg": angle_error,
                        "truth_kept": truth_kept, "status": result["status"],
                        "outcome": outcome})
        cv2.imwrite(str(out_dir / f"{Path(entry['file']).stem}_annotated.png"),
                    annotate(image, result))
    report = {"images": len(details), **rows, "rows": details,
              "angle_tolerance_deg": ANGLE_TOLERANCE_DEG,
              "note": ("Scores describe this labelled set only; synthetic "
                       "fixtures prove the harness, not accuracy. No "
                       "real-hardware validation performed.")}
    (out_dir / "eval_report.json").write_text(json.dumps(report, indent=2)
                                              + "\n", encoding="utf-8")
    return report
