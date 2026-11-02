"""Core <-> Android wire-spelling drift guard (Wave 1.5).

The Android guidance reducers pin their enum spellings against local
literals only (``ReducersTest``); this is the frozen-vector drift
check the ``Reducers.kt`` header asks for. It parses the Kotlin enum
spellings and ties them to the frozen G0 ``control.schema.json`` enums
plus the Core guided-wire fixtures and the journal's delivery mapping.

It guards spelling compatibility only. Wave-2 landed the guided
transport (``GuidedControlClient``); the old no-transport tripwire
was retired into a presence + scatter check below: the endpoint
literals must exist in the client module and nowhere else.

Run from the receiver directory::

    python -m unittest discover -s tests -v
"""

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anastylos_control_journal as journal

RECEIVER_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = RECEIVER_DIR.parents[1]
SCHEMA = (REPO_ROOT / "capture-companion" / "control" / "contracts"
          / "schemas" / "control.schema.json")
REDUCERS = (REPO_ROOT / "capture-companion" / "android" / "app" / "src"
            / "main" / "kotlin" / "org" / "anastylos" / "capture"
            / "guidance" / "Reducers.kt")
ANDROID_MAIN = REDUCERS.parents[3]
FIXTURES = RECEIVER_DIR / "tests" / "fixtures"


def schema_enum(def_name, prop):
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    return list(schema["$defs"][def_name]["properties"][prop]["enum"])


def kotlin_entries(source, enum_name):
    """Plain UPPER_SNAKE entries of ``enum class <name>`` in order."""
    body = _enum_body(source, enum_name)
    return re.findall(r"^\s+([A-Z][A-Z0-9_]*),?\s*$", body, re.M)


def kotlin_wire_values(source, enum_name):
    """``NAME(\"wire\")`` values of ``enum class <name>`` in order."""
    body = _enum_body(source, enum_name)
    return re.findall(r'^\s+[A-Z][A-Z0-9_]*\("([^"]+)"\)', body, re.M)


def _enum_body(source, enum_name):
    match = re.search(r"enum class %s[^{]*\{(.*?)\n\}" % enum_name,
                      source, re.S)
    if match is None:
        raise AssertionError(f"enum class {enum_name} not found")
    return match.group(1)


class AndroidWireSpellingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = REDUCERS.read_text(encoding="utf-8")

    def test_session_spelling_matches_frozen_schema(self):
        self.assertEqual(kotlin_entries(self.source, "SessionState"),
                         schema_enum("Session", "state"))

    def test_request_spelling_matches_frozen_schema(self):
        self.assertEqual(kotlin_entries(self.source, "RequestState"),
                         schema_enum("ViewRequest", "status"))

    def test_receipt_spelling_matches_frozen_schema(self):
        self.assertEqual(kotlin_wire_values(self.source, "ReceiptKind"),
                         schema_enum("Receipt", "status"))

    def test_registration_spelling_matches_frozen_schema(self):
        self.assertEqual(kotlin_wire_values(self.source, "WireRegistration"),
                         schema_enum("Status", "registration"))

    def test_delivery_states_cover_journal_mapping(self):
        entries = kotlin_entries(self.source, "DeliveryState")
        for shared in ("PACKAGE_ACCEPTED", "SPATIAL_PENDING",
                       "ACKNOWLEDGED", "REJECTED"):
            self.assertIn(shared, entries)
        for receipt_status in schema_enum("Receipt", "status"):
            mapped = journal.delivery_state_for_receipt(receipt_status)
            self.assertIn(mapped, entries)
            back = journal.receipt_for_delivery_state(mapped)
            self.assertIn(back, schema_enum("Receipt", "status"))

    def test_fixtures_use_frozen_spellings(self):
        session_states = set(schema_enum("Session", "state"))
        request_states = set(schema_enum("ViewRequest", "status"))
        delivery_states = set(kotlin_entries(self.source, "DeliveryState"))
        consent = json.loads(
            (FIXTURES / "guided-wire-consent.json").read_text(
                encoding="utf-8"))
        self.assertIn(consent["response"]["state"], session_states)
        pending = json.loads(
            (FIXTURES / "guided-wire-sidecar-pending.json").read_text(
                encoding="utf-8"))
        self.assertEqual(pending["response"]["state"], "SPATIAL_PENDING")
        self.assertIn(pending["response"]["state"], delivery_states)
        view = json.loads(
            (FIXTURES / "guided-wire-request-view.json").read_text(
                encoding="utf-8"))
        self.assertIn(view["response"]["request"]["status"], request_states)

    def test_guided_transport_present_on_android(self):
        """Wave-2: the guided client owns every endpoint literal.

        The guided transport landed in exactly one module,
        ``GuidedControlClient``. This pins that shape: all
        source-role endpoint paths must be present there, and no
        other mainsrc module may carry a guided endpoint literal
        (copies are drift).
        """
        client = ANDROID_MAIN / "anastylos/capture/sync/GuidedControlClient.kt"
        self.assertTrue(client.is_file(), "guided client module missing")
        text = client.read_text(encoding="utf-8")
        endpoints = (
            "/v1/guided/enroll",
            "/v1/guided/sources/",
            "/capabilities",
            "pending-sessions",
            "/v1/guided/sessions/",
            "/changes?after=",
            "/events",
            "/status",
            "/consent",
            "/attempts",
            "/asset-binding",
            "/spatial",
            "/capture-set",
            "/videos",
            "/chunks/",
        )
        for literal in endpoints:
            self.assertIn(literal, text)
        stray = []
        for path in sorted(ANDROID_MAIN.rglob("*.kt")):
            if path.name == "GuidedControlClient.kt":
                continue
            try:
                other = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if "/v1/guided/" in other:
                stray.append(path.name)
        self.assertEqual(stray, [])


if __name__ == "__main__":
    unittest.main()
