"""Core <-> Android wire-spelling drift guard (Wave 1.5).

The Android guidance reducers pin their enum spellings against local
literals only (``ReducersTest``); this is the frozen-vector drift
check the ``Reducers.kt`` header asks for. It parses the Kotlin enum
spellings and ties them to the frozen G0 ``control.schema.json`` enums
plus the Core guided-wire fixtures and the journal's delivery mapping.

It guards spelling compatibility only: Android has no guided transport
(no HTTP client, no identity/cursor plumbing), so every item below is
contract-compatible-but-not-connected. The transport tripwire pins
that Wave-2 boundary and must be retired when transport lands.

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

    def test_no_guided_transport_on_android(self):
        """Tripwire: Android has no guided HTTP client yet (Wave-2).

        Retire this test when the guided transport lands; until then
        any guided endpoint literal in Android mainsrc is either a bug
        or an unannounced Wave-2 start.
        """
        literals = ("v1/guided", "/consent", "/attempts", "/finalize",
                    "/lease", "/claim", "operation_id",
                    "expected_session_revision", "change_seq",
                    "next_cursor")
        hits = []
        for path in sorted(ANDROID_MAIN.rglob("*.kt")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for literal in literals:
                if literal in text:
                    hits.append(f"{path.name}: {literal}")
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
