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

from anastylos_memory.cli import DEFAULT_KIT, main
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


def write_result(session: Path, pair_id: str = "pair_01", regions: list | None = None) -> Path:
    regions = regions if regions is not None else [
        {"label": "R1", "bbox_mm": [52.0, 40.5, 6, 6], "centroid_mm": [55.0, 43.5]},
    ]
    path = session / pair_id / "result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "pair_id": pair_id,
        "status": "single_change" if len(regions) == 1 else "multiple_changes",
        "rectified": {"view": "fixture_back"},
        "regions": regions,
    }), encoding="utf-8")
    return path


def import_result(session: Path, result_json: Path, *extra: str) -> dict:
    return json.loads(run([
        "--session-dir", str(session), "import",
        "--result-json", str(result_json), *extra,
    ]))


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

    def test_default_kit_serves_tray_link_and_lookup(self):
        self.assertTrue(Path(DEFAULT_KIT).is_file(), str(DEFAULT_KIT))
        self.assertEqual(Path(DEFAULT_KIT).parent.name, "calibration")
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)
            record_observation(session)
            link_argv = [
                "--session-dir", str(session), "record", "tray-link",
                "--observation", "obs-0001", "--sheet", "surface_a3",
                "--tray", "6", "--confirmed-by", "operator",
            ]
            link = json.loads(run(link_argv))
            self.assertEqual(link["tray"]["sheet_id"], "surface_a3")
            answer = json.loads(run([
                "--session-dir", str(session), "where",
                "--tray", "6", "--sheet", "surface_a3",
            ]))
            self.assertEqual(answer["status"], "located")
            self.assertEqual(answer["observations"][0]["id"], "obs-0001")

    def test_reimporting_identical_evidence_adds_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)
            result = write_result(session)
            first = import_result(session, result)
            self.assertEqual(len(first["imported"]), 1)
            before = (session / "memory.jsonl").read_bytes()
            second = import_result(session, result)
            self.assertEqual(second["imported"], [])
            self.assertEqual(second["skipped"], ["obs-0001"])
            self.assertEqual((session / "memory.jsonl").read_bytes(), before)
            self.assertEqual(len(load_events(session / "memory.jsonl")), 1)

    def test_import_adds_only_regions_not_yet_seen(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)
            result = write_result(session, regions=[
                {"label": "R1", "bbox_mm": [52.0, 40.5, 6, 6], "centroid_mm": [55.0, 43.5]},
                {"label": "R2", "bbox_mm": [70.0, 40.5, 6, 6], "centroid_mm": [73.0, 43.5]},
            ])
            import_result(session, result, "--region", "R1")
            report = import_result(session, result)
            self.assertEqual([item["id"] for item in report["imported"]], ["obs-0002"])
            self.assertEqual(report["skipped"], ["obs-0001"])
            regions = [item["evidence"]["region"] for item in load_events(session / "memory.jsonl")]
            self.assertEqual(regions, ["R1", "R2"])

    def test_changed_file_bytes_import_as_new_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)
            result = write_result(session)
            import_result(session, result)
            result.write_text(json.dumps({
                "pair_id": "pair_01",
                "status": "single_change",
                "rectified": {"view": "fixture_back"},
                "regions": [{"label": "R1", "bbox_mm": [52.0, 40.5, 6, 6], "centroid_mm": [99.0, 99.0]}],
            }), encoding="utf-8")
            report = import_result(session, result)
            self.assertEqual([item["id"] for item in report["imported"]], ["obs-0002"])
            self.assertEqual(report["skipped"], [])

    def test_tray_snapshot_comes_from_kit(self):
        kit, sha = load_kit(KIT)
        self.assertEqual(len(sha), 64)
        tray = resolve_tray(kit, "surface_a3", 1)
        self.assertEqual(tray["sheet_id"], "surface_a3")
        self.assertEqual(len(tray["rect_frame_mm"]), 4)
        with self.assertRaises(ValidationError):
            resolve_tray(kit, "surface_a3", 99)


class DeclaredEventsTest(unittest.TestCase):
    """Additive events for complete disassemblies: phases, declared removals, storage, groups."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.session = Path(self.tmp.name) / "full"

    def tearDown(self):
        self.tmp.cleanup()

    def rec(self, *argv: str) -> dict:
        return json.loads(run(["--session-dir", str(self.session), "record", *argv]))

    def test_declared_sequence_round_trips_and_old_queries_still_work(self):
        p1 = self.rec("phase", "--label", "P1 back up")
        p2 = self.rec("phase", "--label", "P2 opening", "--starts-after", p1["id"])
        shell = self.rec("declared-removal", "--label", "back shell", "--source", "device", "--phase", p2["id"],
                         "--declared-by", "owner", "--note", "witness video")
        stored = self.rec("storage-link", "--target", shell["id"], "--location", "OUT-B", "--confirmed-by", "owner")
        self.assertEqual(stored["basis"]["kind"], "human")
        obs = record_observation(self.session)
        record_link(self.session, obs["id"], tray=3)
        events = load_events(self.session / "memory.jsonl")
        self.assertEqual([e["type"] for e in events],
                         ["phase", "phase", "declared_removal", "storage_link", "observation", "tray_link"])
        answer = where_tray(events, load_kit(KIT)[0], "surface_a3", 3)
        self.assertEqual(answer["status"], "located")

    def test_group_of_declared_screws_with_operator_statement(self):
        phase = self.rec("phase", "--label", "P1")
        screws = [self.rec("declared-removal", "--label", "screw", "--source", f"hole {i}", "--phase", phase["id"],
                           "--declared-by", "owner")["id"] for i in range(1, 4)]
        group = self.rec("group", "--members", ",".join(screws), "--label", "back-shell screws", "--declared-by", "owner")
        self.assertEqual(group["basis"]["kind"], "operator_statement")
        measured = self.rec("group", "--members", ",".join(screws), "--declared-by", "owner",
                            "--measurement", json.dumps({"length_mm": [8.0, 8.0, 8.1]}), "--supersedes", group["id"])
        self.assertEqual(measured["basis"]["kind"], "measured")

    def test_rejects_declared_removal_without_known_phase_or_declarer(self):
        with self.assertRaises(ValidationError):
            self.rec("declared-removal", "--label", "x", "--source", "y", "--phase", "phase-9", "--declared-by", "owner")
        phase = self.rec("phase", "--label", "P1")
        with self.assertRaises(ValidationError):
            self.rec("declared-removal", "--label", "x", "--source", "y", "--phase", phase["id"], "--declared-by", " ")

    def test_rejects_storage_and_groups_pointing_at_unknown_or_wrong_targets(self):
        phase = self.rec("phase", "--label", "P1")
        with self.assertRaises(ValidationError):
            self.rec("storage-link", "--target", "dec-0042", "--location", "OUT-A", "--confirmed-by", "owner")
        with self.assertRaises(ValidationError):
            self.rec("storage-link", "--target", phase["id"], "--location", "OUT-A", "--confirmed-by", "owner")
        one = self.rec("declared-removal", "--label", "nub", "--source", "s", "--phase", phase["id"], "--declared-by", "owner")
        with self.assertRaises(ValidationError):
            self.rec("group", "--members", one["id"], "--declared-by", "owner")

    def test_unknown_event_type_still_fails_loudly(self):
        self.session.mkdir(parents=True)
        (self.session / "memory.jsonl").write_text(json.dumps({"type": "guess", "id": "g-1"}) + chr(10), encoding="utf-8")
        with self.assertRaises(CorruptLogError):
            load_events(self.session / "memory.jsonl")


if __name__ == "__main__":
    unittest.main()
