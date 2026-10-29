"""Loopback tests for guided control routes (Wave-1b). Stdlib only.

Run from the receiver directory:

    py -3.12 -m unittest discover -s tests -v

Drives the real HTTP server: QR enroll → source exchange, bootstrap →
open → consent → claim → request → attempts → guided upload → sidecar
→ assets → lease → finalize, plus the auth negative matrix. Reuses
`test_receiver.make_package` for the legacy path.
"""

import copy
import hashlib
import http.client
import io
import json
import sys
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import anastylos_capture_receiver as receiver
import anastylos_guided_auth as auth
import anastylos_guided_routes as guided
from test_receiver import make_package

STAMP = "2026-01-15T10:30:00.000Z"
FUTURE = "2030-01-01T00:00:00Z"
VECTORS = Path(__file__).resolve().parents[2] / "control" / "contracts" / "vectors"


def new_id():
    return str(uuid.uuid4())


def make_guided_package(package_id, session_id, observation_id, image_id,
                        payload):
    session = {
        "schema_version": 1, "session_id": session_id, "name": "guided",
        "created_at": STAMP, "updated_at": STAMP,
        "assembly_memory_session": None, "tray_default": None,
    }
    image = {
        "schema_version": 1, "image_id": image_id,
        "observation_id": observation_id, "role": "front",
        "filename": "front.jpg", "mime": "image/jpeg",
        "byte_length": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "captured_at": STAMP, "crop": None,
    }
    observation = {
        "schema_version": 1, "observation_id": observation_id,
        "session_id": session_id, "component_reference": None,
        "tray_reference": None, "captured_at": STAMP,
        "capture_source": "camera", "review_state": "unreviewed",
        "operator_notes": None, "markings": [],
        "category": {"value": None, "state": "unknown"}, "measurements": [],
        "quantity": {"value": None, "state": "unknown"}, "candidates": [],
        "provenance": {"app": "anastylos-capture", "app_version": "0.1.0",
                       "user_agent": None},
        "export_history": [], "images": [image],
    }
    asset_path = "assets/%s/front.jpg" % observation_id
    manifest = {
        "schema_version": 1, "package_id": package_id, "exported_at": STAMP,
        "generator": {"app": "anastylos-capture", "app_version": "0.1.0"},
        "sessions": [session], "observations": [observation],
        "files": [{
            "path": asset_path, "kind": "original",
            "observation_id": observation_id, "image_id": image_id,
            "sha256": image["sha256"], "bytes": len(payload),
            "mime": "image/jpeg",
        }],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
        zf.writestr(asset_path, payload)
    return buffer.getvalue()


class GuidedCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"
        self.state = receiver.ReceiverState(self.data)
        self.server, self.thread = receiver.serve_in_background(self.state)
        self.addCleanup(self.shutdown_server)
        self.host, self.port = self.server.server_address[:2]
        self.valid = json.loads(
            (VECTORS / "control-request-valid.json").read_text())

    def shutdown_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)
        cached = vars(self.state).get("_guided_journal")
        if cached is not None:
            cached.close()

    def call(self, method, path, token=None, body=None, raw=None,
             extra_headers=None):
        headers = dict(extra_headers or {})
        if token is not None:
            headers["Authorization"] = "Bearer %s" % token
        if body is not None:
            raw = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn = http.client.HTTPConnection(self.host, self.port, timeout=10)
        try:
            conn.request(method, path, body=raw or b"", headers=headers)
            response = conn.getresponse()
            return response.status, response.read(), dict(
                response.getheaders())
        finally:
            conn.close()

    def json_call(self, method, path, token=None, body=None):
        status, data, headers = self.call(method, path, token=token,
                                         body=body)
        return status, json.loads(data.decode("utf-8")), headers

    def enroll_source(self, label="lab-phone"):
        code, _record = self.state.mint_code()
        status, data, _headers = self.call(
            "POST", "/v1/pair/qr",
            body={"pairing_code": code, "device_label": label})
        self.assertEqual(status, 202, data)
        request_id = json.loads(data.decode("utf-8"))["request_id"]
        entry = self.state.approve_pair_request(request_id)
        self.assertIsNotNone(entry)
        status, data, _headers = self.call(
            "GET", "/v1/pair/qr/%s" % request_id)
        legacy = json.loads(data.decode("utf-8"))["token"]
        authn = guided.get_auth(self.state)
        legacy_sha = hashlib.sha256(legacy.encode("utf-8")).hexdigest()
        source_id, _record = authn.enroll_source(label, legacy_sha)
        status, payload, _headers = self.json_call(
            "POST", "/v1/guided/enroll", token=legacy)
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["source_id"], source_id)
        return source_id, payload["token"], legacy

    def bootstrap(self, principal="adapter:test"):
        token, _entry = guided.get_auth(self.state).mint(
            "adapter_bootstrap", 3600, principal=principal)
        return token

    def open_session(self, bootstrap, source_id, max_images=20):
        status, session, _headers = self.json_call(
            "POST", "/v1/guided/sessions", token=bootstrap,
            body={"operation_id": new_id(), "source_id": source_id,
                  "max_images": max_images, "expires_at": FUTURE})
        self.assertEqual(status, 201, session)
        return session

    def consent_arm(self, source_token, session, revision=None,
                    max_images=20, consent_revision=1):
        status, out, _headers = self.json_call(
            "POST", "/v1/guided/sessions/%s/consent" % session["session_id"],
            token=source_token,
            body={"operation_id": new_id(),
                  "session_id": session["session_id"],
                  "expected_session_revision": session["revision"]
                  if revision is None else revision,
                  "action": "arm", "consent_revision": consent_revision,
                  "max_images": max_images, "expires_at": FUTURE,
                  "camera_id": "camera:0", "allow_auto_upload": True})
        self.assertEqual(status, 200, out)
        return out

    def claim(self, bootstrap, grant_id):
        status, out, _headers = self.json_call(
            "POST", "/v1/guided/grants/%s/claim" % grant_id, token=bootstrap,
            body={"operation_id": new_id()})
        self.assertEqual(status, 200, out)
        return out

    def request_view(self, session_token, session_id, source_id, revision):
        valid = self.valid
        status, out, _headers = self.json_call(
            "POST", "/v1/guided/sessions/%s/requests" % session_id,
            token=session_token,
            body={"operation_id": new_id(), "session_id": session_id,
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
                  "cad_context": valid["cad_context"]})
        self.assertEqual(status, 201, out)
        return out


class GuidedFlowTests(GuidedCase):
    def test_full_loop_to_package_only_receipt(self):
        source_id, source_token, _legacy = self.enroll_source()
        bootstrap = self.bootstrap()
        session = self.open_session(bootstrap, source_id)
        pending, payload, _headers = self.json_call(
            "GET", "/v1/guided/sources/%s/pending-sessions" % source_id,
            token=source_token)
        self.assertEqual(pending, 200)
        self.assertEqual(len(payload["sessions"]), 1)
        armed = self.consent_arm(source_token, session)
        claimed = self.claim(bootstrap, armed["grant_id"])
        created = self.request_view(claimed["token"], session["session_id"],
                                    source_id, armed["revision"])
        request = created["request"]
        # Source events + status + changes poll.
        status, ack, _headers = self.json_call(
            "POST", "/v1/guided/sessions/%s/events" % session["session_id"],
            token=source_token,
            body={"event_id": new_id(), "session_id": session["session_id"],
                  "source_id": source_id, "request_id": request["request_id"],
                  "request_revision": 1, "kind": "accepted",
                  "occurred_at": STAMP, "monotonic_ns": "9",
                  "attempt_id": None, "reason": None})
        self.assertEqual(status, 200, ack)
        status, page, _headers = self.json_call(
            "GET", "/v1/guided/sessions/%s/changes?after=0"
            % session["session_id"], token=source_token)
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(page["events"]), 2)
        # Attempt → bind → guided upload → sidecar → asset.
        status, ack, _headers = self.json_call(
            "POST", "/v1/guided/sessions/%s/attempts" % session["session_id"],
            token=source_token,
            body={"operation_id": new_id(),
                  "session_id": session["session_id"],
                  "request_id": request["request_id"], "request_revision": 1,
                  "attempt_id": (attempt_id := new_id()),
                  "consent_revision": 1,
                  "board_registration_epoch_id":
                      self.valid["board_registration_epoch_id"],
                  "object_pose_epoch_id": self.valid["object_pose_epoch_id"]})
        self.assertEqual(status, 201, ack)
        package_id, observation_id, image_id = (new_id(), new_id(), new_id())
        payload = b"\xff\xd8\xff" + bytes(range(256))
        image_sha = hashlib.sha256(payload).hexdigest()
        package_bytes = make_guided_package(
            package_id, session["session_id"], observation_id, image_id,
            payload)
        package_sha = hashlib.sha256(package_bytes).hexdigest()
        status, bound, _headers = self.json_call(
            "POST", "/v1/guided/attempts/%s/asset-binding" % attempt_id,
            token=source_token,
            body={"operation_id": new_id(), "attempt_id": attempt_id,
                  "package_id": package_id, "observation_id": observation_id,
                  "image_id": image_id, "image_sha256": image_sha,
                  "package_sha256": package_sha})
        self.assertEqual(status, 200, bound)
        status, data, _headers = self.call(
            "POST", "/v1/packages", token=source_token, raw=package_bytes,
            extra_headers={
                "Content-Type": "application/zip",
                "X-Package-Id": package_id, "X-Package-Sha256": package_sha,
                "X-Guided-Session-Id": session["session_id"],
                "X-Guided-Request-Id": request["request_id"],
                "X-Guided-Request-Revision": "1",
                "X-Guided-Attempt-Id": attempt_id})
        self.assertEqual(status, 201, data[:400])
        upload = json.loads(data.decode("utf-8"))
        self.assertFalse(upload["duplicate"])
        self.assertEqual(upload["receipt"]["status"], "package_only")
        # Redelivery collapses onto the same receipt.
        status, data, _headers = self.call(
            "POST", "/v1/packages", token=source_token, raw=package_bytes,
            extra_headers={
                "Content-Type": "application/zip",
                "X-Package-Id": package_id, "X-Package-Sha256": package_sha,
                "X-Guided-Session-Id": session["session_id"],
                "X-Guided-Request-Id": request["request_id"],
                "X-Guided-Request-Revision": "1",
                "X-Guided-Attempt-Id": attempt_id})
        self.assertEqual(status, 200)
        redelivery = json.loads(data.decode("utf-8"))
        self.assertTrue(redelivery["duplicate"])
        self.assertEqual(redelivery["receipt"]["receipt_id"],
                         upload["receipt"]["receipt_id"])
        # Sidecar associates; asset re-hashes; lease renews.
        deep = {"stub": True}
        sidecar = {
            "schema": "anastylos-spatial-evidence@1",
            "evidence_id": new_id(), "session_id": session["session_id"],
            "source_id": source_id, "request_id": request["request_id"],
            "request_revision": 1, "attempt_id": attempt_id,
            "created_at": STAMP, "supersedes": None, "asset": deep,
            "camera": deep, "calibration": deep, "image_geometry": deep,
            "capture_time": deep, "pose": deep, "target": deep,
            "quality": deep, "observer_relation": deep,
            "interpretations": [], "provenance": deep, "limitations": [],
        }
        status, receipt, _headers = self.json_call(
            "POST", "/v1/guided/attempts/%s/spatial" % attempt_id,
            token=source_token,
            body={"operation_id": new_id(),
                  "expected_sidecar_revision": 0, "sidecar": sidecar})
        self.assertEqual(status, 200, receipt)
        self.assertEqual(receipt["status"], "package_only")
        status, data, headers = self.call(
            "GET", "/v1/guided/sessions/%s/assets/%s"
            % (session["session_id"], image_id), token=claimed["token"])
        self.assertEqual(status, 200)
        self.assertEqual(data, payload)
        self.assertEqual(headers.get("X-Image-Sha256"), image_sha)
        status, lease, _headers = self.json_call(
            "POST", "/v1/guided/sessions/%s/lease" % session["session_id"],
            token=claimed["token"])
        self.assertEqual(status, 200, lease)
        self.assertLessEqual(lease["lease_remaining_ms"], 30000)

    def test_legacy_upload_unaffected(self):
        _source_id, source_token, legacy = self.enroll_source()
        package_id, _obs, data = make_package()
        conn = http.client.HTTPConnection(self.host, self.port, timeout=10)
        try:
            conn.request(
                "POST", "/v1/packages", body=data,
                headers={"Content-Type": "application/zip",
                         "Authorization": "Bearer %s" % legacy,
                         "X-Package-Id": package_id,
                         "X-Package-Sha256": hashlib.sha256(
                             data).hexdigest()})
            response = conn.getresponse()
            self.assertEqual(response.status, 201)
        finally:
            conn.close()
        # Guided token on the legacy route is rejected.
        status, _data, _headers = self.call(
            "POST", "/v1/packages", token=source_token, raw=data,
            extra_headers={"Content-Type": "application/zip",
                           "X-Package-Id": new_id(),
                           "X-Package-Sha256": hashlib.sha256(
                               data).hexdigest()})
        self.assertEqual(status, 401)


class GuidedNegativeTests(GuidedCase):
    def test_legacy_token_gets_no_control_privilege(self):
        _source_id, _token, legacy = self.enroll_source()
        status, _data, _headers = self.json_call(
            "POST", "/v1/guided/sessions", token=legacy,
            body={"operation_id": new_id(), "source_id": new_id(),
                  "max_images": 5, "expires_at": FUTURE})
        self.assertEqual(status, 401)

    def test_cross_session_access_is_404(self):
        source_id, source_token, _legacy = self.enroll_source()
        bootstrap = self.bootstrap()
        first = self.open_session(bootstrap, source_id)
        second = self.open_session(bootstrap, source_id)
        armed = self.consent_arm(source_token, first)
        claimed = self.claim(bootstrap, armed["grant_id"])
        status, _out, _headers = self.json_call(
            "GET", "/v1/guided/sessions/%s" % second["session_id"],
            token=claimed["token"])
        self.assertEqual(status, 404)
        status, _out, _headers = self.json_call(
            "GET", "/v1/guided/sessions/%s" % new_id(),
            token=claimed["token"])
        self.assertEqual(status, 404)

    def test_url_body_mismatch_and_unknown_keys_rejected(self):
        source_id, source_token, _legacy = self.enroll_source()
        bootstrap = self.bootstrap()
        session = self.open_session(bootstrap, source_id)
        armed = self.consent_arm(source_token, session)
        claimed = self.claim(bootstrap, armed["grant_id"])
        status, _out, _headers = self.json_call(
            "POST", "/v1/guided/sessions/%s/requests" % session["session_id"],
            token=claimed["token"],
            body={"operation_id": new_id(), "session_id": new_id(),
                  "expected_session_revision": 1, "source_id": source_id,
                  "target_label": "x", "board_id": new_id(),
                  "calibration_id": new_id(), "target": {},
                  "quality_profile_id": "guidance-hypothesis-1",
                  "expires_at": FUTURE, "reason": "mismatch",
                  "board_registration_epoch_id": new_id(),
                  "object_pose_epoch_id": new_id(), "cad_context": None})
        self.assertEqual(status, 400)
        status, _out, _headers = self.json_call(
            "GET", "/v1/guided/sessions/%s" % session["session_id"],
            token=claimed["token"],
            body=None)
        self.assertEqual(status, 200)
        status, out, _headers = self.json_call(
            "POST", "/v1/guided/sessions/%s/requests" % session["session_id"],
            token=claimed["token"],
            body={"operation_id": new_id(), "unexpected": True})
        self.assertEqual(status, 400)

    def test_stale_cas_and_idempotency_conflict_over_http(self):
        source_id, source_token, _legacy = self.enroll_source()
        bootstrap = self.bootstrap()
        session = self.open_session(bootstrap, source_id)
        armed = self.consent_arm(source_token, session)
        claimed = self.claim(bootstrap, armed["grant_id"])
        created = self.request_view(claimed["token"], session["session_id"],
                                    source_id, armed["revision"])
        status, _out, _headers = self.json_call(
            "POST", "/v1/guided/requests/%s/cancel"
            % created["request"]["request_id"],
            token=claimed["token"],
            body={"operation_id": new_id(),
                  "session_id": session["session_id"],
                  "expected_session_revision": 1,
                  "request_id": created["request"]["request_id"],
                  "reason": "stale", "expected_request_revision": 1})
        self.assertEqual(status, 409)
        operation_id = new_id()
        body = {"operation_id": operation_id,
                "session_id": session["session_id"],
                "expected_session_revision": created["session_revision"],
                "request_id": created["request"]["request_id"],
                "reason": "first", "expected_request_revision": 1}
        status, _out, _headers = self.json_call(
            "POST", "/v1/guided/requests/%s/cancel"
            % created["request"]["request_id"],
            token=claimed["token"], body=body)
        self.assertEqual(status, 200)
        body["reason"] = "second body, same operation"
        status, out, _headers = self.json_call(
            "POST", "/v1/guided/requests/%s/cancel"
            % created["request"]["request_id"],
            token=claimed["token"], body=body)
        self.assertEqual(status, 409)
        self.assertEqual(out["error"]["code"], "idempotency_conflict")

    def test_revoked_grant_denies_lease_but_bytes_stay(self):
        source_id, source_token, _legacy = self.enroll_source()
        bootstrap = self.bootstrap()
        session = self.open_session(bootstrap, source_id)
        armed = self.consent_arm(source_token, session)
        claimed = self.claim(bootstrap, armed["grant_id"])
        journal = guided.get_journal(self.state)
        journal.revoke_grant(armed["grant_id"])
        status, _out, _headers = self.json_call(
            "POST", "/v1/guided/sessions/%s/lease" % session["session_id"],
            token=claimed["token"])
        self.assertIn(status, (401, 404, 410))
        status, snapshot, _headers = self.json_call(
            "GET", "/v1/guided/sessions/%s" % session["session_id"],
            token=claimed["token"])
        self.assertEqual(status, 200)

    def test_finalize_resume_capture_set_over_http(self):
        source_id, source_token, _legacy = self.enroll_source()
        bootstrap = self.bootstrap()
        session = self.open_session(bootstrap, source_id)
        armed = self.consent_arm(source_token, session)
        claimed = self.claim(bootstrap, armed["grant_id"])
        created = self.request_view(claimed["token"], session["session_id"],
                                    source_id, armed["revision"])
        status, sealed, _headers = self.json_call(
            "POST", "/v1/guided/sessions/%s/finalize" % session["session_id"],
            token=claimed["token"],
            body={"operation_id": new_id(),
                  "session_id": session["session_id"],
                  "expected_session_revision":
                      created["session_revision"]})
        self.assertEqual(status, 200, sealed)
        self.assertEqual(sealed["revision"], 1)
        status, fetched, _headers = self.json_call(
            "GET", "/v1/guided/sessions/%s/capture-set"
            % session["session_id"], token=claimed["token"])
        self.assertEqual(status, 200)
        self.assertEqual(fetched["manifest_sha256"],
                         sealed["manifest_sha256"])
        current = self._revision(session)
        status, resumed, _headers = self.json_call(
            "POST", "/v1/guided/sessions/%s/resume" % session["session_id"],
            token=claimed["token"],
            body={"operation_id": new_id(),
                  "session_id": session["session_id"],
                  "expected_session_revision": current})
        self.assertEqual(status, 200, resumed)
        self.assertEqual(resumed["session"]["state"], "DISARMED")

    def _revision(self, session):
        return guided.get_journal(
            self.state).get_session(session["session_id"])["session"][
                "revision"]


class GuidedCliTests(GuidedCase):
    def test_cli_enroll_bootstrap_and_revoke(self):
        import argparse
        import io as _io
        from contextlib import redirect_stdout

        code, _record = self.state.mint_code()
        status, data, _headers = self.call(
            "POST", "/v1/pair/qr",
            body={"pairing_code": code, "device_label": "cli-phone"})
        request_id = json.loads(data.decode("utf-8"))["request_id"]
        self.state.approve_pair_request(request_id)
        out = _io.StringIO()
        with redirect_stdout(out):
            code_out = guided.cmd_guided_enroll(argparse.Namespace(
                data_dir=self.data, request=request_id[:8], label=None))
        self.assertEqual(code_out, 0)
        source_id = out.getvalue().split("source_id:")[1].split()[0]
        out = _io.StringIO()
        with redirect_stdout(out):
            code_out = guided.cmd_guided_bootstrap(argparse.Namespace(
                data_dir=self.data, principal="adapter:cli", ttl_hours=24))
        self.assertEqual(code_out, 0)
        bootstrap = [line for line in out.getvalue().splitlines()
                     if len(line) == 64][0]
        session = self.open_session(bootstrap, source_id)
        status, payload, _headers = self.json_call(
            "POST", "/v1/guided/enroll", token=self._legacy_token())
        self.assertEqual(status, 201)
        armed = self.consent_arm(payload["token"], session)
        out = _io.StringIO()
        with redirect_stdout(out):
            code_out = guided.cmd_guided_revoke_grant(argparse.Namespace(
                data_dir=self.data, grant=armed["grant_id"]))
        self.assertEqual(code_out, 0)
        out = _io.StringIO()
        with redirect_stdout(out):
            code_out = guided.cmd_guided_status(argparse.Namespace(
                data_dir=self.data, json=True))
        self.assertEqual(code_out, 0)
        status_doc = json.loads(out.getvalue())
        self.assertIn(source_id, status_doc["sources"])

    def _legacy_token(self):
        for entry in self.state.list_pair_requests().values():
            if entry.get("token"):
                return entry["token"]
        raise AssertionError("no approved token")


if __name__ == "__main__":
    unittest.main()
