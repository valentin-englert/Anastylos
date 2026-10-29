"""Dataset manifest builder unit tests.

Run from the calibration/spatial directory:

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

SPATIAL = Path(__file__).resolve().parents[1]
REPO = SPATIAL.parents[1]
sys.path.insert(0, str(SPATIAL))

from anastylos_spatial.dataset import (
    build_dataset,
    manifest_sha256,
    validate_dataset_manifest,
)
from anastylos_spatial.validator import validate_sidecar

SESSION = "00000000-0000-4000-8000-000000000002"
DELIVERY = {"delivery_capture_set_id": "00000000-0000-4000-8000-000000000060",
            "delivery_capture_set_revision": 1,
            "delivery_manifest_sha256": "d" * 64}


def frozen_sidecar() -> dict:
    path = REPO / "capture-companion" / "control" / "contracts" / "vectors" / "spatial-valid.json"
    return json.loads(path.read_text(encoding="utf-8"))


def evaluated_view(evidence_suffix: str, request_suffix: str, dx_mm: float = 0.0,
                   pitched: bool = False) -> dict:
    doc = frozen_sidecar()
    doc["evidence_id"] = f"00000000-0000-4000-8000-0000000000{evidence_suffix}"
    doc["request_id"] = f"00000000-0000-4000-8000-0000000000{request_suffix}"
    doc["attempt_id"] = f"00000000-0000-4000-8000-0000000000{evidence_suffix}"
    matrix = doc["pose"]["T_board_camera"]["matrix"]
    matrix[3] += dx_mm
    if pitched:
        # 30-degree pitch about the board x-axis: same center, turned axis.
        matrix[5], matrix[6], matrix[9], matrix[10] = 0.8660254037844386, -0.5, 0.5, 0.8660254037844386
    result = validate_sidecar(doc, {})
    assert result["verdict"] == "accept", result
    return {"sidecar": doc, "sidecar_sha256": "e" * 64, "verdict": result["verdict"],
            "metric_ready": result["metric_ready"], "reasons": [], "reviews": []}


class DatasetTest(unittest.TestCase):
    def test_complete_two_view_dataset(self):
        first = evaluated_view("41", "51")
        second = evaluated_view("42", "52", dx_mm=10.0)
        required = [{"request_id": first["sidecar"]["request_id"], "request_revision": 1},
                    {"request_id": second["sidecar"]["request_id"], "request_revision": 1}]
        manifest, issues = build_dataset(
            [first, second], copy.deepcopy(DELIVERY), required,
            capture_set_id="00000000-0000-4000-8000-000000000070",
            created_at="2026-10-29T12:00:02.000Z", target_plan_sha256="f" * 64)
        self.assertEqual(issues, [])
        self.assertEqual(manifest["result"], "complete")
        self.assertTrue(manifest["geometry_ready"])
        self.assertEqual(len(manifest["accepted"]), 2)
        self.assertEqual(manifest["missing"], [])
        self.assertEqual(validate_dataset_manifest(manifest), [])
        self.assertRegex(manifest_sha256(manifest), r"^[0-9a-f]{64}$")

    def test_no_delivery_cannot_complete(self):
        first = evaluated_view("41", "51")
        manifest, issues = build_dataset(
            [first], None, [{"request_id": first["sidecar"]["request_id"], "request_revision": 1}],
            capture_set_id="00000000-0000-4000-8000-000000000070",
            created_at="2026-10-29T12:00:02.000Z")
        self.assertEqual(issues, [])
        self.assertEqual(manifest["result"], "partial")
        self.assertIsNone(manifest["delivery_manifest_sha256"])

    def test_missing_required_is_partial(self):
        first = evaluated_view("41", "51")
        required = [{"request_id": first["sidecar"]["request_id"], "request_revision": 1},
                    {"request_id": "00000000-0000-4000-8000-000000000099", "request_revision": 2}]
        manifest, _ = build_dataset([first], copy.deepcopy(DELIVERY), required,
                                    capture_set_id="00000000-0000-4000-8000-000000000070",
                                    created_at="2026-10-29T12:00:02.000Z")
        self.assertEqual(manifest["result"], "partial")
        self.assertEqual(len(manifest["missing"]), 1)

    def test_duplicate_request_revision_flagged(self):
        first = evaluated_view("41", "51")
        second = evaluated_view("42", "51", dx_mm=10.0)
        _manifest, issues = build_dataset([first, second], copy.deepcopy(DELIVERY), [])
        self.assertTrue(any(i.startswith("DUPLICATE_REQUEST_REVISION") for i in issues))

    def test_redundant_pair_flagged(self):
        first = evaluated_view("41", "51")
        second = evaluated_view("42", "52")
        manifest, _ = build_dataset([first, second], copy.deepcopy(DELIVERY), [])
        self.assertEqual(len(manifest["redundant_view_pairs"]), 1)
        self.assertFalse(manifest["geometry_ready"])
        self.assertIn("INSUFFICIENT_BASELINE", manifest["geometry_notes"])

    def test_degenerate_pair_flagged(self):
        first = evaluated_view("41", "51")
        second = evaluated_view("42", "52", pitched=True)
        manifest, _ = build_dataset([first, second], copy.deepcopy(DELIVERY), [])
        self.assertEqual(len(manifest["degenerate_view_pairs"]), 1)
        self.assertEqual(manifest["redundant_view_pairs"], [])

    def test_rejected_verdict_listed_not_dropped(self):
        doc = frozen_sidecar()
        bad = {"sidecar": doc, "sidecar_sha256": "e" * 64, "verdict": "reject",
               "metric_ready": False, "reasons": ["RMS_OVER_GATE"], "reviews": []}
        manifest, _ = build_dataset([bad], copy.deepcopy(DELIVERY), [])
        self.assertEqual(manifest["accepted"], [])
        self.assertEqual(len(manifest["rejected"]), 1)
        self.assertEqual(manifest["rejected"][0]["reason"], ["RMS_OVER_GATE"])
        self.assertEqual(manifest["result"], "failed")

    def test_mixed_sessions_flagged(self):
        first = evaluated_view("41", "51")
        second = evaluated_view("42", "52", dx_mm=10.0)
        second["sidecar"]["session_id"] = "00000000-0000-4000-8000-000000000077"
        _manifest, issues = build_dataset([first, second], copy.deepcopy(DELIVERY), [])
        self.assertIn("MIXED_SESSIONS", issues)

    def test_complete_without_delivery_rejected(self):
        manifest, _ = build_dataset([], None, [])
        manifest["result"] = "complete"
        issues = validate_dataset_manifest(manifest)
        self.assertIn("COMPLETE_WITHOUT_DELIVERY", issues)


if __name__ == "__main__":
    unittest.main()
