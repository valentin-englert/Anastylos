"""Gate 2A unit tests: selection, parsing, budget, pack building.

Fully offline: no API calls. Synthetic fixtures only.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import gate2a_run as run
import gate2a_select as select


def _region(rid, role, box, occluded=False):
    return {"region_id": rid, "role": role, "bbox_xywh": box,
            "occluded": occluded, "persistent": True}


def _event(eid, before_t, after_t, regions, pairs):
    return {"id": eid, "before_t": before_t, "after_t": after_t,
            "before_frame": f"frame_{before_t + 1:04d}.jpg",
            "after_frame": f"frame_{after_t + 1:04d}.jpg",
            "persistent_regions": regions, "ranked_pairs": pairs}


class BlindSelection(unittest.TestCase):
    def _doc(self):
        e1 = _event("E1", 0, 10,
                    [_region("r0", "disappearance", [100, 100, 50, 50]),
                     _region("r1", "appearance", [100, 3200, 60, 60])],
                    [{"src_region": "r0", "dest_region": "r1",
                      "score": 0.4}])
        e2 = _event("E2", 20, 30,
                    [_region("s0", "disappearance", [200, 200, 40, 40]),
                     _region("s1", "appearance", [200, 3300, 70, 70]),
                     _region("s2", "appearance", [400, 3350, 70, 70])],
                    [{"src_region": "s0", "dest_region": "s1",
                      "score": 0.2},
                     {"src_region": "s0", "dest_region": "s2",
                      "score": 0.1}])
        return {"events": [e1, e2]}

    def test_four_cases_deterministic(self):
        cases = select.select(self._doc())
        self.assertEqual([c["id"] for c in cases],
                         ["A1", "A2", "A3", "A4"])
        for case in cases:
            self.assertEqual(case["group"], "blind")

    def test_a1_picks_most_tray_appearances(self):
        cases = {c["id"]: c for c in select.select(self._doc())}
        self.assertEqual(cases["A1"]["event_id"], "E2")

    def test_a2_picks_highest_score_pair(self):
        cases = {c["id"]: c for c in select.select(self._doc())}
        self.assertEqual(cases["A2"]["dest"]["region_id"], "r1")

    def test_a3_cross_event_control(self):
        cases = {c["id"]: c for c in select.select(self._doc())}
        a3 = cases["A3"]
        self.assertEqual(a3["source"]["frame"], "frame_0021.jpg")
        self.assertEqual(a3["dest"]["frame"], "frame_0011.jpg")

    def test_a4_picks_lowest_score_pair(self):
        cases = {c["id"]: c for c in select.select(self._doc())}
        self.assertEqual(cases["A4"]["dest"]["region_id"], "s2")


class JsonParsing(unittest.TestCase):
    def test_direct_parse(self):
        parsed, method = run.extract_json('{"a": 1}')
        self.assertEqual((parsed, method), ({"a": 1}, "direct"))

    def test_span_parse(self):
        parsed, method = run.extract_json('Sure: {"a": 1} done')
        self.assertEqual((parsed, method), ({"a": 1}, "span"))

    def test_empty_and_garbage(self):
        self.assertEqual(run.extract_json(None), (None, "empty"))
        self.assertEqual(run.extract_json("no braces here"),
                         (None, "no-braces"))
        self.assertEqual(run.extract_json("{oops"), (None, "no-braces"))

    def test_verdict_enum(self):
        self.assertEqual(set(run.VERDICTS),
                         {"same_instance_candidate",
                          "different_instance_candidate",
                          "insufficient_evidence"})


class BudgetMath(unittest.TestCase):
    def test_estimate_scales(self):
        self.assertAlmostEqual(run.estimate(1000, 1000, 1.25, 4.25),
                               0.0055)


class PackBuild(unittest.TestCase):
    def test_build_verifies_hashes_and_crops(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            frames.mkdir()
            image = np.full((96, 64, 3), 200, np.uint8)
            cv2.imwrite(str(frames / "frame_0001.jpg"), image)
            cv2.imwrite(str(frames / "frame_0002.jpg"), image)
            import hashlib
            files = []
            for name in ("frame_0001.jpg", "frame_0002.jpg"):
                data = (frames / name).read_bytes()
                files.append({"name": name, "bytes": len(data),
                              "sha256": hashlib.sha256(data).hexdigest(),
                              "width": 64, "height": 96})
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps(
                {"schema": "snes01-benchmark/manifest@1",
                 "frames": {"files": files}}), encoding="utf-8")
            cases = root / "cases.json"
            cases.write_text(json.dumps(
                {"schema": "snes01-gate2a/cases@1", "cases": [{
                    "id": "T1", "group": "blind",
                    "selection_rule": "test",
                    "source": {"frame": "frame_0001.jpg", "t_s": 0,
                               "bbox_xywh": [8, 8, 16, 16]},
                    "dest": {"frame": "frame_0002.jpg", "t_s": 1,
                             "bbox_xywh": [8, 8, 16, 16]},
                    "context": {"before_frame": "frame_0001.jpg",
                                "after_frame": "frame_0002.jpg"}}]}),
                encoding="utf-8")
            pa = root / "a.txt"
            pb = root / "b.txt"
            pa.write_text("A", encoding="utf-8")
            pb.write_text("B", encoding="utf-8")
            code = run.main(["build", "--cases", str(cases),
                             "--frames-dir", str(frames),
                             "--manifest", str(manifest),
                             "--prompt-a", str(pa),
                             "--prompt-b", str(pb),
                             "--out", str(root / "pack")])
            self.assertEqual(code, 0)
            pack = json.loads((root / "pack"
                               / "evidence_pack.json").read_text(
                                   encoding="utf-8"))
            self.assertEqual(pack["schema"], run.SCHEMA_PACK)
            self.assertEqual(len(pack["cases"]), 1)
            self.assertTrue((root / "pack" / "crops"
                             / "T1_src.png").is_file())

    def test_tampered_frame_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            frames.mkdir()
            image = np.full((96, 64, 3), 200, np.uint8)
            cv2.imwrite(str(frames / "frame_0001.jpg"), image)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps(
                {"schema": "snes01-benchmark/manifest@1",
                 "frames": {"files": [{"name": "frame_0001.jpg",
                                       "bytes": 1,
                                       "sha256": "00" * 32,
                                       "width": 64,
                                       "height": 96}]}}), encoding="utf-8")
            cases = root / "cases.json"
            cases.write_text(json.dumps(
                {"schema": "snes01-gate2a/cases@1", "cases": [{
                    "id": "T1", "group": "blind",
                    "selection_rule": "test",
                    "source": {"frame": "frame_0001.jpg", "t_s": 0,
                               "bbox_xywh": [8, 8, 16, 16]},
                    "dest": {"frame": "frame_0001.jpg", "t_s": 0,
                             "bbox_xywh": [8, 8, 16, 16]},
                    "context": {"before_frame": "frame_0001.jpg",
                                "after_frame": "frame_0001.jpg"}}]}),
                encoding="utf-8")
            pa = root / "a.txt"
            pb = root / "b.txt"
            pa.write_text("A", encoding="utf-8")
            pb.write_text("B", encoding="utf-8")
            with self.assertRaises(SystemExit):
                run.main(["build", "--cases", str(cases),
                          "--frames-dir", str(frames),
                          "--manifest", str(manifest),
                          "--prompt-a", str(pa),
                          "--prompt-b", str(pb),
                          "--out", str(root / "pack")])


if __name__ == "__main__":
    unittest.main()
