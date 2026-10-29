"""Replay evaluator unit tests.

Run from the calibration/spatial directory:

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SPATIAL = Path(__file__).resolve().parents[1]
REPO = SPATIAL.parents[1]
CONTRACTS = REPO / "capture-companion" / "control" / "contracts"
sys.path.insert(0, str(SPATIAL))

from anastylos_spatial.replay import (
    replay,
    replay_guidance,
    replay_negative_vector,
    report_bytes,
    report_sha256,
    verify_contract,
)
from anastylos_spatial.validator import load_json_strict

CANDIDATE = "9f4a496d74350f19012df577bc5278da66babcf7"
CONTRACT_SHA = "ec313f7ff814a62bc7bcd487b18169b9b34e2ba8bee9e6d829e61d4373da4663"


class ReplayTest(unittest.TestCase):
    def test_replay_green(self):
        report = replay(CONTRACTS, SPATIAL, CANDIDATE)
        self.assertTrue(report["green"], json.dumps(report, indent=2)[:3000])
        self.assertTrue(report["sidecar_valid"]["matched"])
        self.assertEqual((report["guidance_frozen"]["matched"], report["guidance_frozen"]["total"]), (2, 2))
        self.assertEqual(report["guidance_extensions"]["matched"], report["guidance_extensions"]["total"])
        self.assertGreater(report["guidance_extensions"]["total"], 0)
        self.assertEqual(report["negatives"]["matched"], report["negatives"]["total"])
        self.assertGreater(report["negatives"]["total"], 0)
        self.assertTrue(report["synthetic_profile"]["matched"])
        self.assertEqual(report["contract_sha256"], CONTRACT_SHA)

    def test_report_deterministic(self):
        first = replay(CONTRACTS, SPATIAL, CANDIDATE)
        second = replay(CONTRACTS, SPATIAL, CANDIDATE)
        self.assertEqual(report_sha256(first), report_sha256(second))
        self.assertEqual(report_bytes(first), report_bytes(second))

    def test_contract_tamper_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = Path(tmp) / "contracts"
            shutil.copytree(CONTRACTS, clone)
            vector = clone / "vectors" / "spatial-valid.json"
            raw = bytearray(vector.read_bytes())
            raw[100] ^= 0x01
            vector.write_bytes(bytes(raw))
            sha, mismatches = verify_contract(clone)
            self.assertEqual(sha, CONTRACT_SHA)
            self.assertEqual(mismatches, ["vectors/spatial-valid.json"])

    def test_negative_vector_counts_as_vector(self):
        doc, sha = load_json_strict(CONTRACTS / "vectors" / "spatial-valid.json")
        path = SPATIAL / "vectors" / "reflection-det-minus-one.json"
        result = replay_negative_vector(path, doc, sha)
        self.assertTrue(result["matched"])
        self.assertEqual(result["verdict"], "reject")
        self.assertIn("REFLECTION_DET_MINUS_ONE", result["reasons"])

    def test_guidance_recompute_mismatch_detected(self):
        doc = json.loads((CONTRACTS / "vectors" / "guidance-signs.json").read_text(encoding="utf-8"))
        tampered = copy.deepcopy(doc)
        tampered["cases"][0]["expected_cues"] = ["left 20"]
        result = replay_guidance(tampered)
        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["total"], 2)


if __name__ == "__main__":
    unittest.main()
