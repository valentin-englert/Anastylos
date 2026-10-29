"""Gate 1 unit tests: allowlist, detection logic, scoring.

Needs OpenCV + NumPy (shared calibration venv). Run from the
tools/snes01-benchmark directory:

    <venv>\\Scripts\\python -m unittest discover -s tests -v

All fixtures are synthetic and generated in tmp dirs; no benchmark
data, no labels, no network.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import gate1_run as run
import gate1_score as score


def _write_jpg(path: Path, image) -> bytes:
    ok, buf = cv2.imencode(".jpg", image,
                           [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert ok
    data = bytes(buf)
    path.write_bytes(data)
    return data


def _manifest_bytes(entries) -> dict:
    return {
        "schema": run.MANIFEST_SCHEMA,
        "frames": {
            "mapping_rule": run.MAPPING_RULE,
            "files": entries,
        },
    }


def _frame_entry(name: str, data: bytes, image) -> dict:
    height, width = image.shape[:2]
    return {"name": name, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "width": width, "height": height}


class AllowlistReader(unittest.TestCase):
    def test_frame_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = np.full((48, 64, 3), 200, np.uint8)
            data = _write_jpg(root / "frame_0001.jpg", image)
            manifest = _manifest_bytes(
                [_frame_entry("frame_0001.jpg", data, image)])
            (root / "frame_0001.jpg").write_bytes(data + b"\x00")
            with self.assertRaises(SystemExit):
                run.load_frames(root, manifest)

    def test_wrong_manifest_schema_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text(json.dumps({"schema": "other@9"}),
                            encoding="utf-8")
            with self.assertRaises(SystemExit):
                run.load_manifest(path)

    def test_mapping_rule_change_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            manifest = _manifest_bytes([])
            manifest["frames"]["mapping_rule"] = "frame_N -> t=2*(N-1)"
            manifest["frames"]["files"] = [
                {"name": "x", "bytes": 1, "sha256": "y",
                 "width": 1, "height": 1}]
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(SystemExit):
                run.load_manifest(path)

    def test_frame_time_is_identity(self):
        self.assertEqual([run.frame_time(i) for i in range(4)],
                         [0, 1, 2, 3])


class DetectionLogic(unittest.TestCase):
    def test_stable_frames_need_quiet_both_sides(self):
        change = [1.0, 1.0, 50.0, 60.0, 1.0, 1.0]
        stable, level = run.stable_frames(change)
        self.assertIn(0, stable)
        self.assertIn(6, stable)
        self.assertNotIn(2, stable)
        self.assertNotIn(3, stable)
        self.assertGreater(level, 0)

    def test_single_frame_blip_is_no_transition(self):
        self.assertEqual(run.find_transitions([0, 2, 3], 4), [])

    def test_transition_needs_stable_bracket(self):
        self.assertEqual(run.find_transitions([0, 4], 5), [(0, 4)])
        self.assertEqual(run.find_transitions([2], 5), [])

    def test_point_in_box_edges(self):
        self.assertTrue(run.point_in_box([0, 0], [0, 0, 5, 5]))
        self.assertTrue(run.point_in_box([5, 5], [0, 0, 5, 5]))
        self.assertFalse(run.point_in_box([6, 5], [0, 0, 5, 5]))

    def test_skin_mask_separates_skin_from_gray(self):
        ycc_skin = np.full((20, 20, 3), [150, 150, 100], np.uint8)
        skin_bgr = cv2.cvtColor(ycc_skin, cv2.COLOR_YCrCb2BGR)
        gray = np.full((20, 20, 3), 128, np.uint8)
        skin_frac = run.skin_fraction(skin_bgr, [0, 0, 20, 20])
        gray_frac = run.skin_fraction(gray, [0, 0, 20, 20])
        self.assertGreater(skin_frac, 0.9)
        self.assertLess(gray_frac, 0.1)

    def test_scores_stay_in_unit_range(self):
        rng = np.random.default_rng(11)
        textured = rng.integers(0, 255, (120, 120, 3), dtype=np.uint8)
        flat = np.full((40, 40, 3), 200, np.uint8)
        for a, b in ((textured, flat), (flat, textured),
                     (textured, textured)):
            feats = run.pair_score(a, b)
            for key in ("score", "color", "keypoints", "size"):
                self.assertGreaterEqual(feats[key], 0.0)
                self.assertLessEqual(feats[key], 1.0)

    def test_identical_pair_outranks_different_pair(self):
        rng = np.random.default_rng(7)
        base = rng.integers(0, 255, (60, 60, 3), dtype=np.uint8)
        other = rng.integers(0, 255, (60, 60, 3), dtype=np.uint8)
        same = run.pair_score(base, base)
        diff = run.pair_score(base, other)
        self.assertGreater(same["score"], diff["score"])
        self.assertGreaterEqual(same["score"], 0.0)
        self.assertLessEqual(same["score"], 1.0)


class SyntheticEndToEnd(unittest.TestCase):
    def _sequence(self, root: Path):
        """5 frames: static bg, red square appears at frame 3 (0-based
        index 2) inside the assembly ROI and stays. Hands: none."""
        bg = np.full((240, 320, 3), 210, np.uint8)
        frames = []
        for i in range(5):
            frame = bg.copy()
            if i >= 2:
                cv2.rectangle(frame, (150, 100), (210, 160), (0, 0, 255), -1)
            path = root / f"frame_{i + 1:04d}.jpg"
            frames.append((path, frame))
        return frames

    def _run_gate1(self, root: Path, out: Path):
        root.mkdir(parents=True, exist_ok=True)
        entries = []
        for path, frame in self._sequence(root):
            data = _write_jpg(path, frame)
            entries.append(_frame_entry(path.name, data, frame))
        manifest_path = root / "manifest.json"
        manifest_path.write_text(json.dumps(_manifest_bytes(entries)),
                                 encoding="utf-8")
        old_roi, old_min = run.ASSEMBLY_ROI, run.MIN_COMPONENT_PX
        run.ASSEMBLY_ROI, run.MIN_COMPONENT_PX = [120, 60, 140, 140], 50
        try:
            code = run.main(["--frames-dir", str(root),
                             "--manifest", str(manifest_path),
                             "--out", str(out)])
        finally:
            run.ASSEMBLY_ROI, run.MIN_COMPONENT_PX = old_roi, old_min
        self.assertEqual(code, 0)
        return json.loads((out / "candidates.json").read_text(
            encoding="utf-8"))

    def test_appearance_detected_with_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = self._run_gate1(Path(tmp) / "in", Path(tmp) / "out")
            self.assertEqual(doc["schema"], run.SCHEMA)
            self.assertGreaterEqual(len(doc["events"]), 1)
            roles = [r["role"] for e in doc["events"]
                     for r in e["persistent_regions"]]
            self.assertIn("disappearance", roles)

    def test_run_is_byte_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self._run_gate1(root / "in", root / "out1")
            second = self._run_gate1(root / "in", root / "out2")
            self.assertEqual(json.dumps(first, sort_keys=True),
                             json.dumps(second, sort_keys=True))

    def test_sheet_and_log_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run_gate1(Path(tmp) / "in", Path(tmp) / "out")
            self.assertTrue((Path(tmp) / "out"
                             / "contact_sheet_gate1.jpg").is_file())
            self.assertTrue((Path(tmp) / "out"
                             / "uncertainty_log.md").is_file())


class ScoringMath(unittest.TestCase):
    def _prediction(self, pid, before_t, after_t, src_box, dest_box,
                    top1=True):
        regions = [
            {"region_id": "r0", "frame": "frame_0001",
             "bbox_xywh": src_box, "role": "disappearance",
             "persistent": True},
            {"region_id": "r1", "frame": "frame_0002",
             "bbox_xywh": dest_box, "role": "appearance",
             "persistent": True},
        ]
        return {"id": pid, "before_t": before_t, "after_t": after_t,
                "persistent_regions": regions, "regions": regions,
                "correspondence_top1":
                    {"src_region": "r0", "dest_region": "r1",
                     "src_frame": "frame_0001",
                     "dest_frame": "frame_0002"}
                    if top1 else None}

    def _target(self, tid, start, end, src, dest, disposition="distinct"):
        return {"id": tid, "disposition": disposition,
                "event_window_s": {"start_s": start, "end_s": end},
                "source_box_xywh": src, "destination_box_xywh": dest}

    def test_perfect_target_scores_hit(self):
        pred = self._prediction("E1", 10, 20, [0, 0, 10, 10],
                                [50, 50, 10, 10])
        target = self._target("rear_housing", 5, 25, [0, 0, 10, 10],
                              [50, 50, 10, 10])
        detail = score.score_target(target, [pred])
        self.assertTrue(detail["hit"])
        self.assertEqual(detail["correspondence"], "correct")

    def test_wrong_destination_is_no_hit(self):
        pred = self._prediction("E1", 10, 20, [0, 0, 10, 10],
                                [500, 500, 10, 10])
        target = self._target("dpad", 5, 25, [0, 0, 10, 10],
                              [50, 50, 10, 10])
        detail = score.score_target(target, [pred])
        self.assertFalse(detail["hit"])
        self.assertEqual(detail["correspondence"], "wrong_destination")

    def test_ambiguous_abstention_scores(self):
        pred = self._prediction("E1", 10, 20, [0, 0, 10, 10],
                                [50, 50, 10, 10], top1=False)
        target = self._target("membrane_ambiguity", 5, 25, [0, 0, 10, 10],
                              None, disposition="ambiguous")
        detail = score.score_target(target, [pred])
        self.assertTrue(detail["hit"])
        self.assertEqual(detail["correspondence"], "correct_abstention")

    def test_silence_is_missed_even_when_ambiguous(self):
        target = self._target("membrane_ambiguity", 5, 25, [0, 0, 10, 10],
                              None, disposition="ambiguous")
        detail = score.score_target(target, [])
        self.assertFalse(detail["hit"])
        self.assertEqual(detail["correspondence"], "missed")

    def test_timing_outside_tolerance_misses(self):
        pred = self._prediction("E1", 100, 110, [0, 0, 10, 10],
                                [50, 50, 10, 10])
        target = self._target("dpad", 5, 25, [0, 0, 10, 10],
                              [50, 50, 10, 10])
        detail = score.score_target(target, [pred])
        self.assertFalse(detail["hit"])

    def test_unsealed_ground_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cand = root / "cand.json"
            cand.write_text(json.dumps({"schema": score.CAND_SCHEMA,
                                        "events": []}), encoding="utf-8")
            ground = root / "gt.json"
            ground.write_text(json.dumps({"schema": score.GT_SCHEMA,
                                          "status": "template",
                                          "targets": []}), encoding="utf-8")
            with self.assertRaises(SystemExit):
                score.main(["--candidates", str(cand),
                            "--ground", str(ground),
                            "--out", str(root / "o")])


if __name__ == "__main__":
    unittest.main()
