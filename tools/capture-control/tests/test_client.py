"""Live client tests against a loopback receiver (no SDK needed)."""

import copy
import hashlib
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

CONTROL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CONTROL))
sys.path.insert(
    0, str(CONTROL.parents[1] / "capture-companion" / "receiver"))

import anastylos_capture_receiver as receiver
import anastylos_guided_routes as guided
from control_client import ControlClient, ControlClientError

VECTORS = (CONTROL.parents[1] / "capture-companion" / "control"
           / "contracts" / "vectors")
FUTURE = "2030-01-01T00:00:00Z"


def new_id():
    return str(uuid.uuid4())


class ClientCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = receiver.ReceiverState(Path(self.tmp.name) / "data")
        self.server, self.thread = receiver.serve_in_background(self.state)
        self.addCleanup(self.shutdown_server)
        host, port = self.server.server_address[:2]
        self.client = ControlClient("http://%s:%d" % (host, port))
        self.valid = json.loads(
            (VECTORS / "control-request-valid.json").read_text())
        self.journal = guided.get_journal(self.state)

    def shutdown_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)
        cached = vars(self.state).get("_guided_journal")
        if cached is not None:
            cached.close()

    def enroll_source(self):
        legacy = self.state.issue_token("client-test")
        legacy_sha = hashlib.sha256(legacy.encode("utf-8")).hexdigest()
        source_id, _record = guided.get_auth(self.state).enroll_source(
            "client-phone", legacy_sha)
        return source_id

    def open_and_arm(self, source_id, principal="adapter:client"):
        bootstrap, _entry = guided.get_auth(self.state).mint(
            "adapter_bootstrap", 3600, principal=principal)
        session = self.client.open_session(
            bootstrap,
            {"operation_id": new_id(), "source_id": source_id,
             "max_images": 20, "expires_at": FUTURE})
        armed = self.journal.consent(
            source_id,
            {"operation_id": new_id(), "session_id": session["session_id"],
             "expected_session_revision": session["revision"],
             "action": "arm", "consent_revision": 1, "max_images": 20,
             "expires_at": FUTURE, "camera_id": "camera:0",
             "allow_auto_upload": True})
        claimed = self.client.claim(bootstrap, armed["grant_id"], new_id())
        return session, armed, claimed["token"]

    def request_body(self, session_id, source_id, revision):
        valid = self.valid
        return {"operation_id": new_id(), "session_id": session_id,
                "expected_session_revision": revision,
                "source_id": source_id,
                "target_label": valid["target_label"],
                "board_id": valid["board_id"],
                "calibration_id": valid["calibration_id"],
                "target": copy.deepcopy(valid["target"]),
                "quality_profile_id": valid["quality_profile_id"],
                "expires_at": FUTURE, "reason": valid["reason"],
                "board_registration_epoch_id":
                    valid["board_registration_epoch_id"],
                "object_pose_epoch_id": valid["object_pose_epoch_id"],
                "cad_context": valid["cad_context"]}


class ClientFlowTests(ClientCase):
    def test_request_status_evidence_finish_round_trip(self):
        source_id = self.enroll_source()
        session, armed, token = self.open_and_arm(source_id)
        created = self.client.request_view(
            token, session["session_id"],
            self.request_body(session["session_id"], source_id,
                              armed["revision"]))
        snapshot = self.client.get_session(token, session["session_id"])
        self.assertEqual(len(snapshot["requests"]), 1)
        page = self.client.changes(token, session["session_id"], after="0")
        self.assertGreaterEqual(len(page["events"]), 1)
        sealed = self.client.finalize(
            token, session["session_id"],
            {"operation_id": new_id(), "session_id": session["session_id"],
             "expected_session_revision": created["session_revision"]})
        self.assertEqual(sealed["revision"], 1)
        fetched = self.client.capture_set(token, session["session_id"])
        self.assertEqual(fetched["manifest_sha256"],
                         sealed["manifest_sha256"])

    def test_revise_cancel_and_resume(self):
        source_id = self.enroll_source()
        session, armed, token = self.open_and_arm(source_id)
        created = self.client.request_view(
            token, session["session_id"],
            self.request_body(session["session_id"], source_id,
                              armed["revision"]))
        request_id = created["request"]["request_id"]
        revised = self.client.revise_view(
            token, request_id,
            {"operation_id": new_id(), "session_id": session["session_id"],
             "expected_session_revision": created["session_revision"],
             "request_id": request_id,
             "target": copy.deepcopy(self.valid["target"]),
             "expires_at": FUTURE, "reason": "again",
             "expected_request_revision": 1, "cad_context": None})
        self.assertEqual(revised["request"]["revision"], 2)
        cancelled = self.client.cancel_view(
            token, request_id,
            {"operation_id": new_id(), "session_id": session["session_id"],
             "expected_session_revision": revised["session_revision"],
             "request_id": request_id, "reason": "stop",
             "expected_request_revision": 2})
        self.assertEqual(cancelled["request"]["status"], "CANCELLED")
        resumed = self.client.resume(
            token, session["session_id"],
            {"operation_id": new_id(), "session_id": session["session_id"],
             "expected_session_revision":
                 cancelled["session_revision"]})
        self.assertEqual(resumed["session"]["state"], "DISARMED")

    def test_http_errors_map_to_codes(self):
        source_id = self.enroll_source()
        _session, _armed, token = self.open_and_arm(source_id)
        with self.assertRaises(ControlClientError) as raised:
            self.client.get_session(token, new_id())
        self.assertEqual(raised.exception.status, 404)
        with self.assertRaises(ControlClientError) as raised:
            self.client.get_session("bad-token", new_id())
        self.assertEqual(raised.exception.status, 401)

    def test_plaintext_refuses_non_loopback(self):
        with self.assertRaises(ValueError):
            ControlClient("http://192.168.1.20:8443")


if __name__ == "__main__":
    unittest.main()
