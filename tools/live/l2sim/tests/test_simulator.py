import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import episodes  # noqa: E402
import fakes  # noqa: E402
from episodes import Episode, Question  # noqa: E402
from metrics import accept, paired, summarize  # noqa: E402
from oracle import Oracle  # noqa: E402
from policies import P0Policy, ProposerPolicy, RandomValidPolicy, state_for_model  # noqa: E402
from run import CappedProposer  # noqa: E402
from session import TERMINAL  # noqa: E402
from simulator import INTEGRITY, simulate  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "full01"


def full01():
    return episodes.load_full01(FIXTURE)


def synthetic_set():
    return [episodes.synthetic(f, s) for f in episodes.FAMILIES for s in episodes.GENERATOR_SEEDS]


def assert_integrity(test, result):
    for name in INTEGRITY:
        test.assertEqual(result["counters"][name], 0, f"{name} in {result['episode']}")


class StatusProposer:
    """Returns the given statuses in order, then repeats the last one."""

    def __init__(self, *statuses):
        self.statuses, self.calls = list(statuses), 0

    def propose(self, task, payload, request_id):
        status = self.statuses[min(self.calls, len(self.statuses) - 1)]
        self.calls += 1
        output = payload["valid_actions"][0] if status == "ok" else None
        return fakes.provider_result(request_id, task, payload, status, output)


class EpisodeLoadingTest(unittest.TestCase):
    def test_full01_has_twelve_questions_with_owner_truth(self):
        loaded = full01()
        self.assertEqual([e.id for e in loaded], ["FULL-01-P1", "FULL-01-P2", "FULL-01-P3"])
        questions = {q.id: q for e in loaded for q in e.questions}
        self.assertEqual(len(questions), 12)
        self.assertEqual(questions["Q-P2-01"].truth, {"kind": "choice", "value": "E-0035"})
        self.assertEqual(questions["Q-P3-03"].truth["kind"], "not_a_part")
        self.assertEqual(questions["Q-P3-06"].kind, "destination_of_removal")
        self.assertEqual(questions["Q-P1-01"].candidates, ())
        p3 = loaded[2]
        self.assertEqual({p["part_ref"] for p in p3.committed_at_start}, {"P3-C03", "P3-C06"})
        self.assertTrue(all(a[1] < b[0] for a, b in zip(p3.busy, p3.busy[1:])), "busy intervals merged")
        self.assertTrue(all(len(h) == 64 for h in p3.provenance.values()))

    def test_synthetic_families_are_deterministic_and_adversarial(self):
        self.assertEqual(episodes.synthetic("stale_revisions", 1), episodes.synthetic("stale_revisions", 1))
        self.assertEqual(len(episodes.synthetic("stale_revisions", 1).revision_changes), 2)
        self.assertEqual(len(episodes.synthetic("simultaneous", 2).questions), 8)
        unanswerable = episodes.synthetic("unanswerable", 1).questions
        self.assertEqual([q.truth["kind"] for q in unanswerable[:2]], ["unanswerable", "unanswerable"])
        self.assertEqual(len(unanswerable[0].candidates), 0)
        resolvable = episodes.synthetic("resolvable", 2).questions
        self.assertEqual(sum(q.resolvable_from_t is not None for q in resolvable), 2)


class OracleTest(unittest.TestCase):
    def test_common_random_numbers_and_unanswerable(self):
        truths = {"Q1": {"kind": "declared"}, "Q2": {"kind": "unanswerable"}}
        a, b = Oracle(truths, 0, "E"), Oracle(truths, 0, "E")
        self.assertEqual(a.reply("Q1", 1, (), False, 1), b.reply("Q1", 1, ("x",), False, 1))
        self.assertTrue(all(a.reply("Q2", 1, (), False, n).unsure for n in range(1, 20)))


class P0Test(unittest.TestCase):
    def test_p0_on_full01_resolves_without_integrity_violations(self):
        for seed in (0, 1, 2):
            for episode in full01():
                result = simulate(episode, P0Policy(), seed)
                c = result["counters"]
                assert_integrity(self, result)
                self.assertEqual(c["invalid_proposals"], 0)
                self.assertEqual(c["executed_protocol_prompt"] + c["executed_ask_dependency"], 0)
                self.assertEqual(c["open_at_end"], 0)
                self.assertEqual(c["asks_while_busy"], 0)
                self.assertTrue(all(q["state"] in TERMINAL for q in result["questions"]))

    def test_p0_asks_only_after_segment_end_in_deposit_order(self):
        episode = full01()[1]
        result = simulate(episode, P0Policy(), 0)
        asks = [e for e in result["trace"] if e.get("executed_action", {}) and e["executed_action"]["action"] == "ask"]
        self.assertTrue(all(e["t"] >= episode.segment_end for e in asks))
        first_asks = []
        for e in asks:
            if e["executed_action"]["question_id"] not in first_asks:
                first_asks.append(e["executed_action"]["question_id"])
        self.assertEqual(first_asks, ["Q-P2-01", "Q-P2-02", "Q-P2-03"])

    def test_unanswerable_questions_end_left_unresolved(self):
        result = simulate(episodes.synthetic("unanswerable", 1), P0Policy(), 0)
        states = {q["id"]: q["state"] for q in result["questions"]}
        self.assertEqual(states["Q-S-01"], "LEFT_UNRESOLVED")
        self.assertEqual(states["Q-S-02"], "LEFT_UNRESOLVED")


class StaleRevisionTest(unittest.TestCase):
    def test_revision_change_while_asked_drops_the_pending_answer(self):
        q = Question("Q1", "source_of_part", "P-1", "cell-1", 10.0, ("E-1",), {"kind": "choice", "value": "E-1"})
        episode = Episode("STALE", "test", "S", (q,), busy=(), segment_end=12.0,
                          revision_changes=((15.0, "Q1", ("E-1", "E-2")),))
        result = simulate(episode, P0Policy(), 0)
        c = result["counters"]
        assert_integrity(self, result)
        self.assertEqual(c["superseded_answers"], 1)
        self.assertIn("stale_answer_dropped", [e.get("event") for e in result["trace"]])
        commits = [e for e in result["trace"] if e.get("event") == "commit"]
        self.assertTrue(all(e["revision"] == 2 for e in commits))


class ProposerArmTest(unittest.TestCase):
    def run_arm(self, proposer, episode_list, seeds=(0,)):
        policy = ProposerPolicy(proposer, "test")
        return policy, [simulate(e, policy, s) for s in seeds for e in episode_list]

    def test_good_fake_is_valid_and_clean(self):
        _, results = self.run_arm(fakes.make("good"), full01() + synthetic_set())
        summary = summarize(results)
        self.assertTrue(summary["integrity_ok"])
        self.assertEqual(summary["invalid_action_rate"], 0.0)
        self.assertGreater(summary["resolved_parts"], 0)

    def test_random_fake_stays_within_valid_actions(self):
        _, results = self.run_arm(fakes.make("random", seed=3), synthetic_set())
        summary = summarize(results)
        self.assertTrue(summary["integrity_ok"])
        self.assertEqual(summary["totals"]["invalid_proposals"], 0)

    def test_adversarial_fake_is_contained_by_validator_and_p0_fallback(self):
        _, results = self.run_arm(fakes.make("adversarial"), full01() + synthetic_set(), seeds=(0, 1))
        summary = summarize(results)
        self.assertTrue(summary["integrity_ok"])
        t = summary["totals"]
        self.assertGreater(t["forbidden_proposals"], 0)
        self.assertGreater(t["wrong_target_questions"], 0)
        self.assertGreater(summary["invalid_action_rate"], 0.5)
        for code in ("forbidden_action:request_view", "forbidden_action:commit", "stale_revision",
                     "unknown_question", "schema:not_an_object", "schema:unexpected_field:text"):
            self.assertIn(code, summary["validator_rejections"])
        rejected = [e for r in results for e in r["trace"]
                    if "turn" in e and e["validator_verdict"] != "valid"]
        self.assertTrue(rejected)
        for entry in rejected:
            self.assertIn("model_action", entry)
            self.assertIn(entry["executed_by"], ("p0_fallback", "none"))
            executed = entry["executed_action"]
            if executed is not None and executed["question_revision"] is not None:
                # True == 1 in Python, so the executed P0 action must carry a real int revision.
                self.assertIs(type(executed["question_revision"]), int)

    def test_timeouts_are_retried_within_the_episode_limit_then_fall_back(self):
        proposer = StatusProposer("timeout")
        policy, results = self.run_arm(proposer, [full01()[0]])
        c = results[0]["counters"]
        self.assertEqual(c["proposals"], 0)
        self.assertEqual(c["fallbacks_provider"], c["turns"])
        self.assertEqual(proposer.calls, c["turns"] + 3)
        assert_integrity(self, results[0])

    def test_budget_exhaustion_stops_calls_for_the_rest_of_the_run(self):
        capped = CappedProposer(StatusProposer("ok"), cap=2)
        policy, results = self.run_arm(capped, full01())
        self.assertEqual(capped.calls, 2)
        self.assertTrue(capped.exhausted)
        self.assertEqual(policy.stopped_status, "budget_exhausted")
        self.assertTrue(all(r["counters"]["open_at_end"] == 0 for r in results))

    def test_model_state_carries_no_operator_text_or_truth(self):
        seen = []

        class Recorder:
            def propose(self, task, payload, request_id):
                seen.append(payload)
                return fakes.GoodFake().propose(task, payload, request_id)
        self.run_arm(Recorder(), [full01()[2]])
        text = repr(seen)
        for forbidden in ("Taste", "Schraube", "Gummi", "truth", "E-0071", "not_a_part"):
            self.assertNotIn(forbidden, text)
        self.assertIn("label#1", text)
        self.assertEqual(set(seen[0]), {"state", "valid_actions"})


class PolicyComparisonTest(unittest.TestCase):
    def test_pr_is_seeded_and_metrics_pair_by_episode(self):
        eps = synthetic_set()
        a = [simulate(e, RandomValidPolicy(), 1) for e in eps]
        b = [simulate(e, RandomValidPolicy(), 1) for e in eps]
        self.assertEqual([r["counters"] for r in a], [r["counters"] for r in b])
        p0 = [simulate(e, P0Policy(), 1) for e in eps]
        tally = paired(p0, a)
        self.assertEqual(sum(tally.values()), len(eps))
        self.assertEqual(tally["unpaired"], 0)

    def test_acceptance_rules(self):
        def arm(rpi, invalid=0.0, fallback=0.0, ok=True):
            return {"resolved_per_interruption": rpi, "invalid_action_rate": invalid,
                    "provider_fallback_rate": fallback, "integrity_ok": ok}
        self.assertEqual(accept(arm(0.9), arm(0.8), arm(0.4), True)["verdict"], "accepted")
        self.assertEqual(accept(arm(0.8), arm(0.8), arm(0.4), True)["verdict"], "rejected")
        self.assertEqual(accept(arm(0.9, invalid=0.06), arm(0.8), arm(0.4), True)["verdict"], "rejected")
        self.assertEqual(accept(arm(0.9, ok=False), arm(0.8), arm(0.4), True)["verdict"], "rejected")
        self.assertEqual(accept(arm(0.9), arm(0.8), arm(0.4), False)["verdict"], "inconclusive")
        self.assertEqual(accept(arm(0.9, fallback=0.3), arm(0.8), arm(0.4), True)["verdict"], "inconclusive")

    def test_state_for_model_lists_only_open_work(self):
        from session import Session
        s = Session.start(full01()[0])
        self.assertEqual(state_for_model(s)["questions"], [])


if __name__ == "__main__":
    unittest.main()
