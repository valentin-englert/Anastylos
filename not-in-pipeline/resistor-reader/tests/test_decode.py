"""Deterministic IEC 60062 decoder checks. Standard library only.

Run from the resistor-reader directory:

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anastylos_resistor.decode import decode, format_resistance


class DecodeTest(unittest.TestCase):
    def test_four_band_forward(self):
        (candidate,) = decode(["yellow", "violet", "red", "gold"])
        self.assertEqual(candidate["orientation"], "forward")
        self.assertEqual(candidate["resistance_ohms"], 4700.0)
        self.assertEqual(candidate["resistance"], "4.7 kΩ")
        self.assertEqual(candidate["tolerance_percent"], 5.0)
        self.assertIsNone(candidate["tcr_ppm_k"])

    def test_four_band_reversed_input_reads_reversed(self):
        (candidate,) = decode(["gold", "red", "violet", "yellow"])
        self.assertEqual(candidate["orientation"], "reversed")
        self.assertEqual(candidate["sequence"], ["yellow", "violet", "red", "gold"])
        self.assertEqual(candidate["resistance_ohms"], 4700.0)

    def test_five_band(self):
        (candidate,) = decode(["yellow", "violet", "black", "red", "brown"])
        self.assertEqual(candidate["orientation"], "forward")
        self.assertEqual(candidate["resistance_ohms"], 47000.0)
        self.assertEqual(candidate["resistance"], "47 kΩ")
        self.assertEqual(candidate["tolerance_percent"], 1.0)

    def test_fractional_multipliers(self):
        (low,) = decode(["orange", "orange", "silver", "gold"])
        self.assertAlmostEqual(low["resistance_ohms"], 0.33)
        (lower,) = decode(["red", "red", "gold", "silver"])
        self.assertAlmostEqual(lower["resistance_ohms"], 2.2)
        self.assertEqual(lower["tolerance_percent"], 10.0)

    def test_valid_both_directions_yields_two_candidates(self):
        candidates = decode(["red", "black", "black", "brown"])
        self.assertEqual(len(candidates), 2)
        by_orientation = {item["orientation"]: item for item in candidates}
        self.assertEqual(by_orientation["forward"]["resistance_ohms"], 20.0)
        self.assertEqual(by_orientation["reversed"]["resistance_ohms"], 10.0)

    def test_invalid_both_directions_yields_none(self):
        self.assertEqual(decode(["gold", "silver", "gold", "silver"]), [])
        self.assertEqual(decode(["black", "brown", "red", "black"]), [])

    def test_leading_zero_rejected(self):
        self.assertEqual(decode(["black", "brown", "red", "gold"]), [])

    def test_bad_tolerance_color_rejected(self):
        self.assertEqual(decode(["yellow", "violet", "black", "orange"]), [])
        self.assertEqual(decode(["yellow", "violet", "black", "yellow"]), [])

    def test_unknown_color_token_rejected(self):
        self.assertEqual(decode(["yellow", "unknown", "red", "gold"]), [])

    def test_wrong_band_counts_rejected(self):
        self.assertEqual(decode(["red", "red", "red"]), [])
        self.assertEqual(decode(["red"] * 7), [])

    def test_explicit_band_count_must_match(self):
        self.assertEqual(decode(["yellow", "violet", "red", "gold"], band_count=5), [])
        (candidate,) = decode(["yellow", "violet", "red", "gold"], band_count=4)
        self.assertEqual(candidate["orientation"], "forward")

    def test_six_band_tcr_extension(self):
        (candidate,) = decode(["brown", "black", "black", "red", "brown", "red"])
        self.assertEqual(candidate["resistance_ohms"], 10000.0)
        self.assertEqual(candidate["tolerance_percent"], 1.0)
        self.assertEqual(candidate["tcr_ppm_k"], 50)

    def test_six_band_bad_tcr_rejected(self):
        self.assertEqual(decode(["brown", "black", "black", "red", "brown", "gold"]), [])

    def test_aliases(self):
        (candidate,) = decode(["grey", "red", "black", "gold"])
        self.assertEqual(candidate["sequence"][0], "gray")
        self.assertEqual(candidate["resistance_ohms"], 82.0)

    def test_formatting(self):
        self.assertEqual(format_resistance(0.33), "0.33 Ω")
        self.assertEqual(format_resistance(10.0), "10 Ω")
        self.assertEqual(format_resistance(4700.0), "4.7 kΩ")
        self.assertEqual(format_resistance(1_000_000.0), "1 MΩ")


if __name__ == "__main__":
    unittest.main()
