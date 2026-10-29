"""Contract tests for the guided-control journal (Wave 1a). Stdlib only.

Run from the receiver directory:

    py -3.12 -m unittest discover -s tests -v

These tests execute the frozen G0 vectors
(`capture-companion/control/contracts/vectors/`, contract
`ec313f7f...`) against `anastylos_control_journal`: one valid
ViewRequest, 8 negative cases, and the CAS/idempotency/queue/cursor
subset of the 17 transition cases. Receipt/sidecar/consent/attempt
transitions are NOT_TESTED here (Wave-1b); see EVIDENCE.md.
"""

import copy
import hashlib
import json
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anastylos_control_journal as journal

VECTORS = Path(__file__).resolve().parents[2] / "control" / "contracts" / "vectors"

# Frozen vector created_at 12:00Z, expires_at 12:03Z; pin server now inside.
NOW = datetime(2026, 10, 29, 12, 1, 0, tzinfo=timezone.utc)
NOW_ISO = "2026-10-29T12:01:00Z"
OPEN_EXPIRES = "2026-10-29T12:31:00Z"

SOURCE = "00000000-0000-4000-8000-000000000003"
OTHER_SOURCE = "00000000-0000-4000-8000-000000000099"
PRINCIPAL = "adapter:test-host"


def load_vector(name):
    return json.loads((VECTORS / name).read_text(encoding="utf-8"))


def new_id():
    return str(uuid.uuid4())


def open_body(source_id=SOURCE, operation_id=None, max_images=20):
    return {
        "operation_id": operation_id or new_id(),
        "source_id": source_id,
        "max_images": max_images,
        "expires_at": OPEN_EXPIRES,
    }


def request_body(session_id, source_id, operation_id, revision_expected, valid):
    return {
        "operation_id": operation_id,
        "session_id": session_id,
        "expected_session_revision": revision_expected,
        "source_id": source_id,
        "target_label": valid["target_label"],
        "board_id": valid["board_id"],
        "calibration_id": valid["calibration_id"],
        "target": copy.deepcopy(valid["target"]),
        "quality_profile_id": valid["quality_profile_id"],
        "expires_at": valid["expires_at"],
        "reason": valid["reason"],
        "board_registration_epoch_id": valid["board_registration_epoch_id"],
        "object_pose_epoch_id": valid["object_pose_epoch_id"],
        "cad_context": valid["cad_context"],
    }


class JournalCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "control.db"
        self.journal = journal.ControlJournal(
            self.db, contract_sha="ec313f7ftest", now_fn=lambda: NOW
        )
        self.addCleanup(self.journal.close)
        self.valid = load_vector("control-request-valid.json")

    def open_session(self, source_id=SOURCE, operation_id=None):
        return self.journal.open_session(
            PRINCIPAL, open_body(source_id, operation_id), source_id
        )


class ValidVectorTests(JournalCase):
    def test_valid_viewrequest_passes_structural_and_semantic(self):
        schema_errors = journal.validate_view_request_structural(self.valid)
        self.assertEqual(schema_errors, [])
        semantic_errors = journal.validate_view_request_semantic(self.valid, NOW)
        self.assertEqual(semantic_errors, [])

    def test_valid_request_creates_queued_request(self):
        session = self.open_session()
        self.assertEqual(session["state"], "DISARMED")
        self.assertEqual(session["revision"], 1)
        self.assertIsNone(session["lease_expires_at"])
        self.assertEqual(session["lease_remaining_ms"], 0)
        out = self.journal.request_view(
            PRINCIPAL,
            SOURCE,
            request_body(session["session_id"], SOURCE, new_id(), 1, self.valid),
        )
        self.assertEqual(out["session_revision"], 2)
        request = out["request"]
        self.assertEqual(request["revision"], 1)
        self.assertEqual(request["status"], "QUEUED")
        self.assertEqual(request["target_label"], self.valid["target_label"])


class NegativeVectorTests(JournalCase):
    def test_unknown_key_schema_reject(self):
        bad = copy.deepcopy(self.valid)
        bad["unexpected"] = True
        self.assertTrue(journal.validate_view_request_structural(bad))

    def test_bad_revision_schema_reject(self):
        bad = copy.deepcopy(self.valid)
        bad["revision"] = 0
        self.assertTrue(journal.validate_view_request_structural(bad))

    def test_wrong_source_authorization_reject_structurally_valid(self):
        bad = copy.deepcopy(self.valid)
        bad["source_id"] = OTHER_SOURCE
        self.assertEqual(journal.validate_view_request_structural(bad), [])
        session = self.open_session()
        with self.assertRaises(journal.ControlError) as raised:
            self.journal.request_view(
                PRINCIPAL,
                SOURCE,
                request_body(
                    session["session_id"], OTHER_SOURCE, new_id(), 1, bad
                ),
            )
        self.assertEqual(raised.exception.code, "unauthorized")

    def test_wrong_board_side_semantic_reject(self):
        bad = copy.deepcopy(self.valid)
        bad["target"]["T_B_C"]["matrix"][11] = 450
        self.assertEqual(journal.validate_view_request_structural(bad), [])
        self.assertTrue(journal.validate_view_request_semantic(bad, NOW))

    def test_reflection_semantic_reject(self):
        bad = copy.deepcopy(self.valid)
        bad["target"]["T_B_C"]["matrix"][0] = -1
        self.assertEqual(journal.validate_view_request_structural(bad), [])
        errors = journal.validate_view_request_semantic(bad, NOW)
        self.assertTrue(errors, "determinant -1 must be rejected")

    def test_expired_semantic_reject(self):
        bad = copy.deepcopy(self.valid)
        bad["expires_at"] = "2026-10-29T11:00:00Z"
        self.assertTrue(journal.validate_view_request_semantic(bad, NOW))

    def test_nan_parse_reject(self):
        with self.assertRaises(journal.ControlError) as raised:
            journal.parse_strict_json(b'{"revision": NaN}')
        self.assertEqual(raised.exception.code, "invalid_input")

    def test_duplicate_keys_parse_reject(self):
        with self.assertRaises(journal.ControlError):
            journal.parse_strict_json(b'{"a": 1, "a": 2}')

    def test_same_operation_new_body_conflicts(self):
        session = self.open_session()
        operation_id = new_id()
        first = request_body(session["session_id"], SOURCE, operation_id, 1, self.valid)
        self.journal.request_view(PRINCIPAL, SOURCE, first)
        second = request_body(session["session_id"], SOURCE, operation_id, 1, self.valid)
        second["target_label"] = "Changed"
        with self.assertRaises(journal.ControlError) as raised:
            self.journal.request_view(PRINCIPAL, SOURCE, second)
        self.assertEqual(raised.exception.code, "idempotency_conflict")


class TransitionTests(JournalCase):
    def test_cas_revision_conflict_no_state_change(self):
        session = self.open_session()
        out = self.journal.request_view(
            PRINCIPAL, SOURCE,
            request_body(session["session_id"], SOURCE, new_id(), 1, self.valid),
        )
        request_id = out["request"]["request_id"]
        revise = {
            "operation_id": new_id(),
            "session_id": session["session_id"],
            "expected_session_revision": out["session_revision"],
            "request_id": request_id,
            "target": copy.deepcopy(self.valid["target"]),
            "expires_at": self.valid["expires_at"],
            "reason": "stale revise attempt",
            "expected_request_revision": 99,
            "cad_context": None,
        }
        with self.assertRaises(journal.ControlError) as raised:
            self.journal.revise_view(PRINCIPAL, SOURCE, revise)
        self.assertEqual(raised.exception.code, "revision_conflict")
        snapshot = self.journal.get_session(session["session_id"])
        self.assertEqual(snapshot["session"]["revision"], out["session_revision"])
        self.assertEqual(snapshot["requests"][0]["revision"], 1)

    def test_dedupe_same_operation_replays_original(self):
        session = self.open_session()
        operation_id = new_id()
        body = request_body(session["session_id"], SOURCE, operation_id, 1, self.valid)
        first = self.journal.request_view(PRINCIPAL, SOURCE, body)
        second = self.journal.request_view(PRINCIPAL, SOURCE, copy.deepcopy(body))
        self.assertEqual(first, second)
        snapshot = self.journal.get_session(session["session_id"])
        self.assertEqual(len(snapshot["requests"]), 1)

    def test_idempotency_key_includes_operation_kind(self):
        session = self.open_session()
        operation_id = new_id()
        created = self.journal.request_view(
            PRINCIPAL, SOURCE,
            request_body(session["session_id"], SOURCE, operation_id, 1, self.valid),
        )
        # Same (principal, session, operation_id) but kind revise_view is new.
        revise = {
            "operation_id": operation_id,
            "session_id": session["session_id"],
            "expected_session_revision": created["session_revision"],
            "request_id": created["request"]["request_id"],
            "target": copy.deepcopy(self.valid["target"]),
            "expires_at": self.valid["expires_at"],
            "reason": "kind-scoped retest",
            "expected_request_revision": 1,
            "cad_context": None,
        }
        out = self.journal.revise_view(PRINCIPAL, SOURCE, revise)
        self.assertEqual(out["request"]["revision"], 2)

    def test_journal_survives_reopen_and_replay(self):
        session = self.open_session()
        operation_id = new_id()
        body = request_body(session["session_id"], SOURCE, operation_id, 1, self.valid)
        first = self.journal.request_view(PRINCIPAL, SOURCE, body)
        self.journal.close()
        reopened = journal.ControlJournal(
            self.db, contract_sha="ec313f7ftest", now_fn=lambda: NOW
        )
        self.addCleanup(reopened.close)
        replay = reopened.request_view(PRINCIPAL, SOURCE, copy.deepcopy(body))
        self.assertEqual(replay, first)
        snapshot = reopened.get_session(session["session_id"])
        self.assertEqual(snapshot["session"]["revision"], first["session_revision"])

    def test_concurrent_requests_queue_bounded_at_20(self):
        session = self.open_session()
        revision = session["revision"]
        for _ in range(20):
            out = self.journal.request_view(
                PRINCIPAL, SOURCE,
                request_body(session["session_id"], SOURCE, new_id(), revision, self.valid),
            )
            revision = out["session_revision"]
            self.assertEqual(out["request"]["status"], "QUEUED")
        with self.assertRaises(journal.ControlError) as raised:
            self.journal.request_view(
                PRINCIPAL, SOURCE,
                request_body(session["session_id"], SOURCE, new_id(), revision, self.valid),
            )
        self.assertEqual(raised.exception.code, "busy")

    def test_changes_cursor_round_trip(self):
        session = self.open_session()
        out = self.journal.request_view(
            PRINCIPAL, SOURCE,
            request_body(session["session_id"], SOURCE, new_id(), 1, self.valid),
        )
        page = self.journal.get_changes(session["session_id"], "0")
        self.assertFalse(page["snapshot_required"])
        self.assertGreaterEqual(len(page["events"]), 1)
        revisions = [event["session_revision"] for event in page["events"]]
        self.assertEqual(revisions, sorted(revisions))
        self.assertTrue(all(event["change_seq"] >= 1 for event in page["events"]))
        again = self.journal.get_changes(session["session_id"], page["next_cursor"])
        self.assertEqual(again["events"], [])

    def test_finalize_seals_capture_set_atomically(self):
        session = self.open_session()
        out = self.journal.request_view(
            PRINCIPAL, SOURCE,
            request_body(session["session_id"], SOURCE, new_id(), 1, self.valid),
        )
        capture_set = self.journal.finalize(
            PRINCIPAL,
            {
                "operation_id": new_id(),
                "session_id": session["session_id"],
                "expected_session_revision": out["session_revision"],
            },
        )
        self.assertEqual(capture_set["revision"], 1)
        check = dict(capture_set)
        digest = check.pop("manifest_sha256")
        canonical = json.dumps(check, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.assertEqual(hashlib.sha256(canonical).hexdigest(), digest)
        # Exactly one winner: a second seal at the same revision conflicts.
        with self.assertRaises(journal.ControlError) as raised:
            self.journal.finalize(
                PRINCIPAL,
                {
                    "operation_id": new_id(),
                    "session_id": session["session_id"],
                    "expected_session_revision": out["session_revision"],
                },
            )
        self.assertEqual(raised.exception.code, "revision_conflict")

    def test_attempt_delivery_mapping(self):
        self.assertEqual(journal.delivery_state_for_receipt("qualified"), "ACKNOWLEDGED")
        self.assertEqual(journal.delivery_state_for_receipt("package_only"), "PACKAGE_ACCEPTED")
        self.assertEqual(journal.receipt_for_delivery_state("ACKNOWLEDGED"), "qualified")
        self.assertEqual(journal.receipt_for_delivery_state("PACKAGE_ACCEPTED"), "package_only")

    def test_event_id_reuse_conflicts(self):
        session = self.open_session()
        out = self.journal.request_view(
            PRINCIPAL, SOURCE,
            request_body(session["session_id"], SOURCE, new_id(), 1, self.valid),
        )
        event = {
            "event_id": new_id(),
            "session_id": session["session_id"],
            "source_id": SOURCE,
            "request_id": out["request"]["request_id"],
            "request_revision": 1,
            "kind": "accepted",
            "occurred_at": NOW_ISO,
            "monotonic_ns": "123456789",
            "attempt_id": None,
            "reason": None,
        }
        first = self.journal.record_event(SOURCE, event)
        replay = self.journal.record_event(SOURCE, copy.deepcopy(event))
        self.assertEqual(first, replay)
        altered = copy.deepcopy(event)
        altered["kind"] = "cancelled"
        with self.assertRaises(journal.ControlError) as raised:
            self.journal.record_event(SOURCE, altered)
        self.assertEqual(raised.exception.code, "idempotency_conflict")


class WireFixtureTests(unittest.TestCase):
    """ANDROID handoff fixtures stay valid against the frozen contract."""

    def setUp(self):
        self.fixtures = Path(__file__).resolve().parent / "fixtures"
        self.valid = load_vector("control-request-valid.json")

    def load(self, name):
        return json.loads((self.fixtures / name).read_text(encoding="utf-8"))

    def test_open_session_fixture_valid(self):
        fixture = self.load("guided-wire-open-session.json")
        self.assertIn("SYNTHETIC", fixture["_note"])
        self.assertEqual(
            journal.validate_open_input(fixture["request"]), [])
        session = fixture["response"]
        self.assertEqual(session["state"], "DISARMED")
        self.assertIsNone(session["lease_expires_at"])
        self.assertEqual(session["lease_remaining_ms"], 0)

    def test_request_view_fixture_valid(self):
        fixture = self.load("guided-wire-request-view.json")
        self.assertEqual(
            journal.validate_request_input(fixture["request"]), [])
        request = fixture["response"]["request"]
        self.assertEqual(
            journal.validate_view_request_structural(request), [])
        self.assertEqual(
            journal.validate_view_request_semantic(request, NOW), [])

    def test_source_event_fixture_valid(self):
        fixture = self.load("guided-wire-source-event.json")
        self.assertEqual(
            journal.validate_event_structural(fixture["event"]), [])
        self.assertEqual(
            fixture["ack"]["accepted_event_id"],
            fixture["event"]["event_id"])

    def test_changes_fixture_shape(self):
        fixture = self.load("guided-wire-changes.json")
        page = fixture["response"]
        self.assertEqual(
            sorted(page),
            ["events", "lease_expires_at", "lease_remaining_ms",
             "next_cursor", "server_time", "snapshot_required"])
        self.assertLessEqual(len(page["events"]), 100)
        for event in page["events"]:
            self.assertEqual(
                sorted(event),
                ["change_seq", "kind", "receipt", "request",
                 "session_revision", "source_event"])

    def test_finalize_fixture_manifest_verifies(self):
        fixture = self.load("guided-wire-finalize.json")
        self.assertEqual(
            journal.validate_mutation_input(fixture["request"]), [])
        capture_set = fixture["response"]
        check = dict(capture_set)
        digest = check.pop("manifest_sha256")
        canonical = json.dumps(
            check, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.assertEqual(hashlib.sha256(canonical).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
