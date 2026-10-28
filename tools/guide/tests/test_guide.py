import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from check import known, problems, split_answers  # noqa: E402
from package import build  # noqa: E402


def _write(tmp, name, content):
    path = Path(tmp) / name
    path.write_text(content, encoding="utf-8")
    return path


def _session(tmp, with_inventory=True):
    memory = "\n".join(json.dumps(line) for line in [
        {"type": "observation", "id": "obs-0001", "centroid_mm": [256.0, 132.7], "bbox_mm": [250, 126, 12, 15],
         "evidence": {"pair_id": "E-0017_W-YELLOW", "result_sha256": "a" * 64}},
        {"type": "observation", "id": "obs-0002", "centroid_mm": [100.0, 100.0], "bbox_mm": [95, 95, 10, 10],
         "evidence": {"pair_id": "E-0020_X", "result_sha256": "b" * 64}},
        {"type": "tray_link", "id": "link-0001", "observation_id": "obs-0001", "tray": {"number": 6},
         "basis": {"kind": "human", "confirmed_by": "owner", "note": "L-0001"}},
    ]) + "\n"
    inventory = json.dumps({"sites": [{"id": "W-YELLOW", "rect_mm": [249.5, 125.9, 13, 13]}]})
    return build(_write(tmp, "memory.jsonl", memory),
                 _write(tmp, "inventory.json", inventory) if with_inventory else None, None, None)


class PackageTest(unittest.TestCase):
    def test_parts_carry_source_storage_and_reverse_install_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = _session(tmp)
        first, second = package["parts"]
        self.assertEqual(first["source"]["site"], "W-YELLOW")
        self.assertEqual(first["stored_in"]["cell"], "cell-6")
        self.assertIsNone(second["source"]["site"])
        self.assertEqual([s["observation"] for s in package["install_order"]["steps"]], ["obs-0002", "obs-0001"])
        self.assertTrue(any("obs-0002 has no confirmed storage cell" in u for u in package["unknowns"]))

    def test_without_inventory_every_source_is_anonymous(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = _session(tmp, with_inventory=False)
        self.assertTrue(all(p["source"]["site"] is None for p in package["parts"]))


class GroundingCheckTest(unittest.TestCase):
    def setUp(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.facts = known(_session(tmp))

    def test_grounded_answer_passes(self):
        self.assertEqual(problems("Das Teil aus Zelle 6 gehört nach W-YELLOW [obs-0001, link-0001].", self.facts), [])

    def test_honest_unknown_passes(self):
        self.assertEqual(problems("Das wurde in dieser Sitzung nicht beobachtet. Welches Teil meinst du?", self.facts), [])

    def test_invented_location_and_id_fail(self):
        found = " ".join(problems("Es gehört nach W-RED in Zelle 3 [obs-0009].", self.facts))
        for expected in ("unknown id obs-0009", "unknown cell 3", "unknown site W-RED"):
            self.assertIn(expected, found)

    def test_uncited_claim_and_identity_claim_fail(self):
        self.assertTrue(problems("Erst die Membran, dann die Knöpfe.", self.facts))
        self.assertTrue(any("identity" in p for p in problems("Das ist eindeutig erkannt der gelbe Knopf [obs-0001].", self.facts)))

    def test_answers_file_is_split_by_question(self):
        pairs = split_answers("## Q1: Wohin?\nNach W-YELLOW [obs-0001].\n\n## Q2: Reihenfolge?\nNicht beobachtet.\n")
        self.assertEqual([q for q, _ in pairs], ["Wohin?", "Reihenfolge?"])


if __name__ == "__main__":
    unittest.main()
