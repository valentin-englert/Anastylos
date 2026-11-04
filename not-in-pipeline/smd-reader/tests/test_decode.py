"""Marking decoder checks. Standard library only.

Run from the smd-reader directory:

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anastylos_smd.decode import E96, decode


def reading(code, **kwargs):
    return decode(code, **kwargs)


class DecodeTest(unittest.TestCase):
    def test_three_digit(self):
        result = reading("472")
        self.assertEqual(result["status"], "ok")
        (candidate,) = result["candidates"]
        self.assertEqual(candidate["schemes"], ["3-digit"])
        self.assertEqual(candidate["resistance_ohms_exact"], "4700")
        self.assertEqual(candidate["resistance_ohms"], 4700.0)
        self.assertIn("47 x 10^2", candidate["explanation"])

    def test_four_digit(self):
        result = reading("4702")
        (candidate,) = result["candidates"]
        self.assertEqual(candidate["schemes"], ["4-digit"])
        self.assertEqual(candidate["resistance_ohms_exact"], "47000")

    def test_r_notation(self):
        self.assertEqual(reading("4R7")["candidates"][0]["resistance_ohms_exact"], "4.7")
        self.assertEqual(reading("R47")["candidates"][0]["resistance_ohms_exact"], "0.47")
        self.assertEqual(reading("R010")["candidates"][0]["resistance_ohms_exact"], "0.01")
        self.assertEqual(reading("10R")["candidates"][0]["resistance_ohms_exact"], "10")
        self.assertEqual(reading("4r7")["candidates"][0]["resistance_ohms_exact"], "4.7")

    def test_eia96(self):
        result = reading("01C")
        (candidate,) = result["candidates"]
        self.assertEqual(candidate["schemes"], ["EIA-96"])
        self.assertEqual(candidate["resistance_ohms_exact"], "10000")
        self.assertIn("100 x 100", candidate["explanation"])

    def test_eia96_table_boundaries(self):
        self.assertEqual(len(E96), 96)
        self.assertEqual(list(sorted(E96)), list(E96))
        self.assertEqual(E96[0], 100)
        self.assertEqual(E96[95], 976)
        self.assertEqual(reading("01A")["candidates"][0]["resistance_ohms_exact"], "100")
        self.assertEqual(reading("96F")["candidates"][0]["resistance_ohms_exact"], "97600000")

    def test_eia96_default_letters(self):
        self.assertEqual(reading("01B")["candidates"][0]["resistance_ohms_exact"], "1000")
        self.assertEqual(reading("01D")["candidates"][0]["resistance_ohms_exact"], "100000")
        self.assertEqual(reading("01E")["candidates"][0]["resistance_ohms_exact"], "1000000")
        self.assertEqual(reading("50A")["candidates"][0]["resistance_ohms_exact"], "324")

    def test_zero_jumpers(self):
        for code in ("0", "00", "000", "0000"):
            result = reading(code)
            self.assertEqual(result["status"], "ok", code)
            (candidate,) = result["candidates"]
            self.assertIn("zero-jumper", candidate["schemes"])
            self.assertEqual(candidate["resistance_ohms_exact"], "0")
        merged = reading("000")["candidates"][0]["schemes"]
        self.assertIn("3-digit", merged)

    def test_tolerance_never_inferred(self):
        for code in ("472", "4702", "4R7", "01C"):
            (candidate,) = reading(code)["candidates"]
            self.assertIsNone(candidate["tolerance_percent"], code)
            self.assertIn("does not confirm", candidate["tolerance_context"], code)
        (jumper,) = reading("000")["candidates"]
        self.assertIsNone(jumper["tolerance_percent"])
        self.assertIn("does not apply", jumper["tolerance_context"])

    def test_invalid_markings(self):
        for code in ("", "   ", "XYZ", "R", "47", "47255", "4R7X", "00A", "97A", "01Q", "01X", "R-"):
            result = reading(code)
            self.assertEqual(result["status"], "invalid", code)
            self.assertEqual(result["candidates"], [])

    def test_manufacturer_aliases_opt_in(self):
        self.assertEqual(reading("01S")["status"], "invalid")
        aliased = reading("01S", aliases={"S": "0.1", "R": "0.01"})
        (candidate,) = aliased["candidates"]
        self.assertEqual(candidate["resistance_ohms_exact"], "10")
        self.assertEqual(aliased["status"], "ok")

    def test_conflicting_alias_preserves_both(self):
        result = reading("01C", aliases={"C": "1000"})
        self.assertEqual(result["status"], "ambiguous")
        exact = sorted(c["resistance_ohms_exact"] for c in result["candidates"])
        self.assertEqual(exact, ["10000", "100000"])

    def test_agreeing_alias_stays_single(self):
        result = reading("01C", aliases={"C": "100"})
        self.assertEqual(result["status"], "ok")

    def test_exact_decimals(self):
        self.assertEqual(reading("R010")["candidates"][0]["resistance_ohms"], 0.01)
        self.assertEqual(reading("01A", aliases={"A": "1"})["status"], "ok")
        self.assertEqual(reading("680")["candidates"][0]["resistance_ohms_exact"], "68")
        self.assertEqual(reading("0001")["candidates"][0]["resistance_ohms_exact"], "0")


if __name__ == "__main__":
    unittest.main()
