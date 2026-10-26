"""Deterministic tray-counter regression tests. Needs the calibration virtualenv.

Fixtures are hand-built detection pairs (result.json plus flat rectified
images with exact brightness steps), so no camera or detection run is needed.

Run from the tray-counter directory:

    ..\\.venv\\Scripts\\python -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2
import numpy as np

from anastylos_trays.cli import main
from anastylos_trays.config import ConfigError, load_config
from anastylos_trays.counter import analyze_pair

PPM = 4
BASE = 200
DARK = 120
VIEW = "surface_a3"

CONFIG = {
    "trays": [
        {"id": "T1", "frame": VIEW, "rect_frame_mm": [10, 10, 26, 26]},
        {"id": "T2", "frame": VIEW, "rect_frame_mm": [40, 10, 26, 26]},
    ],
    "device_area": {"frame": VIEW, "rect_frame_mm": [10, 45, 40, 20]},
    "params": {"min_overlap": 0.5, "delta_threshold": 8.0, "surface": "light"},
}


def _px(rect_mm):
    return [int(v * PPM) for v in rect_mm]


def make_pair(folder: Path, pair_id: str, regions=(), blobs=(), view: str = VIEW,
              status: str | None = None, ext: str = "png", with_files: bool = True,
              registration_ok: bool = True) -> Path:
    """Write a fake detection pair. blobs: (rect_mm, value, 'before'|'after')."""
    pair_dir = folder / pair_id
    pair_dir.mkdir(parents=True, exist_ok=True)
    size = (100 * PPM, 75 * PPM)
    before = np.full((size[1], size[0], 3), BASE, np.uint8)
    after = before.copy()
    for rect_mm, value, when in blobs:
        x, y, w, h = _px(rect_mm)
        canvas = before if when == "before" else after
        canvas[y:y + h, x:x + w] = value
    names = {}
    if with_files:
        for key, canvas in (("before_rectified", before), ("after_rectified", after)):
            name = f"{key}.{ext}"
            cv2.imwrite(str(pair_dir / name), canvas)
            names[key] = name
    result = {
        "pair_id": pair_id,
        "status": status or ("no_change" if not regions else
                             "single_change" if len(regions) == 1 else "multiple_changes"),
        "regions": [{
            "label": label, "bbox_mm": bbox,
            "centroid_mm": [bbox[0] + bbox[2] / 2, bbox[1] + bbox[3] / 2],
            "area_mm2": bbox[2] * bbox[3], "sign": "darker_after",
        } for label, bbox in regions],
        "view": view,
        "rectified": {"view": view, "px_per_mm": PPM, "size_px": list(size),
                      "origin_frame_mm": [0, 0]},
        "registration_before": {"valid": registration_ok, "reprojection_rms_mm": 0.1},
        "registration_after": {"valid": registration_ok, "reprojection_rms_mm": 0.12},
        "output_files": names,
    }
    (pair_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
    return pair_dir / "result.json"


def write_config(folder: Path, config: dict = CONFIG) -> Path:
    path = folder / "trays.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def verdicts(answer):
    return {tray["tray_id"]: tray for tray in answer["trays"]}


class TrayCounterTest(unittest.TestCase):
    def test_addition_to_tray(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            target = make_pair(folder, "add_01", regions=[("R1", [12, 12, 10, 10])],
                               blobs=[([12, 12, 10, 10], DARK, "after")])
            answer = analyze_pair(target, load_config(write_config(folder)))
            tray = verdicts(answer)["T1"]
            self.assertEqual(tray["verdict"], "addition")
            (hit,) = tray["candidates"]
            self.assertEqual(hit["event"], "addition")
            self.assertEqual(hit["delta"], DARK - BASE)
            self.assertEqual(hit["overlap"], 1.0)
            self.assertIsNone(tray["count_estimate"])
            self.assertIn("evidence", hit)
            self.assertEqual(verdicts(answer)["T2"]["verdict"], "unchanged")

    def test_removal_from_tray(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            target = make_pair(folder, "rem_01", regions=[("R1", [12, 12, 10, 10])],
                               blobs=[([12, 12, 10, 10], DARK, "before")])
            answer = analyze_pair(target, load_config(write_config(folder)))
            tray = verdicts(answer)["T1"]
            self.assertEqual(tray["verdict"], "removal")
            self.assertEqual(tray["candidates"][0]["delta"], BASE - DARK)

    def test_multiple_regions_keep_count_null(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            target = make_pair(
                folder, "multi_01",
                regions=[("R1", [12, 12, 8, 8]), ("R2", [24, 24, 8, 8])],
                blobs=[([12, 12, 8, 8], DARK, "after"), ([24, 24, 8, 8], DARK, "after")])
            answer = analyze_pair(target, load_config(write_config(folder)))
            tray = verdicts(answer)["T1"]
            self.assertEqual(tray["verdict"], "addition")
            self.assertEqual(len(tray["candidates"]), 2)
            self.assertIsNone(tray["count_estimate"])
            self.assertIn("overlapping", tray["count_note"])

    def test_overlapping_objects_stay_one_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            target = make_pair(
                folder, "overlap_01", regions=[("R1", [12, 12, 20, 12])],
                blobs=[([12, 12, 9, 12], DARK, "after"), ([21, 12, 11, 12], DARK, "after")])
            answer = analyze_pair(target, load_config(write_config(folder)))
            tray = verdicts(answer)["T1"]
            self.assertEqual(len(tray["candidates"]), 1)
            self.assertIsNone(tray["count_estimate"])

    def test_boundary_crossing_is_ambiguous(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            target = make_pair(folder, "edge_01", regions=[("R1", [32, 15, 12, 10])],
                               blobs=[([32, 15, 12, 10], DARK, "after")])
            answer = analyze_pair(target, load_config(write_config(folder)))
            zones = verdicts(answer)
            self.assertEqual(zones["T1"]["verdict"], "ambiguous")
            self.assertEqual(zones["T2"]["verdict"], "ambiguous")
            self.assertIn("boundary", zones["T1"]["candidates"][0]["note"])

    def test_unchanged_trays(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            target = make_pair(folder, "same_01")
            answer = analyze_pair(target, load_config(write_config(folder)))
            self.assertEqual(answer["detection_status"], "no_change")
            for tray in answer["trays"]:
                self.assertEqual(tray["verdict"], "unchanged")
                self.assertEqual(tray["candidates"], [])

    def test_weak_signal_is_ambiguous(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            target = make_pair(folder, "weak_01", regions=[("R1", [12, 12, 10, 10])],
                               blobs=[([12, 12, 10, 10], BASE - 3, "after")])
            answer = analyze_pair(target, load_config(write_config(folder)))
            tray = verdicts(answer)["T1"]
            self.assertEqual(tray["verdict"], "ambiguous")
            self.assertIn("occlusion", tray["candidates"][0]["note"])

    def test_registration_failure_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            target = make_pair(folder, "bad_01", status="registration_invalid",
                               registration_ok=False)
            raw = json.loads(target.read_text(encoding="utf-8"))
            del raw["rectified"]
            target.write_text(json.dumps(raw), encoding="utf-8")
            answer = analyze_pair(target, load_config(write_config(folder)))
            for tray in answer["trays"]:
                self.assertEqual(tray["verdict"], "unknown")
            self.assertEqual(answer["device"]["verdict"], "unknown")

    def test_frame_mismatch_is_not_observed(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            target = make_pair(folder, "other_01", regions=[("R1", [12, 12, 10, 10])],
                               blobs=[([12, 12, 10, 10], DARK, "after")],
                               view="fixture_back")
            answer = analyze_pair(target, load_config(write_config(folder)))
            for tray in answer["trays"]:
                self.assertEqual(tray["verdict"], "not_observed")

    def test_proposes_device_removal_to_tray_addition(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            target = make_pair(
                folder, "move_01",
                regions=[("R1", [12, 12, 10, 10]), ("R2", [15, 48, 10, 10])],
                blobs=[([12, 12, 10, 10], DARK, "after"), ([15, 48, 10, 10], DARK, "before")])
            answer = analyze_pair(target, load_config(write_config(folder)))
            self.assertEqual(answer["device"]["verdict"], "removal")
            (link,) = answer["proposed_links"]
            self.assertEqual(link["to_tray"], "T1")
            self.assertTrue(link["requires_confirmation"])
            self.assertIn("not the same object", link["assertion"])
            self.assertEqual(link["area_ratio"], 1.0)

    def test_missing_images_limit_verdicts(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            target = make_pair(folder, "noimg_01", regions=[("R1", [12, 12, 10, 10])],
                               with_files=False)
            answer = analyze_pair(target, load_config(write_config(folder)))
            zones = verdicts(answer)
            self.assertEqual(zones["T1"]["verdict"], "unknown")
            self.assertEqual(zones["T2"]["verdict"], "unchanged")

    def test_jpg_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            target = make_pair(folder, "jpg_01", regions=[("R1", [12, 12, 10, 10])],
                               blobs=[([12, 12, 10, 10], DARK, "after")], ext="jpg")
            answer = analyze_pair(target, load_config(write_config(folder)))
            self.assertEqual(verdicts(answer)["T1"]["verdict"], "addition")

    def test_config_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            base = json.loads(json.dumps(CONFIG))
            dup = json.loads(json.dumps(CONFIG))
            dup["trays"].append(dup["trays"][0])
            with self.assertRaises(ConfigError):
                load_config(write_config(folder, dup))
            bad = json.loads(json.dumps(CONFIG))
            bad["trays"][0]["rect_frame_mm"] = [10, 10, -26, 26]
            path = folder / "bad.json"
            path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(path)
            params = json.loads(json.dumps(CONFIG))
            params["params"]["min_overlap"] = 2.0
            path.write_text(json.dumps(params), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(path)
            self.assertEqual(load_config(write_config(folder, base))["trays"][0]["id"], "T1")

    def test_cli_writes_overlay_and_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            make_pair(folder / "pairs", "add_01", regions=[("R1", [12, 12, 10, 10])],
                      blobs=[([12, 12, 10, 10], DARK, "after")])
            config_path = write_config(folder)
            out = folder / "out"
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = main(["--config", str(config_path), "--pairs-dir", str(folder / "pairs"),
                             "--out", str(out)])
            self.assertEqual(code, 0)
            summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["pairs"], 1)
            overlay = cv2.imread(str(out / "add_01" / "tray_overlay.png"))
            self.assertIsNotNone(overlay)
            self.assertEqual(overlay.shape[2], 3)
            stored = json.loads((out / "add_01" / "tray_result.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["trays"][0]["verdict"], "addition")


if __name__ == "__main__":
    unittest.main()
