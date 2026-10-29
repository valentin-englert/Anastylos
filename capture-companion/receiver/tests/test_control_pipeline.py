"""Pipeline tests: consent/grants/attempts/sidecar/receipts (Wave-1b).

Run from the receiver directory:

    py -3.12 -m unittest discover -s tests -v

Drives the remaining `control-transitions.json` cases through
`anastylos_control_journal`: revoke-then-qualify, session-cancel,
cancel/expire races (server halves; the shutter latch is phone-side),
superseded bytes, sidecar/package reorder, duplicate upload, consent
cap, pause/resume, leases, status, validator reports (synthetic), and
the Wave-1a → Wave-1b store migration.
"""

import copy
import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anastylos_control_journal as journal

VECTORS = Path(__file__).resolve().parents[2] / "control" / "contracts" / "vectors"

NOW = datetime(2026, 10, 29, 12, 1, 0, tzinfo=timezone.utc)
NOW_ISO = "2026-10-29T12:01:00Z"
OPEN_EXPIRES = "2026-10-29T12:31:00Z"
CONSENT_EXPIRES = "2026-10-29T12:21:00Z"

SOURCE = "00000000-0000-4000-8000-000000000003"
PRINCIPAL = "adapter:test-host"


def load_vector(name):
    return json.loads((VECTORS / name).read_text(encoding="utf-8"))


def new_id():
    return str(uuid.uuid4())


def hex64(seed):
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


class PipelineCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "control.db"
        self.journal = journal.ControlJournal(
            self.db, contract_sha="ec313f7ftest", now_fn=lambda: NOW
        )
        self.addCleanup(self.journal.close)
        self.valid = load_vector("control-request-valid.json")

    def open_session(self, max_images=20):
        return self.journal.open_session(
            PRINCIPAL,
            {"operation_id": new_id(), "source_id": SOURCE,
             "max_images": max_images, "expires_at": OPEN_EXPIRES},
            SOURCE)

    def arm(self, session, revision=None, max_images=20, consent_revision=1):
        body = {"operation_id": new_id(), "session_id": session["session_id"],
                "expected_session_revision": session["revision"]
                if revision is None else revision,
                "action": "arm", "consent_revision": consent_revision,
                "max_images": max_images, "expires_at": CONSENT_EXPIRES,
                "camera_id": "camera:0", "allow_auto_upload": True}
        return self.journal.consent(SOURCE, body)

    def make_request(self, session_id, revision):
        valid = self.valid
        return self.journal.request_view(
            PRINCIPAL, SOURCE,
            {"operation_id": new_id(), "session_id": session_id,
             "expected_session_revision": revision, "source_id": SOURCE,
             "target_label": valid["target_label"], "board_id": valid["board_id"],
             "calibration_id": valid["calibration_id"],
             "target": copy.deepcopy(valid["target"]),
             "quality_profile_id": valid["quality_profile_id"],
             "expires_at": valid["expires_at"], "reason": valid["reason"],
             "board_registration_epoch_id": valid["board_registration_epoch_id"],
             "object_pose_epoch_id": valid["object_pose_epoch_id"],
             "cad_context": valid["cad_context"]})

    def reserve(self, session_id, request_id, request_revision,
                consent_revision=1, attempt_id=None):
        valid = self.valid
        return self.journal.register_attempt(
            SOURCE,
            {"operation_id": new_id(), "session_id": session_id,
             "request_id": request_id, "request_revision": request_revision,
             "attempt_id": attempt_id or new_id(),
             "consent_revision": consent_revision,
             "board_registration_epoch_id": valid["board_registration_epoch_id"],
             "object_pose_epoch_id": valid["object_pose_epoch_id"]})

    def bind(self, attempt_id):
        return self.journal.bind_asset(
            SOURCE,
            {"operation_id": new_id(), "attempt_id": attempt_id,
             "package_id": new_id(), "observation_id": new_id(),
             "image_id": new_id(), "image_sha256": hex64("img" + attempt_id),
             "package_sha256": hex64("pkg" + attempt_id)})

    def sidecar_body(self, session_id, request_id, request_revision,
                     attempt_id, evidence_id=None, supersedes=None,
                     expected=0, package_id=None):
        deep = {"stub": True}
        sidecar = {
            "schema": "anastylos-spatial-evidence@1",
            "evidence_id": evidence_id or new_id(), "session_id": session_id,
            "source_id": SOURCE, "request_id": request_id,
            "request_revision": request_revision, "attempt_id": attempt_id,
            "created_at": NOW_ISO, "supersedes": supersedes,
            "asset": {"package_id": package_id} if package_id else deep,
            "camera": deep, "calibration": deep, "image_geometry": deep,
            "capture_time": deep, "pose": deep, "target": deep,
            "quality": deep, "observer_relation": deep,
            "interpretations": [], "provenance": deep, "limitations": [],
        }
        return {"operation_id": new_id(),
                "expected_sidecar_revision": expected, "sidecar": sidecar}

    def full_flow(self, max_images=20):
        """Open → request → arm → reserve → bind → package_only receipt."""
        session = self.open_session(max_images=max_images)
        created = self.make_request(session["session_id"], 1)
        armed = self.arm({"session_id": session["session_id"],
                          "revision": created["session_revision"]},
                         max_images=max_images)
        ack = self.reserve(session["session_id"],
                           created["request"]["request_id"], 1)
        bound = self.bind(ack["attempt_id"])
        attempt = self.journal._attempt_row(ack["attempt_id"])
        receipt = self.journal.accept_package(
            SOURCE, ack["attempt_id"],
            {"package_id": attempt[9], "package_sha256": attempt[13],
             "observation_id": attempt[10], "image_id": attempt[11],
             "image_sha256": attempt[12]})
        return {"session": armed, "request": created["request"],
                "ack": bound, "receipt": receipt}


class ConsentGrantTests(PipelineCase):
    def test_arm_mints_grant_with_effective_minimum(self):
        session = self.open_session(max_images=20)
        armed = self.arm(session, max_images=5)
        self.assertEqual(armed["state"], "ACTIVE")
        self.assertIsNotNone(armed["grant_id"])
        self.assertEqual(armed["consent_revision"], 1)
        ack_session = self.journal._session_row(session["session_id"])
        self.assertEqual(ack_session[14], 5)

    def test_adapter_ceiling_caps_grant(self):
        session = self.open_session(max_images=3)
        armed = self.arm(session, max_images=20)
        row = self.journal._session_row(session["session_id"])
        self.assertEqual(row[14], 3)

    def test_reconsent_increments_without_resetting_counter(self):
        flow = self.full_flow()
        session_id = flow["session"]["session_id"]
        before = self.journal._session_row(session_id)
        rearmed = self.arm({"session_id": session_id,
                            "revision": before[3]},
                           max_images=20, consent_revision=2)
        self.assertEqual(rearmed["consent_revision"], 2)
        after = self.journal._session_row(session_id)
        self.assertEqual(after[15], 1)

    def test_disarm_cancels_session_and_queued(self):
        session = self.open_session()
        created = self.make_request(session["session_id"], 1)
        out = self.journal.consent(
            SOURCE,
            {"operation_id": new_id(), "session_id": session["session_id"],
             "expected_session_revision": created["session_revision"],
             "action": "disarm", "consent_revision": 1, "max_images": 20,
             "expires_at": CONSENT_EXPIRES, "camera_id": "camera:0",
             "allow_auto_upload": True})
        self.assertEqual(out["state"], "CANCELLED")
        snapshot = self.journal.get_session(session["session_id"])
        self.assertEqual(snapshot["requests"][0]["status"], "CANCELLED")

    def test_no_reservation_after_cancel(self):
        session = self.open_session()
        created = self.make_request(session["session_id"], 1)
        self.journal.consent(
            SOURCE,
            {"operation_id": new_id(), "session_id": session["session_id"],
             "expected_session_revision": created["session_revision"],
             "action": "disarm", "consent_revision": 1, "max_images": 20,
             "expires_at": CONSENT_EXPIRES, "camera_id": "camera:0",
             "allow_auto_upload": True})
        with self.assertRaises(journal.ControlError) as raised:
            self.reserve(session["session_id"],
                         created["request"]["request_id"], 1)
        self.assertEqual(raised.exception.code, "unarmed")

    def test_claim_requires_creating_principal(self):
        flow = self.full_flow()
        grant_id = flow["session"]["grant_id"]
        claimed = self.journal.claim_grant(PRINCIPAL, grant_id, new_id())
        self.assertEqual(claimed["grant"]["grant_id"], grant_id)
        with self.assertRaises(journal.ControlError) as raised:
            self.journal.claim_grant("adapter:other", grant_id, new_id())
        self.assertEqual(raised.exception.code, "unauthorized")

    def test_revoke_blocks_new_auth_but_keeps_bytes(self):
        flow = self.full_flow()
        session_id = flow["session"]["session_id"]
        grant = self.journal.revoke_grant(flow["session"]["grant_id"])
        self.assertTrue(grant["revoked"])
        with self.assertRaises(journal.ControlError) as raised:
            self.reserve(session_id, flow["request"]["request_id"], 1)
        self.assertEqual(raised.exception.code, "unauthorized")
        # In-flight package_only receipt stays readable, never qualifies.
        stayed = self.journal.maybe_qualify(flow["receipt"]["receipt_id"])
        self.assertEqual(stayed["status"], "package_only")


class AttemptCapTests(PipelineCase):
    def test_every_reservation_counts_and_21st_denied(self):
        flow = self.full_flow(max_images=20)
        session_id = flow["session"]["session_id"]
        for _ in range(19):
            self.reserve(session_id, flow["request"]["request_id"], 1)
        with self.assertRaises(journal.ControlError) as raised:
            self.reserve(session_id, flow["request"]["request_id"], 1)
        self.assertEqual(raised.exception.code, "invalid_input")

    def test_stale_consent_and_revision_rejected(self):
        flow = self.full_flow()
        session_id = flow["session"]["session_id"]
        with self.assertRaises(journal.ControlError) as raised:
            self.reserve(session_id, flow["request"]["request_id"], 1,
                         consent_revision=99)
        self.assertEqual(raised.exception.code, "revision_conflict")
        with self.assertRaises(journal.ControlError) as raised:
            self.reserve(session_id, flow["request"]["request_id"], 99)
        self.assertEqual(raised.exception.code, "revision_conflict")

    def test_unknown_attempt_binding_returns_409_shape(self):
        with self.assertRaises(journal.ControlError) as raised:
            self.journal.bind_asset(
                SOURCE,
                {"operation_id": new_id(), "attempt_id": new_id(),
                 "package_id": new_id(), "observation_id": new_id(),
                 "image_id": new_id(), "image_sha256": hex64("x"),
                 "package_sha256": hex64("y")})
        self.assertEqual(raised.exception.code, "revision_conflict")

    def test_binding_is_immutable(self):
        flow = self.full_flow()
        attempt_id = flow["ack"]["attempt_id"]
        replay = self.journal.bind_asset(
            SOURCE,
            {"operation_id": new_id(), "attempt_id": attempt_id,
             "package_id": self.journal._attempt_row(attempt_id)[9],
             "observation_id": self.journal._attempt_row(attempt_id)[10],
             "image_id": self.journal._attempt_row(attempt_id)[11],
             "image_sha256": self.journal._attempt_row(attempt_id)[12],
             "package_sha256": self.journal._attempt_row(attempt_id)[13]})
        self.assertEqual(replay["status"], "asset_bound")
        with self.assertRaises(journal.ControlError) as raised:
            self.journal.bind_asset(
                SOURCE,
                {"operation_id": new_id(), "attempt_id": attempt_id,
                 "package_id": new_id(), "observation_id": new_id(),
                 "image_id": new_id(), "image_sha256": hex64("other"),
                 "package_sha256": hex64("other2")})
        self.assertEqual(raised.exception.code, "idempotency_conflict")

    def test_pause_retains_queue_and_bars_reservation(self):
        flow = self.full_flow()
        session_id = flow["session"]["session_id"]
        self.journal.record_event(
            SOURCE,
            {"event_id": new_id(), "session_id": session_id,
             "source_id": SOURCE, "request_id": None, "request_revision": None,
             "kind": "paused", "occurred_at": NOW_ISO,
             "monotonic_ns": "42", "attempt_id": None, "reason": None})
        snapshot = self.journal.get_session(session_id)
        self.assertEqual(snapshot["session"]["state"], "PAUSED")
        self.assertEqual(len(snapshot["requests"]), 1)
        with self.assertRaises(journal.ControlError) as raised:
            self.reserve(session_id, flow["request"]["request_id"], 1)
        self.assertEqual(raised.exception.code, "unarmed")


class ReceiptRaceTests(PipelineCase):
    def _package_of(self, attempt_id):
        attempt = self.journal._attempt_row(attempt_id)
        return {"package_id": attempt[9], "package_sha256": attempt[13],
                "observation_id": attempt[10], "image_id": attempt[11],
                "image_sha256": attempt[12]}

    def test_duplicate_upload_collapses(self):
        flow = self.full_flow()
        again = self.journal.accept_package(
            SOURCE, flow["ack"]["attempt_id"],
            self._package_of(flow["ack"]["attempt_id"]))
        self.assertEqual(again["receipt_id"], flow["receipt"]["receipt_id"])
        self.assertEqual(again["status"], "package_only")

    def test_binding_mismatch_conflicts(self):
        flow = self.full_flow()
        package = self._package_of(flow["ack"]["attempt_id"])
        package["image_sha256"] = hex64("tampered")
        with self.assertRaises(journal.ControlError) as raised:
            self.journal.accept_package(
                SOURCE, flow["ack"]["attempt_id"], package)
        self.assertEqual(raised.exception.code, "idempotency_conflict")

    def test_cancel_before_bytes_is_late(self):
        # Reserve-then-cancel ordering: bind first, cancel wins, bytes late.
        session2 = self.open_session()
        created = self.make_request(session2["session_id"], 1)
        armed = self.arm({"session_id": session2["session_id"],
                          "revision": created["session_revision"]})
        ack2 = self.reserve(session2["session_id"],
                            created["request"]["request_id"], 1)
        self.bind(ack2["attempt_id"])
        snap = self.journal.get_session(session2["session_id"])
        self.journal.cancel_view(
            PRINCIPAL, SOURCE,
            {"operation_id": new_id(), "session_id": session2["session_id"],
             "expected_session_revision": snap["session"]["revision"],
             "request_id": created["request"]["request_id"],
             "reason": "cancel wins", "expected_request_revision": 1})
        late = self.journal.accept_package(
            SOURCE, ack2["attempt_id"], self._package_of(ack2["attempt_id"]))
        self.assertEqual(late["status"], "late_after_cancel")

    def test_superseded_bytes_cannot_satisfy_new_revision(self):
        session = self.open_session()
        created = self.make_request(session["session_id"], 1)
        armed = self.arm({"session_id": session["session_id"],
                          "revision": created["session_revision"]})
        ack = self.reserve(session["session_id"],
                           created["request"]["request_id"], 1)
        self.bind(ack["attempt_id"])
        snap = self.journal.get_session(session["session_id"])
        self.journal.revise_view(
            PRINCIPAL, SOURCE,
            {"operation_id": new_id(), "session_id": session["session_id"],
             "expected_session_revision": snap["session"]["revision"],
             "request_id": created["request"]["request_id"],
             "target": copy.deepcopy(self.valid["target"]),
             "expires_at": self.valid["expires_at"], "reason": "new angle",
             "expected_request_revision": 1, "cad_context": None})
        late = self.journal.accept_package(
            SOURCE, ack["attempt_id"], self._package_of(ack["attempt_id"]))
        self.assertEqual(late["status"], "late_after_supersede")

    def test_revoke_between_accept_and_report_is_late(self):
        flow = self.full_flow()
        self.journal.revoke_grant(flow["session"]["grant_id"])
        report = {"available": True, "valid": True,
                  "package_sha256": flow["receipt"]["package_sha256"],
                  "image_sha256": flow["receipt"]["image_sha256"],
                  "sidecar_sha256": flow["receipt"]["sidecar_sha256"]}
        out = self.journal.apply_validator_report(
            flow["receipt"]["receipt_id"], report)
        self.assertEqual(out["status"], "late_after_revoke")

    def test_valid_report_qualifies_synthetic(self):
        flow = self.full_flow()
        report = {"available": True, "valid": True,
                  "package_sha256": flow["receipt"]["package_sha256"],
                  "image_sha256": flow["receipt"]["image_sha256"],
                  "sidecar_sha256": flow["receipt"]["sidecar_sha256"]}
        out = self.journal.apply_validator_report(
            flow["receipt"]["receipt_id"], report)
        self.assertEqual(out["status"], "qualified")

    def test_bad_report_rejects(self):
        flow = self.full_flow()
        out = self.journal.apply_validator_report(
            flow["receipt"]["receipt_id"],
            {"available": True, "valid": False,
             "reason_codes": ["synthetic:blur"]})
        self.assertEqual(out["status"], "rejected")

    def test_unavailable_seam_never_qualifies(self):
        flow = self.full_flow()
        out = self.journal.maybe_qualify(flow["receipt"]["receipt_id"])
        self.assertEqual(out["status"], "package_only")


class SidecarOrderTests(PipelineCase):
    def test_sidecar_before_package_pends(self):
        session = self.open_session()
        created = self.make_request(session["session_id"], 1)
        self.arm({"session_id": session["session_id"],
                  "revision": created["session_revision"]})
        ack = self.reserve(session["session_id"],
                           created["request"]["request_id"], 1)
        self.bind(ack["attempt_id"])
        out = self.journal.upload_sidecar(
            SOURCE, None, ack["attempt_id"],
            self.sidecar_body(session["session_id"],
                              created["request"]["request_id"], 1,
                              ack["attempt_id"]))
        self.assertEqual(out["state"], "SPATIAL_PENDING")
        self.assertIsNone(out["receipt_status"])

    def test_package_then_sidecar_associates(self):
        flow = self.full_flow()
        out = self.journal.upload_sidecar(
            SOURCE, None, flow["ack"]["attempt_id"],
            self.sidecar_body(flow["session"]["session_id"],
                              flow["request"]["request_id"], 1,
                              flow["ack"]["attempt_id"]))
        self.assertEqual(out["status"], "package_only")
        self.assertIsNotNone(out["sidecar_sha256"])
        self.assertEqual(out["sidecar_revision"], 1)

    def test_sidecar_linkage_mismatch_rejected(self):
        flow = self.full_flow()
        body = self.sidecar_body(flow["session"]["session_id"],
                                 flow["request"]["request_id"], 1,
                                 flow["ack"]["attempt_id"])
        body["sidecar"]["request_id"] = new_id()
        with self.assertRaises(journal.ControlError) as raised:
            self.journal.upload_sidecar(
                SOURCE, None, flow["ack"]["attempt_id"], body)
        self.assertEqual(raised.exception.code, "invalid_input")

    def test_supersedes_chain_and_immutable_evidence(self):
        flow = self.full_flow()
        first = self.journal.upload_sidecar(
            SOURCE, None, flow["ack"]["attempt_id"],
            self.sidecar_body(flow["session"]["session_id"],
                              flow["request"]["request_id"], 1,
                              flow["ack"]["attempt_id"]))
        evidence = first["sidecar_sha256"]
        second = self.journal.upload_sidecar(
            SOURCE, None, flow["ack"]["attempt_id"],
            self.sidecar_body(flow["session"]["session_id"],
                              flow["request"]["request_id"], 1,
                              flow["ack"]["attempt_id"], expected=1))
        self.assertEqual(second["sidecar_revision"], 2)
        # Same evidence_id with different bytes conflicts.
        stored = self.journal._conn.execute(
            "SELECT sidecar_json FROM sidecars ORDER BY sidecar_revision"
            " LIMIT 1").fetchone()[0]
        tampered = json.loads(stored)
        tampered["quality"] = {"overall": "rejected"}
        with self.assertRaises(journal.ControlError) as raised:
            self.journal.upload_sidecar(
                SOURCE, None, flow["ack"]["attempt_id"],
                {"operation_id": new_id(), "expected_sidecar_revision": 2,
                 "sidecar": tampered})
        self.assertEqual(raised.exception.code, "idempotency_conflict")

    def test_cross_attempt_supersedes_rejected(self):
        flow = self.full_flow()
        first = self.journal.upload_sidecar(
            SOURCE, None, flow["ack"]["attempt_id"],
            self.sidecar_body(flow["session"]["session_id"],
                              flow["request"]["request_id"], 1,
                              flow["ack"]["attempt_id"]))
        other_ack = self.reserve(flow["session"]["session_id"],
                                 flow["request"]["request_id"], 1)
        self.bind(other_ack["attempt_id"])
        evidence = self.journal._conn.execute(
            "SELECT evidence_id FROM sidecars LIMIT 1").fetchone()[0]
        with self.assertRaises(journal.ControlError) as raised:
            self.journal.upload_sidecar(
                SOURCE, None, other_ack["attempt_id"],
                self.sidecar_body(flow["session"]["session_id"],
                                  flow["request"]["request_id"], 1,
                                  other_ack["attempt_id"],
                                  supersedes=evidence))
        self.assertEqual(raised.exception.code, "invalid_input")


class LeaseStatusResumeTests(PipelineCase):
    def test_lease_bounded_and_reported(self):
        flow = self.full_flow()
        lease = self.journal.renew_lease(flow["session"]["session_id"],
                                         flow["session"]["grant_id"])
        self.assertLessEqual(lease["lease_remaining_ms"], 30000)
        self.assertGreater(lease["lease_remaining_ms"], 0)
        self.assertIn("server_time", lease)

    def test_status_guidance_must_mirror_request(self):
        flow = self.full_flow()
        body = {"session_id": flow["session"]["session_id"],
                "source_id": SOURCE,
                "request_id": flow["request"]["request_id"],
                "request_revision": 1, "event_seq": 0,
                "observed_at": NOW_ISO, "device_monotonic_ns": "7",
                "registration": "valid", "guidance_state": "QUEUED",
                "pose_age_ms": 12, "translation_error_camera_mm": [1, 2, 3],
                "rotation_error_deg": 1.5, "hold_elapsed_ms": 0,
                "quality_blockers": [], "queue_count": 1}
        ack = self.journal.post_status(SOURCE, body)
        self.assertEqual(ack["accepted_seq"], 0)
        body["guidance_state"] = "CAPTURING"
        with self.assertRaises(journal.ControlError) as raised:
            self.journal.post_status(SOURCE, body)
        self.assertEqual(raised.exception.code, "invalid_input")

    def test_resume_disarms_and_opens_new_seal_slot(self):
        flow = self.full_flow()
        session_id = flow["session"]["session_id"]
        snap = self.journal.get_session(session_id)
        sealed = self.journal.finalize(
            PRINCIPAL,
            {"operation_id": new_id(), "session_id": session_id,
             "expected_session_revision": snap["session"]["revision"]})
        self.assertEqual(sealed["revision"], 1)
        snap2 = self.journal.get_session(session_id)
        resumed = self.journal.resume(
            PRINCIPAL,
            {"operation_id": new_id(), "session_id": session_id,
             "expected_session_revision": snap2["session"]["revision"]})
        self.assertEqual(resumed["session"]["state"], "DISARMED")
        snap3 = self.journal.get_session(session_id)
        sealed2 = self.journal.finalize(
            PRINCIPAL,
            {"operation_id": new_id(), "session_id": session_id,
             "expected_session_revision": snap3["session"]["revision"]})
        self.assertEqual(sealed2["revision"], 2)
        self.assertEqual(sealed2["capture_set_id"], sealed["capture_set_id"])

    def test_finalize_seals_qualified_receipts(self):
        flow = self.full_flow()
        report = {"available": True, "valid": True,
                  "package_sha256": flow["receipt"]["package_sha256"],
                  "image_sha256": flow["receipt"]["image_sha256"],
                  "sidecar_sha256": flow["receipt"]["sidecar_sha256"]}
        self.journal.apply_validator_report(
            flow["receipt"]["receipt_id"], report)
        snap = self.journal.get_session(flow["session"]["session_id"])
        sealed = self.journal.finalize(
            PRINCIPAL,
            {"operation_id": new_id(),
             "session_id": flow["session"]["session_id"],
             "expected_session_revision": snap["session"]["revision"]})
        self.assertEqual(sealed["status"], "complete")
        self.assertEqual(len(sealed["receipts"]), 1)
        self.assertEqual(sealed["receipts"][0]["status"], "qualified")


class MigrationTests(PipelineCase):
    def test_wave1a_store_opens_and_preserves(self):
        legacy = Path(self.tmp.name) / "legacy.db"
        conn = sqlite3.connect(str(legacy))
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY,"
                     " value TEXT NOT NULL)")
        conn.execute("INSERT INTO meta VALUES ('contract_sha','ec313f7ftest')")
        conn.execute("INSERT INTO meta VALUES ('schema_version','1')")
        conn.execute(
            "CREATE TABLE sessions (session_id TEXT PRIMARY KEY,"
            " source_id TEXT NOT NULL, grant_id TEXT, revision INTEGER,"
            " state TEXT, created_at TEXT, expires_at TEXT,"
            " lease_expires_at TEXT, lease_remaining_ms INTEGER,"
            " capabilities_json TEXT, active_request_id TEXT,"
            " consent_revision INTEGER, adapter_max_images INTEGER,"
            " event_seq INTEGER)")
        caps = {"source_id": SOURCE, "updated_at": NOW_ISO, "stale": True,
                "camera_ids": [], "resolutions_px": [], "calibration_ids": [],
                "target_kinds": [], "quality_profile_ids": [],
                "app_version": "unpublished", "protocol_version": 1}
        session_id = new_id()
        conn.execute(
            "INSERT INTO sessions VALUES (?,?,NULL,1,'DISARMED',?,?,?,?,?,?,?,?,0)",
            (session_id, SOURCE, NOW_ISO, OPEN_EXPIRES, None, 0,
             json.dumps(caps), None, 0, 20))
        conn.commit()
        conn.close()
        journal.ControlJournal(
            legacy, contract_sha="ec313f7ftest",
            now_fn=lambda: NOW).close()
        reopened = journal.ControlJournal(
            legacy, contract_sha="ec313f7ftest", now_fn=lambda: NOW)
        self.addCleanup(reopened.close)
        snapshot = reopened.get_session(session_id)
        self.assertEqual(snapshot["session"]["state"], "DISARMED")
        cols = {row[1] for row in reopened._conn.execute(
            "PRAGMA table_info(sessions)").fetchall()}
        for name in ("effective_max_images", "used_images",
                     "created_by_principal", "status_json"):
            self.assertIn(name, cols)


class PipelineFixtureTests(unittest.TestCase):
    def setUp(self):
        self.fixtures = Path(__file__).resolve().parent / "fixtures"

    def load(self, name):
        return json.loads((self.fixtures / name).read_text(encoding="utf-8"))

    def test_consent_fixture_valid(self):
        fixture = self.load("guided-wire-consent.json")
        self.assertIn("SYNTHETIC", fixture["_note"])
        self.assertEqual(
            journal.validate_consent_input(fixture["request"]), [])
        self.assertEqual(fixture["response"]["state"], "ACTIVE")

    def test_attempt_fixture_valid(self):
        fixture = self.load("guided-wire-attempt.json")
        self.assertEqual(
            journal.validate_attempt_register_input(fixture["request"]), [])
        ack = fixture["response"]
        self.assertEqual(
            sorted(ack),
            ["attempt_id", "attempt_number", "grant_id",
             "remaining_exposures", "session_revision", "status"])

    def test_binding_fixture_valid(self):
        fixture = self.load("guided-wire-binding.json")
        self.assertEqual(
            journal.validate_attempt_binding(fixture["request"]), [])
        self.assertEqual(fixture["response"]["status"], "asset_bound")

    def test_sidecar_pending_fixture_valid(self):
        fixture = self.load("guided-wire-sidecar-pending.json")
        self.assertEqual(
            journal.validate_sidecar_upload(fixture["request"]), [])
        self.assertEqual(fixture["response"]["state"], "SPATIAL_PENDING")


if __name__ == "__main__":
    unittest.main()
