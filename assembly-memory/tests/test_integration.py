"""End-to-end slice: synthetic result.json -> memory -> tray -> source location.

Needs the calibration virtualenv (change-detection imports cv2). From the
assembly-memory directory:

    ..\\.venv\\Scripts\\python -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parents[0] / "change-detection"))

from anastylos_diff.cli import main as diff_main

from test_memory import KIT, record_link, run


def load_result(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class SliceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._scratch = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._scratch.cleanup)
        out = Path(cls._scratch.name) / "selftest"
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = diff_main(["selftest", "--out", str(out), "--seed", "20261026"])
        assert code == 0, buffer.getvalue()[-2000:]
        cls.results = out / "results"
        singles = sorted(
            path.parent.name
            for path in cls.results.glob("*/result.json")
            if load_result(path).get("status") == "single_change"
        )
        assert len(singles) >= 2, "selftest produced no usable single-change pairs"
        cls.first, cls.second = singles[0], singles[1]
        invalid = next(
            path for path in sorted(cls.results.glob("*/result.json"))
            if load_result(path).get("status") == "registration_invalid"
        )
        cls.invalid = invalid

    def setUp(self):
        self._session = tempfile.TemporaryDirectory()
        self.addCleanup(self._session.cleanup)
        self.session = Path(self._session.name)

    def result_path(self, pair_id: str) -> Path:
        return self.results / pair_id / "result.json"

    def import_pair(self, pair_id: str) -> dict:
        return json.loads(run([
            "--session-dir", str(self.session), "import",
            "--result-json", str(self.result_path(pair_id)),
        ]))

    def test_import_preserves_coordinates_and_provenance(self):
        source = load_result(self.result_path(self.first))
        region = source["regions"][0]
        report = self.import_pair(self.first)
        self.assertEqual(len(report["imported"]), 1)
        obs = report["imported"][0]
        self.assertEqual(obs["source_view"], source["rectified"]["view"])
        self.assertEqual(obs["bbox_mm"], region["bbox_mm"])
        self.assertEqual(obs["centroid_mm"], region["centroid_mm"])
        self.assertEqual(obs["evidence"]["pair_id"], self.first)
        self.assertEqual(obs["evidence"]["region"], region["label"])
        self.assertEqual(obs["evidence"]["result_json"], str(self.result_path(self.first)))
        self.assertEqual(len(obs["evidence"]["result_sha256"]), 64)
        self.assertNotIn("part_name", obs)

    def test_tray_query_returns_original_source_location(self):
        source = load_result(self.result_path(self.first))
        region = source["regions"][0]
        self.import_pair(self.first)
        record_link(self.session, "obs-0001", tray=3)
        answer = json.loads(run([
            "--session-dir", str(self.session), "where",
            "--tray", "3", "--sheet", "surface_a3", "--kit", str(KIT),
        ]))
        self.assertEqual(answer["status"], "located")
        found = answer["observations"][0]
        self.assertEqual(found["source_view"], source["rectified"]["view"])
        self.assertEqual(found["bbox_mm"], region["bbox_mm"])
        self.assertEqual(found["centroid_mm"], region["centroid_mm"])
        self.assertEqual(found["evidence"]["pair_id"], self.first)
        self.assertEqual(found["link_basis"]["kind"], "human")

    def test_unknown_tray_reports_no_observations(self):
        self.import_pair(self.first)
        answer = json.loads(run([
            "--session-dir", str(self.session), "where",
            "--tray", "9", "--sheet", "surface_a3", "--kit", str(KIT),
        ]))
        self.assertEqual(answer["status"], "unknown")
        self.assertEqual(answer["observations"], [])

    def test_shared_tray_reports_ambiguity(self):
        self.import_pair(self.first)
        self.import_pair(self.second)
        record_link(self.session, "obs-0001", tray=2)
        record_link(self.session, "obs-0002", tray=2)
        answer = json.loads(run([
            "--session-dir", str(self.session), "where",
            "--tray", "2", "--sheet", "surface_a3", "--kit", str(KIT),
        ]))
        self.assertEqual(answer["status"], "ambiguous")
        self.assertEqual(len(answer["observations"]), 2)
        by_pair = {item["evidence"]["pair_id"]: item for item in answer["observations"]}
        self.assertEqual(set(by_pair), {self.first, self.second})
        for pair_id, item in by_pair.items():
            source = load_result(self.result_path(pair_id))
            self.assertEqual(item["bbox_mm"], source["regions"][0]["bbox_mm"])

    def test_import_refuses_untrusted_status(self):
        with self.assertRaises((SystemExit, ValueError)):
            run(["--session-dir", str(self.session), "import", "--result-json", str(self.invalid)])
        self.assertFalse((self.session / "memory.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
