"""Evidence/manifest tests: provenance mapping, hash coverage, handoff.

Run from the assembly-reconstruction/cad directory:

    .venv/Scripts/python -m unittest discover -s tests -v

This file is standard library only.
"""

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from anastylos_cad.evidence import (  # noqa: E402
    BUNDLE_FILES, build_feature_evidence, build_manifest, build_validation,
    canonical_json, missing_feature_envelope, sha256_bytes,
)

FIX = ROOT / "fixtures" / "synthetic"


def load(name):
    with open(FIX / name, encoding="utf-8") as handle:
        return json.load(handle)


class FeatureEvidence(unittest.TestCase):
    def test_every_feature_mapped_with_provenance(self):
        model = load("plate_hole.json")
        doc = build_feature_evidence(
            model, residuals={"base_plate": {"median_px": 0.8}})
        self.assertEqual(doc["model_id"], model["model_id"])
        self.assertEqual(set(doc["features"]), {"base_plate", "hole_1"})
        plate = doc["features"]["base_plate"]
        self.assertEqual(plate["basis"], "geometric_measurement")
        self.assertEqual(plate["uncertainty_mm"], 0.4)
        self.assertEqual(plate["evidence_ids"],
                         ["44444444-4444-4444-8444-444444444444"])
        self.assertEqual(plate["residuals"], {"median_px": 0.8})
        self.assertIsNone(doc["features"]["hole_1"]["residuals"])


class ValidationDoc(unittest.TestCase):
    def test_carries_gates_and_versions(self):
        model = load("plate_hole.json")
        compare = {"dims_within_0v01mm": True,
                   "volume_rel_err_le_1e-5": True}
        doc = build_validation(
            model, {"volume": 1.0}, {"volume": 1.0},
            {"cadquery": "2.8.0"}, compare,
            [{"feature_id": "hole_1", "reason": "uncertainty unquantified"}])
        self.assertEqual(doc["roundtrip_compare"], compare)
        self.assertEqual(doc["unresolved_features"],
                         [{"feature_id": "hole_1",
                           "reason": "uncertainty unquantified"}])
        self.assertEqual(doc["tool_versions"], {"cadquery": "2.8.0"})
        self.assertEqual(doc["evidence_class"], "synthetic")


class ManifestDoc(unittest.TestCase):
    def test_covers_whole_bundle(self):
        entries = {}
        for name in BUNDLE_FILES:
            entries[name] = "00" * 32
            entries[name + ":bytes"] = 7
        doc = build_manifest(entries, {"feature_model_sha256": "ff" * 32},
                             "anastylos_cad.compile v1", 20261029,
                             "2026-10-29T12:00:00Z")
        self.assertEqual([a["path"] for a in doc["artifacts"]],
                         list(BUNDLE_FILES))
        self.assertEqual(doc["inputs"]["feature_model_sha256"], "ff" * 32)
        self.assertEqual(doc["deterministic_seed"], 20261029)

    def test_sha256_helpers_match_hashlib(self):
        self.assertEqual(sha256_bytes(b"abc"),
                         hashlib.sha256(b"abc").hexdigest())
        text = canonical_json({"b": 1, "a": [1, 2]})
        self.assertEqual(json.loads(text), {"a": [1, 2], "b": 1})
        self.assertTrue(text.endswith("\n"))


class MissingFeatureHandoff(unittest.TestCase):
    def test_flags_unquantified_and_unresolved_only(self):
        model = load("plate_hole.json")
        model["features"][1]["uncertainty_mm"] = None
        validation = {"unresolved_features": [
            {"feature_id": "hole_1", "reason": "uncertainty unquantified"}]}
        envelope = missing_feature_envelope(model, validation)
        self.assertEqual(envelope["model_id"], model["model_id"])
        self.assertEqual(
            [f["feature_id"] for f in envelope["underconstrained_features"]],
            ["hole_1"])

    def test_empty_when_all_quantified(self):
        model = load("plate_hole.json")
        envelope = missing_feature_envelope(model, {"unresolved_features": []})
        self.assertEqual(envelope["underconstrained_features"], [])


if __name__ == "__main__":
    unittest.main()
