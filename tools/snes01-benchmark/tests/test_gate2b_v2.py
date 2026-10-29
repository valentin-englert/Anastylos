"""Gate 2B.1 v2 extension tests: key gating, mapping, exclusion.

Fully offline. The synthetic 'sealed key' is test scaffolding with
arbitrary verdicts, never presented as truth.
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

import gate2b_run_v2 as v2


def _key(verdicts, status="sealed", annotator="test-op"):
    return {"schema": "snes01-gate2b/answer-key@2",
            "annotation_set_id": "test", "status": status,
            "annotator": annotator, "annotated_date": "2026-10-27",
            "verdicts": verdicts}


def _entry(pid, verdict):
    return {"pair_id": pid, "verdict": verdict,
            "identity_basis": "synthetic", "evidence_type": "visual",
            "supporting_frames": [], "notes": ""}


class KeyGating(unittest.TestCase):
    def _write(self, root: Path, doc: dict) -> Path:
        path = root / "k.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        return path

    def test_template_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(
                Path(tmp), _key([], status="template", annotator=""))
            with self.assertRaises(SystemExit):
                v2.load_v2_key(path)

    def test_unknown_verdict_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(
                Path(tmp), _key([_entry("V2-P01", "maybe")]))
            with self.assertRaises(SystemExit):
                v2.load_v2_key(path)

    def test_missing_evidence_type_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            entry = _entry("V2-P01", "ambiguous")
            del entry["evidence_type"]
            path = self._write(Path(tmp), _key([entry]))
            with self.assertRaises(SystemExit):
                v2.load_v2_key(path)

    def test_all_four_verdicts_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), _key([
                _entry("V2-P01", "same_physical_instance"),
                _entry("V2-P02", "different_physical_instance"),
                _entry("V2-P03", "ambiguous"),
                _entry("V2-P04", "unverifiable")]))
            key = v2.load_v2_key(path)
            self.assertEqual(len(key["verdicts"]), 4)


class Mapping(unittest.TestCase):
    def _manifest(self, root: Path) -> Path:
        (root / "tight").mkdir()
        image = np.full((20, 20, 3), 200, np.uint8)
        cv2.imwrite(str(root / "tight" / "a.png"), image)
        manifest = {
            "video_sha256": "x",
            "pairs": [
                {"id": "V2-P01",
                 "src": {"frame": "f1", "t_s": 0,
                         "bbox_xywh": [0, 0, 10, 10],
                         "file": "tight/a.png"},
                 "dest": {"frame": "f2", "t_s": 1,
                          "bbox_xywh": [0, 0, 10, 10],
                          "file": "tight/a.png"}},
                {"id": "V2-P02",
                 "src": {"frame": "f1", "t_s": 0,
                         "bbox_xywh": [0, 0, 10, 10],
                         "file": "tight/a.png"},
                 "dest": {"frame": "f2", "t_s": 1,
                          "bbox_xywh": [0, 0, 10, 10],
                          "file": "tight/a.png"}}]}
        path = root / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path

    def test_unverifiable_excluded_not_pooled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = self._manifest(root)
            manifest = json.loads(manifest_path.read_text(
                encoding="utf-8"))
            key = _key([_entry("V2-P01", "same_physical_instance"),
                        _entry("V2-P02", "unverifiable")])
            args = type("A", (), {"pairs": str(manifest_path)})()
            v1pairs_path, v1key_path, excluded = v2.stage(
                args, key, manifest, root / "out")
            self.assertEqual(excluded, ["V2-P02"])
            v1pairs = json.loads(v1pairs_path.read_text(encoding="utf-8"))
            self.assertEqual(v1pairs["scored_pairs"], ["V2-P01"])
            self.assertEqual(len(v1pairs["pairs"]), 1)
            v1key = json.loads(v1key_path.read_text(encoding="utf-8"))
            self.assertEqual(v1key["verdicts"],
                             [{"pair_id": "V2-P01", "verdict": "same"}])
            self.assertTrue((root / "out" / "staged" / "tight"
                             / "a.png").is_file())

    def test_unknown_pair_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = self._manifest(root)
            manifest = json.loads(manifest_path.read_text(
                encoding="utf-8"))
            key = _key([_entry("V2-P99", "ambiguous")])
            args = type("A", (), {"pairs": str(manifest_path)})()
            with self.assertRaises(SystemExit):
                v2.stage(args, key, manifest, root / "out")


if __name__ == "__main__":
    unittest.main()
