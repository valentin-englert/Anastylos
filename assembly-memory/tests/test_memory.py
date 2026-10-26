"""Checks for the ANASTYLOS assembly memory core. Standard library only.

Run from the assembly-memory directory:

    python -m unittest discover -s tests -v
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

from anastylos_memory.cli import main
from anastylos_memory.query import resolve_tray, where_observation, where_tray, load_kit
from anastylos_memory.store import CorruptLogError, ValidationError, load_events, validate_observation

KIT = ROOT.parents[0] / "calibration" / "calibration-config.json"


def run(argv: list[str]) -> str:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main(argv)
    assert code == 0
    return buffer.getvalue()


def record_observation(session: Path, **overrides) -> dict:
    args = {
        "--view": "fixture_back",
        "--bbox": "52.125,40.5,6,6",
        "--centroid": "55.125,43.5",
        "--result-json": str(session / "screw_01" / "result.json"),
        "--pair-id": "screw_01",
        "--region": "R1",
    }
    args.update({f"--{key.replace('_', '-')}": value for key, value in overrides.items()})
    argv = ["--session-dir", str(session), "record", "observation"]
    for key, value in args.items():
        argv += [key, value]
    return json.loads(run(argv))


def record_link(session: Path, observation: str, tray: int = 3, **overrides) -> dict:
    argv = [
        "--session-dir", str(session), "record", "tray-link",
        "--observation", observation, "--sheet", "surface_a3",
        "--tray", str(tray), "--kit", str(KIT),
    ]
    if "confirmed_by" not in overrides and "evidence_result_json" not in overrides:
        argv += ["--confirmed-by", "operator"]
    for key, value in overrides.items():
        argv += [f"--{key.replace('_', '-')}", value]
    return json.loads(run(argv))


class MemoryTest(unittest.TestCase):
    def test_persists_coordinates_verbatim_with_stable_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)
            first = record_observation(session)
            second = record_observation(session, pair_id="screw_02", region="R1")
            self.assertEqual(first["id"], "obs-0001")
            self.assertEqual(second["id"], "obs-0002")
            lines = (session / "memory.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            stored = json.loads(lines[0])
            self.assertEqual(stored["bbox_mm"], [52.125, 40.5, 6, 6])
            self.assertEqual(stored["centroid_mm"], [55.125, 43.5])
            self.assertEqual(stored["source_view"], "fixture_back")
            self.assertEqual(stored["evidence"]["pair_id"], "screw_01")
            self.assertTrue(stored["timestamp"].endswith("Z"))
            self.assertEqual(load_events(session / "memory.jsonl"), [stored, json.loads(lines[1])])

    def test_observation_lookup_returns_original_location(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)
            record_observation(session)
            answer = json.loads(run(["--session-dir", str(session), "where", "--observation", "obs-0001"]))
            self.assertEqual(answer["status"], "unassigned")
            self.assertEqual(answer["observation"]["source_view"], "fixture_back")
            self.assertEqual(answer["observation"]["bbox_mm"], [52.125, 40.5, 6, 6])
            self.assertEqual(answer["observation"]["centroid_mm"], [55.125, 43.5])
            self.assertEqual(answer["observation"]["evidence"]["region"], "R1")
            record_link(session, "obs-0001")
            assigned = json.loads(run(["--session-dir", str(session), "where", "--observation", "obs-0001"]))
            self.assertEqual(assigned["status"], "assigned")
            self.assertEqual(assigned["observation"]["bbox_mm"], [52.125, 40.5, 6, 6])
            self.assertEqual(assigned["trays"][0]["tray"]["number"], 3)
            self.assertEqual(assigned["trays"][0]["basis"]["kind"], "human")

    def test_tray_lookup_resolves_removal_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)
            record_observation(session)
            record_link(session, "obs-0001", tray=5)
            answer = json.loads(run([
                "--session-dir", str(session), "where",
                "--tray", "5", "--sheet", "surface_a3", "--kit", str(KIT),
            ]))
            self.assertEqual(answer["status"], "located")
            self.assertEqual(answer["tray"]["number"], 5)
            self.assertEqual(len(answer["observations"]), 1)
            found = answer["observations"][0]
            self.assertEqual(found["id"], "obs-0001")
            self.assertEqual(found["source_view"], "fixture_back")
            self.assertEqual(found["bbox_mm"], [52.125, 40.5, 6, 6])

    def test_tray_with_indistinguishable_candidates_is_ambiguous(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)
            record_observation(session, pair_id="screw_01")
            record_observation(session, pair_id="screw_02")
            record_link(session, "obs-0001", tray=2)
            record_link(session, "obs-0002", tray=2)
            answer = json.loads(run([
                "--session-dir", str(session), "where",
                "--tray", "2", "--sheet", "surface_a3", "--kit", str(KIT),
            ]))
            self.assertEqual(answer["status"], "ambiguous")
            ids = sorted(item["id"] for item in answer["observations"])
            self.assertEqual(ids, ["obs-0001", "obs-0002"])
            for item in answer["observations"]:
                self.assertIn("source_view", item)
                self.assertIn("bbox_mm", item)
                self.assertIn("evidence", item)

    def test_unknown_tray_and_unknown_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)
            record_observation(session)
            empty = json.loads(run([
                "--session-dir", str(session), "where",
                "--tray", "7", "--sheet", "surface_a3", "--kit", str(KIT),
            ]))
            self.assertEqual(empty["status"], "unknown")
            self.assertEqual(empty["observations"], [])
            missing = json.loads(run(["--session-dir", str(session), "where", "--observation", "obs-9999"]))
            self.assertEqual(missing, {"status": "unknown", "id": "obs-9999"})
            events = load_events(session / "memory.jsonl")
            self.assertEqual(where_observation(events, "obs-9999")["status"], "unknown")
            kit, _sha = load_kit(KIT)
            self.assertEqual(where_tray(events, kit, "surface_a3", 7)["status"], "unknown")

    def test_rejects_link_without_evidence_or_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)
            record_observation(session)
            before = (session / "memory.jsonl").read_bytes()
            with self.assertRaises(SystemExit):
                main([
                    "--session-dir", str(session), "record", "tray-link",
                    "--observation", "obs-0001", "--sheet", "surface_a3",
                    "--tray", "1", "--kit", str(KIT),
                ])
            self.assertEqual((session / "memory.jsonl").read_bytes(), before)
            self.assertEqual(len(load_events(session / "memory.jsonl")), 1)

    def test_rejects_link_to_missing_observation_and_bad_tray(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)
            with self.assertRaises((SystemExit, ValidationError)):
                main([
                    "--session-dir", str(session), "record", "tray-link",
                    "--observation", "obs-0001", "--sheet", "surface_a3",
                    "--tray", "1", "--kit", str(KIT), "--confirmed-by", "operator",
                ])
            record_observation(session)
            with self.assertRaises((SystemExit, ValidationError)):
                main([
                    "--session-dir", str(session), "record", "tray-link",
                    "--observation", "obs-0001", "--sheet", "surface_a3",
                    "--tray", "99", "--kit", str(KIT), "--confirmed-by", "operator",
                ])
            self.assertEqual(len(load_events(session / "memory.jsonl")), 1)

    def test_rejects_invented_part_identity(self):
        event = {
            "type": "observation",
            "session": "s",
            "source_view": "fixture_back",
            "bbox_mm": [1, 2, 3, 4],
            "centroid_mm": [1, 2],
            "evidence": {"result_json": "r.json", "pair_id": "p", "region": "R1", "result_sha256": None},
            "part_name": "M3 screw",
        }
        with self.assertRaises(ValidationError):
            validate_observation(event, set())

    def test_corrections_append_and_queries_follow_successor(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)
            record_observation(session)
            record_link(session, "obs-0001", tray=4)
            first_line = (session / "memory.jsonl").read_text(encoding="utf-8").splitlines()[0]
            fixed = record_observation(session, bbox="52.5,40.5,6,6", supersedes="obs-0001")
            self.assertEqual(fixed["id"], "obs-0002")
            lines = (session / "memory.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 3)
            self.assertEqual(lines[0], first_line)
            answer = json.loads(run(["--session-dir", str(session), "where", "--observation", "obs-0001"]))
            self.assertEqual(answer["superseded_by"], "obs-0002")
            self.assertEqual(answer["observation"]["bbox_mm"], [52.5, 40.5, 6, 6])
            self.assertEqual(answer["status"], "assigned")
            kit, _sha = load_kit(KIT)
            tray_answer = where_tray(load_events(session / "memory.jsonl"), kit, "surface_a3", 4)
            self.assertEqual(tray_answer["status"], "located")
            self.assertEqual(tray_answer["observations"][0]["id"], "obs-0002")

    def test_corrupt_log_fails_loudly_with_line_number(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)
            record_observation(session)
            with (session / "memory.jsonl").open("a", encoding="utf-8") as handle:
                handle.write("{not json\n")
            with self.assertRaises(CorruptLogError) as raised:
                load_events(session / "memory.jsonl")
            self.assertIn("line 2", str(raised.exception))
            with self.assertRaises(CorruptLogError):
                main(["--session-dir", str(session), "where", "--observation", "obs-0001"])

    def test_evidence_sha256_when_file_exists_else_null(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)
            result = session / "screw_01" / "result.json"
            result.parent.mkdir()
            result.write_text('{"status": "single_change"}', encoding="utf-8")
            present = record_observation(session)
            self.assertEqual(len(present["evidence"]["result_sha256"]), 64)
            missing = record_observation(session, pair_id="gone", result_json=str(session / "gone" / "result.json"))
            self.assertIsNone(missing["evidence"]["result_sha256"])

    def test_tray_snapshot_comes_from_kit(self):
        kit, sha = load_kit(KIT)
        self.assertEqual(len(sha), 64)
        tray = resolve_tray(kit, "surface_a3", 1)
        self.assertEqual(tray["sheet_id"], "surface_a3")
        self.assertEqual(len(tray["rect_frame_mm"]), 4)
        with self.assertRaises(ValidationError):
            resolve_tray(kit, "surface_a3", 99)


if __name__ == "__main__":
    unittest.main()
