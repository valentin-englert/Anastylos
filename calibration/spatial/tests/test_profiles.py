"""Calibration profile and board-instance unit tests.

Run from the calibration/spatial directory:

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

SPATIAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPATIAL))

from anastylos_spatial.profiles import (
    canonical_sha256,
    validate_board_instance,
    validate_profile,
)

A64 = "a" * 64


def synthetic_profile() -> dict:
    return json.loads((SPATIAL / "calibration-profile.synthetic-v1.json").read_text(encoding="utf-8"))


def board_record() -> dict:
    return {
        "schema": "anastylos-board-instance@1",
        "board_instance_id": "00000000-0000-4000-8000-000000000011",
        "layout_sha256": A64,
        "board_frame_id": "surface_a3",
        "measured_bars_mm": {
            "horizontal_mm": 100.1,
            "vertical_mm": 99.9,
            "nominal_mm": 100.0,
            "instrument": "synthetic-steel-ruler",
            "uncertainty_mm": 0.05,
            "measured_by": "synthetic-operator",
            "measured_at": "2026-10-29T12:00:00.000Z",
        },
        "flatness": "flat",
        "source_photo_refs": [],
    }


class ProfileTest(unittest.TestCase):
    def test_synthetic_profile_accepts(self):
        result = validate_profile(synthetic_profile())
        self.assertEqual(result["verdict"], "accept", result)

    def test_zero_k_rejected(self):
        doc = synthetic_profile()
        doc["K"] = [0.0, 0.0, 639.5, 0.0, 1000.0, 359.5, 0.0, 0.0, 1.0]
        result = validate_profile(doc)
        self.assertEqual(result["verdict"], "reject")
        self.assertIn("NONPOSITIVE_FOCAL_LENGTH", result["reasons"])

    def test_wrong_camera_model_rejected(self):
        doc = synthetic_profile()
        doc["camera_model"] = "pinhole_no_distortion"
        result = validate_profile(doc)
        self.assertEqual(result["verdict"], "reject")
        self.assertIn("BAD_CAMERA_MODEL", result["reasons"])

    def test_distortion_length_rejected(self):
        doc = synthetic_profile()
        doc["distortion"] = [0.0, 0.0, 0.0]
        result = validate_profile(doc)
        self.assertEqual(result["verdict"], "reject")
        self.assertIn("BAD_DISTORTION", result["reasons"])

    def test_too_few_views_needs_review(self):
        doc = synthetic_profile()
        keep = doc["validation"]["calibration_view_ids"][:10]
        doc["validation"]["calibration_view_ids"] = keep
        doc["validation"]["held_out_view_ids"] = keep[:8]
        doc["validation"]["per_view_rms_px"] = {view: 0.4 for view in keep}
        result = validate_profile(doc)
        self.assertEqual(result["verdict"], "needs_review")
        self.assertIn("CALIBRATION_VIEW_COUNT", result["reviews"])

    def test_too_few_held_out_needs_review(self):
        doc = synthetic_profile()
        doc["validation"]["held_out_view_ids"] = doc["validation"]["held_out_view_ids"][:4]
        result = validate_profile(doc)
        self.assertEqual(result["verdict"], "needs_review")
        self.assertIn("HELD_OUT_COUNT", result["reviews"])

    def test_held_out_rms_over_gate_needs_review(self):
        doc = synthetic_profile()
        doc["validation"]["held_out_rms_px"] = 1.2
        result = validate_profile(doc)
        self.assertEqual(result["verdict"], "needs_review")
        self.assertIn("HELD_OUT_RMS_OVER_GATE", result["reviews"])

    def test_missing_scale_check_needs_review(self):
        doc = synthetic_profile()
        del doc["validation"]["independent_scale_check_mm"]
        result = validate_profile(doc)
        self.assertEqual(result["verdict"], "needs_review")
        self.assertIn("NO_INDEPENDENT_SCALE_CHECK", result["reviews"])

    def test_validated_with_open_reviews_rejected(self):
        doc = synthetic_profile()
        doc["metric_validity"] = "validated"
        doc["validation"]["held_out_rms_px"] = 1.2
        result = validate_profile(doc)
        self.assertEqual(result["verdict"], "reject")
        self.assertIn("VALIDATED_PROFILE_WITH_REVIEWS", result["reasons"])

    def test_canonical_hash_stable(self):
        doc = synthetic_profile()
        again = json.loads(json.dumps(doc))
        self.assertEqual(canonical_sha256(doc), canonical_sha256(again))
        self.assertRegex(canonical_sha256(doc), r"^[0-9a-f]{64}$")

    def test_canonical_hash_differs_from_bytes_hash(self):
        raw = (SPATIAL / "calibration-profile.synthetic-v1.json").read_bytes()
        self.assertNotEqual(canonical_sha256(synthetic_profile()), hashlib.sha256(raw).hexdigest())


class BoardInstanceTest(unittest.TestCase):
    def test_valid_record_accepts(self):
        result = validate_board_instance(board_record())
        self.assertEqual(result["verdict"], "accept", result)

    def test_bar_error_over_half_percent_rejected(self):
        doc = board_record()
        doc["measured_bars_mm"]["horizontal_mm"] = 101.0
        result = validate_board_instance(doc)
        self.assertEqual(result["verdict"], "reject")
        self.assertIn("PRINT_BAR_ERROR", result["reasons"])

    def test_missing_bars_needs_review(self):
        doc = board_record()
        doc["measured_bars_mm"] = None
        result = validate_board_instance(doc)
        self.assertEqual(result["verdict"], "needs_review")
        self.assertIn("NO_MEASURED_BARS", result["reviews"])

    def test_warped_board_rejected(self):
        doc = board_record()
        doc["flatness"] = "warped"
        result = validate_board_instance(doc)
        self.assertEqual(result["verdict"], "reject")
        self.assertIn("WARPED_BOARD", result["reasons"])


if __name__ == "__main__":
    unittest.main()
