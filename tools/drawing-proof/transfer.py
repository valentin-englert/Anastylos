"""Run the frozen baseline on every target of a transfer plan; no per-target tuning.

    python tools/drawing-proof/transfer.py --plan tools/drawing-proof/specs/transfer_plan_r1.json --out C:\\tmp\\anastylos-drawing-proof\\transfer-r1
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import proof
import source

GENERIC_TEXT = {
    "title": "Part outline (drawing proof, frozen baseline)",
    "legend": ["Solid outline and fills: observed in this image; sheet-plane mm.",
               "Not drawn: heights, hidden sides; no identity or orientation is confirmed."],
    "geometry_classes": {"observed_outline": "mask boundary, Douglas-Peucker", "observed_regions": "light tone regions"},
    "unknown": ["heights", "hidden sides", "identity", "orientation"],
}


def run(plan_path: Path, out: Path) -> list[dict]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    baseline = json.loads((plan_path.parent / plan["baseline"]).read_text(encoding="utf-8"))
    rows = []
    for target in plan["targets"]:
        spec = {**baseline, **GENERIC_TEXT, "target_id": target["id"], "video": target["video"],
                "frame_index": target["frame_index"], "region_mm": target["region_mm"],
                "subtitle": f'{target["id"]}: {target["kind"]}'}
        try:
            report = proof.build(spec, out / target["id"])
        except source.SourceError as exc:  # registration or read failure is a result, not a crash
            rows.append({"id": target["id"], "status": "failed", "reason": str(exc)})
            continue
        rows.append({"id": target["id"], "status": report["status"], "flags": report["quality"]["flags"],
                     "measures": report["quality"]["measures"], "total_s": report["timings_s"]["total_s"]})
    (out / "summary.json").write_text(json.dumps({"plan": str(plan_path), "baseline_revision": baseline["revision"], "results": rows}, indent=1), encoding="utf-8")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    for row in run(args.plan, args.out):
        print(json.dumps(row))


if __name__ == "__main__":
    main()
