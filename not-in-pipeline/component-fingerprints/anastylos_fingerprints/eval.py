"""Labelled retrieval evaluation for component fingerprints.

Labels file schema:
{"images": [{"file": "query.png", "expect_id": "ic-01 or null",
             "roi": [x, y, w, h], "frame_meta": {"px_per_mm": 4.0},
             "marking": "358"}]}
`expect_id` names the gallery record the query shows, or null for a
query that matches nothing enrolled. Counts describe this labelled
set only; synthetic fixtures prove the harness, not accuracy.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2

from anastylos_fingerprints.annotate import annotate
from anastylos_fingerprints.describe import fingerprint
from anastylos_fingerprints.match import match_gallery
from anastylos_fingerprints.store import load_gallery


def _roi(entry):
    roi = entry.get("roi")
    return tuple(roi) if roi else None


def run_eval(labels_path, images_dir, gallery_dir, out_dir, top_k=5) -> dict:
    """Score retrieval against ground truth; writes report + overlays."""
    labels_path, images_dir, out_dir = Path(labels_path), Path(images_dir), Path(out_dir)
    images = json.loads(labels_path.read_text(encoding="utf-8-sig"))["images"]
    gallery = load_gallery(gallery_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = {"exact": 0, "misses": 0, "false_confident": 0, "undecided": 0}
    top1_hits = topk_hits = expected = 0
    details = []
    for entry in images:
        image = cv2.imread(str(images_dir / entry["file"]))
        if image is None:
            raise ValueError(f"cannot read eval image {entry['file']}")
        query = fingerprint(image, _roi(entry), entry.get("frame_meta"),
                            marking=entry.get("marking"),
                            source={"image": entry["file"]})
        result = match_gallery(query, gallery["records"], gallery["excluded"], top_k=top_k)
        top = result["candidates"][0]["id"] if result["candidates"] else None
        want = entry.get("expect_id")
        if want is None:
            if result["status"] == "ok":
                rows["false_confident"] += 1
                outcome = "false_confident"
            else:
                rows["exact"] += 1
                outcome = "exact"
        else:
            expected += 1
            in_topk = any(item["id"] == want for item in result["candidates"])
            topk_hits += in_topk
            if result["status"] == "ok" and top == want:
                rows["exact"] += 1
                top1_hits += 1
                outcome = "exact"
            elif result["status"] == "ok":
                rows["false_confident"] += 1
                outcome = "false_confident"
            elif result["status"] in ("unknown", "rejected"):
                rows["misses"] += 1
                outcome = "miss"
            else:
                rows["undecided"] += 1
                outcome = "undecided"
        details.append({"file": entry["file"], "expect_id": want, "top": top,
                        "status": result["status"], "outcome": outcome})
        cv2.imwrite(str(out_dir / f"{Path(entry['file']).stem}_annotated.png"),
                    annotate(image, result, _roi(entry)))
    report = {"images": len(details), **rows, "rows": details,
              "retrieval": {"expected": expected, "top1": top1_hits,
                            "topk": topk_hits, "k": top_k},
              "note": "Scores describe this labelled set only; synthetic fixtures prove the harness, not accuracy."}
    (out_dir / "eval_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
