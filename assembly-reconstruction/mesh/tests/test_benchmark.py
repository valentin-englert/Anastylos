"""Benchmark profiles, pre-registration, and a tiny end-to-end rep."""

import csv
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from anastylos_mesh.benchmark import (
    HELDOUT_BOUNDS,
    build_configs,
    run_benchmark,
    run_single,
    write_expected_outcomes,
)


def _tiny_config():
    return {
        "name": "test-tiny", "split": "dev", "scene": "box",
        "scene_params": {}, "views": 4, "width": 96, "height": 72,
        "focal_px": None, "elevations_deg": [25.0, 45.0],
        "azimuth_start_deg": 0.0, "resolution": 12, "margin": 0.05,
        "min_observed": 2, "eval_samples": 200, "eval_tau_mm": 1.0,
        "seed": 0,
    }


class TestBenchmarkProfiles(unittest.TestCase):
    def test_fixed_profiles_and_splits(self):
        configs = build_configs()
        self.assertEqual(len(configs), 7)
        by_split = {}
        for cfg in configs:
            by_split.setdefault(cfg["split"], []).append(cfg["name"])
        self.assertEqual(by_split["dev"],
                         ["dev-box-r48", "dev-box-r96", "dev-box-r144"])
        self.assertEqual(by_split["validation"],
                         ["val-cylinder-r96", "val-stepped-r96"])
        self.assertEqual(by_split["held-out"],
                         ["heldout-rotated-stepped-r96",
                          "heldout-tall-cylinder-r96"])
        # Box trade-off spans 3 resolutions within the 8M voxel cap.
        self.assertEqual([c["resolution"] for c in configs[:3]], [48, 96, 144])

    def test_heldout_angles_and_seeds_unseen_in_dev(self):
        configs = build_configs()
        dev = [c for c in configs if c["split"] == "dev"]
        held = [c for c in configs if c["split"] == "held-out"]
        dev_angles = {c["azimuth_start_deg"] for c in dev}
        dev_seeds = {c["seed"] for c in dev}
        for cfg in held:
            self.assertNotIn(cfg["azimuth_start_deg"], dev_angles)
            self.assertNotIn(cfg["seed"], dev_seeds)
            self.assertNotEqual(cfg["azimuth_start_deg"], 22.5)  # unit-test ring
        # Every held-out config has pre-registered bounds.
        for cfg in held:
            self.assertIn(cfg["name"], HELDOUT_BOUNDS)
            self.assertGreater(len(HELDOUT_BOUNDS[cfg["name"]]), 0)

    def test_expected_outcomes_written_before_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "expected-outcomes.json"
            doc = write_expected_outcomes(path, build_configs())
            self.assertTrue(doc["created_before_execution"])
            self.assertEqual(set(doc["bounds"]),
                             {"heldout-rotated-stepped-r96",
                              "heldout-tall-cylinder-r96"})
            body = path.read_text(encoding="utf-8")
            self.assertNotIn(str(Path(tmp).resolve()), body)  # no abs paths


class TestBenchmarkRun(unittest.TestCase):
    def test_tiny_rep_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rec = run_single(_tiny_config(), root / "runs" / "rep0", 0, root)
            self.assertEqual(rec["status"], "ok")
            self.assertEqual(rec["views"], 4)
            self.assertEqual(rec["mask_provenance"], "synthetic")
            self.assertEqual(len(rec["input_sha256"]), 64)
            self.assertTrue(rec["reopen_ok"])
            self.assertTrue(rec["watertight"])
            self.assertGreater(rec["occupied_voxels"], 0)
            self.assertGreater(rec["total_ms"], 0)
            self.assertGreater(rec["peak_bytes"], 0)
            self.assertIn("tracemalloc", rec["peak_method"])
            self.assertTrue((root / rec["mesh_relpath"]).exists())
            self.assertFalse(os.path.isabs(rec["mesh_relpath"]))

    def test_tiny_full_run_writes_json_and_csv(self):
        import anastylos_mesh.benchmark as bench
        tiny = [_tiny_config()]
        tiny[0]["name"] = "dev-tiny"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "bench"
            orig_configs, orig_bounds = bench.build_configs, bench.HELDOUT_BOUNDS
            bench.build_configs = lambda: tiny
            bench.HELDOUT_BOUNDS = {}
            try:
                doc = run_benchmark(root, repeats=1)
            finally:
                bench.build_configs = orig_configs
                bench.HELDOUT_BOUNDS = orig_bounds
            self.assertEqual(doc["repeats"], 1)
            self.assertIn("cpu_model", doc)
            payload = (root / "benchmark_results.json").read_text(
                encoding="utf-8")
            self.assertNotIn(str(root.resolve()), payload)
            with open(root / "benchmark_results.csv", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["config"], "dev-tiny")
            stats = doc["configs"]["dev-tiny"]["stats_mean_min_max"]
            self.assertIn("mean", stats["total_ms"])
            # Refuses to silently overwrite a previous run.
            with self.assertRaises(FileExistsError):
                run_benchmark(root, repeats=1)


if __name__ == "__main__":
    unittest.main()
