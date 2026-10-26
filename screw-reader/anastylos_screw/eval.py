"""Labelled-image evaluation for screw-head classification.

Labels file schema:
{"images": [{"file": "crop.png", "class": "phillips"}]}
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2

from anastylos_screw.annotate import annotate
from anastylos_screw.vision import analyze


def run_eval(labels_path: str | Path, images_dir: str | Path, out_dir: str | Path) -> dict:
    """Score classifications against ground truth; writes report + overlays."""
    labels_path, images_dir, out_dir = Path(labels_path), Path(images_dir), Path(out_dir)
    images = json.loads(labels_path.read_text(encoding="utf-8-sig"))["images"]
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = {"exact": 0, "misses": 0, "false_confident": 0, "undecided": 0}
    details = []
    for entry in images:
        image = cv2.imread(str(images_dir / entry["file"]))
        if image is None:
            raise ValueError(f"cannot read eval image {entry['file']}")
        result = analyze(image)
        top = result["candidates"][0]["class"] if result["candidates"] else None
        if top == entry["class"] and result["status"] == "ok":
            rows["exact"] += 1
            outcome = "exact"
        elif result["status"] == "unknown":
            rows["misses"] += 1
            outcome = "miss"
        elif result["status"] == "ok":
            rows["false_confident"] += 1
            outcome = "false_confident"
        else:
            rows["undecided"] += 1
            outcome = "undecided"
        details.append({"file": entry["file"], "class": entry["class"],
                        "top": top, "status": result["status"], "outcome": outcome})
        cv2.imwrite(str(out_dir / f"{Path(entry['file']).stem}_annotated.png"), annotate(image, result))
    report = {"images": len(details), **rows, "rows": details,
              "note": "Scores describe this labelled set only; synthetic fixtures prove the harness, not accuracy."}
    (out_dir / "eval_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
