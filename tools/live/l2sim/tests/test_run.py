import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "full01"


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def main(self, *args):
        return run.main(["--data", str(FIXTURE), *args])

    def test_l2_fake_run_writes_results_and_calls(self):
        out = self.tmp / "l2"
        document = self.main("--arm", "L2", "--proposer", "fake:good", "--seeds", "0", "--out", str(out))
        written = json.loads((out / "results.json").read_text(encoding="utf-8"))
        self.assertEqual(written["summary"], document["summary"])
        self.assertEqual(written["summary"]["episode_runs"], 13)
        self.assertTrue(written["complete"])
        self.assertTrue(written["summary"]["integrity_ok"])
        self.assertEqual(set(written["summary_by_source"]), {"FULL-01 v21", "synthetic"})
        self.assertIn("prereg_sha256", written["provenance"])
        calls = (out / "calls.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(calls), written["proposer_calls_made"])
        self.assertIn("trace", written["episode_runs"][0])

    def test_call_cap_marks_the_run_incomplete(self):
        out = self.tmp / "capped"
        document = self.main("--arm", "L2", "--proposer", "fake:good", "--seeds", "0", "--max-calls", "5",
                             "--out", str(out))
        self.assertFalse(document["complete"])
        self.assertEqual(document["proposer_calls_made"], 5)

    def test_p0_and_pr_need_no_proposer(self):
        for arm in ("P0", "PR"):
            document = self.main("--arm", arm, "--seeds", "0,1", "--episodes", "full01",
                                 "--out", str(self.tmp / arm))
            self.assertEqual(document["summary"]["totals"]["proposer_calls"], 0)
            self.assertFalse((self.tmp / arm / "calls.jsonl").exists())

    def test_compare_applies_acceptance_to_paired_runs(self):
        import compare
        common = ("--seeds", "0", "--episodes", "synthetic")
        for arm in ("P0", "PR"):
            self.main("--arm", arm, *common, "--out", str(self.tmp / arm))
        self.main("--arm", "L2", "--proposer", "fake:adversarial", *common, "--out", str(self.tmp / "L2"))
        result = compare.main(["--p0", str(self.tmp / "P0"), "--pr", str(self.tmp / "PR"),
                               "--l2", str(self.tmp / "L2")])
        self.assertEqual(result["acceptance"]["verdict"], "rejected")
        self.assertFalse(result["acceptance"]["checks"]["invalid_rate_ok"])
        self.assertEqual(sum(result["paired_l2_vs_p0"].values()), 10)
        self.assertTrue((self.tmp / "L2" / "comparison.json").exists())

    def test_refuses_repo_paths_and_existing_results(self):
        with self.assertRaises(SystemExit):
            self.main("--arm", "P0", "--out", str(run.HERE / "out"))
        out = self.tmp / "once"
        self.main("--arm", "P0", "--seeds", "0", "--episodes", "synthetic", "--out", str(out))
        with self.assertRaises(SystemExit):
            self.main("--arm", "P0", "--seeds", "0", "--episodes", "synthetic", "--out", str(out))


if __name__ == "__main__":
    unittest.main()
