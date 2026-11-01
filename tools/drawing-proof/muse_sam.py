"""Frozen part segmentation r3: Muse names a tight box for a described part, SAM segments that box.

    python tools/drawing-proof/muse_sam.py --manifest <images.json> --out <dir>

Manifest: {"images": [{"id", "path", "part"}]}. Pixel coordinates only; sheet rectification is the caller's
job (proof.py). Everything that shapes the result is a constant here (FROZEN) and is copied into each result.
Muse proposes a box only; it never sees or writes code, thresholds or geometry.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "sam-tracking"))
from anastylos_sam import client  # noqa: E402  (existing Meta transport, unchanged)

import segment  # noqa: E402

PROMPT = ("Image size {w}x{h} px. Find the {part}. Return the tightest axis-aligned box [left, top, right, bottom] in pixels "
          "of this image around that one part only (exclude shadows, printed lines and other parts). Set fully_visible false "
          "if any part of its outer edge is cut off or hidden. If the part is not in the image, set found false and box [0,0,0,0].")
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["found", "box", "fully_visible", "reason"],
          "properties": {"found": {"type": "boolean"},
                         "box": {"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4},
                         "fully_visible": {"type": "boolean"}, "reason": {"type": "string", "maxLength": 300}}}
FROZEN = {"revision": "r4 (r3 with the calibration-sheet sentence removed from the prompt)","muse_model": "muse-spark-1.3-contributor", "reasoning_effort": "max", "muse_max_side_px": 1024,
          "muse_jpeg_quality": 90, "prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
          "schema_sha256": hashlib.sha256(json.dumps(SCHEMA, sort_keys=True).encode()).hexdigest(),
          "sam_model": "sam-3.1", "sam_prompt": "one box = Muse box scaled back to full resolution",
          "mask_placement": "SAM mask raster resized into records[].box of the full image",
          "outer": "largest component, holes filled", "openings": "holes of the largest component >= 0.05 % of its area"}
SAM = REPO / "sam-tracking" / "sam.py"


def muse_box(image: np.ndarray, part: str, key: str) -> dict:
    h, w = image.shape[:2]
    scale = min(1.0, FROZEN["muse_max_side_px"] / max(h, w))
    small = cv2.resize(image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    ok, jpg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, FROZEN["muse_jpeg_quality"]])
    sw, sh = small.shape[1], small.shape[0]
    payload = {"model": FROZEN["muse_model"], "reasoning": {"effort": FROZEN["reasoning_effort"]},
               "input": [{"role": "user", "content": [{"type": "input_text", "text": PROMPT.format(w=sw, h=sh, part=part)},
                          {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(jpg.tobytes()).decode()}]}],
               "text": {"format": {"type": "json_schema", "name": "part_box", "strict": True, "schema": SCHEMA}}}
    data, meta = client.post_responses_once(payload, key, timeout_s=300)
    text = next(c.get("text") for o in data.get("output", []) if o.get("type") == "message" for c in o.get("content", []))
    answer = json.loads(text)
    box = answer["box"]
    valid = bool(answer["found"]) and 0 <= box[0] < box[2] <= sw and 0 <= box[1] < box[3] <= sh
    return {"answer": answer, "valid": valid, "box_full": [round(v / scale) for v in box] if valid else None,
            "latency_s": meta["latency_s"], "usage": data.get("usage"), "effort_returned": (data.get("reasoning") or {}).get("effort")}


def sam_mask(image_path: Path, box: list[int], shape: tuple[int, int], out: Path, ledger: Path) -> tuple[np.ndarray | None, dict]:
    out.mkdir(parents=True, exist_ok=True)
    (out / "objects.json").write_text(json.dumps({"width": shape[1], "height": shape[0], "objects": [{"id": 1, "box": box}]}))
    began = time.perf_counter()
    done = subprocess.run([sys.executable, "-B", str(SAM), "--live", "--upload-ok", "--budget-usd", "2.0", "--ledger", str(ledger),
                           "segment-image", "--image", str(image_path), "--objects-json", str(out / "objects.json"), "--out", str(out)],
                          capture_output=True, text=True, encoding="utf-8", timeout=600)
    info = {"exit": done.returncode, "wall_s": round(time.perf_counter() - began, 2), "stderr_tail": done.stderr.strip().splitlines()[-1:]}
    evidence = next(out.glob("*/evidence.json"), None)
    if done.returncode or evidence is None:
        return None, info
    records = json.loads(evidence.read_text(encoding="utf-8")).get("records", [])
    if not records:
        return None, {**info, "records": 0}
    record = max(records, key=lambda r: r.get("confidence") or 0)
    raw = cv2.imread(str(evidence.parent / record["mask"]["png"]), cv2.IMREAD_GRAYSCALE)
    b = record["box"]
    full = np.zeros(shape, np.uint8)
    full[b["top"]:b["bottom"], b["left"]:b["right"]] = cv2.resize(raw, (b["right"] - b["left"], b["bottom"] - b["top"]),
                                                                   interpolation=cv2.INTER_NEAREST)[:shape[0] - b["top"], :shape[1] - b["left"]]
    return full > 127, {**info, "confidence": record.get("confidence"), "sam_box": b}


def outer_and_openings(raw: np.ndarray) -> tuple[np.ndarray, list[np.ndarray]]:
    """Outer silhouette (filled) and the openings inside it, as separate results."""
    part = segment._largest_component(raw)
    outer = segment.fill_holes(part)
    holes = (outer & ~part).astype(np.uint8)
    contours, _ = cv2.findContours(holes, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    minimum = 0.0005 * outer.sum()
    return outer, [c.reshape(-1, 2).astype(float) for c in contours if cv2.contourArea(c) >= minimum]


def overlay(image: np.ndarray, outer: np.ndarray, openings: list[np.ndarray], box: list[int] | None) -> np.ndarray:
    view = image.copy()
    if box:
        cv2.rectangle(view, tuple(box[:2]), tuple(box[2:]), (255, 160, 0), 2)
    thickness = max(2, round(max(image.shape[:2]) / 600))
    cv2.polylines(view, [np.round(segment.boundary(outer)).astype(np.int32)], True, (0, 200, 0), thickness, cv2.LINE_AA)
    for opening in openings:
        cv2.polylines(view, [np.round(opening).astype(np.int32)], True, (0, 0, 230), thickness, cv2.LINE_AA)
    return view


def run(manifest: Path, out: Path) -> list[dict]:
    items = json.loads(manifest.read_text(encoding="utf-8"))["images"]
    key = client.load_key()
    rows = []
    for item in items:
        path = Path(item["path"])
        image = cv2.imread(str(path))
        row = {"id": item["id"], "part": item["part"], "frozen": FROZEN}
        try:
            row["muse"] = muse_box(image, item["part"], key)
        except Exception as exc:  # provider failure is a result
            rows.append({**row, "status": "PROVIDER_ERROR", "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
            continue
        if not row["muse"]["valid"]:
            rows.append({**row, "status": "MODEL_BOX_INVALID"})
            continue
        mask, row["sam"] = sam_mask(path, row["muse"]["box_full"], image.shape[:2], out / item["id"] / "sam", out / "ledger.jsonl")
        if mask is None:
            rows.append({**row, "status": "PROVIDER_ERROR"})
            continue
        outer, openings = outer_and_openings(mask)
        cv2.imwrite(str(out / item["id"] / "outer.png"), outer.astype(np.uint8) * 255)
        cv2.imwrite(str(out / item["id"] / "overlay.jpg"), overlay(image, outer, openings, row["muse"]["box_full"]))
        rows.append({**row, "status": "segmented", "outer_area_px": int(outer.sum()), "openings": len(openings),
                     "fully_visible_per_muse": row["muse"]["answer"]["fully_visible"]})
    (out / "results.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    for row in run(args.manifest, args.out):
        print(json.dumps({k: row.get(k) for k in ("id", "status", "openings", "fully_visible_per_muse")}))


if __name__ == "__main__":
    main()
