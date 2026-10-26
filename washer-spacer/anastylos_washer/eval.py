"""Labelled-image evaluation for washers and spacers.

Labels file schema:
{"images": [{"file": "crop.png", "view": "top", "family": "flat_washer",
             "class": "flat_washer", "count": 1, "pixels_per_mm": 10.0,
             "outer_mm": 22.0, "inner_mm": 11.0, "thickness_mm": 2.8,
             "length_mm": 20.0}]}

`family` is an optional operator hint passed to the pipeline.
`pixels_per_mm` enables millimetre error statistics for the
dimensions present. Ambiguity is a valid finding, so undecided
rows name `truth_kept` too. Synthetic fixtures prove the harness
only.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2

from anastylos_washer.annotate import annotate
from anastylos_washer.vision import analyze

DIM_FIELDS = (("outer_mm", "outer_d"), ("inner_mm", "inner_d"),
              ("thickness_mm", "thickness_px"), ("length_mm", "length_px"),
              ("across_corners_mm", "across_corners"),
              ("across_flats_mm", "across_flats"))


def _errors_for(result: dict, entry: dict) -> dict:
    mm = {item["observation_ref"]: item["dimensions_mm"]
          for item in (result.get("geometry_mm") or {}).get("observations",
                                                            [])}
    errors = {}
    if len(result["observations"]) != 1:
        return errors
    dims = mm.get(result["observations"][0]["observation_id"], {})
    for label_key, dim_key in DIM_FIELDS:
        if label_key in entry and dim_key in dims:
            errors[label_key] = round(abs(entry[label_key]
                                          - dims[dim_key]["value_mm"]), 3)
    return errors


def run_eval(labels_path: str | Path, images_dir: str | Path,
             out_dir: str | Path) -> dict:
    """Score classes and dimensions; writes report + overlays."""
    labels_path, images_dir, out_dir = (Path(labels_path),
                                        Path(images_dir), Path(out_dir))
    images = json.loads(labels_path.read_text(encoding="utf-8-sig"))["images"]
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = {"exact": 0, "misses": 0, "false_confident": 0, "undecided": 0}
    count_rows = {"count_exact": 0, "count_wrong": 0}
    dim_errors: dict[str, list] = {key: [] for key, _ in DIM_FIELDS}
    details = []
    for entry in images:
        image = cv2.imread(str(images_dir / entry["file"]))
        if image is None:
            raise ValueError(f"cannot read eval image {entry['file']}")
        metadata = {"pixels_per_mm": entry["pixels_per_mm"]} \
            if "pixels_per_mm" in entry else None
        result = analyze(image, entry["view"], family=entry.get("family"),
                         frame_metadata=metadata)
        first = result["observations"][0] if result["observations"] else None
        top = first["top_family"] if first else None
        class_ok = top == entry.get("class")
        count_ok = ("count" not in entry
                    or len(result["observations"]) == entry["count"])
        if "count" in entry:
            count_rows["count_exact" if count_ok else "count_wrong"] += 1
        status = first["status"] if first else result["status"]
        if status == "ok" and class_ok and count_ok:
            rows["exact"] += 1
            outcome = "exact"
        elif status in ("unknown", "unavailable"):
            rows["misses"] += 1
            outcome = "miss"
        elif status == "ok":
            rows["false_confident"] += 1
            outcome = "false_confident"
        else:
            rows["undecided"] += 1
            outcome = "undecided"
        truth_kept = any(item["family"] == entry.get("class")
                         for item in (first["candidates"] if first else []))
        errors = _errors_for(result, entry)
        for key, value in errors.items():
            dim_errors[key].append(value)
        details.append({"file": entry["file"], "class": entry.get("class"),
                        "top": top, "status": status, "outcome": outcome,
                        "count_ok": count_ok, "truth_kept": truth_kept,
                        "dim_errors_mm": errors})
        cv2.imwrite(str(out_dir / f"{Path(entry['file']).stem}_annotated.png"),
                    annotate(image, result))
    dim_stats = {}
    for key, values in dim_errors.items():
        if values:
            dim_stats[key] = {"n": len(values),
                              "mean_abs_mm": round(sum(values) / len(values),
                                                   3),
                              "max_abs_mm": round(max(values), 3)}
    report = {"images": len(details), **rows, **count_rows,
              "dim_errors_mm": dim_stats, "rows": details,
              "note": ("Scores describe this labelled set only; synthetic "
                       "fixtures prove the harness, not accuracy. No "
                       "real-hardware validation performed.")}
    (out_dir / "eval_report.json").write_text(json.dumps(report, indent=2)
                                              + "\n", encoding="utf-8")
    return report
