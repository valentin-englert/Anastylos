"""Capture loop: phone link, view request against a loopback receiver (real G0 validation), delivery → follow-up job."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

UI_ROOT = Path(__file__).resolve().parents[1]
REPO = UI_ROOT.parents[1]
sys.path.insert(0, str(UI_ROOT))
sys.path.insert(0, str(REPO / "capture-companion" / "receiver"))

import anastylos_capture_receiver as receiver  # noqa: E402
import anastylos_guided_routes as guided  # noqa: E402
import captures  # noqa: E402
from adapter import AdapterError  # noqa: E402
from control_client import ControlClient  # noqa: E402

VECTOR = json.loads((REPO / "capture-companion" / "control" / "contracts" / "vectors" / "control-request-valid.json").read_text())
FUTURE = "2030-01-01T00:00:00Z"


def _workspace(tmp: Path, data_dir: Path, source_id: str) -> tuple[Path, Path]:
    root = tmp / "ws"
    job = root / "s1" / "drawings" / "jobs" / "back-shell-1"
    job.mkdir(parents=True)
    (root / "s1" / "agent-session.json").write_text("{}", encoding="utf-8")
    spec = {"part": "back shell", "scope": "assembly", "view_side": "inner side", "region_mm": [10, 20, 200, 150], "px_per_mm": 4}
    (job / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    (job / "progress.json").write_text(json.dumps({"state": "done", "result": {
        "status": "NEEDS_CAPTURE", "reasons": ["Muse reports the outline is cut off"], "spec": spec}}), encoding="utf-8")
    (root / captures.SETTINGS).write_text(json.dumps({
        "receiver_url": "set-by-test", "receiver_data_dir": str(data_dir), "source_id": source_id,
        "board_id": VECTOR["board_id"], "calibration_id": VECTOR["calibration_id"], "board_frame": "board:test:surface_a3"}))
    return root, job


class LoopbackCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.state = receiver.ReceiverState(self.tmp / "data")
        self.server, self.thread = receiver.serve_in_background(self.state)
        self.addCleanup(self._shutdown)
        host, port = self.server.server_address[:2]
        self.client = ControlClient("http://%s:%d" % (host, port))
        self.journal = guided.get_journal(self.state)
        legacy = self.state.issue_token("phone")
        self.source_id, _ = guided.get_auth(self.state).enroll_source("phone", hashlib.sha256(legacy.encode()).hexdigest())
        self.root, self.job = _workspace(self.tmp, self.state.data_dir, self.source_id)

    def _shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)
        cached = vars(self.state).get("_guided_journal")
        if cached is not None:
            cached.close()

    def _mint(self, conf):
        token, _ = guided.get_auth(self.state).mint("adapter_bootstrap", 3600, principal=captures.PRINCIPAL)
        return token

    def _arm_on_phone(self, session_id, revision):
        self.journal.consent(self.source_id, {
            "operation_id": str(uuid.uuid4()), "session_id": session_id, "expected_session_revision": revision,
            "action": "arm", "consent_revision": 1, "max_images": 20, "expires_at": FUTURE, "camera_id": "camera:0",
            "allow_auto_upload": True})


class LinkAndRequestTest(LoopbackCase):
    def test_connect_waits_for_phone_then_claims_and_requests_a_valid_view(self):
        first = captures.connect_phone(self.root, self.client, mint=self._mint)
        self.assertEqual(first["state"], "waiting_for_phone")
        self.assertEqual(captures.link_status(self.root, self.client)["state"], "waiting_for_phone")
        # the phone arms the session (consent) the way GuidedControlClient.consent does
        snapshot = self.journal.get_session(first["guided_session_id"], None)
        self._arm_on_phone(first["guided_session_id"], snapshot["session"]["revision"])
        self.assertEqual(captures.link_status(self.root, self.client)["state"], "connected")
        link = json.loads((self.root / captures.LINK).read_text())
        self.assertIsNone(link["bootstrap_token"])
        status = captures.request_capture(self.root, "s1", "back-shell-1", self.client)
        self.assertEqual(status["state"], "requested")
        self.assertEqual(status["label"], "back shell, inner side up, on the sheet, hands off")
        self.assertFalse(status["identity_proven"])
        again = captures.request_capture(self.root, "s1", "back-shell-1", self.client)  # a repeat click
        self.assertEqual(again["request_id"], status["request_id"])
        self.assertEqual(len(self.client.get_session(link["adapter_token"], link["guided_session_id"])["requests"]), 1)

    def test_request_needs_a_connected_phone_and_a_job_that_needs_a_capture(self):
        with self.assertRaises(AdapterError):
            captures.request_capture(self.root, "s1", "back-shell-1", self.client)
        progress = json.loads((self.job / "progress.json").read_text())
        progress["result"]["status"] = "drawn_unconfirmed"
        (self.job / "progress.json").write_text(json.dumps(progress))
        (self.root / captures.LINK).write_text(json.dumps({"state": "connected", "guided_session_id": "x", "adapter_token": "t"}))
        with self.assertRaises(AdapterError):
            captures.request_capture(self.root, "s1", "back-shell-1", self.client)


class _FakePhone:
    """Snapshot with one delivered receipt; asset bytes re-hashed like the real client."""
    def __init__(self, data: bytes, status="qualified"):
        self.data = data
        self.digest = hashlib.sha256(data).hexdigest()
        self.status = status
        self.fetches = 0

    def get_session(self, token, session_id):
        return {"session": {"revision": 3}, "requests": [{"request_id": "r-1", "status": "CAPTURED_LOCAL"}],
                "receipts": [{"request_id": "r-1", "request_revision": 1, "image_id": "img-1", "image_sha256": self.digest,
                              "status": self.status, "receipt_id": "rc-1"}]}

    def asset_bytes(self, token, session_id, image_id):
        self.fetches += 1
        return self.data, self.digest


class DeliveryTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root, self.job = _workspace(Path(tmp.name), Path(tmp.name) / "data", str(uuid.uuid4()))
        (self.root / captures.LINK).write_text(json.dumps({"state": "connected", "guided_session_id": "g-1", "adapter_token": "t"}))
        (self.job / captures.REQUEST).write_text(json.dumps({
            "schema": "anastylos-capture-request@1", "state": "requested", "origin_job": "back-shell-1", "request_id": "r-1",
            "request_revision": 1, "guided_session_id": "g-1", "label": "back shell", "reasons": [], "follow_up_job": None,
            "identity_proven": False}))
        self.started = []

    def _start(self, job_dir, still):
        self.started.append(still)
        return "back-shell-1-capture-x"

    def test_delivered_still_is_stored_with_evidence_and_starts_one_follow_up(self):
        phone = _FakePhone(b"jpeg")
        status = captures.capture_status(self.root, "s1", "back-shell-1", phone, start=self._start)
        self.assertEqual((status["state"], status["follow_up_job"]), ("follow_up_started", "back-shell-1-capture-x"))
        still = self.root / "s1" / "drawings" / "captures" / "img-1.jpg"
        evidence = json.loads(still.with_suffix(".json").read_text())
        self.assertEqual((still.read_bytes(), evidence["sha256"], evidence["identity_proven"]), (b"jpeg", phone.digest, False))
        self.assertEqual(self.started[0]["sha256"], phone.digest)
        captures.capture_status(self.root, "s1", "back-shell-1", phone, start=self._start)  # later polls change nothing
        self.assertEqual((len(self.started), phone.fetches), (1, 1))

    def test_hash_mismatch_with_the_receipt_is_refused(self):
        phone = _FakePhone(b"jpeg")
        phone.digest_receipt = "0" * 64
        phone.get_session = lambda token, sid: {"session": {}, "requests": [], "receipts": [
            {"request_id": "r-1", "image_id": "img-1", "image_sha256": "0" * 64, "status": "qualified"}]}
        with self.assertRaises(AdapterError):
            captures.capture_status(self.root, "s1", "back-shell-1", phone, start=self._start)
        self.assertEqual(self.started, [])

    def test_crash_after_delivery_starts_the_follow_up_on_the_next_poll(self):
        record = json.loads((self.job / captures.REQUEST).read_text())
        record.update(state="delivered", still={"path": "p", "sha256": "s", "request_id": "r-1"})
        (self.job / captures.REQUEST).write_text(json.dumps(record))
        status = captures.capture_status(self.root, "s1", "back-shell-1", None, start=self._start)
        self.assertEqual(status["state"], "follow_up_started")
        self.assertEqual(len(self.started), 1)


if __name__ == "__main__":
    unittest.main()
