"""State projector tests: read-only, deterministic, evidence-honest.

Run from the assembly-reconstruction directory:

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anastylos_reconstruction.projector import build_state  # noqa: E402
from anastylos_reconstruction.schema import (  # noqa: E402
    SCHEMA_VERSION,
    validate_state,
)

FIX = ROOT / "fixtures" / "synthetic"


def _fixture_state(**kwargs):
    params = {"memory_path": str(FIX / "memory.jsonl"),
              "results_dir": str(FIX / "results"),
              "graph_path": str(FIX / "assembly_graph.json")}
    params.update(kwargs)
    return build_state(**params)


class ProjectorContract(unittest.TestCase):
    def test_fixture_state_validates_and_keeps_version(self):
        state = _fixture_state()
        validate_state(state)
        self.assertEqual(state["schema_version"], SCHEMA_VERSION)
        self.assertEqual(state["projector"]["version"], "1.1.0")
        for component in state["components"]:
            self.assertEqual(component["kind"], "observation")
            self.assertIsNone(component["declaration"])
            self.assertIn(component["representation"]["kind"],
                          ("mask_contour", "evidence_crop", "declared_card"))

    def test_live_components_in_log_order_with_superseded(self):
        state = _fixture_state()
        self.assertEqual([c["id"] for c in state["components"]],
                         ["obs-0001", "obs-0003", "obs-0004",
                          "obs-0005", "obs-0006"])
        self.assertEqual(state["superseded"], ["obs-0002"])
        for index, component in enumerate(state["components"]):
            self.assertEqual(component["timeline_index"], index)

    def test_identity_always_unknown_without_label(self):
        state = _fixture_state()
        for component in state["components"]:
            self.assertEqual(component["identity"]["status"], "unknown")
            self.assertIsNone(component["identity"]["label"])

    def test_physical_coordinates_verbatim_from_log(self):
        state = _fixture_state()
        first = state["components"][0]
        self.assertEqual(first["physical"]["source_view"], "fixture_back")
        self.assertEqual(first["physical"]["bbox_mm"], [50.0, 40.0, 8.0, 8.0])
        self.assertEqual(first["physical"]["centroid_mm"], [54.0, 44.0])
        self.assertEqual(first["physical"]["evidence"]["pair_id"], "p_back")

    def test_regions_resolved_or_reported_missing(self):
        state = _fixture_state()
        by_id = {c["id"]: c for c in state["components"]}
        self.assertTrue(by_id["obs-0001"]["physical"]["region_resolved"])
        self.assertEqual(by_id["obs-0001"]["physical"]["region"]["view"],
                         "fixture_back")
        self.assertFalse(by_id["obs-0004"]["physical"]["region_resolved"])
        self.assertIsNotNone(by_id["obs-0004"]["physical"]["missing_reason"])
        self.assertEqual(len(state["missing_evidence"]), 1)
        self.assertEqual(state["missing_evidence"][0]["observation_id"], "obs-0004")

    def test_crops_only_for_existing_rectified_images(self):
        state = _fixture_state()
        by_id = {c["id"]: c for c in state["components"]}
        crops = by_id["obs-0001"]["crops"]
        self.assertEqual([c["role"] for c in crops],
                         ["before_rectified", "after_rectified"])
        self.assertEqual(crops[0]["clip_px"], [256, 216, 32, 32])
        self.assertIsNone(by_id["obs-0001"]["crop_unavailable"])
        # tray.json lists no output_files: unavailable with a reason.
        self.assertEqual(by_id["obs-0003"]["crops"], [])
        self.assertIn("output_files", by_id["obs-0003"]["crop_unavailable"])
        # Unresolved region: no crop attempted.
        self.assertEqual(by_id["obs-0004"]["crops"], [])
        self.assertIsNotNone(by_id["obs-0004"]["crop_unavailable"])

    def test_tray_links_preserve_basis_kind(self):
        state = _fixture_state()
        by_id = {c["id"]: c for c in state["components"]}
        human = by_id["obs-0001"]["tray"]
        self.assertEqual(human["link_id"], "link-0001")
        self.assertEqual(human["basis"]["kind"], "human")
        self.assertEqual(human["basis"]["confirmed_by"], "fixture-operator")
        self.assertEqual(human["tray"]["number"], 3)
        evidence = by_id["obs-0003"]["tray"]
        self.assertEqual(evidence["basis"]["kind"], "evidence")
        self.assertIsNone(by_id["obs-0005"]["tray"])
        self.assertIsNone(by_id["obs-0005"]["trays"])

    def test_relationships_copied_verbatim_from_graph(self):
        state = _fixture_state()
        graph = json.loads((FIX / "assembly_graph.json").read_text(encoding="utf-8"))
        rel = state["relationships"]
        self.assertEqual(rel["confirmed"], graph["confirmed"])
        self.assertEqual(rel["candidates"], graph["candidates"])
        self.assertEqual(rel["orderings"], graph["orderings"])
        self.assertEqual(rel["reassembly"], graph["reassembly"])
        confirmed = rel["confirmed"][0]
        self.assertEqual((confirmed["before"], confirmed["after"]),
                         ("obs-0001", "obs-0005"))
        self.assertEqual(confirmed["confirmed_by"], "fixture-operator")
        self.assertEqual(len(rel["candidates"]), 2)

    def test_no_graph_input_is_explicit_unknown(self):
        state = _fixture_state(graph_path=None)
        validate_state(state)
        self.assertEqual(state["relationships"]["status"], "unknown")
        self.assertIsNone(state["relationships"]["source"])
        self.assertEqual(state["relationships"]["confirmed"], [])

    def test_unreadable_graph_is_reported_not_crash(self):
        state = _fixture_state(graph_path=str(FIX / "no-such-graph.json"))
        self.assertIn("unreadable", state["relationships"]["reason"])

    def test_provenance_hashes_match_file_bytes(self):
        state = _fixture_state()
        provenance = state["provenance"]
        expected = hashlib.sha256(
            (FIX / "memory.jsonl").read_bytes()).hexdigest()
        self.assertEqual(provenance["memory_sha256"], expected)
        for path, digest in provenance["result_files"].items():
            self.assertEqual(digest, hashlib.sha256(
                Path(path).read_bytes()).hexdigest())

    def test_deterministic_bytes_across_runs(self):
        first = json.dumps(_fixture_state(), indent=2)
        second = json.dumps(_fixture_state(), indent=2)
        self.assertEqual(first, second)

    def test_exploded_layout_single_column_stack(self):
        state = _fixture_state()
        for component in state["components"]:
            box = component["exploded"]
            self.assertEqual(box["units"], "layout_px")
            self.assertEqual(box["column"], 0)
        rows = [c["exploded"]["row"] for c in state["components"]]
        self.assertEqual(rows, [0, 1, 2, 3, 4])
        ys = [c["exploded"]["y"] for c in state["components"]]
        self.assertEqual(ys, sorted(ys))
        # Symbol size is 4 layout-px per physical mm.
        first = state["components"][0]["exploded"]
        self.assertEqual((first["w"], first["h"]), (32.0, 32.0))

    def test_inputs_never_modified(self):
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in [FIX / "memory.jsonl",
                               FIX / "assembly_graph.json",
                               FIX / "results" / "back.json",
                               FIX / "results" / "tray.json"]}
        _fixture_state()
        for path, digest in before.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                             digest)


class ProjectorEdgeCases(unittest.TestCase):
    def test_operator_declared_events_are_projected_with_null_geometry(self):
        declared = [
            {"type": "phase", "id": "phase-0001", "session": "s", "label": "P1", "starts_after": None},
            {"type": "declared_removal", "id": "dec-0001", "session": "s", "operator_label": "back shell",
             "source": "device", "phase": "phase-0001", "declared_by": "owner", "evidence_note": None,
             "t_s": None},
            {"type": "storage_link", "id": "store-0001", "session": "s", "target_id": "dec-0001",
             "location": "OUT-B", "basis": {"kind": "human", "confirmed_by": "owner"}},
            {"type": "compatible_group", "id": "grp-0001", "session": "s",
             "members": ["dec-0001", "obs-0001"], "label": "demo-group",
             "basis": {"kind": "operator_statement", "declared_by": "owner"}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "memory.jsonl"
            log.write_text((FIX / "memory.jsonl").read_text(encoding="utf-8").rstrip("\n") + "\n"
                           + "".join(json.dumps(event) + "\n" for event in declared), encoding="utf-8")
            with_declared = _fixture_state(memory_path=str(log))
        plain = _fixture_state()
        validate_state(with_declared)
        self.assertEqual(with_declared["status"], plain["status"])
        self.assertEqual(len(with_declared["components"]), len(plain["components"]) + 1)
        by_id = {c["id"]: c for c in with_declared["components"]}
        dec = by_id["dec-0001"]
        self.assertEqual(dec["kind"], "declared_removal")
        self.assertEqual(dec["declaration"]["operator_label"], "back shell")
        self.assertEqual(dec["identity"]["status"], "unknown")
        self.assertIsNone(dec["identity"]["label"])
        self.assertIsNone(dec["physical"]["bbox_mm"])
        self.assertEqual(dec["storage"]["location"], "OUT-B")
        self.assertEqual(dec["representation"]["kind"], "declared_card")
        self.assertTrue(any(g["id"] == "grp-0001" for g in dec["compatible_groups"]))
        self.assertTrue(any(g["id"] == "grp-0001" for g in by_id["obs-0001"]["compatible_groups"]))
        self.assertEqual(with_declared["phases"][0]["id"], "phase-0001")

    def test_superseded_declared_removal_is_not_live(self):
        events = [
            {"type": "phase", "id": "phase-0001", "session": "s", "label": "P1", "starts_after": None},
            {"type": "declared_removal", "id": "dec-0001", "session": "s", "operator_label": "old",
             "source": "device", "phase": "phase-0001", "declared_by": "owner"},
            {"type": "declared_removal", "id": "dec-0002", "session": "s", "operator_label": "new",
             "source": "device", "phase": "phase-0001", "declared_by": "owner",
             "supersedes": "dec-0001"},
            {"type": "storage_link", "id": "store-0001", "session": "s", "target_id": "dec-0002",
             "location": "OUT-A", "basis": {"kind": "human", "confirmed_by": "owner"}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "memory.jsonl"
            log.write_text("".join(json.dumps(event) + "\n" for event in events),
                           encoding="utf-8")
            state = build_state(str(log))
        validate_state(state)
        self.assertEqual([c["id"] for c in state["components"]], ["dec-0002"])
        self.assertEqual(state["superseded"], ["dec-0001"])

    def test_conflicting_storage_locations_are_warned(self):
        events = [
            {"type": "phase", "id": "phase-0001", "session": "s", "label": "P1", "starts_after": None},
            {"type": "declared_removal", "id": "dec-0001", "session": "s", "operator_label": "a",
             "source": "device", "phase": "phase-0001", "declared_by": "owner"},
            {"type": "declared_removal", "id": "dec-0002", "session": "s", "operator_label": "b",
             "source": "device", "phase": "phase-0001", "declared_by": "owner"},
            {"type": "storage_link", "id": "store-0001", "session": "s", "target_id": "dec-0001",
             "location": "cell-1", "basis": {"kind": "human", "confirmed_by": "owner"}},
            {"type": "storage_link", "id": "store-0002", "session": "s", "target_id": "dec-0002",
             "location": "cell-1", "basis": {"kind": "human", "confirmed_by": "owner"}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "memory.jsonl"
            log.write_text("".join(json.dumps(event) + "\n" for event in events),
                           encoding="utf-8")
            state = build_state(str(log))
        self.assertEqual(len(state["components"]), 2)
        self.assertTrue(any("claimed by multiple targets" in w for w in state["warnings"]))

    def test_shared_storage_inside_compatible_group_is_not_a_conflict(self):
        events = [
            {"type": "phase", "id": "phase-0001", "session": "s", "label": "P1", "starts_after": None},
            {"type": "declared_removal", "id": "dec-0001", "session": "s", "operator_label": "a",
             "source": "device", "phase": "phase-0001", "declared_by": "owner"},
            {"type": "declared_removal", "id": "dec-0002", "session": "s", "operator_label": "b",
             "source": "device", "phase": "phase-0001", "declared_by": "owner"},
            {"type": "storage_link", "id": "store-0001", "session": "s", "target_id": "dec-0001",
             "location": "cell-1", "basis": {"kind": "human", "confirmed_by": "owner"}},
            {"type": "storage_link", "id": "store-0002", "session": "s", "target_id": "dec-0002",
             "location": "cell-1", "basis": {"kind": "human", "confirmed_by": "owner"}},
            {"type": "compatible_group", "id": "grp-0001", "session": "s",
             "members": ["dec-0001", "dec-0002"], "label": "screws",
             "basis": {"kind": "operator_statement", "declared_by": "owner"}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "memory.jsonl"
            log.write_text("".join(json.dumps(event) + "\n" for event in events),
                           encoding="utf-8")
            state = build_state(str(log))
        self.assertFalse(any("claimed by multiple targets" in w for w in state["warnings"]))

    def test_missing_log_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = build_state(str(Path(tmp) / "absent.jsonl"))
            validate_state(state)
            self.assertEqual(state["status"], "unavailable")
            self.assertEqual(state["components"], [])

    def test_malformed_log_line_is_unavailable_with_line_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "memory.jsonl"
            log.write_text('{"id": "obs-0001", "type": "observation"}\nnot json\n',
                           encoding="utf-8")
            state = build_state(str(log))
            validate_state(state)
            self.assertEqual(state["status"], "unavailable")
            self.assertIn("line 2", state["reason"])

    def test_empty_log_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "memory.jsonl"
            log.write_text("", encoding="utf-8")
            state = build_state(str(log))
            self.assertEqual(state["status"], "unknown")

    def test_multiple_live_links_flagged_ambiguous(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "memory.jsonl"
            log.write_text(
                '{"type": "observation", "id": "obs-0001", "session": "s", '
                '"source_view": "v", "bbox_mm": [0, 0, 4, 4], '
                '"centroid_mm": [2, 2], "evidence": {"result_json": "x.json", '
                '"pair_id": "p", "region": "R1", "result_sha256": null}}\n'
                '{"type": "tray_link", "id": "link-0001", "session": "s", '
                '"observation_id": "obs-0001", "tray": {"sheet_id": "v", '
                '"frame": "v", "number": 1, "rect_frame_mm": [0, 0, 4, 4]}, '
                '"basis": {"kind": "human", "confirmed_by": "a"}}\n'
                '{"type": "tray_link", "id": "link-0002", "session": "s", '
                '"observation_id": "obs-0001", "tray": {"sheet_id": "v", '
                '"frame": "v", "number": 2, "rect_frame_mm": [4, 4, 4, 4]}, '
                '"basis": {"kind": "human", "confirmed_by": "b"}}\n',
                encoding="utf-8")
            state = build_state(str(log))
            component = state["components"][0]
            self.assertIsNone(component["tray"])
            self.assertEqual(len(component["trays"]), 2)
            self.assertTrue(any("ambiguous" in w for w in state["warnings"]))


if __name__ == "__main__":
    unittest.main()
