"""Tests for spatial -> CAD attribution mapping (Wave 1.5)."""

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anastylos_cad.spatial_attribution import spatial_attribution

VECTORS = (Path(__file__).resolve().parents[3] / "capture-companion"
          / "control" / "contracts" / "vectors")


def load_spatial_valid():
    return json.loads(
        (VECTORS / "spatial-valid.json").read_text(encoding="utf-8"))


class SpatialAttributionTests(unittest.TestCase):
    def test_accept_metric_ready_yields_geometric_basis(self):
        out = spatial_attribution(
            load_spatial_valid(),
            {"verdict": "accept", "reasons": [], "metric_ready": True},
            dataset_sha256="e" * 64)
        self.assertEqual(out["basis"], "geometric_measurement")
        self.assertTrue(out["metric_qualified"])
        self.assertEqual(
            out["evidence_ids"],
            ["00000000-0000-4000-8000-000000000001"])
        self.assertIsNone(out["uncertainty_mm"])
        self.assertEqual(out["dataset_sha256"], "e" * 64)
        self.assertEqual(out["reasons"], [])

    def test_accept_without_metric_yields_no_basis(self):
        out = spatial_attribution(
            load_spatial_valid(),
            {"verdict": "accept", "reasons": [], "metric_ready": False})
        self.assertIsNone(out["basis"])
        self.assertFalse(out["metric_qualified"])
        self.assertEqual(len(out["evidence_ids"]), 1)
        self.assertTrue(any("metric_ready" in r for r in out["reasons"]))

    def test_reject_yields_nothing(self):
        out = spatial_attribution(
            load_spatial_valid(),
            {"verdict": "reject", "reasons": ["WRONG_SOURCE_IMAGE"],
             "metric_ready": False})
        self.assertIsNone(out["basis"])
        self.assertEqual(out["evidence_ids"], [])
        self.assertFalse(out["metric_qualified"])

    def test_needs_review_yields_nothing(self):
        out = spatial_attribution(
            load_spatial_valid(),
            {"verdict": "needs_review", "reasons": ["NULL_UNCERTAINTY"],
             "metric_ready": False})
        self.assertIsNone(out["basis"])
        self.assertEqual(out["evidence_ids"], [])

    def test_malformed_inputs_fail_closed(self):
        for sidecar, result in ((None, None), ([], []),
                                ({"evidence_id": 7}, "accept"),
                                (None, {"verdict": "accept",
                                        "metric_ready": True})):
            out = spatial_attribution(sidecar, result)
            self.assertIsNone(out["basis"])
            self.assertFalse(out["metric_qualified"])

    def test_implausible_dataset_sha_dropped(self):
        out = spatial_attribution(
            load_spatial_valid(),
            {"verdict": "accept", "reasons": [], "metric_ready": True},
            dataset_sha256="not-a-sha")
        self.assertIsNone(out["dataset_sha256"])
        self.assertTrue(any("dataset_sha256" in r for r in out["reasons"]))

    def test_real_validator_outcome_maps(self):
        """End of the honest chain: real validator -> attribution."""
        spatial_dir = (Path(__file__).resolve().parents[3]
                       / "calibration" / "spatial")
        sys.path.insert(0, str(spatial_dir))
        try:
            from anastylos_spatial.validator import validate_sidecar
        finally:
            sys.path.remove(str(spatial_dir))
        sidecar = load_spatial_valid()
        result = validate_sidecar(copy.deepcopy(sidecar), {})
        out = spatial_attribution(sidecar, result)
        self.assertEqual(result["verdict"], "accept")
        self.assertTrue(result["metric_ready"])
        self.assertEqual(out["basis"], "geometric_measurement")


if __name__ == "__main__":
    unittest.main()
