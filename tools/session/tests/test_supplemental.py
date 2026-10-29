import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

SESSION = Path(__file__).resolve().parents[1]
REPO = SESSION.parents[1]
sys.path.insert(0, str(SESSION))

from supplemental.adapter import build_view, load_memory_events  # noqa: E402

A64 = "a" * 64
REPORT_OK = {"verdict": "accept", "metric_ready": True, "reasons": [], "reviews": []}


def frozen_sidecar():
    path = REPO / "capture-companion" / "control" / "contracts" / "vectors" / "spatial-valid.json"
    return json.loads(path.read_text(encoding="utf-8"))


def memory_fixture(directory):
    events = [
        {"id": "obs-1", "type": "observation", "session": "S1", "centroid_mm": [100.0, 100.0]},
        {"id": "rem-1", "type": "declared_removal", "session": "S1", "operator_label": "cross screw"},
    ]
    path = Path(directory) / "memory.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return path


def interpreted_sidecar():
    doc = frozen_sidecar()
    ref = {"asset_id": "00000000-0000-4000-8000-000000000090", "sha256": A64}
    doc["interpretations"] = [
        {"id": "00000000-0000-4000-8000-000000000021", "kind": "hole_seen",
         "basis": "visual_observation", "state": "observed", "claim": "A dark round hole is visible.",
         "evidence_refs": [ref], "method": "synthetic-eye", "region_ref": "roi-1",
         "operator_confirmation_ref": None},
        {"id": "00000000-0000-4000-8000-000000000022", "kind": "hole_center",
         "basis": "geometric_measurement", "state": "derived", "claim": "Hole center at (100, 80) mm.",
         "evidence_refs": [ref], "method": "synthetic-fit", "region_ref": "roi-1",
         "operator_confirmation_ref": None},
        {"id": "00000000-0000-4000-8000-000000000023", "kind": "part_guess",
         "basis": "model_inference", "state": "suggested", "claim": "Looks like an M2 screw.",
         "evidence_refs": [ref], "method": "synthetic-model", "region_ref": None,
         "operator_confirmation_ref": None},
        {"id": "00000000-0000-4000-8000-000000000024", "kind": "operator_note",
         "basis": "operator_testimony", "state": "operator_declared",
         "claim": "Operator: I placed the screw here.",
         "evidence_refs": [ref], "method": "synthetic-note", "region_ref": None,
         "operator_confirmation_ref": None},
    ]
    return doc


class SupplementalViewTest(unittest.TestCase):
    def test_view_builds_with_refs(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = memory_fixture(tmp)
            events, sha = load_memory_events(memory)
            view = build_view(frozen_sidecar(), "s" * 64, REPORT_OK, "r" * 64, events, sha)
        self.assertEqual(view["schema"], "anastylos-supplemental-view@1")
        self.assertEqual(view["evidence_id"], "00000000-0000-4000-8000-000000000001")
        self.assertEqual(view["sidecar_sha256"], "s" * 64)
        self.assertEqual(view["report_sha256"], "r" * 64)
        self.assertEqual(view["report_verdict"], "accept")
        self.assertTrue(view["geometry_eligible"])
        self.assertEqual(view["memory_sha256"], sha)

    def test_view_id_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = memory_fixture(tmp)
            events, sha = load_memory_events(memory)
            first = build_view(frozen_sidecar(), "s" * 64, REPORT_OK, "r" * 64, events, sha)
            second = build_view(frozen_sidecar(), "s" * 64, REPORT_OK, "r" * 64, events, sha)
        self.assertEqual(first["view_id"], second["view_id"])

    def test_memory_subject_found_by_ref(self):
        doc = frozen_sidecar()
        doc["target"]["subject_ref"] = "obs-1"
        with tempfile.TemporaryDirectory() as tmp:
            events, sha = load_memory_events(memory_fixture(tmp))
            view = build_view(doc, "s" * 64, REPORT_OK, "r" * 64, events, sha)
        self.assertEqual(view["memory_subject"]["status"], "linked")
        self.assertEqual(view["memory_subject"]["memory_type"], "observation")

    def test_unknown_subject_stays_unknown(self):
        doc = frozen_sidecar()
        doc["target"]["subject_ref"] = "obs-9"
        with tempfile.TemporaryDirectory() as tmp:
            events, sha = load_memory_events(memory_fixture(tmp))
            view = build_view(doc, "s" * 64, REPORT_OK, "r" * 64, events, sha)
        self.assertEqual(view["memory_subject"]["status"], "unresolved_subject")
        self.assertTrue(any("subject_unresolved" in u for u in view["unknowns"]))

    def test_null_subject_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            events, sha = load_memory_events(memory_fixture(tmp))
            view = build_view(frozen_sidecar(), "s" * 64, REPORT_OK, "r" * 64, events, sha)
        self.assertEqual(view["memory_subject"]["status"], "unknown_subject")

    def test_claims_sorted_by_basis(self):
        with tempfile.TemporaryDirectory() as tmp:
            events, sha = load_memory_events(memory_fixture(tmp))
            view = build_view(interpreted_sidecar(), "s" * 64, REPORT_OK, "r" * 64, events, sha)
        self.assertEqual(len(view["observations"]), 1)
        self.assertEqual(len(view["measurements"]), 1)
        self.assertEqual(len(view["advisory"]), 1)
        self.assertEqual(len(view["testimony"]), 1)
        self.assertEqual(view["testimony"][0]["basis"], "operator_testimony")

    def test_unqualified_geometry_becomes_unknown(self):
        report = {"verdict": "needs_review", "metric_ready": False,
                  "reasons": [], "reviews": ["NULL_RMS"]}
        with tempfile.TemporaryDirectory() as tmp:
            events, sha = load_memory_events(memory_fixture(tmp))
            view = build_view(interpreted_sidecar(), "s" * 64, report, "r" * 64, events, sha)
        self.assertEqual(view["measurements"], [])
        self.assertFalse(view["geometry_eligible"])
        self.assertTrue(any(u.startswith("unqualified_geometry") for u in view["unknowns"]))

    def test_no_custody_claims(self):
        with tempfile.TemporaryDirectory() as tmp:
            events, sha = load_memory_events(memory_fixture(tmp))
            view = build_view(frozen_sidecar(), "s" * 64, REPORT_OK, "r" * 64, events, sha)
        self.assertEqual(view["custody_claims"], [])
        self.assertNotIn("tray_link", view)
        self.assertNotIn("removal", view)
        self.assertTrue(any("history_unknown" in u for u in view["unknowns"]))

    def test_unknowns_always_list_identity_history_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            events, sha = load_memory_events(memory_fixture(tmp))
            view = build_view(frozen_sidecar(), "s" * 64, REPORT_OK, "r" * 64, events, sha)
        joined = " ".join(view["unknowns"])
        self.assertIn("part_identity_unknown", joined)
        self.assertIn("history_unknown", joined)
        self.assertIn("hidden_geometry_unknown", joined)
        self.assertIn("epoch_join_unknown", joined)

    def test_memory_bytes_identical_after_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = memory_fixture(tmp)
            before = memory.read_bytes()
            events, sha = load_memory_events(memory)
            view = build_view(frozen_sidecar(), "s" * 64, REPORT_OK, "r" * 64, events, sha)
            (Path(tmp) / "view.json").write_text(json.dumps(view), encoding="utf-8")
            after = memory.read_bytes()
        self.assertEqual(before, after)
        self.assertEqual(hashlib.sha256(after).hexdigest(), sha)


if __name__ == "__main__":
    unittest.main()
