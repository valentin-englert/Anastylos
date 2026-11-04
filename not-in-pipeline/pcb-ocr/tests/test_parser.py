"""Designator parser checks. Standard library only.

Run from the pcb-ocr directory:

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anastylos_pcb.designators import parse


class ParserTest(unittest.TestCase):
    def test_common_designators(self):
        for raw, prefix, number in (("R12", "R", "12"), ("C5", "C", "5"),
                                    ("U3", "U", "3"), ("J1", "J", "1"),
                                    ("D4", "D", "4"), ("Q7", "Q", "7"),
                                    ("L11", "L", "11"), ("Y2", "Y", "2")):
            reading = parse(raw)
            self.assertTrue(reading["is_designator_like"], raw)
            self.assertEqual(reading["normalized"], raw)
            self.assertEqual(reading["prefix"], prefix)
            self.assertEqual(reading["number"], number)
            self.assertTrue(reading["listed_prefix"], raw)
            self.assertIsNotNone(reading["suggested_type"], raw)

    def test_normalization(self):
        reading = parse("  r-12 ")
        self.assertEqual(reading["normalized"], "R12")
        self.assertEqual(reading["raw_text"], "  r-12 ")
        self.assertEqual(parse("C5.").get("normalized"), "C5")

    def test_suffix_allowed(self):
        reading = parse("R12A")
        self.assertEqual((reading["normalized"], reading["suffix"]), ("R12A", "A"))

    def test_unlisted_prefix_flagged_not_dropped(self):
        reading = parse("REV2")
        self.assertTrue(reading["is_designator_like"])
        self.assertFalse(reading["listed_prefix"])
        self.assertIsNone(reading["suggested_type"])
        self.assertIn("no type suggested", reading["type_note"])

    def test_type_note_disclaims_proof(self):
        self.assertIn("does not prove", parse("R17")["type_note"])

    def test_o_zero_alternates(self):
        reading = parse("RIO")
        self.assertTrue(reading["is_designator_like"])
        alternates = [item["normalized"] for item in reading["alternates"]]
        self.assertIn("R10", [reading["normalized"], *alternates])
        self.assertGreaterEqual(len(alternates), 1)

    def test_i_one_alternates(self):
        reading = parse("CI")
        alternates = [item["normalized"] for item in reading["alternates"]]
        self.assertIn("C1", [reading["normalized"], *alternates])

    def test_false_positives_rejected(self):
        for raw in ("GND", "VCC", "PCB", "5V", "R", "12", "", "---", "TEST"):
            self.assertFalse(parse(raw)["is_designator_like"], raw)

    def test_scores_prefer_fewer_corrections(self):
        reading = parse("R12")
        self.assertEqual(reading["score"], 1.0)
        fixed = parse("RIO")
        self.assertLess(fixed["score"], 1.0)


if __name__ == "__main__":
    unittest.main()
