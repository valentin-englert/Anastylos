"""Context and evidence-input checks: strict validation in, provenance out.

Run from the missing-parts directory:

    ..\\.venv\\Scripts\\python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anastylos_missing.context import load_context
from anastylos_missing.inputs import (load_associations, load_confirmations,
                                      load_detection, load_expected, load_frame_meta)


def write_json(directory: Path, name: str, payload) -> Path:
    path = directory / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class ContextTest(unittest.TestCase):
    def test_valid_context_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(Path(tmp), "ctx.json",
                              {"original": {"role": "original_assembly", "frame": "fixture_back"},
                               "current": {"role": "reassembly_state", "frame": "fixture_back"},
                               "surface": "light"})
            context = load_context(path)
        self.assertTrue(context["declared"])
        self.assertEqual(context["original"]["role"], "original_assembly")
        self.assertEqual(context["surface"], "light")
        self.assertIn("max_shift_px", context["params"])

    def test_none_means_neutral(self):
        context = load_context(None)
        self.assertFalse(context["declared"])
        self.assertTrue(context["reasons"])

    def test_missing_roles_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(Path(tmp), "ctx.json",
                              {"original": {"frame": "fixture_back"},
                               "current": {"role": "reassembly_state"}})
            with self.assertRaises(ValueError):
                load_context(path)

    def test_missing_file_raises(self):
        with self.assertRaises(ValueError):
            load_context(Path("no-such-context.json"))

    def test_cross_frame_without_transform_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(Path(tmp), "ctx.json",
                              {"original": {"role": "a", "frame": "fixture_back"},
                               "current": {"role": "b", "frame": "surface_a3"}})
            with self.assertRaises(ValueError):
                load_context(path)

    def test_cross_frame_with_verified_transform_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(Path(tmp), "ctx.json",
                              {"original": {"role": "a", "frame": "custom:cam_a"},
                               "current": {"role": "b", "frame": "custom:cam_b"},
                               "transform": {"homography": [[1, 0, -40], [0, 1, -10], [0, 0, 1]],
                                             "from_frame": "custom:cam_b",
                                             "to_frame": "custom:cam_a",
                                             "provenance": "checkerboard 2026-10-27",
                                             "reprojection_rms_mm": 0.2}})
            context = load_context(path)
        self.assertEqual(context["transform"]["reprojection_rms_mm"], 0.2)

    def test_loose_transform_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(Path(tmp), "ctx.json",
                              {"original": {"role": "a", "frame": "custom:cam_a"},
                               "current": {"role": "b", "frame": "custom:cam_b"},
                               "transform": {"homography": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                                             "from_frame": "custom:cam_b",
                                             "to_frame": "custom:cam_a",
                                             "provenance": "guess",
                                             "reprojection_rms_mm": 5.0}})
            with self.assertRaises(ValueError):
                load_context(path)

    def test_bad_surface_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(Path(tmp), "ctx.json",
                              {"original": {"role": "a"}, "current": {"role": "b"},
                               "surface": "mirror"})
            with self.assertRaises(ValueError):
                load_context(path)


class DetectionInputTest(unittest.TestCase):
    def test_trusted_result_loads_with_provenance(self):
        from anastylos_missing.synthetic import detection_fixture
        with tempfile.TemporaryDirectory() as tmp:
            info = detection_fixture(Path(tmp) / "pair_01")
            loaded = load_detection(info["result_json"])
        self.assertTrue(loaded["trusted"])
        self.assertEqual(len(loaded["regions"]), 1)
        self.assertEqual(loaded["provenance"]["view"], "fixture_back")
        self.assertIsNotNone(loaded["provenance"]["result_sha256"])

    def test_untrusted_status_loads_distrusted(self):
        from anastylos_missing.synthetic import detection_fixture
        with tempfile.TemporaryDirectory() as tmp:
            info = detection_fixture(Path(tmp) / "pair_02", status="registration_invalid")
            loaded = load_detection(info["result_json"])
        self.assertFalse(loaded["trusted"])

    def test_bad_region_dropped_with_issue(self):
        from anastylos_missing.synthetic import detection_fixture
        with tempfile.TemporaryDirectory() as tmp:
            info = detection_fixture(
                Path(tmp) / "pair_03",
                regions=[{"label": "R1", "bbox_mm": [1, 2], "centroid_mm": [0, 0]}])
            loaded = load_detection(info["result_json"])
        self.assertEqual(loaded["regions"], [])
        self.assertTrue(loaded["issues"])

    def test_missing_file_raises(self):
        with self.assertRaises(ValueError):
            load_detection(Path("no-such-result.json"))


class FrameMetaTest(unittest.TestCase):
    def test_valid_meta_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(Path(tmp), "meta.json", {"px_per_mm": 4.0, "view": "surface_a3"})
            meta = load_frame_meta(path)
        self.assertEqual(meta["px_per_mm"], 4.0)

    def test_bad_scale_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(Path(tmp), "meta.json", {"px_per_mm": -2})
            with self.assertRaises(ValueError):
                load_frame_meta(path)


class ExpectedTest(unittest.TestCase):
    def test_expected_file_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(Path(tmp), "exp.json",
                              {"coverage": "partial",
                               "expected": [{"id": "e1", "bbox_mm": [26.5, 31.5, 7, 7],
                                             "frame": "fixture_back",
                                             "source": "operator list"}]})
            items, provenance = load_expected(path)
        self.assertEqual(len(items), 1)
        self.assertEqual(provenance["entries"], 1)

    def test_duplicate_ids_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(Path(tmp), "exp.json",
                              {"expected": [
                                  {"id": "e1", "bbox_mm": [0, 0, 5, 5],
                                   "frame": "f", "source": "s"},
                                  {"id": "e1", "bbox_mm": [1, 1, 5, 5],
                                   "frame": "f", "source": "s"}]})
            with self.assertRaises(ValueError):
                load_expected(path)

    def test_memory_log_reads_observations(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "memory.jsonl"
            log.write_text(
                json.dumps({"id": "obs-0001", "type": "observation", "session": "day1",
                            "source_view": "fixture_back", "bbox_mm": [52.0, 40.5, 7, 7],
                            "centroid_mm": [55.5, 44.0],
                            "evidence": {"result_json": "r.json", "pair_id": "p",
                                         "region": "R1"}}) + "\n"
                + json.dumps({"id": "lnk-0001", "type": "tray_link"}) + "\n",
                encoding="utf-8")
            items, provenance = load_expected(log)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "obs-0001")
        self.assertEqual(provenance["kind"], "memory_log")

    def test_none_means_no_expectations(self):
        items, provenance = load_expected(None)
        self.assertEqual(items, [])
        self.assertEqual(provenance["entries"], 0)


class AssociationsConfirmationsTest(unittest.TestCase):
    def test_associations_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(Path(tmp), "a.json",
                              {"associations": [{"region": "R1", "candidate_id": "ic-01",
                                                 "score": 0.8, "source": "fp"}]})
            self.assertEqual(len(load_associations(path)), 1)

    def test_bad_score_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(Path(tmp), "a.json",
                              {"associations": [{"region": "R1", "candidate_id": "ic-01",
                                                 "score": 4.0, "source": "fp"}]})
            with self.assertRaises(ValueError):
                load_associations(path)

    def test_confirmations_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(Path(tmp), "c.json",
                              {"confirmations": [{"expected_id": "e1",
                                                  "confirmed_by": "operator",
                                                  "statement": "missing"}]})
            self.assertEqual(len(load_confirmations(path)), 1)

    def test_bad_statement_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(Path(tmp), "c.json",
                              {"confirmations": [{"expected_id": "e1",
                                                  "confirmed_by": "operator",
                                                  "statement": "maybe"}]})
            with self.assertRaises(ValueError):
                load_confirmations(path)


if __name__ == "__main__":
    unittest.main()
