"""Deterministic assembly-graph regression tests. Standard library only.

Fixtures are hand-built memory logs plus minimal change-detection
result dicts following the real schemas (rectified.view, regions with
label/bbox_mm/centroid_mm). The graph builder only reads; tests assert
it never writes to the log.

Run from the cable-routing directory:

    ..\\Anastylos\\.venv\\Scripts\\python -m unittest discover -s tests -v
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
sys.path.insert(0, str(ROOT.parent / "assembly-memory"))

from anastylos_cable.cli import main
from anastylos_cable.graph import build_graph, to_dot

VIEW = "surface_a3"


def _result(pair_id, boxes):
    return {
        "status": "multiple_changes" if len(boxes) > 1 else "single_change",
        "pair_id": pair_id,
        "rectified": {"view": VIEW},
        "regions": [
            {"label": f"R{index}", "bbox_mm": box,
             "centroid_mm": [box[0] + box[2] / 2, box[1] + box[3] / 2]}
            for index, box in enumerate(boxes, 1)
        ],
    }


def _observation(ident, result_file, region, session="s1",
                 timestamp="2026-01-01T00:00:00Z"):
    return {"type": "observation", "id": ident, "session": session,
            "timestamp": timestamp, "source_view": VIEW,
            "bbox_mm": [0, 0, 10, 10], "centroid_mm": [5, 5],
            "evidence": {"result_json": result_file, "pair_id": "p1",
                         "region": region, "result_sha256": None},
            "supersedes": None}


class Fixture:
    """A memory log plus cited result files in a temp directory."""

    def __init__(self, directory):
        self.root = Path(directory)
        self.results = self.root / "results"
        self.results.mkdir(parents=True, exist_ok=True)
        self.log = self.root / "memory.jsonl"

    def add_result(self, name, pair_id, boxes):
        path = self.results / name
        path.write_text(json.dumps(_result(pair_id, boxes)),
                        encoding="utf-8")
        return str(path.relative_to(self.root))

    def write_log(self, events):
        self.log.write_text("\n".join(json.dumps(event) for event in events)
                            + "\n", encoding="utf-8")
        return str(self.log)

    def write_confirmations(self, edges):
        path = self.root / "confirmations.json"
        path.write_text(json.dumps(edges), encoding="utf-8")
        return str(path)


class GraphCases(unittest.TestCase):
    def test_sequential_overlaps_become_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            fix = Fixture(tmp)
            ref = fix.add_result("r1.json", "p1", [[0, 0, 20, 20],
                                                  [5, 5, 10, 10]])
            log = fix.write_log([_observation("obs-0001", ref, "R1"),
                                 _observation("obs-0002", ref, "R2")])
            graph = build_graph(log)
            self.assertEqual(graph["status"], "ok")
            self.assertEqual(graph["timeline"], ["obs-0001", "obs-0002"])
            self.assertEqual(len(graph["candidates"]), 1)
            edge = graph["candidates"][0]
            self.assertEqual((edge["before"], edge["after"]),
                             ("obs-0001", "obs-0002"))
            self.assertEqual(graph["confirmed"], [])

    def test_parallel_independent_operations_have_no_edges(self):
        with tempfile.TemporaryDirectory() as tmp:
            fix = Fixture(tmp)
            ref = fix.add_result("r1.json", "p1", [[0, 0, 10, 10],
                                                  [100, 100, 10, 10]])
            log = fix.write_log([_observation("obs-0001", ref, "R1"),
                                 _observation("obs-0002", ref, "R2")])
            graph = build_graph(log)
            self.assertEqual(graph["candidates"], [])
            self.assertEqual(graph["confirmed"], [])
            self.assertEqual(graph["orderings"]["count_valid"], 2)
            self.assertEqual(graph["orderings"]["parallel_groups"],
                             [["obs-0001", "obs-0002"]])

    def test_temporal_order_alone_never_confirms(self):
        with tempfile.TemporaryDirectory() as tmp:
            fix = Fixture(tmp)
            log = fix.write_log([
                _observation("obs-0001", "missing.json", "R1"),
                _observation("obs-0002", "missing.json", "R2")])
            graph = build_graph(log)
            self.assertEqual(graph["confirmed"], [])
            self.assertEqual(graph["candidates"], [])
            self.assertEqual(len(graph["missing_regions"]), 2)
            self.assertEqual(graph["status"], "unknown")

    def test_confirmed_chain_orders_and_proposes_reassembly(self):
        with tempfile.TemporaryDirectory() as tmp:
            fix = Fixture(tmp)
            ref = fix.add_result("r1.json", "p1", [[0, 0, 20, 20],
                                                  [5, 5, 10, 10],
                                                  [50, 50, 5, 5]])
            log = fix.write_log([_observation("obs-0001", ref, "R1"),
                                 _observation("obs-0002", ref, "R2"),
                                 _observation("obs-0003", ref, "R3")])
            confirm = fix.write_confirmations([
                {"before": "obs-0001", "after": "obs-0002",
                 "confirmed_by": "op"},
                {"before": "obs-0002", "after": "obs-0003",
                 "confirmed_by": "op"}])
            graph = build_graph(log, confirmations_path=confirm)
            self.assertEqual(graph["status"], "ok")
            self.assertEqual(len(graph["confirmed"]), 2)
            orderings = graph["orderings"]
            self.assertEqual(orderings["canonical_disassembly"],
                             ["obs-0001", "obs-0002", "obs-0003"])
            self.assertEqual(orderings["count_valid"], 1)
            proposal = graph["reassembly"]["proposal"]
            self.assertEqual(proposal,
                             ["obs-0003", "obs-0002", "obs-0001"])
            self.assertIn("NOT", graph["reassembly"]["warning"])
            self.assertEqual(graph["reassembly"]["unresolved"], [])

    def test_candidate_contradicting_confirmed_is_ambiguous(self):
        with tempfile.TemporaryDirectory() as tmp:
            fix = Fixture(tmp)
            ref = fix.add_result("r1.json", "p1", [[0, 0, 20, 20],
                                                  [5, 5, 10, 10]])
            log = fix.write_log([_observation("obs-0001", ref, "R1"),
                                 _observation("obs-0002", ref, "R2")])
            confirm = fix.write_confirmations([
                {"before": "obs-0002", "after": "obs-0001",
                 "confirmed_by": "op"}])
            graph = build_graph(log, confirmations_path=confirm)
            self.assertEqual(graph["status"], "ambiguous")
            self.assertEqual(len(graph["contradictions"]), 1)

    def test_confirmed_cycle_blocks_ordering(self):
        with tempfile.TemporaryDirectory() as tmp:
            fix = Fixture(tmp)
            ref = fix.add_result("r1.json", "p1", [[0, 0, 20, 20],
                                                  [30, 30, 5, 5]])
            log = fix.write_log([_observation("obs-0001", ref, "R1"),
                                 _observation("obs-0002", ref, "R2")])
            confirm = fix.write_confirmations([
                {"before": "obs-0001", "after": "obs-0002",
                 "confirmed_by": "op"},
                {"before": "obs-0002", "after": "obs-0001",
                 "confirmed_by": "op"}])
            graph = build_graph(log, confirmations_path=confirm)
            self.assertEqual(graph["status"], "ambiguous")
            self.assertEqual(len(graph["cycles"]), 1)
            self.assertEqual(graph["cycles"][0]["kind"], "confirmed")
            self.assertIsNone(graph["orderings"])
            self.assertIsNone(graph["reassembly"])

    def test_multiple_valid_orders_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            fix = Fixture(tmp)
            ref = fix.add_result("r1.json", "p1", [[0, 0, 5, 5],
                                                  [50, 50, 5, 5],
                                                  [100, 100, 5, 5]])
            log = fix.write_log([_observation("obs-0001", ref, "R1"),
                                 _observation("obs-0002", ref, "R2"),
                                 _observation("obs-0003", ref, "R3")])
            confirm = fix.write_confirmations([
                {"before": "obs-0001", "after": "obs-0002",
                 "confirmed_by": "op"}])
            graph = build_graph(log, confirmations_path=confirm)
            self.assertEqual(graph["orderings"]["count_valid"], 3)
            self.assertEqual(graph["reassembly"]["unresolved"], ["obs-0003"])

    def test_unknown_confirmation_ids_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            fix = Fixture(tmp)
            ref = fix.add_result("r1.json", "p1", [[0, 0, 5, 5]])
            log = fix.write_log([_observation("obs-0001", ref, "R1")])
            confirm = fix.write_confirmations([
                {"before": "obs-0001", "after": "obs-9999",
                 "confirmed_by": "op"}])
            graph = build_graph(log, confirmations_path=confirm)
            self.assertEqual(graph["status"], "ambiguous")
            self.assertEqual(len(graph["missing_confirmations"]), 1)
            self.assertEqual(graph["confirmed"], [])

    def test_superseded_observations_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            fix = Fixture(tmp)
            ref = fix.add_result("r1.json", "p1", [[0, 0, 5, 5]])
            old = _observation("obs-0001", ref, "R1")
            new = _observation("obs-0002", ref, "R1")
            new["supersedes"] = "obs-0001"
            log = fix.write_log([old, new])
            graph = build_graph(log)
            self.assertEqual(graph["timeline"], ["obs-0002"])
            self.assertEqual(graph["superseded"], ["obs-0001"])

    def test_custom_reasoner_adapter(self):
        def reasoner(nodes, regions):
            return [{"before": nodes[0]["id"], "after": nodes[1]["id"],
                     "kind": "candidate", "evidence": {"type": "custom"},
                     "basis": "test adapter", "confirmed_by": None}]

        with tempfile.TemporaryDirectory() as tmp:
            fix = Fixture(tmp)
            ref = fix.add_result("r1.json", "p1", [[0, 0, 5, 5],
                                                  [100, 100, 5, 5]])
            log = fix.write_log([_observation("obs-0001", ref, "R1"),
                                 _observation("obs-0002", ref, "R2")])
            graph = build_graph(log, reasoner=reasoner)
            self.assertEqual(len(graph["candidates"]), 1)
            self.assertEqual(graph["candidates"][0]["basis"], "test adapter")

    def test_build_is_deterministic_and_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            fix = Fixture(tmp)
            ref = fix.add_result("r1.json", "p1", [[0, 0, 20, 20],
                                                  [5, 5, 10, 10]])
            log = fix.write_log([_observation("obs-0001", ref, "R1"),
                                 _observation("obs-0002", ref, "R2")])
            before = Path(log).read_bytes()
            first = build_graph(log)
            second = build_graph(log)
            self.assertEqual(first, second)
            self.assertEqual(Path(log).read_bytes(), before)
            json.dumps(first)

    def test_dot_export_marks_edge_kinds(self):
        with tempfile.TemporaryDirectory() as tmp:
            fix = Fixture(tmp)
            ref = fix.add_result("r1.json", "p1", [[0, 0, 20, 20],
                                                  [5, 5, 10, 10]])
            log = fix.write_log([_observation("obs-0001", ref, "R1"),
                                 _observation("obs-0002", ref, "R2")])
            confirm = fix.write_confirmations([
                {"before": "obs-0001", "after": "obs-0002",
                 "confirmed_by": "op"}])
            graph = build_graph(log, confirmations_path=confirm)
            dot = to_dot(graph)
            self.assertIn('"obs-0001" -> "obs-0002"', dot)
            self.assertIn("confirmed", dot)


class GraphFailures(unittest.TestCase):
    def test_missing_log_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph = build_graph(str(Path(tmp) / "nope.jsonl"))
            self.assertEqual(graph["status"], "unavailable")

    def test_corrupt_log_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "memory.jsonl"
            log.write_text("not json\n", encoding="utf-8")
            graph = build_graph(str(log))
            self.assertEqual(graph["status"], "unavailable")

    def test_empty_log_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "memory.jsonl"
            log.write_text("", encoding="utf-8")
            graph = build_graph(str(log))
            self.assertEqual(graph["status"], "unavailable")


class GraphCliTests(unittest.TestCase):
    def test_graph_command_writes_json_and_dot(self):
        with tempfile.TemporaryDirectory() as tmp:
            fix = Fixture(tmp)
            ref = fix.add_result("r1.json", "p1", [[0, 0, 20, 20],
                                                  [5, 5, 10, 10]])
            log = fix.write_log([_observation("obs-0001", ref, "R1"),
                                 _observation("obs-0002", ref, "R2")])
            out = str(Path(tmp) / "out")
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["graph", log, "--out", out])
            self.assertEqual(code, 0)
            self.assertTrue((Path(out) / "assembly_graph.json").is_file())
            self.assertTrue((Path(out) / "assembly_graph.dot").is_file())


if __name__ == "__main__":
    unittest.main()
