"""Session report export: standalone HTML from a recorded agent session.

Synthetic sessions only (plus one FULL-01-copy session built like test_m2).
No network, no model calls. The export is read-only: every test below also
guards that inputs are byte-identical afterwards where it matters.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import assemble
import guide
import report
import report_cli
from state import SessionState

EVIL_LABEL = '<script>alert("x")</script> & "\u00dcn\u00eflaute"'
LONG_LABEL = "Knopf " + "A" * 600


def synthetic_events(label=EVIL_LABEL, long_label=None) -> list[dict]:
    return [
        {"id": "phase-0001", "type": "phase", "session": "s", "label": "P1 opening", "starts_after": None},
        {"id": "dec-0001", "type": "declared_removal", "session": "s", "operator_label": label,
         "source": "top-left hole H2", "phase": "phase-0001", "declared_by": "tester", "t_s": 25.6,
         "evidence_note": "answer to Q-P1-01"},
        {"id": "store-0001", "type": "storage_link", "session": "s", "target_id": "dec-0001",
         "location": "cell-1", "basis": {"kind": "human", "confirmed_by": "tester"}},
        {"id": "dec-0003", "type": "declared_removal", "session": "s",
         "operator_label": long_label or "second screw", "source": "deep holes of the back plate",
         "phase": "phase-0001", "declared_by": "tester", "t_s": 38.2},
        {"id": "store-0003", "type": "storage_link", "session": "s", "target_id": "dec-0003",
         "location": "cell-1", "basis": {"kind": "human", "confirmed_by": "tester"}},
        {"id": "grp-0001", "type": "compatible_group", "session": "s", "members": ["dec-0001", "dec-0003"],
         "label": "screws", "basis": {"kind": "operator_statement", "declared_by": "tester"}},
        {"id": "obs-0001", "type": "observation", "session": "s", "source_view": "surface_a3",
         "bbox_mm": [236.5, 137.5, 12.5, 12.25], "centroid_mm": [242.28, 143.58],
         "evidence": {"result_json": "C:\\ANASTYLOS\\data\\x\\result.json", "pair_id": "E-0092_SRC-14",
                      "region": "R10", "result_sha256": "f" * 64},
         "note": "source region region_about_part_size", "t_s": 51.2},
        {"id": "link-0001", "type": "tray_link", "session": "s", "observation_id": "obs-0001",
         "tray": {"sheet_id": "surface_a3", "number": 3},
         "basis": {"kind": "evidence", "result_json": "C:\\ANASTYLOS\\data\\y\\result.json",
                   "pair_id": "E-0094_cell-3", "region": "R3", "result_sha256": "b" * 64},
         "inference": {"relation": "source_to_cell", "inferred_by": "candidate_by_elimination",
                       "producer": "custody linker 1.7 via tools/session", "assumptions": ["one part per cell"]}},
        {"id": "dec-0002", "type": "declared_removal", "session": "s", "operator_label": "PCB with cable",
         "source": None, "phase": "phase-0001", "declared_by": "tester", "t_s": None},
    ]


def write_memory(state: SessionState, events: list[dict]) -> None:
    (state.memory_dir / "memory.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")


def make_session(root: Path, events=None, object_name="Test <object> & parts") -> SessionState:
    state = SessionState.create(root, object_name, "tester")
    write_memory(state, synthetic_events() if events is None else events)
    return state


def add_run(state: SessionState, tag="P1") -> Path:
    state.begin_phase(tag, "while opening the housing", Path(f"{tag}.mp4"), "0" * 64)
    run = state.run_dir(tag)
    run.mkdir(parents=True)
    (run / "parts.json").write_text(json.dumps(
        {"schema": "anastylos-session-run@1", "phase": tag, "memory": {"phase": "phase-0001"},
         "parts": [{"part": f"{tag}-C01"}], "counts": {}}), encoding="utf-8")
    (run / "questions.json").write_text(json.dumps(
        {"schema": "anastylos-session-questions@1", "phase": tag, "questions": [
            {"id": f"Q-{tag}-01", "kind": "source_of_part", "part": f"{tag}-C01", "cell": "cell-1",
             "deposit_window_s": [25.6, 28.6], "why": ["no observed removal precedes this deposit"],
             "candidates": [{"removal_event": "E-0049", "window_s": [41.0, 93.2]}],
             "ask": "Where did it come from?"},
            {"id": f"Q-{tag}-02", "kind": "destination_of_removal", "removal_event": "E-0104",
             "window_s": [71.6, 75.0], "sources": [], "why": [],
             "ask": "Where did it go?"}]}), encoding="utf-8")
    state.finish_phase(tag, {"questions": 2})
    return run


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(path): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


class Case(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)


class ExportContent(Case):
    def setUp(self):
        super().setUp()
        self.state = make_session(self.tmp / "s")
        add_run(self.state)
        guide.build(self.state)
        self.out = self.tmp / "report.html"
        self.result = report.export(self.state.root, self.out)
        self.text = self.out.read_text(encoding="utf-8")

    def test_result_document_is_stable_and_bound(self):
        self.assertEqual(self.result["schema"], report.EXPORT_SCHEMA)
        self.assertEqual(self.result["guide"], "fresh")
        self.assertEqual(self.result["parts"], 4)
        self.assertEqual(self.result["install_steps"], 3)  # the screw pair forms one group step
        self.assertEqual(self.result["report_sha256"], hashlib.sha256(self.out.read_bytes()).hexdigest())
        self.assertEqual(len(self.result["memory_sha256"]), 64)
        self.assertEqual(len(self.result["package_sha256"]), 64)

    def test_all_sections_are_present(self):
        for anchor in ("data-state", "parts", "removal-order", "assembly-plan", "open-questions",
                       "progress", "provenance", "figures"):
            self.assertIn(f'id="{anchor}"', self.text)

    def test_origin_and_storage_stay_separate_facts(self):
        self.assertIn("top-left hole H2", self.text)  # origin, operator words
        self.assertIn("cell-1", self.text)  # storage
        self.assertIn("Original location", self.text)
        self.assertIn("Storage place", self.text)

    def test_basis_is_labelled_per_part(self):
        self.assertIn("operator statement", self.text)
        self.assertIn("system observation", self.text)
        self.assertIn("link inferred, not observed", self.text)
        self.assertIn("candidate_by_elimination", self.text)
        self.assertIn("interchangeable group", self.text)

    def test_install_order_is_never_a_verified_sequence(self):
        self.assertIn("Not a verified assembly sequence", self.text)
        self.assertIn("reverse of the recorded removal order", self.text)
        self.assertIn("No mechanical dependency was observed or confirmed", self.text)
        for invented in ("mechanically verified", "assembly approved", "frame confirmed",
                         "identity verified", "confirmed assembly order", "mounting release",
                         "mounting approved"):
            self.assertNotIn(invented, self.text.lower())

    def test_missing_information_stays_visible(self):
        self.assertIn("PCB with cable", self.text)
        self.assertIn("not recorded", self.text)
        self.assertIn("no storage place", self.text)
        self.assertIn("original location", self.text)  # in the missing column

    def test_evidence_is_cited_without_paths(self):
        self.assertIn("E-0092_SRC-14", self.text)
        self.assertIn("R10", self.text)
        self.assertIn("ffffffffffff", self.text)  # short hash, not the full path
        self.assertNotIn("C:", self.text)
        self.assertNotIn(str(self.state.root), self.text)
        self.assertNotIn("result.json", self.text)

    def test_document_is_standalone(self):
        lowered = self.text.lower()
        self.assertNotIn("<script", lowered)
        self.assertNotIn("http", lowered)
        self.assertNotIn("cdn", lowered)
        self.assertIn("<style>", self.text)
        self.assertIn("@media print", self.text)
        self.assertIn('<html lang="en">', self.text)
        self.assertIn("utf-8", lowered)


class EscapingAndLongTexts(Case):
    def test_html_special_chars_long_labels_and_umlauts(self):
        state = make_session(self.tmp / "s", synthetic_events(long_label=LONG_LABEL))
        guide.build(state)
        out = self.tmp / "r.html"
        report.export(state.root, out)
        text = out.read_text(encoding="utf-8")
        self.assertIn("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; &quot;\u00dcn\u00eflaute&quot;", text)
        self.assertIn("Test &lt;object&gt; &amp; parts", text)
        self.assertIn("A" * 600, text)
        self.assertNotIn("<script>", text)
        self.assertNotIn(EVIL_LABEL, text)


class StaleAndTampered(Case):
    def test_stale_guide_is_refused_with_a_rebuild_instruction(self):
        state = make_session(self.tmp / "s")
        guide.build(state)
        out = self.tmp / "r.html"
        first = report.export(state.root, out)
        with open(state.memory_dir / "memory.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"id": "dec-0009", "type": "declared_removal", "session": "s",
                                     "operator_label": "late part", "source": "box",
                                     "phase": "phase-0001", "declared_by": "tester"}) + "\n")
        with self.assertRaisesRegex(report.ReportError, "guide build"):
            report.export(state.root, out)
        self.assertEqual(hashlib.sha256(out.read_bytes()).hexdigest(), first["report_sha256"])  # untouched

    def test_tampered_package_is_refused(self):
        state = make_session(self.tmp / "s")
        guide.build(state)
        package = guide.guide_dir(state) / "session.json"
        package.write_bytes(package.read_bytes().replace(b"cell-1", b"cell-2"))
        with self.assertRaisesRegex(report.ReportError, "changed after it was built"):
            report.export(state.root, self.tmp / "r.html")
        self.assertFalse((self.tmp / "r.html").exists())

    def test_corrupt_memory_is_refused(self):
        state = make_session(self.tmp / "s")
        (state.memory_dir / "memory.jsonl").write_text("not json\n", encoding="utf-8")
        with self.assertRaisesRegex(report.ReportError, "cannot be trusted"):
            report.export(state.root, self.tmp / "r.html")
        self.assertFalse((self.tmp / "r.html").exists())


class MissingGuide(Case):
    def test_export_without_guide_has_records_but_no_plan(self):
        state = make_session(self.tmp / "s")
        out = self.tmp / "r.html"
        result = report.export(state.root, out)
        text = out.read_text(encoding="utf-8")
        self.assertEqual(result["guide"], "none")
        self.assertIsNone(result["package_sha256"])
        self.assertIsNone(result["install_steps"])
        self.assertEqual(result["parts"], 4)
        self.assertIn("No guide package", text)
        self.assertIn("No recorded assembly plan", text)
        self.assertIn("PCB with cable", text)
        self.assertNotIn("reverse of the recorded removal order", text)


class DeterminismAndInputs(Case):
    def test_repeated_export_is_byte_identical(self):
        state = make_session(self.tmp / "s")
        add_run(state)
        guide.build(state)
        first = self.tmp / "a.html"
        second = self.tmp / "b.html"
        report.export(state.root, first)
        report.export(state.root, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_inputs_are_unchanged(self):
        state = make_session(self.tmp / "s")
        add_run(state)
        (state.root / "live").mkdir()
        (state.root / "live" / "journal.jsonl").write_text(
            json.dumps({"kind": "question_state", "question_id": "Q-P1-01", "revision": 1,
                        "state": "OPEN"}) + "\n", encoding="utf-8")
        guide.build(state)
        before = snapshot(state.root)
        report.export(state.root, self.tmp / "r.html")
        self.assertEqual(snapshot(state.root), before)

class QuestionsAndProgress(Case):
    def setUp(self):
        super().setUp()
        self.state = make_session(self.tmp / "s")
        add_run(self.state)
        (self.state.root / "live").mkdir()
        (self.state.root / "live" / "journal.jsonl").write_text("".join(json.dumps(record) + "\n" for record in [
            {"kind": "question_state", "question_id": "Q-P1-01", "revision": 1, "state": "OPEN"},
            {"kind": "question_state", "question_id": "Q-P1-02", "revision": 2,
             "state": "LEFT_UNRESOLVED", "reason": "budget"},
            {"kind": "done", "question_id": "Q-P1-02b"},
        ]), encoding="utf-8")
        (self.state.root / "declare").mkdir()
        (self.state.root / "declare" / "journal.jsonl").write_text("".join(json.dumps(record) + "\n" for record in [
            {"kind": "question_state", "question_id": "D-01", "state": "COMMITTED"},
        ]), encoding="utf-8")
        guide.build(self.state)
        # Removal order is dec-0001, dec-0003, obs-0001, dec-0002; install reverses it.
        assemble.mark_done(self.state, 1)  # dec-0002, by the operator
        returns = self.tmp / "returns.json"
        returns.write_text(json.dumps(
            {"schema": assemble.RETURNS_SCHEMA, "returns": [
                {"id": "R-0001", "status": "candidate_temporal_exclusive",
                 "departure": {"zone": "cell-3"}, "memory_check": "consistent_with_memory"},
                {"id": "R-0002", "status": "ambiguous"}]}), encoding="utf-8")
        self.assertEqual(assemble.apply_returns(self.state, returns), {2: "verified"})
        self.out = self.tmp / "r.html"
        self.result = report.export(self.state.root, self.out)
        self.text = self.out.read_text(encoding="utf-8")

    def test_open_and_unresolved_questions_are_listed_with_detail(self):
        self.assertEqual(self.result["open_questions"], ["Q-P1-01"])
        self.assertEqual(self.result["left_unresolved"], ["Q-P1-02"])
        self.assertIn("Q-P1-01", self.text)
        self.assertIn("E-0049", self.text)  # offered candidate, not an answer
        self.assertIn("no observed removal precedes this deposit", self.text)
        self.assertIn("budget", self.text)  # leave reason
        self.assertIn("D-01", self.text)  # declaration state
        # The committed pseudo-record is not an open question.
        open_section = self.text.split("Open questions")[1].split("What the session does not know")[0]
        self.assertNotIn("Q-P1-02b", open_section)

    def test_progress_distinguishes_operator_done_verified_and_pending(self):
        assembly = self.result["assembly"]
        self.assertEqual((assembly["steps_done"], assembly["steps_total"], assembly["current_step"]), (2, 3, 3))
        self.assertFalse(assembly["complete"])
        self.assertIn("done, by operator", self.text)
        self.assertIn("verified by return check", self.text)
        self.assertIn("R-0001", self.text)
        self.assertIn("pending", self.text)


class CliTest(Case):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = report_cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_export_round_trip(self):
        state = make_session(self.tmp / "s")
        guide.build(state)
        out = self.tmp / "r.html"
        code, stdout, _ = self.run_cli("--session", str(state.root), "--out", str(out))
        self.assertEqual(code, 0)
        result = json.loads(stdout)
        self.assertEqual(result["schema"], report.EXPORT_SCHEMA)
        self.assertTrue(out.is_file())

    def test_missing_session_and_stale_guide_stop_without_output(self):
        code, stdout, err = self.run_cli("--session", str(self.tmp / "absent"), "--out", str(self.tmp / "r.html"))
        self.assertEqual((code, stdout), (1, ""))
        self.assertTrue(err.startswith("STOPPED:"))
        self.assertFalse((self.tmp / "r.html").exists())
        state = make_session(self.tmp / "s")
        guide.build(state)
        out = self.tmp / "r.html"
        self.assertEqual(self.run_cli("--session", str(state.root), "--out", str(out))[0], 0)
        before = out.read_bytes()
        with open(state.memory_dir / "memory.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"id": "dec-0009", "type": "declared_removal", "session": "s",
                                     "operator_label": "late part", "source": "box",
                                     "phase": "phase-0001", "declared_by": "tester"}) + "\n")
        code, stdout, err = self.run_cli("--session", str(state.root), "--out", str(out))
        self.assertEqual(code, 1)
        self.assertIn("STOPPED:", err)
        self.assertIn("guide build", err)
        self.assertEqual(out.read_bytes(), before)


class Full01Copy(Case):
    def test_realistic_session_exports_with_group_steps_and_umlauts(self):
        import test_m2 as m2

        state = m2.full_session(self.tmp / "s")
        guide.build(state)
        assemble.mark_done(state, 1)
        out = self.tmp / "full01.html"
        result = report.export(state.root, out)
        text = out.read_text(encoding="utf-8")
        self.assertEqual(result["guide"], "fresh")
        self.assertEqual(result["install_steps"], 14)
        self.assertEqual(result["open_questions"], [])
        self.assertEqual(result["left_unresolved"], [])
        self.assertIn("Schrauben", text)
        self.assertIn("L-Taste", text)
        self.assertIn("Not a verified assembly sequence", text)
        self.assertIn("done, by operator", text)
        self.assertNotIn("C:", text)
        self.assertNotIn("<script", text.lower())


if __name__ == "__main__":
    unittest.main()
