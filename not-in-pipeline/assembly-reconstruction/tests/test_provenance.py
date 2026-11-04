"""Provenance regression tests: cited vs current SHA-256 of result files.

A memory observation cites a change-detection result.json plus the
result_sha256 recorded at import time. The projector must accept the
cited region as verified only when the cited hash matches the bytes on
disk; a substituted or edited file must be rejected with diagnostics,
and evidence without a usable cited hash must resolve as explicitly
unverified -- never as verified.

Run from the assembly-reconstruction directory:

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anastylos_reconstruction.projector import build_state  # noqa: E402
from anastylos_reconstruction.schema import validate_state  # noqa: E402

VIEW = "surface_a3"


def _result(bbox_mm, pair_id="p1"):
    return {
        "status": "single_change",
        "pair_id": pair_id,
        "rectified": {"view": VIEW, "px_per_mm": 4.0,
                      "size_px": [400, 300],
                      "origin_frame_mm": [0.0, 0.0]},
        "regions": [{"label": "R1", "bbox_mm": bbox_mm,
                     "centroid_mm": [bbox_mm[0] + bbox_mm[2] / 2,
                                     bbox_mm[1] + bbox_mm[3] / 2],
                     "bbox_px": [10, 10, 20, 20]}],
    }


def _observation(ident, name, cited_hash, session="s1"):
    return {"type": "observation", "id": ident, "session": session,
            "timestamp": "2026-01-01T00:00:00Z", "source_view": VIEW,
            "bbox_mm": [0, 0, 10, 10], "centroid_mm": [5, 5],
            "evidence": {"result_json": name, "pair_id": "p1",
                         "region": "R1", "result_sha256": cited_hash},
            "supersedes": None}


def _write_result(path: Path, bbox_mm) -> str:
    payload = json.dumps(_result(bbox_mm)).encode("utf-8")
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _write_log(path: Path, events) -> str:
    path.write_text("\n".join(json.dumps(event) for event in events)
                    + "\n", encoding="utf-8")
    return str(path)


class ProvenanceVerification(unittest.TestCase):
    def test_matching_hash_resolves_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            digest = _write_result(root / "r1.json", [0, 0, 10, 10])
            log = _write_log(root / "memory.jsonl",
                             [_observation("obs-0001", "r1.json", digest)])
            state = build_state(log, results_dir=str(root))
            validate_state(state)
            component = state["components"][0]
            self.assertTrue(component["physical"]["region_resolved"])
            region = component["physical"]["region"]
            self.assertTrue(region["provenance_verified"])
            self.assertEqual(region["result_sha256"], digest)
            self.assertEqual(region["result_sha256_current"], digest)
            self.assertEqual(state["missing_evidence"], [])
            self.assertEqual(state["status"], "ok")

    def test_mismatched_hash_rejects_region(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cited = _write_result(root / "r1.json", [0, 0, 10, 10])
            log = _write_log(root / "memory.jsonl",
                             [_observation("obs-0001", "r1.json", cited)])
            # Substitute the file after import: new geometry, same label.
            current = _write_result(root / "r1.json", [500, 500, 40, 40])
            self.assertNotEqual(cited, current)
            state = build_state(log, results_dir=str(root))
            validate_state(state)
            component = state["components"][0]
            self.assertFalse(component["physical"]["region_resolved"])
            self.assertIsNone(component["physical"]["region"])
            self.assertIn("mismatch",
                          component["physical"]["missing_reason"])
            self.assertEqual(component["crops"], [])
            self.assertEqual(len(state["missing_evidence"]), 1)
            entry = state["missing_evidence"][0]
            self.assertEqual(entry["observation_id"], "obs-0001")
            self.assertIn("mismatch", entry["reason"])
            self.assertEqual(entry["cited_sha256"], cited)
            self.assertEqual(entry["current_sha256"], current)
            # The tampered geometry must appear nowhere in the state.
            dumped = json.dumps(state)
            self.assertNotIn("500", dumped)
            self.assertEqual(state["status"], "ambiguous")

    def test_missing_hash_resolves_explicitly_unverified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = _write_result(root / "r1.json", [0, 0, 10, 10])
            log = _write_log(root / "memory.jsonl",
                             [_observation("obs-0001", "r1.json", None)])
            state = build_state(log, results_dir=str(root))
            validate_state(state)
            component = state["components"][0]
            self.assertTrue(component["physical"]["region_resolved"])
            region = component["physical"]["region"]
            self.assertFalse(region["provenance_verified"])
            self.assertIn("unverified", region["provenance_note"])
            self.assertIsNone(region["result_sha256"])
            self.assertEqual(region["result_sha256_current"], current)
            self.assertEqual(state["missing_evidence"], [])

    def test_malformed_hash_resolves_explicitly_unverified(self):
        for bad in ("not-a-hash", "", 12345, ["aa" * 32]):
            with self.subTest(cited=bad), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _write_result(root / "r1.json", [0, 0, 10, 10])
                log = _write_log(
                    root / "memory.jsonl",
                    [_observation("obs-0001", "r1.json", bad)])
                state = build_state(log, results_dir=str(root))
                validate_state(state)
                component = state["components"][0]
                self.assertTrue(component["physical"]["region_resolved"])
                region = component["physical"]["region"]
                self.assertFalse(region["provenance_verified"])
                self.assertIn("unverified", region["provenance_note"])

    def test_missing_file_reports_unreadable_with_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cited = "ab" * 32
            log = _write_log(root / "memory.jsonl",
                             [_observation("obs-0001", "absent.json",
                                           cited)])
            state = build_state(log, results_dir=str(root))
            validate_state(state)
            component = state["components"][0]
            self.assertFalse(component["physical"]["region_resolved"])
            self.assertIn("unreadable",
                          component["physical"]["missing_reason"])
            entry = state["missing_evidence"][0]
            self.assertEqual(entry["cited_sha256"], cited)
            self.assertIsNone(entry["current_sha256"])

    def test_provenance_outcomes_are_deterministic_and_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cited = _write_result(root / "r1.json", [0, 0, 10, 10])
            log = _write_log(root / "memory.jsonl",
                             [_observation("obs-0001", "r1.json", cited),
                              _observation("obs-0002", "r1.json",
                                           "00" * 32)])
            before = (root / "r1.json").read_bytes()
            first = json.dumps(build_state(log, results_dir=str(root)),
                               sort_keys=True)
            second = json.dumps(build_state(log, results_dir=str(root)),
                                sort_keys=True)
            self.assertEqual(first, second)
            self.assertEqual((root / "r1.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
