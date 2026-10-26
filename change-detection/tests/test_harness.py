"""Hash guard plus the synthetic selftest. Failures stay visible in the report."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anastylos_diff.cli import main
from anastylos_diff.config import canonical_sha256, load_configs


class HarnessTest(unittest.TestCase):
    def test_rejects_wrong_kit_hash(self):
        harness, _kit, meta = load_configs(ROOT / "harness-config.json")
        harness["calibration_config"]["sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "harness-config.json"
            path.write_text(json.dumps(harness), encoding="utf-8")
            with self.assertRaises(SystemExit):
                load_configs(path)
        self.assertEqual(len(meta["kit_config_sha256"]), 64)

    def test_require_config_sha256_aborts(self):
        with self.assertRaises(SystemExit):
            main([
                "run",
                "--input", str(ROOT),
                "--out", str(ROOT / "selftest_output" / "unused"),
                "--require-config-sha256", "f" * 64,
            ])

    def test_synthetic_selftest(self):
        with tempfile.TemporaryDirectory() as directory:
            code = main(["selftest", "--out", directory, "--seed", "20261026"])
            report = json.loads((Path(directory) / "test-report.json").read_text(encoding="utf-8"))
        print(json.dumps(report["summary"], indent=2))
        print(json.dumps(report["metrics"], indent=2))
        for check in report["checks"]:
            print(f"{check['pair_id']:22} {check['status']:22} expected={check['expected']:16} passed={check['passed']} {check['note']}")
        failed = [check for check in report["checks"] if not check["passed"]]
        self.assertEqual(failed, [], json.dumps(failed, indent=2))
        self.assertEqual(code, 0)
        self.assertGreaterEqual(report["summary"]["asserted_cases"], 10)
        self.assertTrue(report["metrics"]["label"].startswith("SYNTHETIC"))
        frozen = canonical_sha256(json.loads((ROOT / "harness-config.json").read_text(encoding="utf-8")))
        self.assertEqual(report["config_meta"]["harness_config_canonical_sha256"], frozen)


if __name__ == "__main__":
    unittest.main()
