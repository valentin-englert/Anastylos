"""Sidecar validator unit tests, incl. real-layout and profile binding.

Run from the calibration/spatial directory:

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

SPATIAL = Path(__file__).resolve().parents[1]
REPO = SPATIAL.parents[1]
sys.path.insert(0, str(SPATIAL))

from anastylos_spatial.validator import (
    NonFiniteError,
    load_json_strict,
    validate_sidecar,
)

A64 = "a" * 64


def frozen_sidecar() -> dict:
    path = REPO / "capture-companion" / "control" / "contracts" / "vectors" / "spatial-valid.json"
    return json.loads(path.read_text(encoding="utf-8"))


def real_layout() -> tuple[dict, str]:
    path = REPO / "calibration" / "calibration-config.json"
    raw = path.read_bytes()
    return json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()


def synthetic_profile_doc() -> tuple[dict, str]:
    path = SPATIAL / "calibration-profile.synthetic-v1.json"
    raw = path.read_bytes()
    return json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()


class FrozenVectorTest(unittest.TestCase):
    def test_frozen_valid_accepts_and_is_metric_ready(self):
        result = validate_sidecar(frozen_sidecar(), {})
        self.assertEqual(result["verdict"], "accept", result)
        self.assertTrue(result["metric_ready"])

    def test_skipped_chain_checks_are_noted(self):
        result = validate_sidecar(frozen_sidecar(), {})
        self.assertIn("LAYOUT_CHECK_SKIPPED", result["notes"])
        self.assertIn("NO_ASSET_STORE", result["notes"])
        self.assertIn("EPOCH_CHECK_SKIPPED", result["notes"])

    def test_nonfinite_json_refused_at_load(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            handle.write('{"value": NaN}')
            name = handle.name
        try:
            with self.assertRaises(NonFiniteError):
                load_json_strict(name)
        finally:
            Path(name).unlink()

    def test_unknown_top_level_field_rejects(self):
        doc = frozen_sidecar()
        doc["future_field"] = 1
        result = validate_sidecar(doc, {})
        self.assertEqual(result["verdict"], "reject")
        self.assertIn("UNKNOWN_TOP_LEVEL_FIELD", result["reasons"])

    def test_reflection_rejects(self):
        doc = frozen_sidecar()
        doc["pose"]["T_board_camera"]["matrix"][0] = -1.0
        result = validate_sidecar(doc, {})
        self.assertEqual(result["verdict"], "reject")
        self.assertIn("REFLECTION_DET_MINUS_ONE", result["reasons"])

    def test_invalid_rotation_rejects(self):
        doc = frozen_sidecar()
        doc["pose"]["T_board_camera"]["matrix"][1] = 0.5
        result = validate_sidecar(doc, {})
        self.assertEqual(result["verdict"], "reject")
        self.assertIn("INVALID_ROTATION", result["reasons"])

    def test_bottom_row_rejects(self):
        doc = frozen_sidecar()
        doc["pose"]["T_board_camera"]["matrix"][15] = 0.0
        result = validate_sidecar(doc, {})
        self.assertEqual(result["verdict"], "reject")
        self.assertIn("BAD_TRANSFORM_BOTTOM_ROW", result["reasons"])

    def test_inverted_board_z_rejects(self):
        doc = frozen_sidecar()
        doc["pose"]["T_board_camera"]["matrix"][11] = 400.0
        result = validate_sidecar(doc, {})
        self.assertEqual(result["verdict"], "reject")
        self.assertIn("INVERTED_BOARD_Z", result["reasons"])

    def test_validity_without_transform_rejects(self):
        doc = frozen_sidecar()
        doc["pose"]["T_board_camera"] = None
        result = validate_sidecar(doc, {})
        self.assertEqual(result["verdict"], "reject")
        self.assertIn("VALIDITY_WITHOUT_TRANSFORM", result["reasons"])

    def test_transform_id_mismatch_rejects(self):
        doc = frozen_sidecar()
        doc["pose"]["T_board_camera"]["from"] = "camera:other-camera"
        result = validate_sidecar(doc, {})
        self.assertEqual(result["verdict"], "reject")
        self.assertIn("TRANSFORM_FROM_MISMATCH", result["reasons"])

    def test_transform_units_rejects(self):
        doc = frozen_sidecar()
        doc["pose"]["T_board_camera"]["units"] = "m"
        result = validate_sidecar(doc, {})
        self.assertEqual(result["verdict"], "reject")
        self.assertIn("TRANSFORM_UNITS_NOT_MM", result["reasons"])

    def test_target_frame_mismatch_rejects(self):
        doc = frozen_sidecar()
        doc["target"]["requested_roi"]["frame"] = "board:00000000-0000-4000-8000-000000000011:surface_a4"
        result = validate_sidecar(doc, {})
        self.assertEqual(result["verdict"], "reject")
        self.assertIn("TARGET_FRAME_MISMATCH", result["reasons"])

    def test_unknown_solve_source_needs_review(self):
        doc = frozen_sidecar()
        doc["pose"]["solve_source"] = "unknown"
        doc["pose"]["sample_age_ms"] = None
        result = validate_sidecar(doc, {})
        self.assertEqual(result["verdict"], "needs_review")
        self.assertIn("UNKNOWN_SOLVE_SOURCE", result["reviews"])
        self.assertFalse(result["metric_ready"])

    def test_exposure_not_bracketed_rejects(self):
        doc = frozen_sidecar()
        doc["pose"]["solve_source"] = "timestamp_interpolated"
        asset = {"asset_id": "00000000-0000-4000-8000-000000000090", "sha256": A64}
        doc["pose"]["bracketing_samples"] = [
            {"sample_id": "00000000-0000-4000-8000-000000000031", "sensor_ns": "1000000000",
             "pose_asset": asset},
            {"sample_id": "00000000-0000-4000-8000-000000000032", "sensor_ns": "1004000000",
             "pose_asset": asset},
        ]
        result = validate_sidecar(doc, {})
        self.assertEqual(result["verdict"], "reject")
        self.assertIn("EXPOSURE_NOT_BRACKETED", result["reasons"])


class LayoutBindingTest(unittest.TestCase):
    def test_real_layout_accepts_frozen_markers(self):
        layout, sha = real_layout()
        result = validate_sidecar(frozen_sidecar(), {"layout": layout, "layout_sha256": sha})
        self.assertEqual(result["verdict"], "reject")
        self.assertIn("LAYOUT_HASH_MISMATCH", result["reasons"])

    def test_real_layout_with_matching_hash_accepts(self):
        layout, sha = real_layout()
        doc = frozen_sidecar()
        doc["calibration"]["layout_sha256"] = sha
        result = validate_sidecar(doc, {"layout": layout, "layout_sha256": sha})
        self.assertEqual(result["verdict"], "accept", result)

    def test_unknown_marker_id_rejects(self):
        layout, sha = real_layout()
        doc = frozen_sidecar()
        doc["calibration"]["layout_sha256"] = sha
        doc["pose"]["metrics"]["detected_marker_ids"] = [10, 11, 99]
        doc["pose"]["metrics"]["inlier_marker_ids"] = [10, 11, 99]
        result = validate_sidecar(doc, {"layout": layout, "layout_sha256": sha})
        self.assertEqual(result["verdict"], "reject")
        self.assertIn("UNKNOWN_MARKER_ID", result["reasons"])

    def test_reserved_marker_id_rejects(self):
        layout, sha = real_layout()
        doc = frozen_sidecar()
        doc["calibration"]["layout_sha256"] = sha
        doc["pose"]["metrics"]["detected_marker_ids"] = [10, 11, 13]
        doc["pose"]["metrics"]["inlier_marker_ids"] = [10, 11, 40]
        result = validate_sidecar(doc, {"layout": layout, "layout_sha256": sha})
        self.assertEqual(result["verdict"], "reject")
        self.assertIn("UNKNOWN_MARKER_ID", result["reasons"])


class ProfileBindingTest(unittest.TestCase):
    def _bound(self):
        doc = frozen_sidecar()
        profile, sha = synthetic_profile_doc()
        doc["calibration"]["profile_sha256"] = sha
        return doc, {"profile": profile, "profile_sha256": sha}

    def test_matching_profile_accepts(self):
        doc, ctx = self._bound()
        result = validate_sidecar(doc, ctx)
        self.assertEqual(result["verdict"], "accept", result)

    def test_profile_hash_mismatch_rejects(self):
        doc, ctx = self._bound()
        ctx["profile_sha256"] = "c" * 64
        result = validate_sidecar(doc, ctx)
        self.assertEqual(result["verdict"], "reject")
        self.assertIn("PROFILE_HASH_MISMATCH", result["reasons"])

    def test_profile_camera_mismatch_rejects(self):
        doc, ctx = self._bound()
        ctx["profile"] = dict(ctx["profile"], camera_id="other-camera")
        result = validate_sidecar(doc, ctx)
        self.assertEqual(result["verdict"], "reject")
        self.assertIn("PROFILE_CAMERA_MISMATCH", result["reasons"])

    def test_profile_pixel_frame_mismatch_rejects(self):
        doc = frozen_sidecar()
        profile, sha = synthetic_profile_doc()
        doc["calibration"]["profile_sha256"] = sha
        doc["image_geometry"]["calibration_pixel_frame_id"] = "other-frame"
        result = validate_sidecar(doc, {"profile": profile, "profile_sha256": sha})
        self.assertEqual(result["verdict"], "reject")
        self.assertIn("PIXEL_FRAME_MISMATCH", result["reasons"])


class AssetAndEpochTest(unittest.TestCase):
    def test_resolved_image_accepts(self):
        result = validate_sidecar(frozen_sidecar(), {"image_shas": {A64: 1024}})
        self.assertEqual(result["verdict"], "accept", result)
        self.assertIn("IMAGE_RESOLVED", result["notes"])

    def test_byte_length_mismatch_rejects(self):
        result = validate_sidecar(frozen_sidecar(), {"image_shas": {A64: 999}})
        self.assertEqual(result["verdict"], "reject")
        self.assertIn("BYTE_LENGTH_MISMATCH", result["reasons"])

    def test_unresolved_image_needs_review(self):
        result = validate_sidecar(frozen_sidecar(), {"image_shas": {}})
        self.assertEqual(result["verdict"], "needs_review")
        self.assertIn("IMAGE_UNRESOLVED", result["reviews"])
        self.assertFalse(result["metric_ready"])

    def test_verified_join_inside_one_epoch_accepts(self):
        doc = frozen_sidecar()
        doc["observer_relation"]["association"] = "verified"
        ref = {"asset_id": "00000000-0000-4000-8000-000000000090", "sha256": A64}
        doc["observer_relation"]["run_ref"] = ref
        doc["observer_relation"]["epoch_id"] = "E1"
        doc["observer_relation"]["time_mapping_ref"] = ref
        epochs = [{"epoch_id": "E1", "start_utc": "2026-10-29T11:59:00.000Z",
                   "end_utc": "2026-10-29T12:01:00.000Z"}]
        result = validate_sidecar(doc, {"epochs": epochs})
        self.assertEqual(result["verdict"], "accept", result)

    def test_verified_without_refs_rejects(self):
        doc = frozen_sidecar()
        doc["observer_relation"]["association"] = "verified"
        result = validate_sidecar(doc, {})
        self.assertEqual(result["verdict"], "reject")
        self.assertIn("UNVERIFIED_REFS", result["reasons"])


class BasisSeparationTest(unittest.TestCase):
    def test_testimony_with_wrong_state_needs_review(self):
        doc = frozen_sidecar()
        doc["interpretations"] = [{
            "id": "00000000-0000-4000-8000-000000000021",
            "kind": "part_identity",
            "basis": "operator_testimony",
            "state": "observed",
            "claim": "Operator says this is a screw.",
            "evidence_refs": [{"asset_id": "00000000-0000-4000-8000-000000000090", "sha256": A64}],
            "method": "synthetic-note",
            "region_ref": None,
            "operator_confirmation_ref": None,
        }]
        result = validate_sidecar(doc, {})
        self.assertEqual(result["verdict"], "needs_review")
        self.assertIn("TESTIMONY_STATE", result["reviews"])

    def test_declared_testimony_accepts(self):
        doc = frozen_sidecar()
        doc["interpretations"] = [{
            "id": "00000000-0000-4000-8000-000000000021",
            "kind": "part_identity",
            "basis": "operator_testimony",
            "state": "operator_declared",
            "claim": "Operator says this is a screw.",
            "evidence_refs": [{"asset_id": "00000000-0000-4000-8000-000000000090", "sha256": A64}],
            "method": "synthetic-note",
            "region_ref": None,
            "operator_confirmation_ref": None,
        }]
        result = validate_sidecar(doc, {})
        self.assertEqual(result["verdict"], "accept", result)


if __name__ == "__main__":
    unittest.main()
