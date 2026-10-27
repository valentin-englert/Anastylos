"""Graph provenance regression tests: cited vs current result hashes.

The graph builder resolves cited change-detection regions to derive
candidate edges. A region whose file bytes no longer match the cited
result_sha256 must be excluded, so tampered geometry can never yield a
candidate edge. Regions without a usable cited hash still resolve --
for backward compatibility with historical logs -- but must be listed
explicitly as unverified.

Run from the cable-routing directory:

    ..\\Anastylos\\.venv\\Scripts\\python -m unittest discover -s tests -v
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
sys.path.insert(0, str(ROOT.parent / "assembly-memory"))

from anastylos_cable.graph import build_graph  # noqa: E402

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


def _observation(ident, result_file, region, cited_hash,
                 session="s1",
                 timestamp="2026-01-01T00:00:00Z"):
    return {"type": "observation", "id": ident, "session": session,
            "timestamp": timestamp, "source_view": VIEW,
            "bbox_mm": [0, 0, 10, 10], "centroid_mm": [5, 5],
            "evidence": {"result_json": result_file, "pair_id": "p1",
                         "region": region, "result_sha256": cited_hash},
            "supersedes": None}


class Fixture:
    def __init__(self, directory):
        self.root = Path(directory)
        self.results = self.root / "results"
        self.results.mkdir(parents=True, exist_ok=True)
        self.log = self.root / "memory.jsonl"

    def add_result(self, name, pair_id, boxes):
        path = self.results / name
        payload = json.dumps(_result(pair_id, boxes)).encode("utf-8")
        path.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        return str(path.relative_to(self.root)), digest

    def rewrite_result(self, name, pair_id, boxes):
        path = self.results / name
        payload = json.dumps(_result(pair_id, boxes)).encode("utf-8")
        path.write_bytes(payload)
        return hashlib.sha256(payload).hexdigest()

    def write_log(self, events):
        self.log.write_text("\n".join(json.dumps(event) for event in events)
                            + "\n", encoding="utf-8")
        return str(self.log)

    def write_confirmations(self, edges):
        path = self.root / "confirmations.json"
        path.write_text(json.dumps(edges), encoding="utf-8")
        return str(path)


class GraphProvenance(unittest.TestCase):
    def test_matching_hashes_yield_verified_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            fix = Fixture(tmp)
            ref, digest = fix.add_result("r1.json", "p1",
                                         [[0, 0, 20, 20], [5, 5, 10, 10]])
            log = fix.write_log([_observation("obs-0001", ref, "R1", digest),
                                 _observation("obs-0002", ref, "R2", digest)])
            graph = build_graph(log)
            self.assertEqual(graph["status"], "ok")
            self.assertEqual(len(graph["candidates"]), 1)
            self.assertEqual(graph["missing_regions"], [])
            self.assertEqual(graph["unverified_regions"], [])
            self.assertEqual(graph["regions_resolved"], 2)

    def test_mismatched_hash_excludes_region_from_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            fix = Fixture(tmp)
            # Import-time geometry: far apart, no edge possible.
            ref, cited = fix.add_result("r1.json", "p1",
                                        [[0, 0, 5, 5], [100, 100, 5, 5]])
            log = fix.write_log([_observation("obs-0001", ref, "R1", cited),
                                 _observation("obs-0002", ref, "R2", cited)])
            # Substitute the file: forged overlap that would yield an edge.
            current = fix.rewrite_result(
                "r1.json", "p1", [[0, 0, 20, 20], [5, 5, 10, 10]])
            self.assertNotEqual(cited, current)
            graph = build_graph(log)
            self.assertEqual(len(graph["missing_regions"]), 2)
            for entry in graph["missing_regions"]:
                self.assertIn("mismatch", entry["reason"])
                self.assertEqual(entry["cited_sha256"], cited)
                self.assertEqual(entry["current_sha256"], current)
            self.assertEqual(graph["candidates"], [])
            self.assertEqual(graph["regions_resolved"], 0)
            self.assertEqual(graph["status"], "unknown")

    def test_partial_mismatch_breaks_only_affected_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            fix = Fixture(tmp)
            ref_a, digest_a = fix.add_result(
                "a.json", "p1", [[0, 0, 20, 20], [5, 5, 10, 10]])
            ref_b, digest_b = fix.add_result(
                "b.json", "p2", [[0, 0, 20, 20], [5, 5, 10, 10]])
            log = fix.write_log([_observation("obs-0001", ref_a, "R1",
                                              digest_a),
                                 _observation("obs-0002", ref_a, "R2",
                                              digest_a),
                                 _observation("obs-0003", ref_b, "R1",
                                              digest_b),
                                 _observation("obs-0004", ref_b, "R2",
                                              digest_b)])
            # Tamper with only the second file.
            fix.rewrite_result("b.json", "p2",
                               [[0, 0, 20, 20], [5, 5, 10, 10], [0, 0, 1, 1]])
            graph = build_graph(log)
            self.assertEqual(len(graph["missing_regions"]), 2)
            self.assertEqual(
                {entry["observation_id"]
                 for entry in graph["missing_regions"]},
                {"obs-0003", "obs-0004"})
            # The intact pair still yields its candidate edge.
            self.assertEqual(
                [(edge["before"], edge["after"])
                 for edge in graph["candidates"]],
                [("obs-0001", "obs-0002")])

    def test_null_hash_resolves_explicitly_unverified(self):
        with tempfile.TemporaryDirectory() as tmp:
            fix = Fixture(tmp)
            ref, _digest = fix.add_result("r1.json", "p1",
                                          [[0, 0, 20, 20], [5, 5, 10, 10]])
            log = fix.write_log([_observation("obs-0001", ref, "R1", None),
                                 _observation("obs-0002", ref, "R2", None)])
            graph = build_graph(log)
            # Backward compatible: candidates still form ...
            self.assertEqual(len(graph["candidates"]), 1)
            # ... but the unverified basis is explicit.
            self.assertEqual(len(graph["unverified_regions"]), 2)
            for entry in graph["unverified_regions"]:
                self.assertIn("unverified", entry["reason"])
            self.assertEqual(graph["missing_regions"], [])

    def test_malformed_hash_resolves_explicitly_unverified(self):
        for bad in ("not-a-hash", 12345):
            with self.subTest(cited=bad), tempfile.TemporaryDirectory() as tmp:
                fix = Fixture(tmp)
                ref, _digest = fix.add_result(
                    "r1.json", "p1", [[0, 0, 20, 20], [5, 5, 10, 10]])
                log = fix.write_log([_observation("obs-0001", ref, "R1", bad),
                                     _observation("obs-0002", ref, "R2", bad)])
                graph = build_graph(log)
                self.assertEqual(len(graph["candidates"]), 1)
                self.assertEqual(len(graph["unverified_regions"]), 2)

    def test_confirmed_edge_survives_region_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            fix = Fixture(tmp)
            ref, cited = fix.add_result("r1.json", "p1",
                                        [[0, 0, 20, 20], [5, 5, 10, 10]])
            log = fix.write_log([_observation("obs-0001", ref, "R1", cited),
                                 _observation("obs-0002", ref, "R2", cited)])
            fix.rewrite_result("r1.json", "p1",
                               [[0, 0, 20, 20], [5, 5, 10, 10], [9, 9, 1, 1]])
            confirm = fix.write_confirmations([
                {"before": "obs-0001", "after": "obs-0002",
                 "confirmed_by": "op"}])
            graph = build_graph(log, confirmations_path=confirm)
            # Operator confirmation never derived from region bytes.
            self.assertEqual(len(graph["confirmed"]), 1)
            # Rejected regions yield no candidates.
            self.assertEqual(graph["candidates"], [])
            self.assertEqual(len(graph["missing_regions"]), 2)

    def test_graph_provenance_is_deterministic_and_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            fix = Fixture(tmp)
            ref, digest = fix.add_result("r1.json", "p1",
                                         [[0, 0, 20, 20], [5, 5, 10, 10]])
            log = fix.write_log([_observation("obs-0001", ref, "R1", digest),
                                 _observation("obs-0002", ref, "R2",
                                              "00" * 32)])
            before = [Path(log).read_bytes(),
                      (fix.results / "r1.json").read_bytes()]
            first = json.dumps(build_graph(log), sort_keys=True)
            second = json.dumps(build_graph(log), sort_keys=True)
            self.assertEqual(first, second)
            self.assertEqual(Path(log).read_bytes(), before[0])
            self.assertEqual((fix.results / "r1.json").read_bytes(),
                             before[1])


if __name__ == "__main__":
    unittest.main()
