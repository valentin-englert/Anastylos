"""Integration: report export and capture needs through the session UI (offline).

Covers the wired path only: the planner and the report keep their own suites.
Closure of a capture need stays locked: this build has no technical image
check, so no review can be submitted and the planner keeps gaps open or
needs_review.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

UI_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UI_ROOT))
sys.path.insert(0, str(UI_ROOT / "demo"))

import adapter  # noqa: E402
import prepare_demo  # noqa: E402
import server  # noqa: E402
from adapter import AdapterError  # noqa: E402


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def input_hashes(root: Path) -> dict[str, str | None]:
    paths = {"memory": root / "memory" / "memory.jsonl",
             "guide": root / "guide" / "session.json",
             "builds": root / "guide" / "builds.jsonl"}
    return {key: sha(path) if path.is_file() else None for key, path in paths.items()}


class Workspace(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "workspace"
        self.root.mkdir()
        self.session_id = "p1"
        self.session_dir = self.root / self.session_id
        prepare_demo.make_fresh_session(self.session_dir, phases=("P1",))
        self.script = prepare_demo.full01_script()

    def answer_all(self) -> None:
        for _ in range(3):
            asked = adapter.question_next(self.root, self.session_id)
            qid = asked["question_id"]
            answered = adapter.question_answer(self.root, self.session_id, qid,
                                               self.script[qid]["answer"][0], None)
            self.assertEqual(answered["stage"], "confirm", answered)
            done = adapter.question_confirm(self.root, self.session_id, qid, "yes")
            self.assertEqual(done["status"], "committed", done)

    def declare_extra(self) -> None:
        import declare  # product module, used as the CLI uses it

        from state import SessionState
        book = declare.Declarations(SessionState.load(self.session_dir)).open()
        try:
            proposal = book.propose_extra("P1", "integration probe part", "cell 9")
            result = book.confirm(proposal["op_key"], {"source": "typed", "text": "yes", "choice": None,
                                                       "received_at": "2026-10-31T00:00:00.000+00:00",
                                                       "transcript": None})
            self.assertEqual(result["status"], "committed")
        finally:
            book.close()


class ReportExportTest(Workspace):
    def test_export_writes_only_the_report(self):
        self.answer_all()
        adapter.guide_build(self.root, self.session_id)
        before = input_hashes(self.session_dir)
        first = adapter.report_export(self.root, self.session_id)
        self.assertEqual(first["file"], "report.html")
        self.assertEqual(first["guide"], "fresh")
        self.assertGreater(first["parts"], 0)
        self.assertNotIn("C:\\", json.dumps(first))
        self.assertNotIn("/", first["file"])
        report_path = self.session_dir / "report.html"
        self.assertTrue(report_path.is_file())
        self.assertEqual(input_hashes(self.session_dir), before)
        # A repeated export is byte-identical: no duplicate state change.
        second = adapter.report_export(self.root, self.session_id)
        self.assertEqual(second["report_sha256"], first["report_sha256"])
        status = adapter.report_status(self.root, self.session_id)
        self.assertTrue(status["exists"])
        self.assertEqual(status["report_sha256"], first["report_sha256"])
        body = adapter.report_bytes(self.root, self.session_id)
        self.assertTrue(body.startswith(b"<!DOCTYPE html>"))
        self.assertIn(b"no external content, no scripts", body)

    def test_export_without_guide_has_no_plan(self):
        result = adapter.report_export(self.root, self.session_id)
        self.assertEqual(result["guide"], "none")
        self.assertIsNone(result["install_steps"])

    def test_stale_guide_blocks_export_and_writes_nothing(self):
        self.answer_all()
        adapter.guide_build(self.root, self.session_id)
        exported = adapter.report_export(self.root, self.session_id)
        self.declare_extra()
        with self.assertRaisesRegex(AdapterError, "stale"):
            adapter.report_export(self.root, self.session_id)
        # The previous report is left untouched; nothing new was written.
        status = adapter.report_status(self.root, self.session_id)
        self.assertEqual(status["report_sha256"], exported["report_sha256"])

    def test_missing_report_is_404(self):
        with self.assertRaises(AdapterError) as raised:
            adapter.report_bytes(self.root, self.session_id)
        self.assertEqual(raised.exception.status, 404)


class CaptureGapsTest(Workspace):
    def setUp(self):
        super().setUp()
        self.answer_all()
        adapter.guide_build(self.root, self.session_id)
        from state import SessionState
        prepare_demo.write_capture_gaps(SessionState.load(self.session_dir))

    def test_gaps_load_with_instruction_and_criteria(self):
        view = adapter.gaps_view(self.root, self.session_id)
        self.assertEqual(len(view["gaps"]), 3)
        self.assertIn("locked", view["closure"])
        fresh = next(gap for gap in view["gaps"] if gap["file"] == "gap-rim-outer.json")
        self.assertEqual(fresh["status"], "open")
        for key in ("missing_information", "instruction", "technical_criteria",
                    "content_criterion", "detail"):
            self.assertTrue(fresh[key], key)
        self.assertEqual(len(view["evidence"]), 1)
        candidate = view["evidence"][0]
        self.assertEqual(candidate["file"], "candidate-rim-1.png")
        actual = (self.session_dir / "capture_gaps" / "evidence" / candidate["file"]).read_bytes()
        self.assertEqual(candidate["image_sha256"], hashlib.sha256(actual).hexdigest())

    def test_candidate_without_reviews_stays_needs_review(self):
        result = adapter.gaps_evaluate(self.root, self.session_id, "gap-rim-outer.json",
                                       "candidate-rim-1.png")
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("review", result["reason"])
        self.assertEqual(result["candidate"]["evidence_id"], "candidate-rim-1.png")

    def test_stale_binding_and_duplicate_bytes_close_nothing(self):
        stale = adapter.gaps_evaluate(self.root, self.session_id, "gap-rim-stale.json",
                                      "candidate-rim-1.png")
        self.assertEqual(stale["status"], "needs_review")
        self.assertIn("Stale", stale["reason"])
        duplicate = adapter.gaps_evaluate(self.root, self.session_id, "gap-rim-duplicate.json",
                                          "candidate-rim-1.png")
        self.assertEqual(duplicate["status"], "needs_review")
        self.assertIn("not a new view", duplicate["reason"])

    def test_missing_technical_review_prevents_closure(self):
        # The planner closes only with explicit technical AND operator reviews; the
        # adapter never attaches reviews, so no evaluation here can close a gap.
        for gap in ("gap-rim-outer.json", "gap-rim-stale.json", "gap-rim-duplicate.json"):
            with self.subTest(gap=gap):
                result = adapter.gaps_evaluate(self.root, self.session_id, gap, "candidate-rim-1.png")
                self.assertNotEqual(result["status"], "closed")
                self.assertIsNone(result["basis"])

    def test_evaluation_writes_nothing(self):
        before = {(path.relative_to(self.session_dir), sha(path))
                  for path in sorted(self.session_dir.rglob("*")) if path.is_file()}
        adapter.gaps_evaluate(self.root, self.session_id, "gap-rim-outer.json", "candidate-rim-1.png")
        adapter.gaps_evaluate(self.root, self.session_id, "gap-rim-outer.json", "candidate-rim-1.png")
        adapter.gaps_view(self.root, self.session_id)
        after = {(path.relative_to(self.session_dir), sha(path))
                 for path in sorted(self.session_dir.rglob("*")) if path.is_file()}
        self.assertEqual(after, before)

    def test_unknown_gap_and_evidence_are_rejected(self):
        with self.assertRaises(AdapterError):
            adapter.gaps_evaluate(self.root, self.session_id, "../x.json", None)
        with self.assertRaises(AdapterError):
            adapter.gaps_evaluate(self.root, self.session_id, "gap-rim-outer.json", "../../x.png")
        with self.assertRaises(AdapterError):
            adapter.evidence_bytes(self.root, self.session_id, "missing.png")


def call(base: str, path: str, body: dict | None = None, origin: str | None = None):
    request = urllib.request.Request(base + path, method="POST" if body is not None else "GET")
    if origin:
        request.add_header("Origin", origin)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    try:
        with urllib.request.urlopen(request, data=data, timeout=60) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class WiredRoutesTest(Workspace):
    def setUp(self):
        super().setUp()
        self.answer_all()
        adapter.guide_build(self.root, self.session_id)
        from state import SessionState
        prepare_demo.write_capture_gaps(SessionState.load(self.session_dir))
        self.httpd = server.serve(self.root, "127.0.0.1", 0)
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.thread.join, 30)
        self.addCleanup(self.httpd.shutdown)

    def test_report_export_and_download_over_http(self):
        status, payload = call(self.base, "/api/report/export", {"session": "p1"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["guide"], "fresh")
        status, body = call(self.base, "/api/report?session=p1")
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"<!DOCTYPE html>"))

    def test_gaps_view_and_evaluate_over_http(self):
        status, payload = call(self.base, "/api/gaps?session=p1")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(payload)["gaps"]), 3)
        status, payload = call(self.base, "/api/gaps/evaluate",
                               {"session": "p1", "gap": "gap-rim-outer.json",
                                "candidate": "candidate-rim-1.png"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["status"], "needs_review")
        status, body = call(self.base, "/api/gaps/evidence?session=p1&file=candidate-rim-1.png")
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"\x89PNG"))

    def test_foreign_origin_and_escape_stay_rejected(self):
        for path, body in (("/api/report/export", {"session": "p1"}),
                            ("/api/gaps/evaluate", {"session": "p1", "gap": "gap-rim-outer.json"})):
            with self.subTest(path=path):
                status, _ = call(self.base, path, body, origin="https://evil.example")
                self.assertEqual(status, 403)
        status, _ = call(self.base, "/api/gaps/evidence?session=p1&file=..%2Fx.png")
        self.assertEqual(status, 404)
        status, _ = call(self.base, "/api/gaps/evaluate",
                         {"session": "p1", "gap": "..%2Fx.json"})
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
