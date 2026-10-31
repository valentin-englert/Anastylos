"""Read-only selection contract; synthetic records only, no providers."""
import contextlib
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import anastylos
import guide
import selection


class SelectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.memory = self.root / "memory.jsonl"
        self.package = self.root / "guide" / "session.json"
        self.events = [
            {"id": "phase-0001", "type": "phase", "session": "s", "label": "opening"},
            {"id": "dec-0001", "type": "declared_removal", "session": "s", "operator_label": "shell",
             "source": "SRC-A or SRC-B (operator prose)", "declared_by": "test operator", "phase": "phase-0001",
             "t_s": 3.0, "evidence_note": "typed answer Q-1", "observation_id": None, "supersedes": None},
            {"id": "store-0001", "type": "storage_link", "session": "s", "target_id": "dec-0001",
             "location": "outside", "basis": {"kind": "human", "confirmed_by": "test operator"}, "supersedes": None}]
        self.binding = {"schema": selection.SCHEMA, "drawing_element_id": "drawing-shell", "event_id": "dec-0001",
                        "source": {"session": "s", "memory_path": str(self.memory)},
                        "memory_sha256": "", "guide_sha256": ""}
        self.build()

    def sha(self, path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def build(self):
        self.memory.write_text("".join(json.dumps(e) + "\n" for e in self.events), encoding="utf-8")
        done = subprocess.run([sys.executable, str(guide.PACKAGE_BUILDER), "--memory", str(self.memory),
                               "--out", str(self.package.parent)], capture_output=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.binding.update(memory_sha256=self.sha(self.memory), guide_sha256=self.sha(self.package))

    def resolve(self):
        return selection.resolve(self.memory, self.package, self.binding)

    def rewrite_package(self, change):
        raw = json.loads(self.package.read_text(encoding="utf-8"))
        change(raw)
        self.package.write_text(json.dumps(raw), encoding="utf-8")
        self.binding["guide_sha256"] = self.sha(self.package)

    def test_valid_legacy_guide_without_session_root_and_inputs_unchanged(self):
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        result = self.resolve()
        self.assertEqual(result["drawing_element_id"], "drawing-shell")
        self.assertEqual(result["event_id"], "dec-0001")
        self.assertEqual(result["original_location"]["value"], self.events[1]["source"])
        self.assertEqual(result["stored_location"]["value"], "outside")
        self.assertEqual(result["original_location"]["basis"], "operator_declared")
        self.assertIn("direct_image_evidence", result["missing"])
        self.assertFalse((self.root / "agent-session.json").exists())
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_wrong_memory_hash(self):
        self.binding["memory_sha256"] = "0" * 64
        with self.assertRaisesRegex(selection.SelectionError, "Memory SHA-256"):
            self.resolve()

    def test_wrong_guide_hash(self):
        self.binding["guide_sha256"] = "0" * 64
        with self.assertRaisesRegex(selection.SelectionError, "Guide SHA-256"):
            self.resolve()

    def test_guide_binds_different_memory(self):
        self.rewrite_package(lambda r: r["inputs"]["memory"].update(sha256="0" * 64))
        with self.assertRaisesRegex(selection.SelectionError, "Guide input"):
            self.resolve()

    def test_guide_binds_different_source_path(self):
        self.rewrite_package(lambda r: r["inputs"]["memory"].update(path=str(self.root / "other.jsonl")))
        with self.assertRaisesRegex(selection.SelectionError, "Guide input"):
            self.resolve()

    def test_same_id_other_meaning_cannot_replace_bound_memory(self):
        self.events[1]["operator_label"] = "membranes"
        self.memory.write_text("".join(json.dumps(e) + "\n" for e in self.events))
        with self.assertRaisesRegex(selection.SelectionError, "Memory SHA-256"):
            self.resolve()

    def test_missing_id_and_unsupported_record(self):
        for event_id in ("dec-9999", "phase-0001", "store-0001", "grp-0001"):
            with self.subTest(event_id=event_id):
                self.binding["event_id"] = event_id
                with self.assertRaisesRegex(selection.SelectionError, "supported single"):
                    self.resolve()

    def test_missing_origin_and_storage_remain_missing(self):
        self.events[1]["source"] = None
        self.events.pop()
        self.build()
        result = self.resolve()
        self.assertIsNone(result["original_location"]["value"])
        self.assertIsNone(result["stored_location"]["value"])
        self.assertEqual(set(result["missing"]), {"original_location", "stored_location", "direct_image_evidence"})

    def test_superseded_part_is_not_redirected(self):
        self.events.append({**self.events[1], "id": "dec-0002", "supersedes": "dec-0001"})
        self.build()
        with self.assertRaisesRegex(selection.SelectionError, "superseded"):
            self.resolve()

    def test_two_active_storage_records_are_not_silently_preferred(self):
        self.events.append({**self.events[-1], "id": "store-0002", "location": "elsewhere"})
        self.build()
        with self.assertRaisesRegex(selection.SelectionError, "ambiguous active storage"):
            self.resolve()

    def test_duplicate_memory_records_rejected(self):
        self.events.append(self.events[1].copy())
        self.build()
        with self.assertRaisesRegex(selection.SelectionError, "duplicate records"):
            self.resolve()

    def test_duplicate_guide_parts_rejected(self):
        self.rewrite_package(lambda r: r["parts"].append(r["parts"][0].copy()))
        with self.assertRaisesRegex(selection.SelectionError, "duplicate part"):
            self.resolve()

    def test_guide_contradiction_rejected(self):
        self.rewrite_package(lambda r: r["parts"][0]["source"].update(described_as="invented socket"))
        with self.assertRaisesRegex(selection.SelectionError, "contradicts"):
            self.resolve()

    def test_composite_selection_rejected(self):
        self.binding["event_ids"] = ["dec-0001"]
        with self.assertRaisesRegex(selection.SelectionError, "composite"):
            self.resolve()

    def test_cli_success_and_failure_without_part_output(self):
        path = self.root / "selection.json"
        args = ["select", "--selection", str(path), "--memory", str(self.memory), "--guide", str(self.package)]
        path.write_text(json.dumps(self.binding))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(anastylos.main(args), 0)
        self.assertEqual(json.loads(out.getvalue())["event_id"], "dec-0001")
        self.binding["memory_sha256"] = "0" * 64
        path.write_text(json.dumps(self.binding))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(anastylos.main(args), 1)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("Memory SHA-256", err.getvalue())

    def test_existing_observation_reference_keeps_recorded_evidence_hash(self):
        self.events.append({"id": "obs-0001", "type": "observation", "session": "s", "source_view": "sheet",
                            "centroid_mm": [1, 2], "bbox_mm": [0, 0, 2, 4],
                            "evidence": {"result_json": "unopened-result.json", "pair_id": "pair", "region": "R1",
                                         "result_sha256": "a" * 64}})
        self.events[1]["observation_id"] = "obs-0001"
        self.build()
        result = self.resolve()
        self.assertEqual(result["original_location"]["basis"], "operator_declared")
        self.assertEqual(result["original_location"]["evidence"]["result_sha256"], "a" * 64)
        self.assertNotIn("direct_image_evidence", result["missing"])
