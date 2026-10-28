"""Offline transport-helper tests: SSE parsing, budget, ledger."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anastylos_sam import client as CL  # noqa: E402


class SseParsing(unittest.TestCase):
    def test_events_comments_done(self):
        raw = (": connected\n"
               'data: {"type": "response.output_text.delta", "delta": "a"}\n\n'
               'data: {"type": "response.completed"}\n\n'
               "data: [DONE]\n\n").encode()
        events = list(CL._iter_sse_events(raw))
        self.assertEqual([event["type"] for event in events],
                         ["response.output_text.delta", "response.completed"])

    def test_unparsable_line_kept_explicit(self):
        events = list(CL._iter_sse_events(b"data: not json\n\n"))
        self.assertEqual(events[0]["type"], "anastylos.unparsable")

    def test_multiline_data_joined(self):
        raw = b'data: {"a":\ndata: 1}\n\n'
        events = list(CL._iter_sse_events(raw))
        self.assertEqual(events, [{"a": 1}])


class BudgetGuard(unittest.TestCase):
    def test_within_budget_passes(self):
        self.assertAlmostEqual(CL.check_budget(1.0, 0.5, 3.0), 1.5)

    def test_over_budget_refuses(self):
        with self.assertRaises(CL.BudgetExceeded):
            CL.check_budget(2.99, 0.02, 3.0)


class Ledger(unittest.TestCase):
    def test_redacted_entry_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            CL.append_ledger(path, {"kind": "segment-image", "cost": {"usd": 0.0025}})
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            entry = json.loads(lines[0])
            self.assertIn("ts_utc", entry)
            blob = json.dumps(entry).lower()
            self.assertNotIn("bearer", blob)
            self.assertNotIn("model_api_key", blob)


if __name__ == "__main__":
    unittest.main()
