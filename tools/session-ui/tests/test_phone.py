"""Phone page: pairing code, pending requests, the operator's decision, enrolment and the localhost routes."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

UI_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UI_ROOT))

import phone  # noqa: E402
import server  # noqa: E402
from adapter import AdapterError  # noqa: E402

CERT = UI_ROOT.parents[1] / "capture-companion" / "android" / "app" / "src" / "test" / "resources" / "tls" / "workstation.pem"
REQUEST = "0f3c9a7e-1b2d-4c5e-8f60-a1b2c3d4e5f6"


def done(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class FakeCli:
    """Stands in for the receiver CLI and keeps its own little state."""

    def __init__(self, status=None, enroll_out="source_id: src-new\n"):
        self.commands = []
        self.requests = {REQUEST: {"device_label": "Redmi Note 10 Pro", "status": "pending", "created_at": "t",
                                   "expires_at": time.time() + 120, "token": False}} if status is None else status
        self.enroll_out = enroll_out
        self.fail = set()

    def __call__(self, command):
        verb = next(part for part in command[5:] if not part.startswith("-"))
        self.commands.append(command[5:])
        if verb in self.fail:
            return done(returncode=1, stderr=f"error: {verb} refused")
        if verb == "pair-status":
            return done(json.dumps({"workstation_id": "w", "requests": self.requests}))
        if verb == "pair-code":
            Path(command[command.index("--qr-out") + 1]).write_bytes(b"\x89PNG fake")
            return done("pairing code ...")
        if verb == "guided-enroll":
            return done(self.enroll_out)
        return done("ok")


class PhoneCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.data = self.root / "receiver-data"
        self.write_settings(source_id="src-old")

    def write_settings(self, **extra):
        conf = {"receiver_data_dir": str(self.data), "receiver_url": "https://192.168.0.7:8443", "receiver_cert": str(CERT),
                "phone_host": "192.168.0.7", **extra}
        (self.root / phone.SETTINGS).write_text(json.dumps(conf))


class TestPairing(PhoneCase):
    def test_pairing_code_uses_the_receiver_cli_with_host_cert_and_qr_path(self):
        cli = FakeCli()
        result = phone.start_pairing(self.root, cli)
        command = cli.commands[0]
        self.assertEqual(command[0], "pair-code")
        self.assertEqual(command[command.index("--host") + 1], "192.168.0.7")
        self.assertEqual(command[command.index("--port") + 1], "8443")
        self.assertEqual(command[command.index("--cert") + 1], str(CERT))
        self.assertEqual(result["state"], "waiting_for_scan")
        self.assertRegex(result["certificate_sha256"], r"^([0-9A-F]{2}:){31}[0-9A-F]{2}$")
        self.assertEqual(phone.pairing_qr_png(self.root), b"\x89PNG fake")

    def test_missing_setup_or_certificate_is_a_clear_refusal(self):
        (self.root / phone.SETTINGS).unlink()
        with self.assertRaises(AdapterError) as missing:
            phone.start_pairing(self.root, FakeCli())
        self.assertIn("capture-settings.json", str(missing.exception))
        self.write_settings(receiver_cert=str(self.root / "nope.pem"))
        with self.assertRaises(AdapterError) as no_cert:
            phone.start_pairing(self.root, FakeCli())
        self.assertIn("receiver_cert", str(no_cert.exception))
        with self.assertRaises(AdapterError):
            phone.pairing_qr_png(self.root)

    def test_cli_failure_surfaces_the_receivers_reason(self):
        cli = FakeCli()
        cli.fail.add("pair-code")
        with self.assertRaises(AdapterError) as caught:
            phone.start_pairing(self.root, cli)
        self.assertIn("pair-code refused", str(caught.exception))


class TestDecision(PhoneCase):
    def test_pending_lists_device_short_id_and_certificate_but_not_old_or_decided_requests(self):
        cli = FakeCli()
        cli.requests["old"] = {"device_label": "Old", "status": "pending", "expires_at": time.time() - 5}
        cli.requests["done-1"] = {"device_label": "Done", "status": "approved", "expires_at": time.time() + 99}
        view = phone.pending_requests(self.root, cli)
        self.assertEqual([(r["device"], r["short_id"]) for r in view["requests"]], [("Redmi Note 10 Pro", REQUEST[:12])])
        self.assertTrue(view["certificate_sha256"])
        self.assertEqual(view["source_id"], "src-old")

    def test_approval_enrols_the_phone_and_updates_the_source_id_and_drops_the_old_link(self):
        (self.root / phone.LINK).write_text("{}")
        cli = FakeCli()
        result = phone.decide(self.root, REQUEST, "approve", cli)
        self.assertEqual(result, {"decision": "approved", "request": REQUEST, "source_id": "src-new"})
        verbs = [next(p for p in c if not p.startswith("-")) for c in cli.commands]
        self.assertEqual(verbs, ["pair-status", "pair-approve", "guided-enroll"])
        self.assertEqual(json.loads((self.root / phone.SETTINGS).read_text())["source_id"], "src-new")
        self.assertFalse((self.root / phone.LINK).exists())

    def test_reject_never_enrols_and_leaves_the_settings_alone(self):
        cli = FakeCli()
        self.assertEqual(phone.decide(self.root, REQUEST, "reject", cli)["decision"], "rejected")
        self.assertTrue(any("pair-reject" in c for c in cli.commands))
        self.assertFalse(any("guided-enroll" in c for c in cli.commands))
        self.assertEqual(json.loads((self.root / phone.SETTINGS).read_text())["source_id"], "src-old")

    def test_only_a_listed_pending_request_can_be_decided_and_nothing_happens_by_itself(self):
        cli = FakeCli()
        with self.assertRaises(AdapterError) as unknown:
            phone.decide(self.root, "ffffffff-0000", "approve", cli)
        self.assertEqual(unknown.exception.status, 404)
        with self.assertRaises(AdapterError):
            phone.decide(self.root, "../etc/passwd", "approve", cli)
        with self.assertRaises(AdapterError) as bad:
            phone.decide(self.root, REQUEST, "maybe", cli)
        self.assertEqual(bad.exception.status, 400)
        phone.pending_requests(self.root, cli)
        phone.start_pairing(self.root, cli)
        verbs = [next(p for p in c if not p.startswith("-")) for c in cli.commands]
        self.assertNotIn("pair-approve", verbs)  # approval exists only inside decide()

    def test_a_failed_enrolment_does_not_change_the_settings(self):
        cli = FakeCli(enroll_out="no source here")
        with self.assertRaises(AdapterError) as caught:
            phone.decide(self.root, REQUEST, "approve", cli)
        self.assertIn("enrolment", str(caught.exception))
        self.assertEqual(json.loads((self.root / phone.SETTINGS).read_text())["source_id"], "src-old")


class TestRoutes(PhoneCase):
    def setUp(self):
        super().setUp()
        self.httpd = server.serve(self.root, "127.0.0.1", 0)
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(thread.join, 30)
        self.addCleanup(self.httpd.shutdown)

    def call(self, path, body=None, origin=None):
        request = urllib.request.Request(self.base + path, method="POST" if body is not None else "GET")
        if origin:
            request.add_header("Origin", origin)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        try:
            with urllib.request.urlopen(request, data=data, timeout=30) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_a_foreign_browser_origin_cannot_decide_and_unknown_requests_are_refused(self):
        status, _ = self.call("/api/phone/decision", {"request": REQUEST, "decision": "approve"}, origin="https://evil.example")
        self.assertEqual(status, 403)
        status, body = self.call("/api/phone/pair-qr")
        self.assertEqual(status, 409)
        self.assertIn("pairing code", json.loads(body)["error"])

    def test_the_phone_page_is_in_the_menu_and_the_page_markup(self):
        shell = (UI_ROOT / "web" / "shell.js").read_text(encoding="utf-8")
        html = (UI_ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id: "phone"', shell)
        self.assertIn('data-page="phone"', html)
        for element in ("pair-start", "pair-requests", "phone-qr-block", "video-list"):
            self.assertIn(f'id="{element}"', html)


if __name__ == "__main__":
    unittest.main()
