"""Parser unit tests. Standard library only; runs on plain Python.

Run from the component-markings directory:

    py -3.12 -m unittest tests.test_parse -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anastylos_markings.parse import interpret_crop, load_mapping, parse_line, parse_text, variants


def run(lines, conf=92.0, mapping=None):
    return interpret_crop([[line] if isinstance(line, str) else line for line in lines],
                          [conf] * len(lines), mapping)


def kinds(line):
    return [item["kind"] for item in line["parsed"]]


class IntegratedCircuitTest(unittest.TestCase):
    def test_part_number_plus_date_code_ok(self):
        result = run(["LM358N", "2413"])
        self.assertEqual(result["status"], "ok")
        first, second = result["candidates"]
        self.assertEqual(first["identity"]["class"], "part_number")
        self.assertFalse(first["identity"]["resolved"])
        self.assertEqual(kinds(second), ["date_code"])
        self.assertEqual(second["parsed"][0]["week"], "13")

    def test_identity_never_verified(self):
        result = run(["NE555"])
        (candidate,) = result["candidates"]
        self.assertIsNone(candidate["verified_spec"])
        self.assertFalse(candidate["identity"]["resolved"])
        self.assertIn("datasheet", candidate["identity"]["note"])

    def test_rival_part_numbers_ambiguous(self):
        result = run(["NE555", "LM358"])
        self.assertEqual(result["status"], "ambiguous")
        self.assertIn("rival", result["reason"])

    def test_date_without_part_context_ambiguous(self):
        result = run(["2413"])
        self.assertEqual(result["status"], "ambiguous")
        self.assertIn("without part context", result["reason"])

    def test_short_code_unresolved_without_mapping(self):
        result = run(["1AM"])
        self.assertEqual(result["status"], "ambiguous")
        (candidate,) = result["candidates"]
        self.assertEqual(candidate["identity"]["class"], "short_code")
        self.assertFalse(candidate["identity"]["resolved"])

    def test_short_code_resolved_with_mapping(self):
        result = run(["1AM"], mapping={"1AM": "MMBT3904"})
        self.assertEqual(result["status"], "ok")
        identity = result["candidates"][0]["identity"]
        self.assertTrue(identity["resolved"])
        self.assertEqual(identity["resolved_identity"], "MMBT3904")
        self.assertEqual(identity["resolution_source"], "operator-supplied mapping")
        self.assertIsNone(result["candidates"][0]["verified_spec"])

    def test_manufacturer_code_stays_unresolved(self):
        result = run(["TI"])
        self.assertEqual(result["status"], "ambiguous")
        identity = result["candidates"][0]["identity"]
        self.assertEqual(identity["class"], "manufacturer_code")
        self.assertFalse(identity["resolved"])

    def test_diode_identifier_transcribed_not_capacitance(self):
        result = run(["1N4148"])
        self.assertEqual(result["status"], "ok")
        (candidate,) = result["candidates"]
        self.assertEqual(candidate["parsed"], [])
        self.assertEqual(candidate["identity"]["class"], "part_number")


class CapacitorTest(unittest.TestCase):
    def test_explicit_capacitance_ok(self):
        for text, farads in (("100n", "0.0000001"), ("4u7", "0.0000047"),
                             ("10p", "0.00000000001"), ("2.2U", "0.0000022")):
            with self.subTest(text=text):
                result = run([text])
                self.assertEqual(result["status"], "ok")
                (entry,) = result["candidates"][0]["parsed"]
                self.assertEqual(entry["kind"], "capacitance")
                self.assertEqual(entry["farads_exact"], farads)

    def test_bare_eia3_quantity_unconfirmed(self):
        result = run(["104"])
        self.assertEqual(result["status"], "ambiguous")
        self.assertIn("quantity unconfirmed", result["reason"])
        (candidate,) = result["candidates"]
        self.assertIn("eia3_code", kinds(candidate))
        eia3 = next(item for item in candidate["parsed"] if item["kind"] == "eia3_code")
        self.assertEqual(eia3["picofarads_exact"], "100000")

    def test_tolerance_letter_observed_not_confirmed(self):
        result = run(["104K"])
        kinds_seen = kinds(result["candidates"][0])
        self.assertIn("eia3_code", kinds_seen)
        self.assertIn("tolerance_letter", kinds_seen)
        tol = next(item for item in result["candidates"][0]["parsed"]
                   if item["kind"] == "tolerance_letter")
        self.assertEqual(tol["letter"], "K")
        self.assertIn("unconfirmed", tol["note"])
        self.assertEqual(result["status"], "ambiguous")

    def test_rival_capacitance_values_ambiguous(self):
        result = run([["100n", "47n"]])
        self.assertEqual(result["status"], "ambiguous")
        self.assertIn("rival capacitance", result["reason"])

    def test_polarity_mark_observed(self):
        (candidate,) = run([["+", "104"]])["candidates"]
        self.assertIn("polarity_mark", kinds(candidate))

    def test_partial_digits_unresolved(self):
        result = run(["10"])
        self.assertEqual(result["status"], "ambiguous")
        identity = result["candidates"][0]["identity"]
        self.assertEqual(identity["class"], "lot_or_numeric")
        self.assertFalse(identity["resolved"])


class FuseTest(unittest.TestCase):
    def test_current_rating_ok(self):
        result = run(["T2A"])
        self.assertEqual(result["status"], "ok")
        (entry,) = result["candidates"][0]["parsed"]
        self.assertEqual((entry["kind"], entry["value_exact"], entry["unit"]),
                         ("current", "2", "A"))
        self.assertEqual(entry["amps_exact"], "2")
        self.assertEqual(entry["speed_letter"]["letter"], "T")
        self.assertIn("unconfirmed", entry["speed_letter"]["context"])

    def test_milliamp_rating_ok(self):
        result = run(["F500MA"])
        (entry,) = result["candidates"][0]["parsed"]
        self.assertEqual((entry["value_exact"], entry["unit"], entry["amps_exact"]),
                         ("500", "mA", "0.5"))

    def test_combined_current_voltage_ok(self):
        result = run([["T", "2A", "250V"]])
        self.assertEqual(result["status"], "ok")
        (candidate,) = result["candidates"]
        self.assertEqual(kinds(candidate).count("current"), 2)  # word + spanning joint
        self.assertIn("voltage", kinds(candidate))
        self.assertIsNone(candidate["identity"])

    def test_voltage_alone_is_not_a_fuse_claim(self):
        result = run(["250V"])
        (entry,) = result["candidates"][0]["parsed"]
        self.assertEqual(entry["kind"], "voltage")
        self.assertIn("component class unconfirmed", entry["note"])


class ConfusionTest(unittest.TestCase):
    def test_repairs_labeled_and_ambiguous(self):
        result = run(["B01"])
        self.assertEqual(result["status"], "ambiguous")
        self.assertIn("repair", result["reason"])
        (candidate,) = result["candidates"]
        self.assertTrue(candidate["repaired"])
        self.assertEqual(candidate["subs"], [{"position": 0, "from": "B", "to": "8", "word": 0}])
        self.assertIn("eia3_code", kinds(candidate))

    def test_raw_wins_when_it_reads(self):
        (candidate,) = run(["104"])["candidates"]
        self.assertFalse(candidate["repaired"])
        self.assertEqual(candidate["subs"], [])

    def test_variants_capped_and_cheapest_first(self):
        every = variants("B8851")
        self.assertLessEqual(len(every), 12)
        self.assertEqual(every[0]["text"], "B8851")
        costs = [len(item["subs"]) for item in every]
        self.assertEqual(costs, sorted(costs))


class VerdictTest(unittest.TestCase):
    def test_noise_is_unknown(self):
        result = run(["###"])
        self.assertEqual((result["status"], result["candidates"]),
                         ("unknown", []))

    def test_empty_is_unknown(self):
        result = interpret_crop([[]], [None])
        self.assertEqual(result["status"], "unknown")

    def test_low_confidence_ambiguous(self):
        result = run(["LM358N"], conf=25.0)
        self.assertEqual(result["status"], "ambiguous")
        self.assertIn("below 40", result["reason"])

    def test_result_is_json_serializable(self):
        result = run(["LM358N", "2413"])
        json.dumps(result)

    def test_parse_line_keeps_word_evidence(self):
        line = parse_line(["T", "2A"])
        self.assertEqual(line["match"], "T2A")
        self.assertEqual([word["raw"] for word in line["words"]], ["T", "2A"])
        self.assertTrue(any(item.get("spanning_words") for item in line["parsed"]))


class MappingFileTest(unittest.TestCase):
    def test_load_mapping_accepts_strings_and_objects(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mapping.json"
            path.write_text(json.dumps({"codes": {"1AM": "MMBT3904",
                                                 "J3Y": {"identity": "S8050"}}}), encoding="utf-8")
            mapping = load_mapping(path)
            self.assertEqual(mapping["1AM"], "MMBT3904")
            result = run(["J3Y"], mapping=mapping)
            self.assertEqual(result["candidates"][0]["identity"]["resolved_identity"], "S8050")

    def test_load_mapping_rejects_bad_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "gone.json"
            with self.assertRaises(ValueError):
                load_mapping(missing)
            bad = Path(tmp) / "bad.json"
            bad.write_text(json.dumps({"codes": {}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_mapping(bad)
            broken = Path(tmp) / "broken.json"
            broken.write_text("{nope", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_mapping(broken)


if __name__ == "__main__":
    unittest.main()
