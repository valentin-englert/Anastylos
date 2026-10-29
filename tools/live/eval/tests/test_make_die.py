import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from synthetic import Take

import make_die
import score_live01

SEED = bytes(range(32))


class Die(unittest.TestCase):
    def test_same_seed_same_die_and_balanced(self):
        a = make_die.derive(SEED, 16, 15)
        self.assertEqual(a, make_die.derive(SEED, 16, 15))
        self.assertEqual(list(a["slots"].values()).count("live"), 8)
        self.assertEqual(list(a["live_questions"].values()).count("L1"), 8)
        self.assertEqual(sorted(a["slots"], key=int), [str(i) for i in range(1, 17)])
        self.assertNotEqual(a["slots"], make_die.derive(bytes(32), 16, 15)["slots"])

    def test_verify_detects_tampering_and_wrong_seed(self):
        die = make_die.build_die(SEED, 8, 8, 12.0, None, 600.0, "2026-10-29T00:00:00+00:00")
        self.assertEqual(make_die.verify(die, SEED), [])
        flipped = dict(die, slots={**die["slots"], "1": "offline" if die["slots"]["1"] == "live" else "live"})
        self.assertEqual(make_die.verify(flipped, SEED), ["slots differ from the assignment derived from the seed"])
        self.assertIn("seed does not match the die's seed_sha256", make_die.verify(die, bytes(32)))

    def test_rejects_non_positive_delay(self):
        with self.assertRaises(ValueError):
            make_die.build_die(SEED, 8, 8, 0, None, 600.0, "t")

    def test_cli_prints_hashes_and_refuses_a_second_die(self):
        with tempfile.TemporaryDirectory() as tmp:
            basis = Path(tmp) / "traces.json"
            basis.write_text("[]", encoding="utf-8")
            cmd = [sys.executable, str(Path(make_die.__file__)), "--out", tmp, "--slots", "6", "--live-questions", "4",
                   "--delivery-delay-s", "12", "--delay-basis", str(basis), "--offline-delay-s", "600"]
            first = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            printed = json.loads(first.stdout)
            self.assertNotIn("slots", printed)  # assignments are never printed
            die = json.loads((Path(tmp) / make_die.DIE_NAME).read_text(encoding="utf-8"))
            seed = make_die.read_seed(Path(tmp) / make_die.SEED_NAME)
            self.assertEqual(make_die.verify(die, seed), [])
            self.assertEqual(die["delay_basis_sha256"], printed["delay_basis_sha256"])
            self.assertNotEqual(subprocess.run(cmd, capture_output=True, text=True).returncode, 0)

    def test_scorer_checks_the_seed(self):
        with tempfile.TemporaryDirectory() as tmp:
            take = Take(Path(tmp) / "take").baseline()
            take.die = {**make_die.build_die(SEED, 6, 4, 10.0, None, 600.0, "t"),
                        "slots": take.die["slots"], "live_questions": take.die["live_questions"]}
            seed_file = Path(tmp) / "seed.txt"
            seed_file.write_text(SEED.hex(), encoding="ascii")
            result = score_live01.score(**take.write(), die_seed=seed_file)
            self.assertTrue(result["contamination"]["seed_problems"]["count"])
            self.assertEqual(result["live_timing"]["verdict"], "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
