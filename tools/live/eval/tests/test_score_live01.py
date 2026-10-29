import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from synthetic import BAD_TEXT, Take, box_around, ts

import score_live01
from h3_rubric import rubric


class ScoreCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def take(self, name="take") -> Take:
        return Take(self.tmp / name)

    @staticmethod
    def flagged(result, name):
        return result["contamination"][name]["count"]

    @staticmethod
    def counted(result, name):
        return result["integrity"][name]["count"]


class BaselineMetrics(ScoreCase):
    def test_arms_rates_and_verdicts(self):
        result = self.take().baseline().score()
        arms = result["arms"]
        self.assertEqual(arms["live_L0"]["correct_parts"], ["P1"])
        self.assertEqual(arms["live_L1"]["correct_parts"], ["P3"])
        self.assertAlmostEqual(arms["live_L0"]["operator_minutes"], 8 / 60)
        self.assertAlmostEqual(arms["live_L0"]["resolved_parts_per_minute"], 7.5)
        self.assertAlmostEqual(arms["offline"]["operator_minutes"], 2.0)  # whole offline round, 720 -> 840 s
        self.assertAlmostEqual(arms["offline_by_episodes"]["operator_minutes"], (35 + 25) / 60)
        self.assertEqual(arms["offline"]["accuracy"], 0.5)
        self.assertEqual(arms["offline"]["wrong_declarations"], 1)
        self.assertEqual(arms["live_L1"]["median_answer_s"], 3.0)
        self.assertEqual(arms["live_L0"]["median_deposit_to_answer_s"], 15.0)
        self.assertEqual(arms["live"]["h3_pass_rate"], 1.0)
        self.assertEqual(result["safety"]["verdict"], "PASS")
        self.assertEqual(result["live_timing"]["verdict"], "ACCEPT")
        self.assertEqual(result["l1_wording"]["verdict"], "ADOPT")
        self.assertTrue(all(v["count"] == 0 for v in result["contamination"].values()))
        self.assertTrue(all(v["count"] == 0 for k, v in result["integrity"].items() if isinstance(v, dict)))

    def test_cli_writes_score(self):
        take = self.take().baseline()
        paths = take.write()
        out = self.tmp / "score.json"
        cmd = [sys.executable, str(score_live01.HERE / "score_live01.py"), "score", "--memory", str(paths["memory_path"]),
               "--die", str(paths["die_path"]), "--actual", str(paths["actual_path"]), "--out", str(out)]
        for journal in paths["journal_paths"]:
            cmd += ["--journal", str(journal)]
        done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["schema"], "live01-score@1")


class Correctness(ScoreCase):
    def test_false_alarm_dismissal_and_wrong_declaration(self):
        take = self.take().baseline()
        take.ask("live", "Q5", 5, "cell-5", 90, 100, 104, 106, {"outcome": "no_part"}, commit=False)
        take.ask("live", "Q7", 5, "cell-6", 95, 105, 107, 109, {"outcome": "source", "source_box_mm": [0, 0, 5, 5]},
                 wording="L1", extra={"slot": 5})
        rows = {r["question_id"]: r for r in take.score()["questions"]}
        self.assertEqual(rows["Q5"]["verdict"], "correct_dismissal")
        self.assertTrue(rows["Q5"]["false_alarm"])
        self.assertEqual(rows["Q7"]["verdict"], "wrong")

    def test_false_alarm_rate_above_limit_fails_safety(self):
        take = self.take().baseline()
        take.ask("live", "Q5", 5, "cell-5", 90, 100, 104, 106, {"outcome": "no_part"}, commit=False)
        result = take.score()
        self.assertAlmostEqual(result["arms"]["live"]["false_alarm_rate"], 1 / 3)
        self.assertEqual(result["safety"]["verdict"], "FAIL")
        self.assertEqual(result["live_timing"]["verdict"], "SAFETY_FAIL")

    def test_wrong_dismissal(self):
        take = self.take()
        take.start("live", 0)
        take.ask("live", "Q1", 1, "cell-1", 13, 23, 28, 31, {"outcome": "no_part"}, commit=False)
        take.done("live", 60)
        row = take.score()["questions"][0]
        self.assertEqual(row["verdict"], "wrong_dismissal")
        self.assertEqual(row["true_parts"], ["P1"])

    def test_uncommitted_declaration_is_unresolved(self):
        take = self.take()
        take.start("live", 0)
        take.ask("live", "Q1", 1, "cell-1", 13, 23, 28, 31, {"outcome": "source", "source_box_mm": box_around([100, 100])},
                 commit=False)
        take.done("live", 60)
        self.assertEqual(take.score()["questions"][0]["verdict"], "unresolved")

    def test_destination_question(self):
        take = self.take()
        take.start("live", 0)
        extra = {"removal_window_s": [29, 31], "source_box_mm": box_around([150, 100])}
        take.ask("live", "QD", 1, None, 33, 43, 45, 47, {"outcome": "destination", "destination": "cell-2"},
                 kind="destination_of_removal", extra=extra)
        take.ask("live", "QE", 3, None, 53, 63, 65, 67, {"outcome": "destination", "destination": "cell-9"},
                 kind="destination_of_removal", wording="L1",
                 extra={"removal_window_s": [49, 51], "source_box_mm": box_around([200, 100])})
        take.done("live", 90)
        rows = {r["question_id"]: r for r in take.score()["questions"]}
        self.assertEqual((rows["QD"]["verdict"], rows["QD"]["true_part"]), ("correct", "P2"))
        self.assertEqual(rows["QE"]["verdict"], "wrong")

    def test_free_words_need_adjudication_then_count(self):
        take = self.take()
        take.start("live", 0)
        op_key = take.ask("live", "Q1", 1, "cell-1", 13, 23, 28, 31, {"outcome": "source", "place_text": "links oben"})
        take.done("live", 60)
        result = take.score()
        self.assertEqual(result["questions"][0]["verdict"], "needs_adjudication")
        self.assertEqual(result["live_timing"]["verdict"], "INCONCLUSIVE")
        take2 = self.take("adjudicated")
        take2.start("live", 0)
        take2.ask("live", "Q1", 1, "cell-1", 13, 23, 28, 31, {"outcome": "source", "place_text": "links oben"})
        take2.done("live", 60)
        take2.actual["adjudications"] = [{"op_key": op_key, "verdict": "correct", "part": "P1"}]
        row = take2.score()["questions"][0]
        self.assertEqual((row["verdict"], row["true_part"], row["adjudicated"]), ("correct", "P1", True))


class AcceptReject(ScoreCase):
    def test_reject_when_live_accuracy_lower(self):
        take = self.take()
        take.start("live", 0)
        take.ask("live", "Q1", 1, "cell-1", 13, 23, 28, 31, {"outcome": "source", "source_box_mm": [0, 0, 5, 5]})
        take.done("live", 120)
        take.start("offline", 720)
        take.ask("offline", "Q2", 2, "cell-2", 33, 730, 760, 765, {"outcome": "source", "source_box_mm": box_around([150, 100])})
        take.done("offline", 840)
        self.assertEqual(take.score()["live_timing"]["verdict"], "REJECT")

    def test_reject_when_live_time_per_part_more_than_twice_offline(self):
        take = self.take()
        take.start("live", 0)
        take.ask("live", "Q1", 1, "cell-1", 13, 23, 28, 400, {"outcome": "source", "source_box_mm": box_around([100, 100])})
        take.done("live", 420)
        take.start("offline", 1020)
        take.ask("offline", "Q2", 2, "cell-2", 33, 1030, 1040, 1045, {"outcome": "source", "source_box_mm": box_around([150, 100])})
        take.done("offline", 1080)  # 1 min for 1 part; live 377 s for 1 part
        result = take.score()
        self.assertEqual(result["live_timing"]["verdict"], "REJECT")
        self.assertIn("twice", result["live_timing"]["why"][0])

    def test_l1_not_adopted_when_it_declares_wrongly_more_often(self):
        take = self.take().baseline()
        take.journals["live"] = []
        take.start("live", 0)
        take.ask("live", "Q1", 1, "cell-1", 13, 23, 28, 31, {"outcome": "source", "source_box_mm": box_around([100, 100])})
        take.ask("live", "Q3", 3, "cell-3", 53, 63, 64, 66, {"outcome": "source", "source_box_mm": [0, 0, 5, 5]}, wording="L1")
        take.done("live", 120)
        self.assertEqual(take.score()["l1_wording"]["verdict"], "NOT_ADOPTED")


class Contamination(ScoreCase):
    def test_part_asked_twice_across_rounds_is_flagged(self):
        take = self.take().baseline()
        done = take.journals["offline"].pop()
        take.ask("offline", "Q6", 6, "cell-1", 13, 800, 805, 808, {"outcome": "source", "source_box_mm": box_around([100, 100])})
        take.journals["offline"].append(done)
        result = take.score()
        self.assertIn({"part": "P1", "questions": ["Q1", "Q6"]}, result["contamination"]["part_asked_more_than_once"]["items"])
        self.assertEqual(result["live_timing"]["verdict"], "INCONCLUSIVE")

    def test_same_question_asked_in_both_rounds_is_flagged(self):
        take = self.take().baseline()
        take.rec("offline", "question_state", 830, op_key="LS1:Q1:1", question_id="Q1", revision=1, state="ASKED",
                 wording="L0", text="x")
        result = take.score()
        self.assertTrue(any(i.get("question") == "Q1" for i in result["contamination"]["part_asked_more_than_once"]["items"]))

    def test_slot_asked_in_the_wrong_arm(self):
        take = self.take().baseline()
        take.die["slots"]["3"] = "offline"
        result = take.score()
        self.assertEqual(self.flagged(result, "arm_mismatch"), 1)
        self.assertEqual(result["live_timing"]["verdict"], "INCONCLUSIVE")

    def test_wording_mismatch_unless_journaled_fallback(self):
        take = self.take()
        take.start("live", 0)
        take.ask("live", "Q1", 1, "cell-1", 13, 23, 28, 31, {"outcome": "source", "source_box_mm": box_around([100, 100])})
        take.ask("live", "Q3", 3, "cell-3", 53, 63, 66, 68, {"outcome": "source", "source_box_mm": box_around([200, 100])})
        take.done("live", 120)
        self.assertEqual(self.flagged(take.score(), "wording_mismatch"), 1)
        take.journals["live"].insert(-1, {"schema": "anastylos-live-journal@1", "kind": "fallback", "t_wall": ts(62),
                                          "op_key": "LS1:Q3:1", "task": "question_wording", "reason": "provider_status:timeout"})
        take.actual.pop("answers_sha256")
        result = take.score()
        self.assertEqual(self.flagged(result, "wording_mismatch"), 0)
        self.assertEqual(result["providers"]["fallbacks"], {"provider_status:timeout": 1})

    def test_delay_parity_violation_blocks_only_the_wording_verdict(self):
        take = self.take()
        take.start("live", 0)
        take.ask("live", "Q1", 1, "cell-1", 13, 23, 28, 31, {"outcome": "source", "source_box_mm": box_around([100, 100])})
        take.ask("live", "Q3", 3, "cell-3", 53, 67, 70, 72, {"outcome": "source", "source_box_mm": box_around([200, 100])},
                 wording="L1")  # shown 14 s after the deposit instead of D = 10 s
        take.done("live", 120)
        take.start("offline", 720)
        take.ask("offline", "Q2", 2, "cell-2", 33, 730, 760, 765, {"outcome": "source", "source_box_mm": box_around([150, 100])})
        take.done("offline", 840)
        result = take.score()
        self.assertEqual(result["contamination"]["delay_parity_violations"]["items"][0]["question"], "Q3")
        self.assertEqual(result["l1_wording"]["verdict"], "INCONCLUSIVE")
        self.assertEqual(result["live_timing"]["verdict"], "ACCEPT")

    def test_offline_round_started_at_the_wrong_time(self):
        take = self.take().baseline()
        take.die["offline_delay_s"] = 1200
        self.assertEqual(self.flagged(take.score(), "offline_delay_violation"), 1)

    def test_journal_written_against_another_die(self):
        take = self.take().baseline()
        take.die_sha_override = "f" * 64
        self.assertEqual(self.flagged(take.score(), "die_hash_mismatch"), 2)

    def test_answers_changed_after_sealing(self):
        take = self.take().baseline()
        paths = take.write()
        with paths["memory_path"].open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": "dec-9999", "type": "declared_removal"}) + "\n")
        result = score_live01.score(**paths)
        self.assertEqual(self.flagged(result, "answers_not_sealed_before_truth"), 1)
        self.assertEqual(result["live_timing"]["verdict"], "INCONCLUSIVE")


class Integrity(ScoreCase):
    def one_live_question(self, **kwargs) -> Take:
        take = self.take()
        take.start("live", 0)
        take.ask("live", "Q1", 1, "cell-1", 13, 23, 28, 31, {"outcome": "source", "source_box_mm": box_around([100, 100])},
                 **kwargs)
        take.done("live", 60)
        return take

    def test_declaration_promoted_to_evidence(self):
        take = self.take()
        take.memory.append({"id": "obs-0001", "type": "observation"})
        take.memory.append({"id": "lnk-0001", "type": "tray_link", "basis": {"kind": "evidence"}})
        take.start("live", 0)
        take.ask("live", "Q1", 1, "cell-1", 13, 23, 28, 31, {"outcome": "source", "source_box_mm": box_around([100, 100])},
                 written=["obs-0001", "lnk-0001"])
        take.done("live", 60)
        result = take.score()
        self.assertEqual(result["integrity"]["declarations_promoted_to_evidence"]["items"], ["obs-0001", "lnk-0001"])
        self.assertEqual(result["safety"]["verdict"], "FAIL")

    def test_human_tray_link_is_not_a_promotion(self):
        take = self.take()
        take.memory.append({"id": "lnk-0001", "type": "tray_link", "basis": {"kind": "human", "confirmed_by": "owner"}})
        take.start("live", 0)
        take.ask("live", "Q1", 1, "cell-1", 13, 23, 28, 31, {"outcome": "source", "source_box_mm": box_around([100, 100])},
                 written=["lnk-0001"])
        take.done("live", 60)
        self.assertEqual(self.counted(take.score(), "declarations_promoted_to_evidence"), 0)

    def test_commit_without_read_back(self):
        result = self.one_live_question(read_back=False).score()
        self.assertEqual(result["integrity"]["commits_without_read_back"]["items"], ["LS1:Q1:1"])
        self.assertEqual(result["safety"]["verdict"], "FAIL")

    def test_commit_without_intent(self):
        self.assertEqual(self.counted(self.one_live_question(intent=False).score(), "commits_without_intent"), 1)

    def test_stale_commit_after_a_newer_revision(self):
        take = self.take()
        take.start("live", 0)
        take.ask("live", "Q1", 1, "cell-1", 13, 23, 28, 31, {"outcome": "source", "source_box_mm": box_around([100, 100])},
                 commit=False)
        take.rec("live", "question_state", 32, op_key="LS1:Q1:2", question_id="Q1", revision=2, state="OPEN")
        take.rec("live", "intent", 33, op_key="LS1:Q1:1", planned=[])
        take.rec("live", "memory_result", 34, op_key="LS1:Q1:1", status="ok", written_ids=take.declaration())
        take.done("live", 60)
        result = take.score()
        self.assertEqual(result["integrity"]["stale_commits"]["items"], ["LS1:Q1:1"])
        self.assertEqual(result["safety"]["verdict"], "FAIL")

    def test_duplicate_commit(self):
        take = self.one_live_question()
        done = take.journals["live"].pop()
        take.rec("live", "memory_result", 40, op_key="LS1:Q1:1", status="ok", written_ids=take.declaration())
        take.journals["live"].append(done)
        result = take.score()
        self.assertEqual(result["integrity"]["duplicate_commits"]["items"], ["LS1:Q1:1"])
        self.assertEqual(result["safety"]["verdict"], "FAIL")

    def test_scrubbing_counts_in_live_arm_only(self):
        take = self.one_live_question()
        take.rec("live", "ui_event", 50, action="scrub")
        take.start("offline", 660)
        take.rec("offline", "ui_event", 670, action="scrub")
        take.done("offline", 700)
        take.actual["scrubbing_live"] = [{"t_s": 40.0, "note": "witness: finger on the timeline"}]
        result = take.score()
        self.assertEqual(self.counted(result, "scrubbing_live"), 2)
        self.assertIn("scrubbing_live = 2", result["safety"]["why"])

    def test_memory_and_journal_disagree(self):
        take = self.one_live_question(written=["dec-0404"])
        take.memory.append({"id": "dec-0777", "type": "declared_removal"})
        take.memory.append({"id": "dec-0777", "type": "declared_removal"})
        result = take.score()
        self.assertEqual(result["integrity"]["commits_missing_from_memory"]["items"], ["dec-0404"])
        self.assertEqual(result["integrity"]["unjournaled_declarations"]["items"], ["dec-0777", "dec-0777"])
        self.assertEqual(result["integrity"]["duplicate_memory_ids"]["items"], ["dec-0777"])

    def test_torn_last_line_is_counted_torn_middle_line_fails(self):
        take = self.one_live_question()
        paths = take.write()
        live = paths["journal_paths"][0]
        live.write_text(live.read_text(encoding="utf-8") + '{"schema": "anastylos-live-jou', encoding="utf-8")
        paths["actual_path"].write_text(json.dumps({**take.actual, "answers_sha256": score_live01.answers_sha256(
            [live, paths["memory_path"]])}), encoding="utf-8")
        self.assertEqual(score_live01.score(**paths)["integrity"]["journal_torn_lines"], 1)
        live.write_text('{"broken\n' + live.read_text(encoding="utf-8"), encoding="utf-8")
        with self.assertRaises(SystemExit):
            score_live01.score(**paths)


class Providers(ScoreCase):
    def test_latency_and_grounding(self):
        take = self.take().baseline()
        for i, latency in enumerate([4.0, 5.0, 6.0, 30.0]):
            take.rec("live", "provider_call", 1 + i, op_key="LS1:Q3:1", task="question_wording", status="ok",
                     latency_s=latency, cache_hit=False, request_id=f"r{i}")
        take.rec("live", "provider_call", 9, task="question_wording", status="timeout", latency_s=120.0, cache_hit=False)
        take.rec("live", "fallback", 10, op_key="LS1:Q9:1", task="question_wording", reason="grounding: unknown cell")
        result = take.score()["providers"]
        self.assertEqual(result["question_wording_latency_s"]["p95"], 30.0)
        self.assertEqual(result["question_wording_latency_s"]["median"], 5.5)
        self.assertEqual(result["calls"]["question_wording:timeout"], 1)
        self.assertEqual(result["grounding_rejections"], 1)


class Autonomy(ScoreCase):
    def run_dir(self, name, parts):
        run = self.tmp / name
        run.mkdir()
        (run / "parts.json").write_text(json.dumps({"parts": parts}), encoding="utf-8")
        (run / "registry.json").write_text(json.dumps({"sources": [
            {"id": "SRC-1", "geometry": {"bbox_mm": box_around([100, 100])}},
            {"id": "SRC-2", "geometry": {"bbox_mm": box_around([150, 100])}}]}), encoding="utf-8")
        return run

    @staticmethod
    def part(cell, site, autonomy, window):
        return {"part": cell, "cell": cell, "status": "candidate_temporal_exclusive", "autonomy": autonomy,
                "removal_window_s": window, "sources": [{"site": site}]}

    def test_automatic_and_assisted_counted_apart(self):
        auto = self.run_dir("auto", [self.part("cell-1", "SRC-1", "automatic", [9, 11]),
                                     {"part": "x", "status": "unattributed"}])
        assisted = self.run_dir("assisted", [self.part("cell-1", "SRC-1", "automatic", [9, 11]),
                                             self.part("cell-2", "SRC-2", "operator_assisted", [29, 31]),
                                             self.part("cell-3", "SRC-1", "operator_assisted", [49, 51])])
        result = self.take().baseline().score(run_automatic=auto, run_assisted=assisted)
        self.assertEqual(result["autonomy"]["automatic"]["automatic"], {"links": 1, "correct": 1, "incorrect": 0})
        self.assertEqual(result["autonomy"]["automatic"]["operator_assisted"]["links"], 0)
        self.assertEqual(result["autonomy"]["assisted"]["operator_assisted"], {"links": 2, "correct": 1, "incorrect": 1})
        self.assertEqual(result["live_timing"]["verdict"], "ACCEPT")

    def test_declarations_in_the_automatic_run_block_the_verdict(self):
        auto = self.run_dir("auto", [self.part("cell-2", "SRC-2", "operator_assisted", [29, 31])])
        result = self.take().baseline().score(run_automatic=auto)
        self.assertEqual(result["live_timing"]["verdict"], "INCONCLUSIVE")


class Rubric(ScoreCase):
    def test_machine_style_text_fails(self):
        checks = rubric(BAD_TEXT, anchor_possible=True, has_anchor=False)
        for name in ("R1_no_mm", "R2_phase_in_words", "R3_anchor_when_possible", "R4_time_only_supporting",
                     "R6_no_machine_ids"):
            self.assertFalse(checks[name], name)
        self.assertFalse(checks["pass"])

    def test_rubric_rate_per_arm(self):
        take = self.take().baseline()
        take.journals["live"] = []
        take.start("live", 0)
        take.ask("live", "Q1", 1, "cell-1", 13, 23, 28, 31, {"outcome": "source", "source_box_mm": box_around([100, 100])},
                 text=BAD_TEXT)
        take.ask("live", "Q3", 3, "cell-3", 53, 63, 66, 68, {"outcome": "source", "source_box_mm": box_around([200, 100])},
                 wording="L1")
        take.done("live", 120)
        arms = take.score()["arms"]
        self.assertEqual((arms["live_L0"]["h3_pass_rate"], arms["live_L1"]["h3_pass_rate"]), (0.0, 1.0))


if __name__ == "__main__":
    unittest.main()
