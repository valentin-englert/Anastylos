import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from score_e3b import score  # noqa: E402

INFERENCE = {"relation": "source_to_cell", "inferred_by": "candidate_temporal_exclusive", "producer": "custody linker 1.7",
             "assumptions": ["one part per cell"]}


def linked(part, cell, window, sites):
    return {"part": part, "cell": cell, "status": "candidate_temporal_exclusive", "removal_event": "E-1",
            "removal_window_s": window, "sources": [{"site": s} for s in sites]}


def write_run(tmp, parts, events=None, links_inference=True):
    run = Path(tmp) / "run"
    (run / "pass2").mkdir(parents=True)
    (run / "parts.json").write_text(json.dumps({"parts": parts, "counts": {"questions": 1}}), encoding="utf-8")
    (run / "registry.json").write_text(json.dumps({"sources": [
        {"id": "SRC-1", "geometry": {"bbox_mm": [100, 100, 10, 10]}},
        {"id": "SRC-2", "geometry": {"bbox_mm": [200, 100, 10, 10]}}]}), encoding="utf-8")
    (run / "pass2" / "events.json").write_text(json.dumps(events or {"hand_visits": {}, "device": {"realignments": []}}), encoding="utf-8")
    memory = Path(tmp) / "memory.jsonl"
    memory.write_text("".join(json.dumps({"type": "tray_link", "id": f"link-{i:04d}", "basis": {"kind": "evidence"},
                                          **({"inference": INFERENCE} if links_inference else {})}) + "\n"
                              for i, p in enumerate(parts, 1) if p.get("removal_event")), encoding="utf-8")
    return run, memory


def write_actual(tmp, steps, segment="A", controls=None):
    path = Path(tmp) / "actual.json"
    path.write_text(json.dumps({"schema": "e3b-actual@1", "segment": segment, "steps": steps, "controls": controls or {}}), encoding="utf-8")
    return path


STEPS = [{"part": "S1", "source_point_mm": [105, 105], "removal_t_s": 12.0, "destination": "cell-4"},
         {"part": "S2", "source_point_mm": [205, 105], "removal_t_s": 30.0, "destination": "cell-7"}]


class ScoreE3BTest(unittest.TestCase):
    def test_correct_links_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, memory = write_run(tmp, [linked("A-C04", "cell-4", [10, 14], ["SRC-1"]), linked("A-C07", "cell-7", [28, 31], ["SRC-2"])])
            result = score(run, memory, write_actual(tmp, STEPS))
        self.assertEqual(result["counts"]["correct"], 2)
        self.assertEqual(result["outcome"], "STRONG PASS")

    def test_wrong_source_or_time_is_incorrect_and_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, memory = write_run(tmp, [linked("A-C04", "cell-4", [10, 14], ["SRC-2"])])
            result = score(run, memory, write_actual(tmp, STEPS))
        self.assertEqual(result["links"][0]["verdict"], "incorrect")
        self.assertEqual(result["outcome"], "FAIL")
        with tempfile.TemporaryDirectory() as tmp:
            run, memory = write_run(tmp, [linked("A-C04", "cell-4", [40, 44], ["SRC-1"])])
            self.assertEqual(score(run, memory, write_actual(tmp, STEPS))["outcome"], "FAIL")

    def test_link_into_a_cell_no_part_went_to_is_incorrect(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, memory = write_run(tmp, [linked("A-C09", "cell-9", [10, 14], ["SRC-1"])])
            result = score(run, memory, write_actual(tmp, STEPS))
        self.assertEqual(result["links"][0]["why"], "no true part went into this cell")

    def test_realignment_under_hands_makes_a_correct_link_unsupported(self):
        events = {"hand_visits": {"SRC-1": [[11.0, 13.0]]}, "device": {"realignments": [{"t_s": 12.0, "accepted": True}]}}
        with tempfile.TemporaryDirectory() as tmp:
            run, memory = write_run(tmp, [linked("A-C04", "cell-4", [10, 14], ["SRC-1"])], events)
            result = score(run, memory, write_actual(tmp, STEPS))
        self.assertEqual(result["links"][0]["verdict"], "correct")
        self.assertTrue(result["links"][0]["artefact_flags"])
        self.assertEqual(result["outcome"], "FAIL")

    def test_controls_and_missing_inference_fail(self):
        controls = {"pose_push": {"t_s": 9.0}}
        with tempfile.TemporaryDirectory() as tmp:
            run, memory = write_run(tmp, [linked("A-C04", "cell-4", [10, 14], ["SRC-1"])])
            self.assertTrue(score(run, memory, write_actual(tmp, STEPS, controls=controls))["control_violations"])
        with tempfile.TemporaryDirectory() as tmp:
            run, memory = write_run(tmp, [linked("A-C04", "cell-4", [10, 14], ["SRC-1"])], links_inference=False)
            self.assertEqual(score(run, memory, write_actual(tmp, STEPS))["outcome"], "FAIL")

    def test_no_links_is_not_pass_in_segment_a_and_pass_in_segment_b(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, memory = write_run(tmp, [{"part": "A-C04", "cell": "cell-4", "status": "ambiguous"}])
            self.assertEqual(score(run, memory, write_actual(tmp, STEPS))["outcome"], "NOT PASS")
            self.assertEqual(score(run, memory, write_actual(tmp, STEPS, segment="B"))["outcome"], "PASS")


if __name__ == "__main__":
    unittest.main()
