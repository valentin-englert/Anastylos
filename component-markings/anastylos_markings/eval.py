"""Labelled-image evaluation for component-marking readings.

Labels file schema:
{"images": [{"file": "crop.png", "marking": "LM358N", "status": "ok"}]}
`marking` is the expected top-line match text (null skips the text
check); `status` is the expected verdict and defaults to "ok".
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2

from anastylos_markings.annotate import annotate
from anastylos_markings.vision import analyze


def run_eval(labels_path: str | Path, images_dir: str | Path, out_dir: str | Path,
             backend, mapping=None) -> dict:
    """Score readings against ground truth; writes report + overlays."""
    labels_path, images_dir, out_dir = Path(labels_path), Path(images_dir), Path(out_dir)
    images = json.loads(labels_path.read_text(encoding="utf-8-sig"))["images"]
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = {"exact": 0, "misses": 0, "false_confident": 0, "undecided": 0}
    details = []
    for entry in images:
        image = cv2.imread(str(images_dir / entry["file"]))
        if image is None:
            raise ValueError(f"cannot read eval image {entry['file']}")
        result = analyze(image, backend, mapping=mapping, image_name=entry["file"])
        top = result["candidates"][0]["match"] if result["candidates"] else None
        want_marking = entry.get("marking")
        want_status = entry.get("status", "ok")
        if result["status"] == want_status and (want_marking is None or top == want_marking):
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
        details.append({"file": entry["file"], "marking": want_marking,
                        "want_status": want_status, "top": top,
                        "status": result["status"], "outcome": outcome})
        cv2.imwrite(str(out_dir / f"{Path(entry['file']).stem}_annotated.png"), annotate(image, result))
    report = {"images": len(details), **rows, "rows": details,
              "note": "Scores describe this labelled set only; stub fixtures prove the harness, not accuracy."}
    (out_dir / "eval_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
