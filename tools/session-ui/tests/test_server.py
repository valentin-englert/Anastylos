"""Session-UI server tests: routing, guards and one HTTP question round (loopback only)."""

from __future__ import annotations

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

import prepare_demo  # noqa: E402
import server  # noqa: E402
from adapter import AdapterError  # noqa: E402


def call(base: str, path: str, body: dict | None = None, origin: str | None = None):
    request = urllib.request.Request(base + path, method="POST" if body is not None else "GET")
    if origin:
        request.add_header("Origin", origin)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    try:
        with urllib.request.urlopen(request, data=data, timeout=60) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


class ServerCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.workspace = Path(tmp.name) / "workspace"
        self.workspace.mkdir()
        prepare_demo.make_fresh_session(self.workspace / "p1", phases=("P1",))
        self.httpd = server.serve(self.workspace, "127.0.0.1", 0)
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        # addCleanup runs LIFO: shutdown, join the server thread, close the socket.
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.thread.join, 30)
        self.addCleanup(self.httpd.shutdown)

    def test_page_and_sessions(self):
        with urllib.request.urlopen(self.base + "/", timeout=30) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("ANASTYLOS Session", response.read().decode("utf-8"))
        status, payload = call(self.base, "/api/sessions")
        self.assertEqual(status, 200)
        self.assertEqual([s["id"] for s in payload["sessions"]], ["p1"])

    def test_status_and_question_round_over_http(self):
        status, payload = call(self.base, "/api/status?session=p1")
        self.assertEqual(status, 200)
        self.assertFalse(payload["complete"])
        status, asked = call(self.base, "/api/questions/next", {"session": "p1"})
        self.assertEqual(status, 200)
        qid = asked["question_id"]
        script = prepare_demo.full01_script()
        status, answered = call(self.base, "/api/questions/answer",
                                {"session": "p1", "question_id": qid, "text": script[qid]["answer"][0]})
        self.assertEqual(status, 200)
        self.assertEqual(answered["status"], "read_back")
        status, done = call(self.base, "/api/questions/confirm",
                             {"session": "p1", "question_id": qid, "text": "yes"})
        self.assertEqual((status, done["status"]), (200, "committed"))
        status, repeated = call(self.base, "/api/questions/confirm",
                                 {"session": "p1", "question_id": qid, "text": "yes"})
        self.assertEqual((status, repeated["status"]), (200, "already_committed"))

    def test_foreign_origin_is_refused_on_writes(self):
        status, _ = call(self.base, "/api/questions/next", {"session": "p1"},
                         origin="https://evil.example")
        self.assertEqual(status, 403)
        status, _ = call(self.base, "/api/questions/next", {"session": "p1"},
                         origin="http://127.0.0.1:9999")
        self.assertEqual(status, 200)

    def test_unknown_and_traversal_ids_are_rejected(self):
        status, _ = call(self.base, "/api/status?session=nope")
        self.assertEqual(status, 404)
        status, _ = call(self.base, "/api/status?session=..%2Fp1")
        self.assertEqual(status, 404)
        status, _ = call(self.base, "/api/questions/next", {"session": "p1"})
        self.assertEqual(status, 200)
        status, payload = call(self.base, "/api/select", {"session": "p1", "binding": "../x.json"})
        self.assertEqual(status, 404)
        self.assertIn("error", payload)

    def test_missing_parameters_are_bad_requests(self):
        status, _ = call(self.base, "/api/status")
        self.assertEqual(status, 400)
        status, _ = call(self.base, "/api/questions/answer", {"session": "p1"})
        self.assertEqual(status, 400)

    def test_non_loopback_bind_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(AdapterError):
                server.serve(Path(tmp), "0.0.0.0", 0)


if __name__ == "__main__":
    unittest.main()
