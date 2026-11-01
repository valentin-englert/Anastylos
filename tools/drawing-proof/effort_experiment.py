"""EFF-1: reasoning effort high vs max on the frozen part-box request, plus a prompt-cache probe (specs/plan_eff1.json).

    python tools/drawing-proof/effort_experiment.py --out C:\\tmp\\anastylos-drawing-proof\\eff1
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

import cv2
import numpy as np

import muse_sam
import score_mask

R = Path(r"C:\tmp\anastylos-drawing-proof")
PARTS = {"F0-front-p3-f300": "front shell of the game controller (the large grey dog-bone part)",
         "T1-front-p3-f240": "front shell of the game controller (the large grey dog-bone part)",
         "T2-front-p3-f360": "front shell of the game controller (the large grey dog-bone part)",
         "T3-front-empty-aftermath": "front shell of the game controller (the large grey dog-bone part)",
         "T4-dpad-membrane-aftermath": "white D-pad rubber membrane", "T5-shoulder-piece-p3-f300": "grey shoulder button piece"}


def rectified(name: str) -> Path:
    return R / ("run-front-r1" if name.startswith("F0") else f"transfer-r1/{name}") / "rectified.png"


def reference_box(name: str, shape) -> list[int]:
    mask_png = sorted((R / "sam-r3" / name).glob("**/mask_*.png"))[0]
    boxes = score_mask._record_boxes(mask_png.parent)
    mask = score_mask.place_mask(cv2.imread(str(mask_png), cv2.IMREAD_GRAYSCALE), boxes.get(mask_png.name), shape)
    ys, xs = np.nonzero(mask)
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]


def iou(a, b) -> float:
    inter = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    area = lambda r: (r[2] - r[0]) * (r[3] - r[1])  # noqa: E731
    return inter / max(area(a) + area(b) - inter, 1)


def run(out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    key = muse_sam.client.load_key()
    rows = []
    for name, part in PARTS.items():
        image = cv2.imread(str(rectified(name)))
        ref = reference_box(name, image.shape[:2])
        for repeat in (1, 2):
            for effort in ("max", "high"):
                try:
                    box = muse_sam.muse_box(image, part, key, effort=effort)
                except Exception as exc:  # recorded as a failed call
                    rows.append({"image": name, "effort": effort, "repeat": repeat, "error": f"{type(exc).__name__}: {str(exc)[:160]}"})
                    continue
                usage = box.get("usage") or {}
                rows.append({"image": name, "effort": effort, "repeat": repeat, "valid": box["valid"], "box": box["box_full"],
                             "fully_visible": box["answer"]["fully_visible"], "latency_s": box["latency_s"],
                             "effort_returned": box["effort_returned"], "output_tokens": usage.get("output_tokens"),
                             "reasoning_tokens": (usage.get("output_tokens_details") or {}).get("reasoning_tokens"),
                             "iou_reference": round(iou(box["box_full"], ref), 4) if box["valid"] else None})
                print(json.dumps(rows[-1]), flush=True)
    summary = {}
    for effort in ("max", "high"):
        mine = [r for r in rows if r["effort"] == effort and r.get("valid")]
        pairs = [(a, b) for a in mine for b in mine if a["image"] == b["image"] and a["repeat"] == 1 and b["repeat"] == 2]
        summary[effort] = {"valid": f"{len(mine)}/12", "median_latency_s": statistics.median(r["latency_s"] for r in mine) if mine else None,
                           "median_output_tokens": statistics.median(r["output_tokens"] for r in mine) if mine else None,
                           "median_iou_reference": statistics.median(r["iou_reference"] for r in mine) if mine else None,
                           "median_repeat_iou": statistics.median(iou(a["box"], b["box"]) for a, b in pairs) if pairs else None}
    image = cv2.imread(str(rectified("F0-front-p3-f300")))
    cache = []
    for attempt in range(3):
        box = muse_sam.muse_box(image, PARTS["F0-front-p3-f300"], key, cache_key="anastylos-part-box-r4")
        details = (box.get("usage") or {}).get("input_tokens_details") or {}
        cache.append({"attempt": attempt + 1, "latency_s": box["latency_s"], "input_tokens": (box.get("usage") or {}).get("input_tokens"),
                      "cached_tokens": details.get("cached_tokens")})
        print(json.dumps(cache[-1]), flush=True)
    result = {"plan": "specs/plan_eff1.json", "rows": rows, "summary": summary, "cache": cache}
    (out / "eff1.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.out)["summary"], indent=1))


if __name__ == "__main__":
    main()
