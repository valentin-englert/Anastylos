"""CONS-1 analysis and call budget: pre-registered level rules, retry bound and hard cap (no API call, no key)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import consistency_experiment as cons  # noqa: E402

SPEC = {"scope": "part"}
CHOSEN = [SimpleNamespace(t_s=0.5), SimpleNamespace(t_s=18.0)]


class LevelRuleTest(unittest.TestCase):
    def test_view_class_is_deterministic(self):
        self.assertEqual(cons.view_class("Back / inside"), "back")
        self.assertEqual(cons.view_class("top, front face"), "front")
        self.assertEqual(cons.view_class("front and back"), "mixed")
        self.assertEqual(cons.view_class("unclear"), "other")

    def test_gate_outcome_follows_pipeline(self):
        usable = {"usable": True, "tile": 1, "state": "single_part"}
        self.assertEqual(cons.gate_outcome(SPEC, usable, CHOSEN), ("ok", 1))
        self.assertEqual(cons.gate_outcome(SPEC, {**usable, "state": "assembled_object"}, CHOSEN), ("NEEDS_CONFIRMATION", 1))
        self.assertEqual(cons.gate_outcome(SPEC, {**usable, "usable": False}, CHOSEN), ("NEEDS_CAPTURE", None))
        self.assertEqual(cons.gate_outcome(SPEC, {**usable, "tile": 3}, CHOSEN), ("NEEDS_CAPTURE", None))

    def test_frame_levels(self):
        self.assertEqual(cons.frame_level(5, 5), "L0")
        self.assertEqual(cons.frame_level(4, 5), "L1")
        self.assertEqual(cons.frame_level(5, 4), "L1")
        self.assertEqual(cons.frame_level(3, 5), "L2")
        self.assertEqual(cons.frame_level(5, 3), "L2")

    def test_box_levels(self):
        base = [10, 10, 110, 110]
        shifted = [14, 10, 114, 110]  # IoU 0.92
        far = [40, 10, 140, 110]  # IoU 0.54 with base
        self.assertEqual(cons.box_level([base, base, shifted], [True] * 3), "L0")
        self.assertEqual(cons.box_level([base, base, [20, 10, 120, 110]], [True] * 3), "L1")  # IoU 0.82
        self.assertEqual(cons.box_level([base, base, far], [True] * 3), "L2")
        self.assertEqual(cons.box_level([base, base, base], [True, True, False]), "L2")  # visibility split, no vote on it
        self.assertEqual(cons.box_level([base, shifted], [True, True]), "L1")  # one call lost: no L0

    def test_worst_level_and_empty(self):
        self.assertEqual(cons.worst(["L0", "L2", "L1"]), "L2")
        self.assertEqual(cons.worst([]), "L2")

    def test_modal_counts_mixed_values(self):
        mode, count, counts = cons.modal([["ok", 1], ["ok", 1], ["NEEDS_CONFIRMATION", 2]])
        self.assertEqual((mode, count, len(counts)), (["ok", 1], 2, 2))


class BudgetTest(unittest.TestCase):
    def runner(self):
        return cons.Runner("secret-key", Path(tempfile.mkdtemp()) / "log.jsonl")

    def setUp(self):
        self._pause = cons.RETRY_PAUSE_S
        cons.RETRY_PAUSE_S = 0

    def tearDown(self):
        cons.RETRY_PAUSE_S = self._pause

    def test_one_retry_then_failed(self):
        runner, calls = self.runner(), []

        def failing():
            calls.append(1)
            raise ValueError("boom secret-key")
        result, status, attempts = runner.call("d", "u", 1, failing)
        self.assertEqual((result, status, len(calls), runner.used), (None, "failed", 2, 2))
        self.assertNotIn("secret-key", runner.log_path.read_text(encoding="utf-8"))
        self.assertNotIn("secret-key", str(attempts))

    def test_success_after_retry(self):
        runner, state = self.runner(), {"n": 0}

        def flaky():
            state["n"] += 1
            if state["n"] == 1:
                raise ValueError("once")
            return "answer"
        self.assertEqual(runner.call("d", "u", 1, flaky)[:2], ("answer", "ok"))
        self.assertEqual(runner.used, 2)

    def test_hard_cap_stops_calls(self):
        runner = self.runner()
        runner.used = cons.HARD_CAP
        called = []
        result, status, _ = runner.call("d", "u", 1, lambda: called.append(1))
        self.assertEqual((result, status, called), (None, "not_run", []))


if __name__ == "__main__":
    unittest.main()
