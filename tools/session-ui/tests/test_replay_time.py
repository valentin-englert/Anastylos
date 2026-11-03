"""Which time a replay is built around: storage-cell settle, fitting detector window, estimate or unknown (no OpenCV)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

UI_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UI_ROOT))

import replay_time  # noqa: E402

SHA = "ab" * 32


def settle(cells: dict, status: str = "ok", reason: str = "") -> dict:
    return {"schema": replay_time.SETTLE_SCHEMA, "video": {"sha256": SHA}, "reference": {"status": status, "reason": reason},
            "cells": {name: {"reference_ok": True, "occupied": True, "settled_s": 100.0, **values} for name, values in cells.items()}}


def decide(settle_doc, parts=None, pool=None, this="dec-0001", cell="cell-8", meaning="deposit_window_start", detector=35.4):
    def window(start):  # a number stands for a detector window of three seconds
        return None if start is None else (start, start + 3.0)
    return replay_time.decide(settle=settle_doc, cell=cell, meaning=meaning, detector_s=detector, base_text="Detector text.",
                              this_id=this, cell_parts={"dec-0001": window(detector)} if parts is None
                              else {k: window(v) for k, v in parts.items()}, pool=[window(v) for v in pool or []])


class DecideTest(unittest.TestCase):
    def test_a_single_part_takes_the_time_the_cell_settled(self):
        result = decide(settle({"cell-8": {"settled_s": 151.8}}))
        self.assertEqual((result["basis"], result["anchor_s"], result["settled_s"]), ("storage_cell_settle", 151.8, 151.8))
        self.assertIn("Time measured from the storage cell", result["text"])
        self.assertIn("recorded detector time 35.4 s is not used", result["text"])

    def test_several_parts_keep_their_own_window_when_it_fits_the_first_lasting_change(self):
        parts = {"dec-0004": 45.0, "dec-0005": 50.0, "dec-0006": 57.0}
        doc = settle({"cell-1": {"settled_s": 44.0}})
        mine = decide(doc, parts, [45.0, 50.0, 57.0], this="dec-0005", cell="cell-1")
        self.assertEqual((mine["basis"], mine["anchor_s"]), ("detector_fitting_cell", 50.0))
        self.assertIn("several parts share this cell", mine["text"])
        self.assertEqual(decide(doc, parts, [45.0, 50.0, 57.0], this="dec-0006", cell="cell-1")["anchor_s"], 57.0)

    def test_the_limit_is_the_first_lasting_change_not_the_half_filled_time(self):
        parts = {"dec-0004": 25.6, "dec-0005": 39.6, "dec-0006": 57.0}
        doc = settle({"cell-1": {"settled_s": 44.0, "first_s": 25.0}})  # three screws: half full only at 44 s, first one at 25 s
        for pid, expected in (("dec-0004", 25.6), ("dec-0005", 39.6), ("dec-0006", 57.0)):
            result = decide(doc, parts, [25.6, 39.6, 57.0], this=pid, cell="cell-1")
            self.assertEqual((result["basis"], result["anchor_s"]), ("detector_fitting_cell", expected))
        self.assertIn("25.0 s", result["text"])

    def test_a_window_that_starts_before_the_first_change_but_ends_after_it_is_shown_from_that_change(self):
        doc = settle({"cell-1": {"settled_s": 44.0, "first_s": 30.0}})
        result = replay_time.decide(settle=doc, cell="cell-1", meaning="deposit_window_start", detector_s=25.6, base_text="x",
                                    this_id="dec-0004", cell_parts={"dec-0004": (25.6, 35.2), "dec-0005": (39.6, 53.0)},
                                    pool=[(25.6, 35.2), (39.6, 53.0)])
        self.assertEqual((result["basis"], result["anchor_s"], result["window"]), ("detector_fitting_cell", 30.0, [25.6, 35.2]))
        later = replay_time.decide(settle=doc, cell="cell-1", meaning="deposit_window_start", detector_s=39.6, base_text="x",
                                   this_id="dec-0005", cell_parts={"dec-0004": (25.6, 35.2), "dec-0005": (39.6, 53.0)},
                                   pool=[(25.6, 35.2), (39.6, 53.0)])
        self.assertEqual(later["anchor_s"], 39.6)

    def test_a_part_whose_own_window_is_too_early_gets_the_earliest_unclaimed_fitting_window(self):
        parts = {"dec-0004": 25.6, "dec-0005": 45.0, "dec-0006": 57.0, "dec-0007": 63.2}
        doc = settle({"cell-1": {"settled_s": 44.0}})
        pool = [25.6, 45.0, 57.0, 63.2, 90.0]
        self.assertEqual(decide(doc, parts, pool, this="dec-0004", cell="cell-1")["anchor_s"], 90.0)
        self.assertEqual(decide(doc, parts, pool, this="dec-0005", cell="cell-1")["anchor_s"], 45.0)

    def test_no_fitting_window_means_the_moment_is_not_known(self):
        parts = {"dec-0004": 25.6, "dec-0005": 30.0}
        result = decide(settle({"cell-1": {"settled_s": 44.0}}), parts, [25.6, 30.0], this="dec-0004", cell="cell-1")
        self.assertEqual((result["basis"], result["anchor_s"]), ("unknown", None))
        self.assertIn("This part's moment is not known", result["message"])
        self.assertIn("44.0", result["message"])

    def test_a_window_that_ended_before_the_measured_change_does_not_fit(self):
        parts = {"dec-0004": 38.0, "dec-0005": 60.0}  # 38.0-41.0 ends before 44 s - 2 s
        result = decide(settle({"cell-1": {"settled_s": 44.0}}), parts, [38.0, 60.0], this="dec-0004", cell="cell-1")
        self.assertEqual((result["basis"], result["anchor_s"]), ("unknown", None))
        parts = {"dec-0004": 40.0, "dec-0005": 60.0}  # 40.0-43.0 ends within the sampling tolerance of the change
        result = decide(settle({"cell-1": {"settled_s": 44.0}}), parts, [40.0, 60.0], this="dec-0004", cell="cell-1")
        self.assertEqual(result["anchor_s"], 44.0)

    def test_a_cell_without_lasting_change_is_not_known(self):
        result = decide(settle({"cell-8": {"occupied": False, "settled_s": None}}))
        self.assertEqual((result["basis"], result["anchor_s"]), ("unknown", None))
        self.assertIn("shows no lasting change", result["message"])

    def test_without_a_usable_reference_the_detector_time_is_kept_and_called_an_estimate(self):
        for doc, reason in ((None, "have not been measured yet"), (settle({}, "no_reference", "the sheet is not empty at the start"),
                                                                  "the sheet is not empty at the start"),
                            (settle({"cell-8": {"reference_ok": False}}), "was not empty at the start")):
            with self.subTest(reason=reason):
                result = decide(doc)
                self.assertEqual((result["basis"], result["anchor_s"]), ("detector_estimate", 35.4))
                self.assertTrue(result["text"].startswith("Detector text."))
                self.assertIn("This time is a detector estimate", result["text"])
                self.assertIn(reason, result["text"])

    def test_no_cell_and_removal_times_are_estimates_too(self):
        doc = settle({"cell-8": {"settled_s": 151.8}})
        self.assertIn("no storage cell", decide(doc, cell=None)["text"])
        removal = decide(doc, meaning="removal_window_start")
        self.assertEqual((removal["basis"], removal["anchor_s"]), ("detector_estimate", 35.4))
        self.assertIn("removal window the operator chose", removal["text"])


class SettleFileTest(unittest.TestCase):
    def test_only_a_file_of_the_same_video_is_read(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp)
            path = replay_time.settle_path(session, SHA)
            self.assertEqual(path.name, f"{SHA[:12]}-v2.json")
            self.assertIsNone(replay_time.read_settle(session, SHA))
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(settle({})), encoding="utf-8")
            self.assertIsNotNone(replay_time.read_settle(session, SHA))
            path.write_text(json.dumps({**settle({}), "video": {"sha256": "0" * 64}}), encoding="utf-8")
            self.assertIsNone(replay_time.read_settle(session, SHA))
            path.write_text("{broken", encoding="utf-8")
            self.assertIsNone(replay_time.read_settle(session, SHA))


if __name__ == "__main__":
    unittest.main()
