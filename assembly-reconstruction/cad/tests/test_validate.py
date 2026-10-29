"""Validator tests: frozen-contract alignment + v1 rejection classes.

Run from the assembly-reconstruction/cad directory:

    .venv/Scripts/python -m unittest discover -s tests -v

This file is standard library only.
"""

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from anastylos_cad.validate import (  # noqa: E402
    SCHEMA, SEMANTIC, validate_model,
)

FIX = ROOT / "fixtures" / "synthetic"
CONTRACT_VEC = (ROOT.parent.parent / "capture-companion" / "control"
                / "contracts" / "vectors" / "cad-feature-valid.json")


def load(name):
    with open(FIX / name, encoding="utf-8") as handle:
        return json.load(handle)


class FrozenContractAlignment(unittest.TestCase):
    def test_contract_valid_vector_passes(self):
        with open(CONTRACT_VEC, encoding="utf-8") as handle:
            vector = json.load(handle)
        result = validate_model(vector)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["solids"], ["base_plate"])

    def test_contract_invalid_mutations_map_to_classes(self):
        with open(CONTRACT_VEC, encoding="utf-8") as handle:
            vector = json.load(handle)

        def mutate(fn):
            model = copy.deepcopy(vector)
            fn(model)
            return validate_model(model)

        self.assertEqual(
            mutate(lambda m: m["features"].__setitem__(
                0, dict(m["features"][0], kind="planar_profile_extrude")))["reject"],
            SCHEMA)
        self.assertEqual(
            mutate(lambda m: m["features"][0]["parameters"][
                "size_mm"].__setitem__(0, -1))["reject"], SCHEMA)
        self.assertEqual(
            mutate(lambda m: m["features"].__setitem__(
                0, dict(m["features"][0], basis="model_suggested")))["reject"],
            SCHEMA)
        self.assertEqual(
            mutate(lambda m: m["T_CAD_B"].__setitem__(
                "matrix", [1, 0, 0, 0, 0, 1, 0, 0,
                           0, 0, -1, 0, 0, 0, 0, 1]))["reject"],
            SEMANTIC)
        self.assertEqual(
            mutate(lambda m: m["features"][1]["parameters"].__setitem__(
                "parent_feature_id", "nope"))["reject"],
            SEMANTIC)

    def test_contract_cycle_mutation_is_semantic(self):
        with open(CONTRACT_VEC, encoding="utf-8") as handle:
            vector = json.load(handle)
        model = copy.deepcopy(vector)
        hole_b = copy.deepcopy(model["features"][1])
        hole_b["feature_id"] = "hole_b"
        hole_b["parameters"]["parent_feature_id"] = "hole_1"
        model["features"][1]["parameters"]["parent_feature_id"] = "hole_b"
        model["features"].append(hole_b)
        # Two holes parenting each other: v1 parents must be solids, so the
        # cycle is rejected semantically before the kernel either way.
        result = validate_model(model)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reject"], SEMANTIC)


class ValidFixtures(unittest.TestCase):
    def test_plate_hole(self):
        result = validate_model(load("plate_hole.json"))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["solids"], ["base_plate"])

    def test_cylinder(self):
        result = validate_model(load("cylinder.json"))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["solids"], ["post"])

    def test_placed_box(self):
        result = validate_model(load("placed_box.json"))
        self.assertTrue(result["ok"], result)


class InvalidFixtures(unittest.TestCase):
    def test_each_fixture_rejects_with_expected_class(self):
        cases = {
            "invalid_unknown_op.json": SCHEMA,
            "invalid_negative_size.json": SCHEMA,
            "invalid_bad_basis.json": SCHEMA,
            "invalid_reflection.json": SEMANTIC,
            "invalid_dangling_parent.json": SEMANTIC,
            "invalid_parent_cycle.json": SEMANTIC,
        }
        for name, want in cases.items():
            with self.subTest(fixture=name):
                result = validate_model(load(name))
                self.assertFalse(result["ok"], name)
                self.assertEqual(result["reject"], want, result)


class SemanticEdges(unittest.TestCase):
    def test_nan_uncertainty_rejected(self):
        model = load("plate_hole.json")
        model["features"][0]["uncertainty_mm"] = float("nan")
        result = validate_model(model)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reject"], SEMANTIC)

    def test_infinite_size_rejected(self):
        model = load("plate_hole.json")
        model["features"][0]["parameters"]["size_mm"][1] = float("inf")
        self.assertEqual(validate_model(model)["reject"], SEMANTIC)

    def test_depth_must_equal_parent_thickness(self):
        model = load("plate_hole.json")
        model["features"][1]["parameters"]["depth_mm"] = 5.0
        result = validate_model(model)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reject"], SEMANTIC)
        self.assertEqual(result["code"], "depth-mismatch")

    def test_feature_frame_reflection_rejected(self):
        model = load("plate_hole.json")
        model["features"][0]["T_CAD_feature"]["matrix"] = [
            1, 0, 0, 0, 0, 1, 0, 0, 0, 0, -1, 0, 0, 0, 0, 1]
        self.assertEqual(validate_model(model)["reject"], SEMANTIC)

    def test_scaled_feature_frame_rejected(self):
        model = load("plate_hole.json")
        model["features"][0]["T_CAD_feature"]["matrix"] = [
            2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 1]
        self.assertEqual(validate_model(model)["reject"], SEMANTIC)

    def test_duplicate_feature_id_rejected(self):
        model = load("plate_hole.json")
        twin = copy.deepcopy(model["features"][0])
        model["features"].append(twin)
        self.assertEqual(validate_model(model)["reject"], SEMANTIC)

    def test_legacy_T_D_B_name_rejected(self):
        model = load("plate_hole.json")
        model["T_D_B"] = model.pop("T_CAD_B")
        result = validate_model(model)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reject"], SCHEMA)


class ShapeEdges(unittest.TestCase):
    def test_extra_top_level_key_rejected(self):
        model = load("plate_hole.json")
        model["evidence_class"] = "synthetic"
        self.assertEqual(validate_model(model)["reject"], SCHEMA)

    def test_empty_features_rejected(self):
        model = load("plate_hole.json")
        model["features"] = []
        self.assertEqual(validate_model(model)["reject"], SCHEMA)

    def test_revision_zero_rejected(self):
        model = load("plate_hole.json")
        model["revision"] = 0
        self.assertEqual(validate_model(model)["reject"], SCHEMA)

    def test_bad_model_id_rejected(self):
        model = load("plate_hole.json")
        model["model_id"] = "not-a-uuid"
        self.assertEqual(validate_model(model)["reject"], SCHEMA)

    def test_non_object_model_rejected(self):
        self.assertEqual(validate_model([1, 2])["reject"], SCHEMA)
        self.assertEqual(validate_model(None)["reject"], SCHEMA)


if __name__ == "__main__":
    unittest.main()
