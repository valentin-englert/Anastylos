"""Labelled-image evaluation for screw measurement.

Labels file schema::

    {"images": [{"file": "screw.png", "mode": "top",
                 "truth_mm": {"visible_head_diameter": 20.0},
                 "truth_px": {"visible_head_diameter": 200.0}}]}

Millimetre errors are reported only where the run calibration is valid;
otherwise the row records mm_unavailable. Pixel errors are always scored
against truth_px when present.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2

from anastylos_screw_measure.annotate import annotate
from anastylos_screw_measure.calibration import blank
from anastylos_screw_measure.vision import analyze

TRUTH_KEYS = ("visible_head_diameter", "visible_overall_length",
              "visible_shaft_length", "visible_shaft_diameter", "visible_head_length")


def _entry(result: dict, name: str) -> dict | None:
    for item in result.get("measurements", []):
        if item.get("name") == name:
            return item
    return None


def _stats(errors: list[float]) -> dict:
    if not errors:
        return {"n": 0, "mean": None, "max": None}
    return {"n": len(errors), "mean": round(sum(errors) / len(errors), 4),
            "max": round(max(errors), 4)}


def run_eval(labels_path: str | Path, images_dir: str | Path,
             out_dir: str | Path, calibration: dict | None = None) -> dict:
    """Score measurements against ground truth; writes report + overlays."""
    labels_path, images_dir, out_dir = Path(labels_path), Path(images_dir), Path(out_dir)
    try:
        images = json.loads(labels_path.read_text(encoding="utf-8-sig"))["images"]
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read labels {labels_path}: {exc}") from exc
    calibration = calibration or blank("no calibration supplied; pixel-only output")
    out_dir.mkdir(parents=True, exist_ok=True)
    mm_errors: dict[str, list[float]] = {k: [] for k in TRUTH_KEYS}
    px_errors: dict[str, list[float]] = {k: [] for k in TRUTH_KEYS}
    verdicts = {"ok": 0, "ambiguous": 0, "unknown": 0}
    rows = []
    for item in images:
        image = cv2.imread(str(images_dir / item["file"]))
        if image is None:
            raise ValueError(f"cannot read eval image {item['file']}")
        result = analyze(image, mode=item.get("mode", "top"),
                         roi=tuple(item["roi"]) if item.get("roi") else None,
                         calibration=calibration)
        verdicts[result["verdict"]] = verdicts.get(result["verdict"], 0) + 1
        row = {"file": item["file"], "mode": result["mode"],
               "verdict": result["verdict"], "errors": {}}
        for key in TRUTH_KEYS:
            entry = _entry(result, key)
            truth_mm = (item.get("truth_mm") or {}).get(key)
            truth_px = (item.get("truth_px") or {}).get(key)
            cell: dict = {}
            if truth_mm is not None:
                if entry and entry.get("value_mm") is not None:
                    err = abs(entry["value_mm"] - truth_mm)
                    mm_errors[key].append(err)
                    cell["abs_err_mm"] = round(err, 4)
                else:
                    cell["abs_err_mm"] = "mm_unavailable"
            if truth_px is not None:
                if entry and entry.get("value_px") is not None:
                    err = abs(entry["value_px"] - truth_px)
                    px_errors[key].append(err)
                    cell["abs_err_px"] = round(err, 3)
                else:
                    cell["abs_err_px"] = "unmeasured"
            if cell:
                row["errors"][key] = cell
        rows.append(row)
        cv2.imwrite(str(out_dir / f"{Path(item['file']).stem}_annotated.png"),
                    annotate(image, result))
        (out_dir / f"{Path(item['file']).stem}_result.json").write_text(
            json.dumps({**result, "image": item["file"]}, indent=2) + "\n", encoding="utf-8")
    report = {"images": len(rows), "verdicts": verdicts,
              "mm_abs_err": {k: _stats(v) for k, v in mm_errors.items() if v},
              "px_abs_err": {k: _stats(v) for k, v in px_errors.items() if v},
              "calibration_valid": bool(calibration.get("valid")),
              "rows": rows,
              "note": ("Errors describe this labelled set only; synthetic fixtures "
                       "prove the harness, not real-world accuracy.")}
    (out_dir / "eval_report.json").write_text(json.dumps(report, indent=2) + "\n",
                                              encoding="utf-8")
    return report
