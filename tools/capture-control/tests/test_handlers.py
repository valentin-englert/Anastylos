"""Handler unit tests with a fake client (no SDK, no network)."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import handlers
from control_client import ControlClientError


class FakeClient:
    def __init__(self):
        self.calls = []
        self.session = {"session": {"session_id": "s", "revision": 3},
                        "requests": [], "latest_status": None,
                        "receipts": []}
        self.page = {"events": [], "next_cursor": "9"}
        self.assets = {}

    def get_session(self, token, session_id):
        self.calls.append(("get_session", token, session_id))
        return dict(self.session)

    def changes(self, token, session_id, after="0"):
        self.calls.append(("changes", token, session_id, after))
        return dict(self.page)

    def capabilities(self, token, source_id):
        self.calls.append(("capabilities", token, source_id))
        return {"source_id": source_id, "stale": True}

    def open_session(self, token, body):
        self.calls.append(("open_session", token, body))
        return {"session_id": "s", "revision": 1}

    def resume(self, token, session_id, body):
        self.calls.append(("resume", token, session_id, body))
        return dict(self.session)

    def request_view(self, token, session_id, body):
        self.calls.append(("request_view", token, session_id, body))
        return {"request": {"request_id": "r"}, "session_revision": 4}

    def revise_view(self, token, request_id, body):
        self.calls.append(("revise_view", token, request_id, body))
        return {"request": {"revision": 2}, "session_revision": 5}

    def cancel_view(self, token, request_id, body):
        self.calls.append(("cancel_view", token, request_id, body))
        return {"request": {"status": "CANCELLED"}, "session_revision": 6}

    def finalize(self, token, session_id, body):
        self.calls.append(("finalize", token, session_id, body))
        return {"capture_set_id": "c", "revision": 1}

    def asset_bytes(self, token, session_id, image_id):
        self.calls.append(("asset_bytes", token, session_id, image_id))
        return self.assets[image_id], "0" * 64


SID = "00000000-0000-4000-8000-000000000002"
RID = "00000000-0000-4000-8000-000000000001"


class HandlerTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.saved = os.environ.pop("ANASTYLOS_PROVIDER_IMAGES", None)
        self.addCleanup(self.restore_env)

    def restore_env(self):
        if self.saved is not None:
            os.environ["ANASTYLOS_PROVIDER_IMAGES"] = self.saved
        else:
            os.environ.pop("ANASTYLOS_PROVIDER_IMAGES", None)

    def test_status_combines_snapshot_and_changes(self):
        out = handlers.capture_status(self.client, "tok", SID, after="4")
        self.assertIn("session", out)
        self.assertIn("changes", out)
        self.assertEqual(out["changes"]["next_cursor"], "9")
        self.assertIn(("changes", "tok", SID, "4"), self.client.calls)

    def test_nine_tool_names_frozen(self):
        self.assertEqual(len(handlers.TOOL_NAMES), 9)
        self.assertIn("capture.finish_set", handlers.TOOL_NAMES)

    def test_bad_uuid_rejected_before_client_call(self):
        with self.assertRaises(handlers.ToolInputError):
            handlers.capture_status(self.client, "tok", "not-a-uuid")
        self.assertEqual(self.client.calls, [])

    def test_open_session_validates_bounds(self):
        with self.assertRaises(handlers.ToolInputError):
            handlers.capture_open_session(self.client, "tok", SID, 21,
                                          "2030-01-01T00:00:00Z")
        self.assertEqual(self.client.calls, [])

    def test_request_revise_cancel_route_by_id(self):
        handlers.capture_request_view(
            self.client, "tok", {"session_id": SID, "operation_id": RID})
        handlers.capture_revise_view(
            self.client, "tok", {"request_id": RID})
        handlers.capture_cancel_view(
            self.client, "tok", {"request_id": RID})
        kinds = [call[0] for call in self.client.calls]
        self.assertEqual(kinds, ["request_view", "revise_view",
                                 "cancel_view"])

    def test_evidence_lists_hashes_without_bytes_by_default(self):
        self.client.session["receipts"] = [{
            "request_id": RID, "image_id": "i-1",
            "image_sha256": "1" * 64, "status": "qualified"}]
        out = handlers.capture_evidence(self.client, "tok", SID)
        self.assertFalse(out["provider_image_authorized"])
        self.assertNotIn("bytes_hex", out["assets"][0])
        self.assertTrue(out["assets"][0]["resource_uri"].startswith(
            "anastylos://"))

    def test_evidence_bytes_need_operator_opt_in(self):
        self.client.session["receipts"] = [{
            "request_id": RID, "image_id": "i-1",
            "image_sha256": "1" * 64, "status": "qualified"}]
        with self.assertRaises(handlers.ToolInputError):
            handlers.capture_evidence(self.client, "tok", SID,
                                       include_images=True)
        os.environ["ANASTYLOS_PROVIDER_IMAGES"] = "authorized"
        self.client.assets["i-1"] = b"JPEG"
        out = handlers.capture_evidence(self.client, "tok", SID,
                                        include_images=True)
        self.assertTrue(out["provider_image_authorized"])
        self.assertEqual(out["assets"][0]["bytes_hex"], b"JPEG".hex())

    def test_client_errors_propagate_unchanged(self):
        class Failing(FakeClient):
            def get_session(self, token, session_id):
                raise ControlClientError(404, "not_found", "gone")

        with self.assertRaises(ControlClientError) as raised:
            handlers.capture_status(Failing(), "tok", SID)
        self.assertEqual(raised.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
