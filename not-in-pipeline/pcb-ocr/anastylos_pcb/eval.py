"""Small ground-truth evaluation workflow for real-photo scoring.

Labels file schema:
{"images": [{"file": "img.jpg", "labels": [{"text": "R12", "bbox_xywh": [x, y, w, h]}]}]}
Boxes are optional; text-only labels match on normalized text alone.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2

from anastylos_pcb.annotate import annotate
from anastylos_pcb.designators import parse
from anastylos_pcb.pipeline import analyze

MATCH_IOU = 0.25


def _iou(a: list, b: list) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def _match(label: dict, candidates: list[dict]) -> dict | None:
    want = parse(label["text"]).get("normalized") or label["text"].strip().upper()
    for item in candidates:
        if item["normalized"] != want:
            continue
        if label.get("bbox_xywh") and _iou(label["bbox_xywh"], item["bbox_xywh"]) < MATCH_IOU:
            continue
        return item
    return None


def run_eval(labels_path: str | Path, images_dir: str | Path, out_dir: str | Path,
             backend, frame_meta: dict | None = None) -> dict:
    """Score predictions against ground truth; writes a report plus overlays."""
    labels_path, images_dir, out_dir = Path(labels_path), Path(images_dir), Path(out_dir)
    images = json.loads(labels_path.read_text(encoding="utf-8-sig"))["images"]
    out_dir.mkdir(parents=True, exist_ok=True)
    rows, hits, total_labels, total_predicted = [], 0, 0, 0
    for entry in images:
        image = cv2.imread(str(images_dir / entry["file"]))
        if image is None:
            raise ValueError(f"cannot read eval image {entry['file']}")
        result = analyze(image, backend, frame_meta=frame_meta)
        matched_ids = set()
        label_rows = []
        for label in entry.get("labels", []):
            hit = _match(label, [c for c in result["candidates"] if c["id"] not in matched_ids])
            if hit:
                matched_ids.add(hit["id"])
                hits += 1
            label_rows.append({"label": label["text"], "hit": hit["id"] if hit else None})
        total_labels += len(label_rows)
        total_predicted += len(result["candidates"])
        false_positives = [c["normalized"] for c in result["candidates"] if c["id"] not in matched_ids]
        rows.append({"file": entry["file"], "status": result["status"], "labels": label_rows,
                     "false_positives": false_positives})
        cv2.imwrite(str(out_dir / f"{Path(entry['file']).stem}_annotated.png"), annotate(image, result))
    report = {
        "images": len(rows), "labels": total_labels, "predicted": total_predicted, "hits": hits,
        "recall": round(hits / total_labels, 3) if total_labels else None,
        "precision": round(hits / total_predicted, 3) if total_predicted else None,
        "backend": backend.name, "rows": rows,
        "note": "Scores describe this labelled set only; synthetic fixtures prove the harness, not accuracy.",
    }
    (out_dir / "eval_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
