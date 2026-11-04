import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from retrospective_custody import (
    EvidenceError,
    _geometry,
    _pts_is_valid,
    _safe_mask_path,
    _validate_roi,
    inspect_track,
    verify_file_hash,
)


class EvidenceValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        repo_root = Path(__file__).resolve().parents[3]
        cls.output_root = repo_root / "random" / "retrospective-custody-20261028"
        cls.created_output_root = not cls.output_root.exists()
        cls.temp_root = cls.output_root / "test-temp"
        cls.temp_root.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp_root.rmdir()
        if cls.created_output_root:
            cls.output_root.rmdir()
            try:
                cls.output_root.parent.rmdir()
            except OSError:
                pass

    def test_mask_touching_display_edge_is_ambiguous(self):
        mask = np.zeros((100, 30), dtype=np.uint8)
        cv2.line(mask, (15, 0), (15, 99), 255, 12)
        result = _geometry(mask, (100, 0, 130, 100), (200, 200))
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["reason"], "MASK_TOUCHES_DISPLAY_BORDER")

    def test_symmetric_ends_do_not_guess_a_tool_tip(self):
        mask = np.zeros((100, 30), dtype=np.uint8)
        cv2.line(mask, (15, 5), (15, 94), 255, 10)
        result = _geometry(mask, (100, 50, 130, 150), (200, 200))
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["reason"], "ENDPOINT_AMBIGUOUS")

    def test_roi_bounds_are_validated_in_display_coordinates(self):
        self.assertEqual(_validate_roi("10,20,90,100", 200, 300), (10, 20, 90, 100))
        with self.assertRaises(EvidenceError):
            _validate_roi("10,20,201,100", 200, 300)

    def test_mask_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root) as temp:
            root = Path(temp) / "evidence"
            root.mkdir()
            outside = Path(temp) / "outside.png"
            outside.write_bytes(b"mask")
            with self.assertRaises(EvidenceError):
                _safe_mask_path(root, "../outside.png")

    def test_changed_evidence_hash_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root) as temp:
            path = Path(temp) / "evidence.json"
            path.write_bytes(b"evidence")
            expected = hashlib.sha256(b"different evidence").hexdigest()
            with self.assertRaisesRegex(EvidenceError, "SHA-256 mismatch"):
                verify_file_hash(path, expected, "evidence")

    def test_changed_transcript_hash_is_rejected_by_runtime_path(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root) as temp:
            root = Path(temp)
            clip = root / "clip.mp4"
            clip.write_bytes(b"not decoded because transcript integrity fails first")
            transcript = root / "transcript.jsonl"
            transcript.write_bytes(b"changed transcript")
            evidence = root / "evidence.json"
            evidence.write_text(json.dumps({
                "schema": "sam-evidence-v1/request@1",
                "prompt": {"concept": "screwdriver"},
                "records": [],
                "files": {"transcript": {
                    "path": "transcript.jsonl",
                    "sha256": hashlib.sha256(b"original transcript").hexdigest(),
                }},
            }), encoding="utf-8")
            with self.assertRaisesRegex(EvidenceError, "transcript SHA-256 mismatch"):
                inspect_track(clip, evidence, root / "out", None)

    def test_pts_must_be_nonnegative_and_strictly_increasing(self):
        self.assertTrue(_pts_is_valid(0.0, 0, float("-inf")))
        self.assertFalse(_pts_is_valid(-0.1, 0, float("-inf")))
        self.assertTrue(_pts_is_valid(33.3, 1, 0.0))
        self.assertFalse(_pts_is_valid(33.3, 1, 33.3))

    def test_out_of_range_mask_cannot_count_as_source_roi_candidate(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root) as temp:
            root = Path(temp)
            clip = root / "clip.avi"
            writer = cv2.VideoWriter(
                str(clip), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (100, 100)
            )
            self.assertTrue(writer.isOpened())
            writer.write(np.zeros((100, 100, 3), dtype=np.uint8))
            writer.release()

            evidence_dir = root / "evidence"
            evidence_dir.mkdir()
            mask = np.zeros((50, 20), dtype=np.uint8)
            cv2.fillPoly(mask, [np.array([[8, 49], [12, 49], [18, 0], [2, 0]])], 255)
            mask_path = evidence_dir / "mask.png"
            self.assertTrue(cv2.imwrite(str(mask_path), mask))
            mask_sha = hashlib.sha256(mask_path.read_bytes()).hexdigest()
            transcript_path = evidence_dir / "transcript.jsonl"
            transcript_path.write_text("{}\n", encoding="utf-8")
            transcript_sha = hashlib.sha256(transcript_path.read_bytes()).hexdigest()
            evidence = {
                "schema": "sam-evidence-v1/request@1",
                "prompt": {"concept": "screwdriver"},
                "media": {
                    "kind": "video",
                    "sha256": hashlib.sha256(clip.read_bytes()).hexdigest(),
                    "width": 100,
                    "height": 100,
                    "nb_frames": 1,
                },
                "files": {
                    "transcript": {"path": "transcript.jsonl", "sha256": transcript_sha},
                    "masks": {"mask.png": {"sha256": mask_sha}},
                },
                "records": [{
                    "frame": 1,
                    "object_id": "tool-1",
                    "scope": "request:test",
                    "confidence": 0.99,
                    "box": {"left": 10, "top": 10, "right": 30, "bottom": 60},
                    "mask": {"png": "mask.png", "sha256": mask_sha},
                }],
            }
            evidence_path = evidence_dir / "evidence.json"
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

            summary = inspect_track(
                clip, evidence_path, root / "out", "1,1,99,99"
            )

            row = summary["records"][0]
            self.assertEqual(row["geometry_status"], "unknown")
            self.assertEqual(row["geometry"]["status"], "unknown")
            self.assertFalse(row["narrow_end_in_source_area"])
            self.assertEqual(row["reasons"], ["FRAME_INDEX_OUT_OF_RANGE"])
            self.assertEqual(
                summary["counts"]["narrow_end_geometry_candidates_in_source_roi"], 0
            )


if __name__ == "__main__":
    unittest.main()
