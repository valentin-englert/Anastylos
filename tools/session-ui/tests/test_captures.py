"""Capture loop: phone link, view request against a loopback receiver (real G0 validation), delivery → follow-up job."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

UI_ROOT = Path(__file__).resolve().parents[1]
REPO = UI_ROOT.parents[1]
sys.path.insert(0, str(UI_ROOT))
sys.path.insert(0, str(REPO / "capture-companion" / "receiver"))
sys.path.insert(0, str(REPO / "capture-companion" / "receiver" / "tests"))

import anastylos_capture_receiver as receiver  # noqa: E402
import anastylos_guided_routes as guided  # noqa: E402
import captures  # noqa: E402
from adapter import AdapterError  # noqa: E402
from control_client import ControlClient  # noqa: E402
from test_guided_routes import make_guided_package  # noqa: E402

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
        self.source_token, _ = guided.get_auth(self.state).mint("source", 3600, source_id=self.source_id)
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
        return self.journal.consent(self.source_id, {
            "operation_id": str(uuid.uuid4()), "session_id": session_id, "expected_session_revision": revision,
            "action": "arm", "consent_revision": 1, "max_images": 20, "expires_at": FUTURE, "camera_id": "camera:0",
            "allow_auto_upload": True})

    def _phone_json(self, method, path, body):
        status, payload, _headers = self.client._call(method, path, self.source_token, body=body)
        return status, json.loads(payload.decode("utf-8"))

    def _upload_capture(self, session_id, request_id, request_revision):
        snapshot = self.client.get_session(json.loads((self.root / captures.LINK).read_text())["adapter_token"], session_id)
        request = next(item for item in snapshot["requests"] if item["request_id"] == request_id)
        attempt_id = str(uuid.uuid4())
        status, attempt = self._phone_json("POST", f"/v1/guided/sessions/{session_id}/attempts", {
            "operation_id": str(uuid.uuid4()), "session_id": session_id, "request_id": request_id,
            "request_revision": request_revision, "attempt_id": attempt_id, "consent_revision": 1,
            "board_registration_epoch_id": request["board_registration_epoch_id"],
            "object_pose_epoch_id": request["object_pose_epoch_id"]})
        self.assertEqual(status, 201, attempt)

        package_id, observation_id, image_id = (str(uuid.uuid4()) for _ in range(3))
        data = b"\xff\xd8\xffcapture-loopback-fixture"
        image_sha256 = hashlib.sha256(data).hexdigest()
        package = make_guided_package(package_id, session_id, observation_id, image_id, data)
        package_sha256 = hashlib.sha256(package).hexdigest()
        status, binding = self._phone_json("POST", f"/v1/guided/attempts/{attempt_id}/asset-binding", {
            "operation_id": str(uuid.uuid4()), "attempt_id": attempt_id, "package_id": package_id,
            "observation_id": observation_id, "image_id": image_id, "image_sha256": image_sha256,
            "package_sha256": package_sha256})
        self.assertEqual(status, 200, binding)

        status, payload, _headers = self.client._call("POST", "/v1/packages", self.source_token, raw=package,
            content_type="application/zip", extra_headers={"X-Package-Id": package_id,
                "X-Package-Sha256": package_sha256, "X-Guided-Session-Id": session_id,
                "X-Guided-Request-Id": request_id, "X-Guided-Request-Revision": str(request_revision),
                "X-Guided-Attempt-Id": attempt_id})
        self.assertEqual(status, 201, payload)
        return json.loads(payload.decode("utf-8"))["receipt"], data


class LinkAndRequestTest(LoopbackCase):
    def test_loopback_receipt_bytes_reach_one_follow_up_job(self):
        link = captures.connect_phone(self.root, self.client, mint=self._mint)
        snapshot = self.journal.get_session(link["guided_session_id"], None)
        self._arm_on_phone(link["guided_session_id"], snapshot["session"]["revision"])
        self.assertEqual(captures.link_status(self.root, self.client)["state"], "connected")
        requested = captures.request_capture(self.root, "s1", "back-shell-1", self.client)

        receipt, data = self._upload_capture(link["guided_session_id"], requested["request_id"], 1)
        self.assertEqual((receipt["request_id"], receipt["request_revision"], receipt["status"]),
                         (requested["request_id"], 1, "package_only"))
        started = []

        def start(job_dir, still):
            started.append(still)
            return f"{job_dir.name}-capture-{hashlib.sha256(still['request_id'].encode()).hexdigest()[:24]}"

        status = captures.capture_status(self.root, "s1", "back-shell-1", self.client, start=start)
        self.assertEqual((status["state"], len(started)), ("follow_up_started", 1))
        evidence_path = self.root / "s1" / "drawings" / "captures" / f"{receipt['image_id']}.json"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual((evidence["sha256"], evidence["request_id"], evidence["request_revision"],
                          evidence["guided_session_id"], evidence["requested_for_job"], evidence["identity_proven"]),
                         (hashlib.sha256(data).hexdigest(), requested["request_id"], 1,
                          link["guided_session_id"], "back-shell-1", False))

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

    def test_session_qr_is_a_png_and_needs_a_link(self):
        with self.assertRaises(AdapterError):
            captures.session_qr_png(self.root)
        captures.connect_phone(self.root, self.client, mint=self._mint)
        self.assertEqual(captures.session_qr_png(self.root)[:4], bytes([0x89, 0x50, 0x4E, 0x47]))

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
    def __init__(self, data: bytes, status="qualified", source_id="source-1"):
        self.data = data
        self.digest = hashlib.sha256(data).hexdigest()
        self.status = status
        self.source_id = source_id
        self.session_id = "g-1"
        self.request_revision = 1
        self.request_status = "CAPTURED_LOCAL"
        self.fetches = 0
        self.receipts = [{"session_id": self.session_id, "source_id": self.source_id, "request_id": "r-1",
                          "request_revision": 1, "image_id": "img-1", "image_sha256": self.digest,
                          "status": self.status, "receipt_id": "rc-1", "accepted_at": "2026-11-02T12:00:00Z"}]

    def get_session(self, token, session_id):
        return {"session": {"session_id": self.session_id, "source_id": self.source_id, "revision": 3},
                "requests": [{"request_id": "r-1", "session_id": self.session_id, "source_id": self.source_id,
                               "revision": self.request_revision, "status": self.request_status}],
                "receipts": list(self.receipts)}

    def asset_bytes(self, token, session_id, image_id):
        self.fetches += 1
        return self.data, self.digest


class DeliveryTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.source_id = str(uuid.uuid4())
        self.root, self.job = _workspace(Path(tmp.name), Path(tmp.name) / "data", self.source_id)
        (self.root / captures.LINK).write_text(json.dumps({"state": "connected", "guided_session_id": "g-1", "adapter_token": "t"}))
        spec_bytes = (self.job / "spec.json").read_bytes()
        (self.job / captures.REQUEST).write_text(json.dumps({
            "schema": "anastylos-capture-request@1", "state": "requested", "origin_job": "back-shell-1", "request_id": "r-1",
            "request_revision": 1, "guided_session_id": "g-1", "source_id": self.source_id,
            "origin_spec_sha256": hashlib.sha256(spec_bytes).hexdigest(), "label": "back shell", "reasons": [], "follow_up_job": None,
            "identity_proven": False}))
        self.started = []

    @staticmethod
    def _follow_up_name(job_dir, still):
        return f"{job_dir.name}-capture-{hashlib.sha256(still['request_id'].encode()).hexdigest()[:24]}"

    def _start(self, job_dir, still):
        self.started.append(still)
        return self._follow_up_name(job_dir, still)

    def test_delivered_still_is_stored_with_evidence_and_starts_one_follow_up(self):
        phone = _FakePhone(b"jpeg", source_id=self.source_id)
        status = captures.capture_status(self.root, "s1", "back-shell-1", phone, start=self._start)
        self.assertEqual((status["state"], status["follow_up_job"]), ("follow_up_started", self._follow_up_name(self.job, self.started[0])))
        still = self.root / "s1" / "drawings" / "captures" / "img-1.jpg"
        evidence = json.loads(still.with_suffix(".json").read_text())
        self.assertEqual((still.read_bytes(), evidence["sha256"], evidence["identity_proven"]), (b"jpeg", phone.digest, False))
        self.assertEqual(self.started[0]["sha256"], phone.digest)
        captures.capture_status(self.root, "s1", "back-shell-1", phone, start=self._start)  # later polls change nothing
        self.assertEqual((len(self.started), phone.fetches), (1, 1))

    def test_hash_mismatch_with_the_receipt_is_refused(self):
        phone = _FakePhone(b"jpeg", source_id=self.source_id)
        phone.get_session = lambda token, sid: {"session": {"session_id": "g-1", "source_id": self.source_id},
            "requests": [{"request_id": "r-1", "session_id": "g-1", "source_id": self.source_id,
                          "revision": 1, "status": "CAPTURED_LOCAL"}], "receipts": [
                {"session_id": "g-1", "source_id": self.source_id, "request_id": "r-1", "request_revision": 1,
                 "image_id": "img-1", "image_sha256": "0" * 64, "status": "qualified", "receipt_id": "rc-1"}]}
        status = captures.capture_status(self.root, "s1", "back-shell-1", phone, start=self._start)
        self.assertEqual(status["state"], "failed")
        self.assertEqual(self.started, [])

    def test_crash_after_delivery_starts_the_follow_up_on_the_next_poll(self):
        record = json.loads((self.job / captures.REQUEST).read_text())
        still = {"path": "p", "sha256": "s", "request_id": "r-1", "origin_spec_sha256": record["origin_spec_sha256"]}
        record.update(state="delivered", still=still, follow_up_job=self._follow_up_name(self.job, still))
        (self.job / captures.REQUEST).write_text(json.dumps(record))
        status = captures.capture_status(self.root, "s1", "back-shell-1", None, start=self._start)
        self.assertEqual(status["state"], "follow_up_started")
        self.assertEqual(len(self.started), 1)

    def test_crash_after_job_start_never_starts_a_second_follow_up(self):
        phone = _FakePhone(b"jpeg", source_id=self.source_id)
        write_atomic = captures._write_atomic
        starts = []

        def start_job(spec_path, job_dir):
            starts.append(job_dir)
            (job_dir / "spec.json").write_bytes(spec_path.read_bytes())
            now = captures.time.time()
            (job_dir / "progress.json").write_text(json.dumps({"state": "running", "stage": "starting",
                "pid": os.getpid(), "heartbeat_at": now, "stage_started_at": now}), encoding="utf-8")
            return {"state": "started", "pid": os.getpid()}

        def interrupt_final_state(path, document):
            if path == self.job / captures.REQUEST and document.get("state") == "follow_up_started":
                raise OSError("simulated process interruption after follow-up start")
            write_atomic(path, document)

        with mock.patch.object(captures.job_control, "start", side_effect=start_job) as start_mock:
            with mock.patch.object(captures, "_write_atomic", side_effect=interrupt_final_state):
                with self.assertRaisesRegex(OSError, "simulated process interruption"):
                    captures.capture_status(self.root, "s1", "back-shell-1", phone)

            record = json.loads((self.job / captures.REQUEST).read_text())
            self.assertEqual(record["state"], "follow_up_starting")
            resumed = captures.capture_status(self.root, "s1", "back-shell-1", None)
            self.assertEqual(resumed["state"], "follow_up_started")
            self.assertEqual((start_mock.call_count, len(starts)), (1, 1))

    def test_reconnected_wrong_guided_session_is_refused(self):
        phone = _FakePhone(b"jpeg", source_id=self.source_id)
        link = json.loads((self.root / captures.LINK).read_text())
        link["guided_session_id"] = "g-2"
        (self.root / captures.LINK).write_text(json.dumps(link))
        status = captures.capture_status(self.root, "s1", "back-shell-1", phone, start=self._start)
        self.assertEqual(status["state"], "failed")
        self.assertEqual((phone.fetches, self.started), (0, []))

    def test_revised_request_cannot_accept_a_late_old_revision_receipt(self):
        phone = _FakePhone(b"jpeg", source_id=self.source_id)
        phone.request_revision = 2
        status = captures.capture_status(self.root, "s1", "back-shell-1", phone, start=self._start)
        self.assertEqual(status["state"], "failed")
        self.assertEqual((phone.fetches, self.started), (0, []))

    def test_changed_origin_job_spec_cannot_receive_the_old_request_photo(self):
        phone = _FakePhone(b"jpeg", source_id=self.source_id)
        spec = json.loads((self.job / "spec.json").read_text())
        spec["part"] = "front shell"
        (self.job / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
        status = captures.capture_status(self.root, "s1", "back-shell-1", phone, start=self._start)
        self.assertEqual(status["state"], "failed")
        self.assertEqual((phone.fetches, self.started), (0, []))

    def test_terminal_request_ignores_a_delayed_receipt(self):
        phone = _FakePhone(b"jpeg", source_id=self.source_id)
        phone.request_status = "CANCELLED"
        status = captures.capture_status(self.root, "s1", "back-shell-1", phone, start=self._start)
        self.assertEqual(status["state"], "failed")
        self.assertEqual((phone.fetches, self.started), (0, []))

    def test_receipt_from_another_guided_session_is_refused(self):
        phone = _FakePhone(b"jpeg", source_id=self.source_id)
        phone.receipts[0]["session_id"] = "g-2"
        status = captures.capture_status(self.root, "s1", "back-shell-1", phone, start=self._start)
        self.assertEqual(status["state"], "failed")
        self.assertEqual((phone.fetches, self.started), (0, []))

    def test_duplicate_receipts_choose_the_earliest_once(self):
        phone = _FakePhone(b"jpeg", source_id=self.source_id)
        first = phone.receipts[0]
        later = {**first, "image_id": "img-later", "receipt_id": "rc-later", "accepted_at": "2026-11-02T12:01:00Z"}
        phone.receipts = [later, first]
        status = captures.capture_status(self.root, "s1", "back-shell-1", phone, start=self._start)
        self.assertEqual(status["state"], "follow_up_started")
        self.assertTrue((self.root / "s1" / "drawings" / "captures" / "img-1.jpg").is_file())
        self.assertFalse((self.root / "s1" / "drawings" / "captures" / "img-later.jpg").exists())
        self.assertEqual((phone.fetches, len(self.started)), (1, 1))

    def test_existing_follow_up_without_proven_worker_pauses_for_recovery(self):
        record = json.loads((self.job / captures.REQUEST).read_text())
        still = {"path": "p", "sha256": "s", "request_id": "r-1", "origin_spec_sha256": record["origin_spec_sha256"]}
        follow_name = self._follow_up_name(self.job, still)
        record.update(state="follow_up_starting", still=still, follow_up_job=follow_name)
        (self.job / captures.REQUEST).write_text(json.dumps(record))
        follow = self.job.parent / follow_name
        follow.mkdir()
        spec = json.loads((self.job / "spec.json").read_text(encoding="utf-8"))
        spec["still"] = still
        (follow / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
        now = captures.time.time()
        (follow / "progress.json").write_text(json.dumps({"state": "running", "stage": "starting", "pid": None,
            "heartbeat_at": now, "stage_started_at": now}), encoding="utf-8")

        status = captures.capture_status(self.root, "s1", "back-shell-1", None, start=self._start)
        self.assertEqual(status["state"], "follow_up_pending_recovery")
        self.assertIn("Automatic restart is paused", status["failure"])
        self.assertEqual(self.started, [])


if __name__ == "__main__":
    unittest.main()
