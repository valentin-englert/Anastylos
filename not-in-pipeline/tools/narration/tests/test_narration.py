import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from check import problems  # noqa: E402

BUNDLE = {
    "events": [
        {"id": "E-0017", "zone": "W-YELLOW", "kind": "site_changed", "t_before_s": 12.649, "t_after_s": 20.048},
        {"id": "E-0018", "zone": "cell-6", "kind": "appeared", "t_before_s": 12.449, "t_after_s": 23.847},
        {"id": "E-0024", "zone": "cell-9", "kind": "appeared", "t_before_s": 52.242, "t_after_s": 59.441},
    ],
    "links": [
        {"id": "L-0001", "status": "candidate_temporal_exclusive", "deposit": {"id": "E-0018"},
         "removal": {"event": "E-0017", "site": "W-YELLOW"}},
        {"id": "L-0004", "status": "unattributed", "deposit": {"id": "E-0024"}},
    ],
}


class ProvenanceCheckTest(unittest.TestCase):
    def test_faithful_narration_is_accepted(self):
        text = ("Zwischen 12.6 s und 20.0 s leerte sich die Mulde W-YELLOW, danach erschien ein Teil in Zelle 6 "
                "[L-0001, E-0017, E-0018]. Das ist ein Vorschlag, der noch nicht bestätigt ist [L-0001]. "
                "Die Ablage in Zelle 9 bleibt ohne Quelle [L-0004].")
        self.assertEqual(problems(text, BUNDLE), [])

    def test_invented_id_cell_site_and_time_are_rejected(self):
        text = "Ein Teil kam bei 33.0 s aus W-RED in Zelle 3 [L-0009]."
        found = " ".join(problems(text, BUNDLE))
        for expected in ("unknown id L-0009", "unknown cell 3", "unknown site W-RED", "unknown time"):
            self.assertIn(expected, found)

    def test_confirmation_claim_is_rejected(self):
        found = problems("Die gelbe Taste wurde in Zelle 6 bestätigt [L-0001].", BUNDLE)
        self.assertTrue(any("confirmation" in p for p in found))

    def test_uncited_factual_sentence_is_rejected(self):
        found = problems("In Zelle 6 lag danach ein Teil.", BUNDLE)
        self.assertTrue(any("without citation" in p for p in found))


if __name__ == "__main__":
    unittest.main()
