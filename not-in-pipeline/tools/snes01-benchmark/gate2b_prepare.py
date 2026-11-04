"""Gate 2B preparation: candidate pairs + operator annotation package.

Derives correspondence CANDIDATES from frozen machine outputs by fixed
deterministic rules. Assigns NO verdicts, NO identities, NO classes:
truth comes only from the operator's sealed answer key. The model will
never see the key; the key is sealed BEFORE any Gate 2B inference.

Pair sourcing (neutral descriptions, not truth claims):
  P01-P04 same-cell-time: E12 tray-band appearance boxes (first 4
          non-occluded by region id), same box cropped at frame_0151
          (t=150) and frame_0209 (t=208).
  P05-P06 cross-cell:     pairs of distinct E12 tray boxes, both
          cropped at frame_0209 (simultaneously visible objects).
  P07-P08 occluded-clear: first two E2/E3 disappearance regions with
          before-frame skin fraction > 0.5 (event order), each vs the
          clear E2-r8 appearance box at frame_0026.
  P09 rotation:           Gate 2A B1 boxes (brown @0096 vs blue @0209),
          read programmatically from cases_diag.json.
  S01 sanity:             identical file twice (technical check,
          EXCLUDED from scoring, labeled as such everywhere).

Outputs to --out: pairs.json (manifest, verdict-free), crops/,
context/, annotate_sheet.jpg, answer_key_TEMPLATE.json (empty),
WORKFLOW.md (seal-then-run order). Offline; verifies frame hashes.

Usage:
    python gate2b_prepare.py --candidates <frozen gate1> ^
        --diag <gate2a cases_diag> --frames-dir <frames-1fps> ^
        --manifest <source_manifest> --out <gate2b/annotate>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from pathlib import Path

try:
    import cv2
    import numpy as np
except ImportError as exc:  # pragma: no cover
    sys.exit(f"gate2b_prepare needs OpenCV + NumPy: {exc}")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate2a_run as g2a

SCHEMA = "snes01-gate2b/pairs@1"
KEY_SCHEMA = "snes01-gate2b/answer-key@1"
TRAY_BAND_Y = 3100


def _die(message: str):
    print(f"gate2b_prepare: ERROR: {message}", file=sys.stderr)
    sys.exit(2)


def centroid(box) -> list:
    return [box[0] + box[2] / 2.0, box[1] + box[3] / 2.0]


def build_pairs(gate1: dict, diag: dict) -> list:
    events = {e["id"]: e for e in gate1.get("events", [])}
    e12 = events["E12"]
    tray = sorted(
        (r for r in e12["persistent_regions"]
         if r["role"] == "appearance" and not r["occluded"]
         and centroid(r["bbox_xywh"])[1] > TRAY_BAND_Y),
        key=lambda r: r["region_id"])[:4]
    if len(tray) < 4:
        _die("fewer than 4 clear E12 tray boxes")
    before, after = e12["before_frame"], e12["after_frame"]
    before_t = e12["before_t"]
    after_t = e12["after_t"]
    pairs = []
    for num, box in enumerate(tray, 1):
        pairs.append({
            "id": f"P{num:02d}", "sourcing": "same-cell-time",
            "src": {"frame": before, "t_s": before_t,
                    "bbox_xywh": box["bbox_xywh"],
                    "machine_region": box["region_id"]},
            "dest": {"frame": after, "t_s": after_t,
                     "bbox_xywh": box["bbox_xywh"],
                     "machine_region": box["region_id"]},
        })
    cross = [(tray[1], tray[2]), (tray[0], tray[3])]
    for num, (a, b) in enumerate(cross, 5):
        pairs.append({
            "id": f"P{num:02d}", "sourcing": "cross-cell",
            "src": {"frame": after, "t_s": after_t,
                    "bbox_xywh": a["bbox_xywh"],
                    "machine_region": a["region_id"]},
            "dest": {"frame": after, "t_s": after_t,
                     "bbox_xywh": b["bbox_xywh"],
                     "machine_region": b["region_id"]},
        })
    occluded = []
    for event in gate1["events"]:
        for region in event["persistent_regions"]:
            if (region["role"] == "disappearance"
                    and (region.get("skin_fraction_before") or 0) > 0.5):
                occluded.append((event, region))
    occluded.sort(key=lambda item: (item[0]["before_t"],
                                    item[1]["region_id"]))
    e2 = events["E2"]
    dest_r8 = next(r for r in e2["persistent_regions"]
                   if r["region_id"] == "E2-r8")
    for num, (event, region) in enumerate(occluded[:2], 7):
        pairs.append({
            "id": f"P{num:02d}", "sourcing": "occluded-clear",
            "src": {"frame": event["before_frame"],
                    "t_s": event["before_t"],
                    "bbox_xywh": region["bbox_xywh"],
                    "machine_region": region["region_id"]},
            "dest": {"frame": e2["after_frame"], "t_s": e2["after_t"],
                     "bbox_xywh": dest_r8["bbox_xywh"],
                     "machine_region": "E2-r8"},
        })
    b1 = next(c for c in diag["cases"] if c["id"] == "B1")
    pairs.append({
        "id": "P09", "sourcing": "rotation-opposite-faces",
        "src": {"frame": b1["source"]["frame"], "t_s": b1["source"]["t_s"],
                "bbox_xywh": b1["source"]["bbox_xywh"],
                "machine_region": None,
                "note": "human box from Gate 2A B1 (disclosed)"},
        "dest": {"frame": b1["dest"]["frame"], "t_s": b1["dest"]["t_s"],
                 "bbox_xywh": b1["dest"]["bbox_xywh"],
                 "machine_region": None,
                 "note": "human box from Gate 2A B1 (disclosed)"},
    })
    return pairs


WORKFLOW = """# Gate 2B sealed answer-key workflow

1. Open `annotate_sheet.jpg`: 9 candidate pairs (P01-P09) + 1 technical
   sanity pair (S01, identical image twice, EXCLUDED from scoring).
   Each panel shows source crop, dest crop, frame ids, timestamps,
   boxes, and crop hashes. Full-frame context is in `context/`.
2. For EACH pair, decide from the pixels alone:
   - `same`: both crops show the same physical component instance.
   - `different`: demonstrably different components.
   - `ambiguous`: same-instance cannot be justified (occlusion,
     lookalikes, opposite faces without linkage, too little detail).
3. Fill a COPY of `answer_key_TEMPLATE.json`: set status to "sealed",
   fill annotator + annotated_date, one verdict per pair + a short
   identity_basis note citing visible features. Leave S01 verdict
   "same" ONLY as a technical checkmark (it never enters scoring).
4. Save as `gate2b_answerkey.sealed.json` in this directory.
5. Then (and only then) run the frozen inference:
   `gate2b_run.py run --pairs pairs.json --key
   gate2b_answerkey.sealed.json --model muse-spark-1.3-contributor ...`
   The runner refuses unsealed keys and never shows the key to the
   model. Annotate BEFORE any Gate 2B inference on these pairs.
"""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build annotation pack.")
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--diag", required=True)
    parser.add_argument("--frames-dir", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        gate1 = json.loads(Path(args.candidates).read_text(
            encoding="utf-8"))
        diag = json.loads(Path(args.diag).read_text(encoding="utf-8"))
        manifest = json.loads(Path(args.manifest).read_text(
            encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _die(f"cannot read input: {exc}")
    if gate1.get("schema") != "snes01-gate1/candidates@1":
        _die("not frozen Gate 1 candidates")
    file_hash = {f["name"]: f["sha256"]
                 for f in manifest["frames"]["files"]}
    frames_dir = Path(args.frames_dir)
    out = Path(args.out)
    (out / "crops").mkdir(parents=True, exist_ok=True)
    (out / "context").mkdir(parents=True, exist_ok=True)
    pairs = build_pairs(gate1, diag)
    for pair in pairs:
        for role in ("src", "dest"):
            spec = pair[role]
            frame_path = frames_dir / spec["frame"]
            digest = g2a.sha256_file(frame_path)
            if digest != file_hash.get(spec["frame"]):
                _die(f"{pair['id']}: {spec['frame']} != manifest")
            image = cv2.imread(str(frame_path))
            crop = g2a.cut_crop(image, spec["bbox_xywh"])
            name = f"{pair['id']}_{role}.png"
            cv2.imwrite(str(out / "crops" / name), crop)
            spec["file"] = f"crops/{name}"
            spec["frame_sha256"] = digest
            spec["crop_sha256"] = g2a.sha256_file(out / "crops" / name)
            spec["crop_size_xy"] = [int(crop.shape[1]),
                                    int(crop.shape[0])]
    # Sanity pair reuses the P02 source crop file for both sides.
    p02 = next(p for p in pairs if p["id"] == "P02")
    sanity = {
        "id": "S01", "sourcing": "sanity-identical",
        "scored": False,
        "src": dict(p02["src"], file=p02["src"]["file"]),
        "dest": dict(p02["src"], file=p02["src"]["file"],
                     note="identical file as src (technical check)"),
    }
    (out / "pairs.json").write_text(json.dumps(
        {"schema": SCHEMA, "video_sha256": g2a.VIDEO_SHA256,
         "scored_pairs": [p["id"] for p in pairs],
         "pairs": pairs + [sanity]}, indent=2), encoding="utf-8")
    rows = []
    for pair in pairs + [sanity]:
        cells = []
        for role in ("src", "dest"):
            image = cv2.imread(
                str(out / pair[role]["file"]))
            height, width = image.shape[:2]
            scale = min(1.0, 360 / max(height, width))
            if scale < 1.0:
                image = cv2.resize(image, (int(width * scale),
                                           int(height * scale)))
            canvas = np.full((360, 480, 3), 20, np.uint8)
            h, w = image.shape[:2]
            canvas[:h, :w] = image
            spec = pair[role]
            cv2.putText(canvas, f"{role} {spec['frame']} t={spec['t_s']}s",
                        (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                        (255, 255, 255), 1)
            cv2.putText(canvas, f"box={spec['bbox_xywh']}", (8, 48),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1)
            cells.append(canvas)
        panel = np.full((360, 480, 3), 20, np.uint8)
        cv2.putText(panel, f"{pair['id']} [{pair['sourcing']}]", (8, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        verdict = "verdict: ___ (same/different/ambiguous)"
        if pair["id"] == "S01":
            verdict = "SANITY: identical files, NOT SCORED"
        cv2.putText(panel, verdict, (8, 70),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.putText(panel, "identity_basis: ___", (8, 110),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cells.append(panel)
        rows.append(np.hstack(cells))
    cv2.imwrite(str(out / "annotate_sheet.jpg"), np.vstack(rows),
                [cv2.IMWRITE_JPEG_QUALITY, 88])
    key_id = f"gate2b-{uuid.uuid4().hex[:8]}"
    (out / "answer_key_TEMPLATE.json").write_text(json.dumps(
        {"schema": KEY_SCHEMA, "annotation_set_id": key_id,
         "status": "template",
         "instructions": "Fill verdict per pair from pixels alone; "
                         "set status to sealed; record annotator/date. "
                         "S01 is a technical check, never scored.",
         "annotator": "", "annotated_date": "",
         "verdicts": [
             {"pair_id": p["id"], "verdict": "",
              "identity_basis": "", "info_sufficient": ""}
             for p in pairs + [sanity]]}, indent=2), encoding="utf-8")
    (out / "WORKFLOW.md").write_text(WORKFLOW, encoding="utf-8")
    print(json.dumps({"pairs": len(pairs), "key_id": key_id,
                      "out": str(out)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
