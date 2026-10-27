"""Tests for the capture workstation receiver. Standard library only.

Run from the receiver directory:

    py -3.12 -m unittest discover -s tests -v

Every transfer test drives a real HTTP server on loopback (the receiver's
explicit development mode) with real package bytes, then asserts what was
— and was not — staged on disk.
"""

import contextlib
import hashlib
import http.client
import io
import json
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anastylos_capture_receiver as receiver
import anastylos_qr

STAMP = "2026-01-15T10:30:00.000Z"

OPENSSL_BIN = None
for _candidate in (
    # Git for Windows first: deterministic and known-good. PATH second.
    # (Some PATH clients, e.g. Strawberry Perl's, cannot mint certs.)
    Path("C:/Program Files/Git/usr/bin/openssl.exe"),
    Path(shutil.which("openssl") or ""),
):
    if _candidate.is_file():
        OPENSSL_BIN = str(_candidate)
        break


def make_package(package_id=None, session_id=None):
    """Minimal valid package: one session, one observation, one photo."""
    package_id = package_id or str(uuid.uuid4())
    session_id = session_id or str(uuid.uuid4())
    observation_id = str(uuid.uuid4())
    image_id = str(uuid.uuid4())
    payload = b"\xff\xd8\xff" + bytes(range(256))
    session = {
        "schema_version": 1,
        "session_id": session_id,
        "name": "Day 1 teardown",
        "created_at": STAMP,
        "updated_at": STAMP,
        "assembly_memory_session": None,
        "tray_default": None,
    }
    image = {
        "schema_version": 1,
        "image_id": image_id,
        "observation_id": observation_id,
        "role": "front",
        "filename": "front.jpg",
        "mime": "image/jpeg",
        "byte_length": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "captured_at": STAMP,
        "crop": None,
    }
    observation = {
        "schema_version": 1,
        "observation_id": observation_id,
        "session_id": session_id,
        "component_reference": None,
        "tray_reference": None,
        "captured_at": STAMP,
        "capture_source": "camera",
        "review_state": "unreviewed",
        "operator_notes": None,
        "markings": [],
        "category": {"value": None, "state": "unknown"},
        "measurements": [],
        "quantity": {"value": None, "state": "unknown"},
        "candidates": [],
        "provenance": {"app": "anastylos-capture", "app_version": "0.1.0", "user_agent": None},
        "export_history": [],
        "images": [image],
    }
    asset_path = "assets/%s/front.jpg" % observation_id
    manifest = {
        "schema_version": 1,
        "package_id": package_id,
        "exported_at": STAMP,
        "generator": {"app": "anastylos-capture", "app_version": "0.1.0"},
        "sessions": [session],
        "observations": [observation],
        "files": [
            {
                "path": asset_path,
                "kind": "original",
                "observation_id": observation_id,
                "image_id": image_id,
                "sha256": image["sha256"],
                "bytes": len(payload),
                "mime": "image/jpeg",
            }
        ],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
        zf.writestr(asset_path, payload)
    return package_id, observation_id, buffer.getvalue()


class ReceiverCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = receiver.ReceiverState(Path(self.tmp.name) / "data")
        self.server, self.thread = receiver.serve_in_background(self.state)
        self.addCleanup(self.shutdown_server)
        self.host, self.port = self.server.server_address[:2]

    def shutdown_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=10)
        try:
            conn.request(method, path, body=body or b"", headers=headers or {})
            response = conn.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            conn.close()

    def pair(self, code):
        return self.request(
            "POST",
            "/v1/pair",
            body=json.dumps({"pairing_code": code}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

    def pair_fresh_device(self):
        code, _record = self.state.mint_code()
        status, body, _headers = self.pair(code)
        self.assertEqual(status, 200, body)
        payload = json.loads(body.decode("utf-8"))
        return payload["token"], payload["workstation_id"]

    def upload(self, package_id, data, token, sha=None):
        sha = sha if sha is not None else hashlib.sha256(data).hexdigest()
        return self.request(
            "POST",
            "/v1/packages",
            body=data,
            headers={
                "Content-Type": "application/zip",
                "Authorization": "Bearer %s" % token,
                "X-Package-Id": package_id,
                "X-Package-Sha256": sha,
            },
        )

    def staged_packages(self):
        return sorted(p.name for p in self.state.packages_dir.iterdir() if not p.name.startswith(".upload-"))


class InfoTests(ReceiverCase):
    def test_info_is_public_but_minimal(self):
        status, body, _headers = self.request("GET", "/v1/info")
        self.assertEqual(status, 200)
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload["service"], "anastylos-capture-receiver")
        self.assertEqual(payload["version"], 1)
        # No identity leak to unauthenticated callers.
        self.assertNotIn("workstation_id", payload)
        self.assertNotIn("token", payload)

    def test_unknown_endpoints_are_404(self):
        status, _body, _headers = self.request("GET", "/v1/nothing-here")
        self.assertEqual(status, 404)


class PairingTests(ReceiverCase):
    def test_pair_redeems_a_single_use_code(self):
        code, _record = self.state.mint_code()
        status, body, _headers = self.pair(code)
        self.assertEqual(status, 200)
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload["workstation_id"], self.state.workstation_id())
        self.assertTrue(len(payload["token"]) >= 32)
        # The issued token authenticates.
        status, body, _headers = self.request(
            "GET", "/v1/status", headers={"Authorization": "Bearer %s" % payload["token"]}
        )
        self.assertEqual(status, 200)
        # The code is consumed: reuse fails.
        status, _body, _headers = self.pair(code)
        self.assertEqual(status, 403)

    def test_pair_accepts_case_and_separator_variants(self):
        code, _record = self.state.mint_code()
        entered = "  " + code.upper().replace(" ", "-") + "  "
        status, _body, _headers = self.pair(entered)
        self.assertEqual(status, 200)

    def test_wrong_code_is_forbidden_without_detail(self):
        self.state.mint_code()
        status, body, _headers = self.pair("wrong code entirely for sure")
        self.assertEqual(status, 403)
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload["error"]["code"], "pairing_failed")

    def test_expired_code_is_rejected(self):
        code, _record = self.state.mint_code(ttl_seconds=-1)
        status, _body, _headers = self.pair(code)
        self.assertEqual(status, 403)

    def test_five_failures_lock_out_pairing(self):
        self.state.mint_code()
        statuses = [self.pair("nope nope nope nope nope nope")[0] for _ in range(6)]
        self.assertEqual(statuses[:4], [403, 403, 403, 403])
        self.assertEqual(statuses[4], 429)
        self.assertEqual(statuses[5], 429)

    def test_new_code_invalidates_the_old_one(self):
        old, _record = self.state.mint_code()
        new, _record = self.state.mint_code()
        self.assertNotEqual(old, new)
        self.assertEqual(self.pair(old)[0], 403)
        self.assertEqual(self.pair(new)[0], 200)


class AuthTests(ReceiverCase):
    def test_status_requires_a_token(self):
        package_id, _obs, data = make_package()
        cases = [
            ("GET", "/v1/status", None),
            ("GET", "/v1/packages", None),
            ("GET", "/v1/packages/%s" % package_id, None),
            ("POST", "/v1/packages", data),
        ]
        for method, path, body in cases:
            with self.subTest(method=method, path=path):
                try:
                    status, _body, headers = self.request(method, path, body=body)
                except (ConnectionAbortedError, ConnectionResetError):
                    # Windows may RST the graceful-401 close while a request
                    # body is still in flight; a dropped connection rejects
                    # the upload just as firmly (GETs carry no body, so only
                    # the POST case may take this path).
                    self.assertEqual(method, "POST")
                    status, headers = None, {}
                if status is not None:
                    self.assertEqual(status, 401)
                    self.assertIn("Bearer", headers.get("WWW-Authenticate", ""))
        self.assertEqual(self.staged_packages(), [])
        self.assertEqual(self.state.read_index(), {})

    def test_wrong_token_is_rejected(self):
        token, _workstation = self.pair_fresh_device()
        status, _body, _headers = self.request(
            "GET", "/v1/status", headers={"Authorization": "Bearer %sX" % token}
        )
        self.assertEqual(status, 401)
        status, _body, _headers = self.request(
            "GET", "/v1/status", headers={"Authorization": "Token %s" % token}
        )
        self.assertEqual(status, 401)

    def test_revoke_tokens_kills_every_device(self):
        token, _workstation = self.pair_fresh_device()
        args = type("Args", (), {"data_dir": self.state.data_dir, "yes": True})()
        self.assertEqual(receiver.cmd_revoke_tokens(args), 0)
        status, _body, _headers = self.request(
            "GET", "/v1/status", headers={"Authorization": "Bearer %s" % token}
        )
        self.assertEqual(status, 401)


class TransferTests(ReceiverCase):
    def test_successful_transfer_stages_validated_evidence(self):
        token, workstation = self.pair_fresh_device()
        package_id, observation_id, data = make_package()
        status, body, _headers = self.upload(package_id, data, token)
        self.assertEqual(status, 201, body)
        payload = json.loads(body.decode("utf-8"))
        self.assertFalse(payload["duplicate"])
        report = payload["report"]
        self.assertEqual(report["status"], "valid")
        self.assertEqual(report["package_id"], package_id)
        self.assertEqual(report["accepted_for_review"], [observation_id])

        target = self.state.packages_dir / package_id
        self.assertTrue((target / "manifest.json").is_file())
        self.assertTrue((target / "report.json").is_file())
        self.assertEqual((target / "package.zip").read_bytes(), data)
        assets = list((target / "assets").rglob("*.jpg"))
        self.assertEqual(len(assets), 1)

        index = self.state.read_index()
        self.assertIn(package_id, index)
        self.assertEqual(index[package_id]["zip_sha256"], hashlib.sha256(data).hexdigest())

        status, body, _headers = self.request(
            "GET", "/v1/status", headers={"Authorization": "Bearer %s" % token}
        )
        status_payload = json.loads(body.decode("utf-8"))
        self.assertEqual(status_payload["workstation_id"], workstation)
        self.assertEqual(status_payload["staged_packages"], 1)

        status, body, _headers = self.request(
            "GET", "/v1/packages", headers={"Authorization": "Bearer %s" % token}
        )
        listed = json.loads(body.decode("utf-8"))["packages"]
        self.assertEqual([entry["package_id"] for entry in listed], [package_id])

        status, body, _headers = self.request(
            "GET", "/v1/packages/%s" % package_id, headers={"Authorization": "Bearer %s" % token}
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body.decode("utf-8"))["report"]["package_id"], package_id)

    def test_duplicate_upload_never_stages_twice(self):
        token, _workstation = self.pair_fresh_device()
        package_id, _obs, data = make_package()
        self.assertEqual(self.upload(package_id, data, token)[0], 201)
        target = self.state.packages_dir / package_id
        first_report = (target / "report.json").read_bytes()

        status, body, _headers = self.upload(package_id, data, token)
        self.assertEqual(status, 200, body)
        payload = json.loads(body.decode("utf-8"))
        self.assertTrue(payload["duplicate"])
        self.assertEqual(payload["report"]["package_id"], package_id)

        self.assertEqual(self.staged_packages(), [package_id])
        self.assertEqual((target / "report.json").read_bytes(), first_report)
        index = self.state.read_index()
        self.assertEqual(index[package_id]["duplicate_deliveries"], 1)

    def test_tampered_bytes_with_original_hash_are_rejected(self):
        token, _workstation = self.pair_fresh_device()
        package_id, _obs, data = make_package()
        tampered = bytearray(data)
        tampered[len(tampered) // 2] ^= 0xFF
        status, body, _headers = self.upload(package_id, bytes(tampered), token, sha=hashlib.sha256(data).hexdigest())
        self.assertEqual(status, 400, body)
        self.assertIn("checksum", body.decode("utf-8"))
        self.assertEqual(self.staged_packages(), [])
        self.assertEqual(self.state.read_index(), {})

    def test_tampered_bytes_with_fresh_hash_fail_validation(self):
        token, _workstation = self.pair_fresh_device()
        package_id, _obs, data = make_package()
        tampered = bytearray(data)
        tampered[len(tampered) // 2] ^= 0xFF
        status, body, _headers = self.upload(package_id, bytes(tampered), token)
        self.assertEqual(status, 422, body)
        report = json.loads(body.decode("utf-8"))["report"]
        self.assertEqual(report["status"], "invalid")
        self.assertEqual(self.staged_packages(), [])
        self.assertEqual(self.state.read_index(), {})

    def test_interrupted_upload_stays_retryable(self):
        token, _workstation = self.pair_fresh_device()
        package_id, _obs, data = make_package()
        sha = hashlib.sha256(data).hexdigest()
        half = data[: len(data) // 2]
        request = (
            "POST /v1/packages HTTP/1.1\r\n"
            "Host: x\r\n"
            "Authorization: Bearer %s\r\n"
            "X-Package-Id: %s\r\n"
            "X-Package-Sha256: %s\r\n"
            "Content-Length: %d\r\n"
            "Connection: close\r\n"
            "\r\n" % (token, package_id, sha, len(data))
        ).encode("utf-8") + half
        sock = socket.create_connection((self.host, self.port), timeout=10)
        try:
            sock.sendall(request)
            sock.shutdown(socket.SHUT_WR)
            response = b""
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                response += chunk
        finally:
            sock.close()
        status_line = response.split(b"\r\n", 1)[0].decode("latin-1")
        self.assertIn("400", status_line, response[:500])
        self.assertEqual(self.staged_packages(), [])
        self.assertEqual(self.state.read_index(), {})
        # The very same bytes succeed on retry.
        status, _body, _headers = self.upload(package_id, data, token)
        self.assertEqual(status, 201)

    def test_unknown_package_is_404(self):
        token, _workstation = self.pair_fresh_device()
        status, _body, _headers = self.request(
            "GET", "/v1/packages/%s" % str(uuid.uuid4()), headers={"Authorization": "Bearer %s" % token}
        )
        self.assertEqual(status, 404)

    def test_path_traversal_is_404(self):
        token, _workstation = self.pair_fresh_device()
        sock = socket.create_connection((self.host, self.port), timeout=10)
        try:
            sock.sendall(
                (
                    "GET /v1/packages/../config.json HTTP/1.1\r\n"
                    "Host: x\r\n"
                    "Authorization: Bearer %s\r\n"
                    "Connection: close\r\n"
                    "\r\n" % token
                ).encode("utf-8")
            )
            response = b""
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                response += chunk
        finally:
            sock.close()
        status_line = response.split(b"\r\n", 1)[0].decode("latin-1")
        self.assertIn("404", status_line, response[:500])

    def test_receiver_never_touches_memory(self):
        token, _workstation = self.pair_fresh_device()
        package_id, _obs, data = make_package()
        self.assertEqual(self.upload(package_id, data, token)[0], 201)
        leftovers = list(self.state.data_dir.rglob("memory.jsonl"))
        self.assertEqual(leftovers, [])


class MetadataTests(ReceiverCase):
    def test_bad_package_id_is_rejected(self):
        token, _workstation = self.pair_fresh_device()
        _pid, _obs, data = make_package()
        for bad_id in ("not-a-uuid", "", "../config.json"):
            with self.subTest(bad_id=bad_id):
                status, _body, _headers = self.upload(bad_id, data, token)
                self.assertEqual(status, 400)
        self.assertEqual(self.staged_packages(), [])

    def test_bad_checksum_header_is_rejected(self):
        token, _workstation = self.pair_fresh_device()
        package_id, _obs, data = make_package()
        for bad_sha in ("zz", "", "00" * 31):
            with self.subTest(bad_sha=bad_sha):
                status, _body, _headers = self.upload(package_id, data, token, sha=bad_sha)
                self.assertEqual(status, 400)
        self.assertEqual(self.staged_packages(), [])

    def test_non_zip_body_is_invalid_not_staged(self):
        token, _workstation = self.pair_fresh_device()
        package_id = str(uuid.uuid4())
        status, body, _headers = self.upload(package_id, b"this is not a zip" * 100, token)
        self.assertEqual(status, 422, body)
        self.assertEqual(self.staged_packages(), [])

    def test_manifest_id_must_match_the_header(self):
        token, _workstation = self.pair_fresh_device()
        _pid, _obs, data = make_package()
        status, _body, _headers = self.upload(str(uuid.uuid4()), data, token)
        self.assertEqual(status, 400)
        self.assertEqual(self.staged_packages(), [])

    def test_empty_body_is_rejected(self):
        token, _workstation = self.pair_fresh_device()
        status, _body, _headers = self.upload(str(uuid.uuid4()), b"", token)
        self.assertEqual(status, 400)

    def test_oversized_upload_is_rejected_before_reading(self):
        token, _workstation = self.pair_fresh_device()
        sock = socket.create_connection((self.host, self.port), timeout=10)
        try:
            sock.sendall(
                (
                    "POST /v1/packages HTTP/1.1\r\n"
                    "Host: x\r\n"
                    "Authorization: Bearer %s\r\n"
                    "X-Package-Id: %s\r\n"
                    "X-Package-Sha256: %s\r\n"
                    "Content-Length: %d\r\n"
                    "Connection: close\r\n"
                    "\r\n" % (token, str(uuid.uuid4()), "ab" * 32, receiver.MAX_UPLOAD_BYTES + 1)
                ).encode("utf-8")
            )
            response = b""
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                response += chunk
        finally:
            sock.close()
        status_line = response.split(b"\r\n", 1)[0].decode("latin-1")
        self.assertIn("413", status_line, response[:500])
        self.assertEqual(self.staged_packages(), [])


class SessionFeedTests(ReceiverCase):
    def stage_two_sessions(self, token):
        session_a = str(uuid.uuid4())
        session_b = str(uuid.uuid4())
        package_a, obs_a, data_a = make_package(session_id=session_a)
        package_b, obs_b, data_b = make_package(session_id=session_b)
        status, body, _headers = self.upload(package_a, data_a, token)
        self.assertEqual(status, 201, body)
        status, body, _headers = self.upload(package_b, data_b, token)
        self.assertEqual(status, 201, body)
        return (package_a, session_a, obs_a), (package_b, session_b, obs_b)

    def get_packages(self, token, query=""):
        headers = {}
        if token is not None:
            headers["Authorization"] = "Bearer %s" % token
        return self.request("GET", "/v1/packages" + query, headers=headers)

    def test_staged_packages_record_session_ids(self):
        token, _workstation = self.pair_fresh_device()
        (package_a, session_a, _), (package_b, session_b, _) = self.stage_two_sessions(token)
        status, body, _headers = self.get_packages(token)
        self.assertEqual(status, 200, body)
        by_id = {p["package_id"]: p for p in json.loads(body.decode("utf-8"))["packages"]}
        self.assertEqual(by_id[package_a]["session_ids"], [session_a])
        self.assertEqual(by_id[package_b]["session_ids"], [session_b])

    def test_filter_returns_only_the_matching_session(self):
        token, _workstation = self.pair_fresh_device()
        (package_a, session_a, _), (package_b, session_b, _) = self.stage_two_sessions(token)
        for session_id, expected in ((session_a, [package_a]), (session_b, [package_b])):
            with self.subTest(session_id=session_id):
                status, body, _headers = self.get_packages(token, "?session_id=" + session_id)
                self.assertEqual(status, 200, body)
                found = [p["package_id"] for p in json.loads(body.decode("utf-8"))["packages"]]
                self.assertEqual(found, expected)

    def test_session_feeds_are_isolated(self):
        token, _workstation = self.pair_fresh_device()
        (_, session_a, obs_a), (_, _session_b, obs_b) = self.stage_two_sessions(token)
        status, body, _headers = self.get_packages(token, "?session_id=" + session_a)
        self.assertEqual(status, 200, body)
        found_obs = [
            o
            for p in json.loads(body.decode("utf-8"))["packages"]
            for o in p["observation_ids"]
        ]
        self.assertIn(obs_a, found_obs)
        self.assertNotIn(obs_b, found_obs)

    def test_unknown_session_yields_an_empty_feed(self):
        token, _workstation = self.pair_fresh_device()
        self.stage_two_sessions(token)
        status, body, _headers = self.get_packages(token, "?session_id=" + str(uuid.uuid4()))
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body.decode("utf-8")), {"packages": []})

    def test_malformed_session_filter_is_rejected(self):
        token, _workstation = self.pair_fresh_device()
        self.stage_two_sessions(token)
        other = str(uuid.uuid4())
        for query in ("?session_id=nope", "?session_id=", "?session_id=%s&session_id=%s" % (other, other)):
            with self.subTest(query=query):
                status, _body, _headers = self.get_packages(token, query)
                self.assertEqual(status, 400)

    def test_session_feed_requires_auth(self):
        token, _workstation = self.pair_fresh_device()
        self.stage_two_sessions(token)
        status, _body, _headers = self.get_packages(None)
        self.assertEqual(status, 401)
        status, _body, _headers = self.get_packages("wrong-token", "?session_id=" + str(uuid.uuid4()))
        self.assertEqual(status, 401)


class CliGuardTests(unittest.TestCase):
    def test_plaintext_refuses_lan_binds(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = type(
                "Args",
                (),
                {
                    "data_dir": Path(tmp),
                    "host": "192.168.1.20",
                    "port": 8443,
                    "cert": None,
                    "key": None,
                    "plaintext_localhost": True,
                },
            )()
            self.assertEqual(receiver.cmd_serve(args), 2)

    def test_tls_needs_cert_and_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = type(
                "Args",
                (),
                {
                    "data_dir": Path(tmp),
                    "host": "192.168.1.20",
                    "port": 8443,
                    "cert": None,
                    "key": None,
                    "plaintext_localhost": False,
                },
            )()
            self.assertEqual(receiver.cmd_serve(args), 2)

    def test_background_servers_bind_loopback_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = receiver.ReceiverState(Path(tmp))
            with self.assertRaises(ValueError):
                receiver.serve_in_background(state, host="0.0.0.0")


class CodeShapeTests(unittest.TestCase):
    def test_wordlist_holds_256_unique_words(self):
        self.assertEqual(len(receiver.WORDLIST), 256)
        self.assertEqual(len(set(receiver.WORDLIST)), 256)
        for word in receiver.WORDLIST:
            self.assertTrue(word.isalpha() and word.islower(), word)

    def test_minted_codes_carry_six_words(self):
        code = receiver.mint_pairing_code()
        words = code.split(" ")
        self.assertEqual(len(words), 6)
        for word in words:
            self.assertIn(word, receiver.WORDLIST)

    def test_code_normalization_is_forgiving(self):
        self.assertEqual(receiver.normalize_pairing_code("  AmberFox-EMBEr "), "amberfox ember")
        self.assertEqual(receiver.normalize_pairing_code("A  B\tC"), "a b c")


class FingerprintTests(unittest.TestCase):
    def test_garbage_file_has_no_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cert.pem"
            path.write_text("not a certificate\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                receiver.cert_fingerprint_sha256(path)

    @unittest.skipUnless(OPENSSL_BIN, "needs openssl to mint a throwaway cert")
    def test_fingerprint_matches_openssl(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            cert = tmpdir / "cert.pem"
            key = tmpdir / "key.pem"
            subprocess.run(
                [
                    OPENSSL_BIN, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", str(key), "-out", str(cert), "-days", "1",
                    "-subj", "/CN=fingerprint-test",
                ],
                check=True,
                capture_output=True,
                timeout=120,
            )
            printed = receiver.cert_fingerprint_sha256(cert)
            asked = subprocess.run(
                [OPENSSL_BIN, "x509", "-in", str(cert), "-noout", "-fingerprint", "-sha256"],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            expected = asked.stdout.strip().split("=", 1)[1].lower()
            self.assertEqual(printed, expected)
            self.assertEqual(len(printed.split(":")), 32)


class TlsServeTests(unittest.TestCase):
    @unittest.skipUnless(OPENSSL_BIN, "needs openssl to mint a throwaway cert")
    def test_tls_serve_answers_verified_clients(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            cert = tmpdir / "cert.pem"
            key = tmpdir / "key.pem"
            subprocess.run(
                [
                    OPENSSL_BIN, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", str(key), "-out", str(cert), "-days", "1",
                    "-subj", "/CN=tls-test",
                    "-addext", "subjectAltName=IP:127.0.0.1",
                ],
                check=True,
                capture_output=True,
                timeout=120,
            )
            state = receiver.ReceiverState(tmpdir / "data")
            server = receiver.ReceiverServer(("127.0.0.1", 0), state)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(str(cert), str(key))
            server.socket = context.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(
                target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
            )
            thread.start()
            try:
                port = server.socket.getsockname()[1]
                client = ssl.create_default_context(cafile=str(cert))
                conn = http.client.HTTPSConnection("127.0.0.1", port, context=client, timeout=10)
                try:
                    conn.connect()
                    peer = conn.sock.getpeercert(binary_form=True)
                    conn.request("GET", "/v1/info")
                    response = conn.getresponse()
                    self.assertEqual(response.status, 200)
                    payload = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(payload["service"], "anastylos-capture-receiver")
                    digest = hashlib.sha256(peer).hexdigest()
                    printed = receiver.cert_fingerprint_sha256(cert).replace(":", "")
                    self.assertEqual(digest, printed)
                finally:
                    conn.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=10)


class QrPayloadTests(unittest.TestCase):
    def kwargs(self, **overrides):
        base = {
            "workstation_id": "9f2b7a01c3d44e5f8a0b1c2d3e4f5061",
            "base_url": "https://192.168.1.20:8443",
            "spki_hex": "5b990e9c6186bf1163ff0c7d2cef2f156e92c57a3b4f3cdd43b98800977b9206",
            "code": "anchor banana cherry desert eagle frost",
            "expires_at": 1793118000.0,
        }
        base.update(overrides)
        return base

    def test_builds_compact_versioned_payload(self):
        text = receiver.build_qr_payload(**self.kwargs())
        parsed = json.loads(text)
        self.assertEqual(
            parsed,
            {
                "p": "anastylos-pair",
                "v": 1,
                "w": "9f2b7a01c3d44e5f8a0b1c2d3e4f5061",
                "u": "https://192.168.1.20:8443",
                "s": "5b990e9c6186bf1163ff0c7d2cef2f156e92c57a3b4f3cdd43b98800977b9206",
                "c": "anchor banana cherry desert eagle frost",
                "e": 1793118000,
            },
        )
        self.assertIsInstance(parsed["e"], int)
        self.assertEqual(json.dumps(parsed, separators=(",", ":")), text)
        self.assertNotIn("token", text)

    def test_rejects_fields_the_phone_would_reject(self):
        bad = [
            {"workstation_id": ""},
            {"workstation_id": "x" * 129},
            {"base_url": "http://192.168.1.20:8443"},
            {"base_url": "https://"},
            {"base_url": "notaurl"},
            {"base_url": "https://h/" + "x" * 120},
            {"spki_hex": "5b99"},
            {"spki_hex": "5B990E9C6186BF1163FF0C7D2CEF2F156E92C57A3B4F3CDD43B98800977B9206"},
            {"spki_hex": "zz990e9c6186bf1163ff0c7d2cef2f156e92c57a3b4f3cdd43b98800977b9206"},
            {"code": ""},
            {"code": "   "},
            {"expires_at": 0},
            {"expires_at": -5},
            {"expires_at": "1793118000"},
        ]
        for override in bad:
            with self.subTest(override=override):
                with self.assertRaises(ValueError):
                    receiver.build_qr_payload(**self.kwargs(**override))

    def test_payload_fits_the_encoder(self):
        text = receiver.build_qr_payload(**self.kwargs())
        code = anastylos_qr.encode_qr(text.encode("ascii"))
        self.assertLessEqual(code.version, 13)

    def test_code_whitespace_is_normalized(self):
        text = receiver.build_qr_payload(**self.kwargs(code="  anchor   banana\tcherry "))
        self.assertEqual(json.loads(text)["c"], "anchor banana cherry")


class DeviceLabelTests(unittest.TestCase):
    def test_accepts_plain_labels(self):
        self.assertEqual(receiver.clean_device_label("Pixel 8"), "Pixel 8")
        self.assertEqual(receiver.clean_device_label("  Galaxy   S24  "), "Galaxy S24")
        self.assertEqual(receiver.clean_device_label("x" * 64), "x" * 64)

    def test_rejects_bad_labels(self):
        for raw in ("", "   ", "x" * 65, "a\x01b", "a\x7fb", None, 123, b"bytes"):
            with self.subTest(raw=raw):
                self.assertIsNone(receiver.clean_device_label(raw))


class SpkiTests(unittest.TestCase):
    def test_garbage_file_has_no_spki(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cert.pem"
            path.write_text("not a certificate\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                receiver.spki_sha256_hex(path)

    def test_key_file_has_no_spki(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "key.pem"
            path.write_text("-----BEGIN PRIVATE KEY-----\nQUJD\n-----END PRIVATE KEY-----\n",
                            encoding="utf-8")
            with self.assertRaises(ValueError):
                receiver.spki_sha256_hex(path)

    def test_truncated_der_is_rejected(self):
        import base64

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cert.pem"
            body = base64.b64encode(b"\x30\x82\x01\x00\x02\x01").decode("ascii")
            path.write_text(f"-----BEGIN CERTIFICATE-----\n{body}\n-----END CERTIFICATE-----\n",
                            encoding="utf-8")
            with self.assertRaises(ValueError):
                receiver.spki_sha256_hex(path)

    @unittest.skipUnless(OPENSSL_BIN, "needs openssl to mint a throwaway cert")
    def test_spki_matches_openssl(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            cert = tmpdir / "cert.pem"
            key = tmpdir / "key.pem"
            subprocess.run(
                [
                    OPENSSL_BIN, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", str(key), "-out", str(cert), "-days", "1",
                    "-subj", "/CN=spki-test",
                ],
                check=True,
                capture_output=True,
                timeout=120,
            )
            asked = subprocess.run(
                [OPENSSL_BIN, "x509", "-in", str(cert), "-noout", "-pubkey"],
                check=True,
                capture_output=True,
                timeout=60,
            )
            pub = tmpdir / "pub.pem"
            pub.write_bytes(asked.stdout)
            der = subprocess.run(
                [OPENSSL_BIN, "pkey", "-pubin", "-in", str(pub), "-outform", "DER"],
                check=True,
                capture_output=True,
                timeout=60,
            )
            self.assertEqual(receiver.spki_sha256_hex(cert), hashlib.sha256(der.stdout).hexdigest())


class QrPairingTests(ReceiverCase):
    def qr_pair(self, code, label="Pixel 8"):
        return self.request(
            "POST",
            "/v1/pair/qr",
            body=json.dumps({"pairing_code": code, "device_label": label}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

    def poll(self, request_id):
        return self.request("GET", "/v1/pair/qr/" + request_id)

    def test_qr_pair_needs_approval_before_token(self):
        code, _record = self.state.mint_code()
        status, body, _headers = self.qr_pair(code)
        self.assertEqual(status, 202, body)
        pending = json.loads(body.decode("utf-8"))
        self.assertEqual(pending["status"], "pending")
        self.assertRegex(pending["request_id"], r"^[0-9a-f]{32}$")
        self.assertNotIn("token", pending)

        status, body, _headers = self.poll(pending["request_id"])
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body.decode("utf-8")), {"status": "pending"})

        entry = self.state.approve_pair_request(pending["request_id"])
        self.assertIsNotNone(entry)
        status, body, _headers = self.poll(pending["request_id"])
        self.assertEqual(status, 200)
        approved = json.loads(body.decode("utf-8"))
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approved["workstation_id"], self.state.workstation_id())
        self.assertTrue(len(approved["token"]) >= 32)

        status, body, _headers = self.request(
            "GET", "/v1/status", headers={"Authorization": "Bearer " + approved["token"]}
        )
        self.assertEqual(status, 200, body)

    def test_wrong_code_fails_generically_and_creates_nothing(self):
        self.state.mint_code()
        status, body, _headers = self.qr_pair("amber amber amber amber amber amber")
        self.assertEqual(status, 403)
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload["error"], {"code": "pairing_failed", "message": "Pairing failed."})
        self.assertEqual(self.state.list_pair_requests(), {})

    def test_redeem_is_single_use_across_both_endpoints(self):
        code, _record = self.state.mint_code()
        status, _body, _headers = self.qr_pair(code)
        self.assertEqual(status, 202)
        status, _body, _headers = self.qr_pair(code)
        self.assertEqual(status, 403)
        status, _body, _headers = self.pair(code)
        self.assertEqual(status, 403)

    def test_expired_code_is_rejected(self):
        record = {
            "code_sha256": receiver.sha256_hex(b"anchor banana cherry desert eagle frost"),
            "created_at": receiver.utcnow_iso(),
            "expires_at": time.time() - 5,
            "attempts": [],
            "locked_until": 0.0,
            "consumed": False,
        }
        self.state._write_json("pairing.json", record)
        status, _body, _headers = self.qr_pair("anchor banana cherry desert eagle frost")
        self.assertEqual(status, 403)

    def test_bad_bodies_are_400(self):
        self.state.mint_code()
        status, _body, _headers = self.request(
            "POST", "/v1/pair/qr", body=b"{nope", headers={"Content-Type": "application/json"}
        )
        self.assertEqual(status, 400)
        for body in (
            {},
            {"device_label": "Pixel 8"},
            {"pairing_code": "x"},
            {"pairing_code": 123, "device_label": "Pixel 8"},
        ):
            with self.subTest(body=body):
                status, _, _ = self.request(
                    "POST", "/v1/pair/qr", body=json.dumps(body).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(status, 400)

    def test_bad_labels_are_400(self):
        code, _record = self.state.mint_code()
        for label in ("", "   ", "x" * 65, "a\x01b", "a\x7fb", None, 42):
            with self.subTest(label=label):
                status, _, _ = self.qr_pair(code, label)
                self.assertEqual(status, 400)
        # None of the 400s consumed the code.
        status, _, _ = self.qr_pair(code, "Pixel 8")
        self.assertEqual(status, 202)

    def test_unknown_and_malformed_poll_ids_share_one_404(self):
        bodies = set()
        for request_id in ("0" * 32, "xyz", "", "g" * 32, "ab12", "A" * 32):
            with self.subTest(request_id=request_id):
                status, body, _headers = self.poll(request_id)
                self.assertEqual(status, 404)
                bodies.add(body)
        self.assertEqual(len(bodies), 1)
        payload = json.loads(next(iter(bodies)).decode("utf-8"))
        self.assertEqual(payload["error"]["code"], "not_found")

    def test_rejected_request_polls_like_unknown(self):
        code, _record = self.state.mint_code()
        status, body, _headers = self.qr_pair(code)
        request_id = json.loads(body.decode("utf-8"))["request_id"]
        self.assertTrue(self.state.reject_pair_request(request_id))
        status, body, _headers = self.poll(request_id)
        self.assertEqual(status, 404)
        self.assertFalse(self.state.reject_pair_request(request_id))

    def test_approve_is_idempotent_and_single_token(self):
        code, _record = self.state.mint_code()
        status, body, _headers = self.qr_pair(code)
        request_id = json.loads(body.decode("utf-8"))["request_id"]
        first = self.state.approve_pair_request(request_id)
        second = self.state.approve_pair_request(request_id)
        self.assertEqual(first["token"], second["token"])
        tokens = self.state._read_json("tokens.json") or {}
        self.assertEqual(len(tokens), 1)
        self.assertIsNone(self.state.approve_pair_request("f" * 32))

    def test_approved_poll_is_repeatable(self):
        code, _record = self.state.mint_code()
        status, body, _headers = self.qr_pair(code)
        request_id = json.loads(body.decode("utf-8"))["request_id"]
        self.state.approve_pair_request(request_id)
        _, first, _ = self.poll(request_id)
        _, second, _ = self.poll(request_id)
        self.assertEqual(first, second)

    def test_expired_requests_are_swept(self):
        self.state._write_json("pairing-requests.json", {"requests": {
            "aa" * 16: {
                "device_label": "old", "created_at": receiver.utcnow_iso(),
                "expires_at": time.time() - 5, "status": "pending", "token": None,
            }
        }})
        self.assertIsNone(self.state.get_pair_request("aa" * 16))
        self.assertIsNone(self.state.approve_pair_request("aa" * 16))
        self.assertEqual(self.state.list_pair_requests(), {})

    def test_qr_attempts_share_the_lockout(self):
        self.state.mint_code()
        last_headers: dict = {}
        for _ in range(4):
            status, _, _ = self.qr_pair("amber amber amber amber amber amber")
            self.assertEqual(status, 403)
        status, _, last_headers = self.qr_pair("amber amber amber amber amber amber")
        self.assertEqual(status, 429)
        self.assertIn("Retry-After", last_headers)
        code, _record = self.state.mint_code()
        # Minting resets attempts, but the fresh code still works via QR.
        status, _, _ = self.qr_pair(code)
        self.assertEqual(status, 202)


class QrCliTests(unittest.TestCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = receiver.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_pair_status_empty_then_pending_then_approved(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = str(Path(tmp) / "data")
            code, out, _ = self.run_main(["--data-dir", data, "pair-status"])
            self.assertEqual(code, 0)
            self.assertIn("0 (0 pending)", out)
            state = receiver.ReceiverState(Path(data))
            pairing, _ = state.mint_code()
            ok, _, _, summary = state.redeem_code_request(pairing, "Pixel 8")
            self.assertTrue(ok)
            code, out, _ = self.run_main(["--data-dir", data, "pair-status"])
            self.assertEqual(code, 0)
            self.assertIn("1 (1 pending)", out)
            self.assertIn("Pixel 8", out)
            code, out, _ = self.run_main(
                ["--data-dir", data, "pair-approve", summary["request_id"][:8]]
            )
            self.assertEqual(code, 0, out)
            self.assertIn("approved Pixel 8", out)
            code, out, _ = self.run_main(["--data-dir", data, "pair-status"])
            self.assertIn("1 (0 pending)", out)
            self.assertIn("approved", out)

    def test_pair_status_json_redacts_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            state = receiver.ReceiverState(data)
            pairing, _ = state.mint_code()
            ok, _, _, summary = state.redeem_code_request(pairing, "Pixel 8")
            self.assertTrue(ok)
            entry = state.approve_pair_request(summary["request_id"])
            raw_token = entry["token"]
            code, out, _ = self.run_main(["--data-dir", str(data), "pair-status", "--json"])
            self.assertEqual(code, 0)
            self.assertNotIn(raw_token, out)
            parsed = json.loads(out)
            only = parsed["requests"][summary["request_id"]]
            self.assertEqual(only["token"], True)

    def test_approve_and_reject_unknown_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = str(Path(tmp) / "data")
            code, _, err = self.run_main(["--data-dir", data, "pair-approve", "deadbeef"])
            self.assertEqual(code, 1)
            self.assertIn("no unexpired pairing request matches", err)
            code, _, err = self.run_main(["--data-dir", data, "pair-reject", "deadbeef"])
            self.assertEqual(code, 1)
            self.assertIn("no unexpired pairing request matches", err)

    def test_ambiguous_prefix_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            state = receiver.ReceiverState(data)
            live = time.time() + 300
            state._write_json("pairing-requests.json", {"requests": {
                "ab111111111111111111111111111111": {
                    "device_label": "one", "created_at": receiver.utcnow_iso(),
                    "expires_at": live, "status": "pending", "token": None,
                },
                "ab222222222222222222222222222222": {
                    "device_label": "two", "created_at": receiver.utcnow_iso(),
                    "expires_at": live, "status": "pending", "token": None,
                },
            }})
            code, _, err = self.run_main(["--data-dir", str(data), "pair-approve", "ab"])
            self.assertEqual(code, 1)
            self.assertIn("ambiguous", err)

    def test_reject_removes_the_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            state = receiver.ReceiverState(data)
            pairing, _ = state.mint_code()
            ok, _, _, summary = state.redeem_code_request(pairing, "Pixel 8")
            self.assertTrue(ok)
            code, out, _ = self.run_main(
                ["--data-dir", str(data), "pair-reject", summary["request_id"]]
            )
            self.assertEqual(code, 0)
            self.assertIn("rejected", out)
            self.assertEqual(state.list_pair_requests(), {})

    def test_pair_code_qr_needs_host_and_cert(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = str(Path(tmp) / "data")
            code, _, err = self.run_main(["--data-dir", data, "pair-code", "--qr"])
            self.assertEqual(code, 2)
            self.assertIn("--qr needs --host and --cert", err)
            code, _, err = self.run_main(
                ["--data-dir", data, "pair-code", "--qr", "--host", "192.168.1.20",
                 "--cert", str(Path(tmp) / "missing.pem")]
            )
            self.assertEqual(code, 2)
            code, _, err = self.run_main(
                ["--data-dir", data, "pair-code", "--qr", "--host", "192.168.1.20",
                 "--port", "99999", "--cert", str(Path(tmp) / "missing.pem")]
            )
            self.assertEqual(code, 2)
            self.assertIn("--port must be within 1..65535", err)

    @unittest.skipUnless(OPENSSL_BIN, "needs openssl to mint a throwaway cert")
    def test_pair_code_qr_prints_and_writes_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            data = tmpdir / "data"
            cert = tmpdir / "cert.pem"
            key = tmpdir / "key.pem"
            subprocess.run(
                [
                    OPENSSL_BIN, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", str(key), "-out", str(cert), "-days", "1",
                    "-subj", "/CN=qr-cli-test",
                ],
                check=True,
                capture_output=True,
                timeout=120,
            )
            png = tmpdir / "pair.png"
            code, out, _ = self.run_main(
                ["--data-dir", str(data), "pair-code", "--qr", "--host", "192.168.1.20",
                 "--cert", str(cert), "--qr-out", str(png)]
            )
            self.assertEqual(code, 0, out)
            self.assertIn('"p":"anastylos-pair"', out)
            self.assertIn("endpoint: https://192.168.1.20:8443", out)
            self.assertIn("spki pin: ", out)
            self.assertIn("fallback:", out)
            self.assertTrue(png.is_file())
            self.assertTrue(png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
            payload_line = next(line for line in out.splitlines() if line.startswith("payload: "))
            payload = json.loads(payload_line[len("payload: "):])
            self.assertEqual(payload["u"], "https://192.168.1.20:8443")
            self.assertLessEqual(len(payload_line), 400)


if __name__ == "__main__":
    unittest.main()
