"""Episode replay: environment events in time order, policy turns, validation with P0 fallback, oracle answers,
read-back and simulated commits. Nothing here writes memory; a "commit" is a state change in the simulation.
"""

import heapq
from collections import Counter

import actions
from oracle import Oracle
from policies import p0_action
from session import (ASKED, COMMITTED, CONFIRMED, DEFERRED, IN_FLIGHT, LEFT_UNRESOLVED, OPEN, PENDING, READ_BACK,
                     RESOLVED_BY_LINKER, TERMINAL, Session)
from validator import WRONG_TARGET, valid_actions, validate

ANSWER_DELAY_S = 8.0
READBACK_S = 3.0
OPERATOR_COST_S = {"ask": ANSWER_DELAY_S + READBACK_S, "ask_dependency": 10.0, "protocol_prompt": 4.0,
                   "defer": 0.0, "leave_unresolved": 0.0}
BUSY_ASK_PENALTY_S = 6.0

# Same-timestamp order: pending answers settle first, then environment changes, then new questions, then the end.
_PRIORITY = {"answer": 0, "readback": 1, "revision": 2, "resolvable": 3, "busy_start": 4, "busy_end": 5,
             "open": 6, "segment_end": 7}

COUNTERS = (
    "turns", "proposals", "invalid_proposals", "forbidden_proposals", "wrong_target_questions",
    "fallbacks_invalid", "fallbacks_provider", "no_action", "proposer_calls",
    "executed_ask", "executed_defer", "executed_protocol_prompt", "executed_ask_dependency",
    "executed_leave_unresolved", "interruptions", "question_interruptions", "asks_while_busy",
    "unnecessary_questions", "operator_seconds", "resolved_by_operator", "resolved_by_linker", "wrong_commits",
    "left_unresolved", "open_at_end", "answers_unsure", "readback_rejections", "superseded_answers",
    "withdrawn_asks", "effect_unknown_protocol_prompt", "effect_unknown_ask_dependency",
    "memory_writes_by_model", "commits_without_readback", "stale_actions_executed", "stale_answers_committed",
)
INTEGRITY = ("memory_writes_by_model", "commits_without_readback", "stale_actions_executed",
             "stale_answers_committed")


def simulate(episode, policy, replication_seed):
    replay = _Replay(episode, policy, replication_seed)
    replay.play()
    return replay.result()


class _Replay:
    def __init__(self, episode, policy, replication_seed):
        self.episode = episode
        self.policy = policy
        self.seed = replication_seed
        self.s = Session.start(episode)
        self.oracle = Oracle({q.id: q.truth for q in episode.questions}, replication_seed, episode.id)
        self.c = Counter({name: 0 for name in COUNTERS})
        self.rejections = Counter()
        self.trace = []
        self._heap = []
        self._seq = 0
        self._tokens = 0
        policy.start_episode(episode.id, replication_seed)

    # -- events -------------------------------------------------------------------------------------------
    def _push(self, t, kind, payload=None):
        self._seq += 1
        heapq.heappush(self._heap, (t, _PRIORITY[kind], self._seq, kind, payload))

    def play(self):
        for q in self.episode.questions:
            self._push(q.open_t, "open", q.id)
            if q.resolvable_from_t is not None:
                self._push(q.resolvable_from_t, "resolvable", q.id)
        for start, end in self.episode.busy:
            self._push(start, "busy_start")
            self._push(end, "busy_end")
        for t, question_id, candidates in self.episode.revision_changes:
            self._push(t, "revision", (question_id, candidates))
        self._push(self.episode.segment_end, "segment_end")
        while self._heap:
            t, _, _, kind, payload = heapq.heappop(self._heap)
            self.s.now = t
            getattr(self, f"_on_{kind}")(payload)
            if self.s.closing:
                self._apply_linker()
            self._turns()
        self.c["open_at_end"] = sum(1 for qs in self.s.questions.values() if qs.state not in TERMINAL)

    def _log(self, event, **fields):
        self.trace.append({"t": round(self.s.now, 3), "event": event, **fields})

    def _on_open(self, question_id):
        self.s.questions[question_id].state = OPEN
        self._log("question_open", question_id=question_id)

    def _on_resolvable(self, question_id):
        self.s.questions[question_id].resolvable = True

    def _on_busy_start(self, _):
        self.s.busy = True

    def _on_busy_end(self, _):
        self.s.busy = False
        for qs in self.s.questions.values():
            if qs.state == DEFERRED and qs.defer_until == "next_pause" and qs.deferred_at < self.s.now:
                self._wake(qs)

    def _on_segment_end(self, _):
        self.s.closing = True
        for qs in self.s.questions.values():
            if qs.state == DEFERRED:
                self._wake(qs)
        self._log("segment_end")

    def _wake(self, qs):
        qs.state, qs.defer_until, qs.deferred_at = OPEN, None, None

    def _on_revision(self, payload):
        question_id, candidates = payload
        qs = self.s.questions[question_id]
        qs.revision += 1
        qs.candidates = tuple(candidates)
        if qs.state in IN_FLIGHT:
            # The pending answer belongs to the old revision: it is dropped, never committed.
            qs.ask_token, qs.reply, qs.state, qs.last_outcome = None, None, OPEN, "superseded"
            self.c["superseded_answers"] += 1
        self._log("revision", question_id=question_id, revision=qs.revision)

    def _on_answer(self, payload):
        question_id, token = payload
        qs = self.s.questions[question_id]
        if qs.ask_token != token:
            self._log("stale_answer_dropped", question_id=question_id)
            return
        if qs.reply.unsure:
            qs.state, qs.last_outcome, qs.ask_token = OPEN, "unsure", None
            self.c["answers_unsure"] += 1
            self._log("answer", question_id=question_id, outcome="unsure")
            return
        qs.state = READ_BACK
        self._push(self.s.now + READBACK_S, "readback", (question_id, token))
        self._log("answer", question_id=question_id, outcome="answered")

    def _on_readback(self, payload):
        question_id, token = payload
        qs = self.s.questions[question_id]
        if qs.ask_token != token:
            self._log("stale_answer_dropped", question_id=question_id)
            return
        if not qs.reply.correct and qs.reply.caught_at_readback:
            qs.state, qs.last_outcome, qs.ask_token = OPEN, "readback_rejected", None
            self.c["readback_rejections"] += 1
            self._log("readback", question_id=question_id, outcome="rejected")
            return
        qs.state = CONFIRMED
        self._commit(qs, actor="coordinator")

    def _commit(self, qs, actor):
        if actor != "coordinator":
            self.c["memory_writes_by_model"] += 1
            return
        if qs.state != CONFIRMED:
            self.c["commits_without_readback"] += 1
            return
        if qs.asked_revision != qs.revision:
            self.c["stale_answers_committed"] += 1
            return
        q, correct = qs.question, qs.reply.correct
        qs.state, qs.ask_token = COMMITTED, None
        self.c["resolved_by_operator" if correct else "wrong_commits"] += 1
        if q.kind == "source_of_part" and q.truth["kind"] in ("choice", "declared"):
            basis = "operator_choice" if q.truth["kind"] == "choice" else "operator_declared"
            self.s.add_committed_part(q.target, basis, q.cell, round(self.s.now, 3))
        self._log("commit", question_id=q.id, revision=qs.revision, correct=correct)

    def _apply_linker(self):
        for qs in self.s.questions.values():
            if qs.resolvable and qs.state not in TERMINAL and qs.state not in IN_FLIGHT and qs.state != PENDING:
                qs.state = RESOLVED_BY_LINKER
                self.c["resolved_by_linker"] += 1
                self._log("resolved_by_linker", question_id=qs.question.id)

    # -- turns --------------------------------------------------------------------------------------------
    def _turns(self):
        while self.s.needs_decision() and self.s.turns < self.s.turn_limit:
            if self.s.closing and self.s.in_flight() is not None:
                return
            valid = valid_actions(self.s)
            if not valid:
                return
            self.s.turns += 1
            self.c["turns"] += 1
            executed = self._arbitrate(self.policy.decide(self.s, valid))
            if executed is None:
                return
            self._execute(executed)

    def _arbitrate(self, decision):
        self.c["proposer_calls"] += decision.calls
        entry = {"t": round(self.s.now, 3), "turn": self.s.turns, "provider_status": decision.status,
                 "model_action": decision.proposal}
        if decision.status != "ok":
            self.c["fallbacks_provider"] += 1
            entry["validator_verdict"] = f"provider_status:{decision.status}"
            executed, executed_by = p0_action(self.s), "p0_fallback"
        else:
            self.c["proposals"] += 1
            reasons = validate(decision.proposal, self.s)
            entry["validator_verdict"] = reasons or "valid"
            if reasons:
                self._count_rejection(decision.proposal, reasons)
                self.c["fallbacks_invalid"] += 1
                executed, executed_by = p0_action(self.s), "p0_fallback"
            else:
                executed, executed_by = decision.proposal, "policy"
        if executed is None:
            self.c["no_action"] += 1
            executed_by = "none"
        entry["executed_action"] = executed
        entry["executed_by"] = executed_by
        self.trace.append(entry)
        return executed

    def _count_rejection(self, proposal, reasons):
        self.c["invalid_proposals"] += 1
        self.rejections.update(reasons)
        if any(r.startswith("forbidden_action:") for r in reasons):
            self.c["forbidden_proposals"] += 1
        if (isinstance(proposal, dict) and proposal.get("action") in actions.QUESTION_ACTIONS
                and any(r in WRONG_TARGET for r in reasons)):
            self.c["wrong_target_questions"] += 1

    def _execute(self, proposal):
        kind = proposal["action"]
        self.c[f"executed_{kind}"] += 1
        self.c["operator_seconds"] += OPERATOR_COST_S[kind]
        if kind in actions.INTERRUPTIONS:
            self.c["interruptions"] += 1
        if kind in actions.QUESTION_ACTIONS:
            qs = self.s.questions[proposal["question_id"]]
            if proposal["question_revision"] != qs.revision:
                self.c["stale_actions_executed"] += 1
        outcome = getattr(self, f"_do_{kind}")(proposal)
        record = {k: v for k, v in proposal.items() if v is not None}
        self.s.history.append({**record, "t": round(self.s.now, 3), "outcome": outcome})

    def _do_ask(self, proposal):
        qs = self.s.questions[proposal["question_id"]]
        qs.attempts += 1
        self.s.question_budget -= 1
        self._tokens += 1
        qs.state, qs.ask_token, qs.asked_revision, qs.asked_busy = ASKED, self._tokens, qs.revision, self.s.busy
        qs.defer_until = qs.deferred_at = None
        qs.reply = self.oracle.reply(qs.question.id, qs.revision, qs.candidates, self.s.busy, qs.attempts)
        self.c["question_interruptions"] += 1
        if self.s.busy:
            self.c["asks_while_busy"] += 1
            self.c["operator_seconds"] += BUSY_ASK_PENALTY_S
        if qs.resolvable:
            self.c["unnecessary_questions"] += 1
        self._push(self.s.now + ANSWER_DELAY_S, "answer", (qs.question.id, qs.ask_token))
        return "asked"

    def _do_defer(self, proposal):
        qs = self.s.questions[proposal["question_id"]]
        if qs.state == ASKED:
            qs.ask_token, qs.reply, qs.last_outcome = None, None, "withdrawn"
            self.c["withdrawn_asks"] += 1
        qs.state, qs.defer_until, qs.deferred_at = DEFERRED, proposal["until"], self.s.now
        return "deferred"

    def _do_protocol_prompt(self, proposal):
        self.s.last_prompt_t = self.s.now
        self.s.prompts_left -= 1
        self.c["effect_unknown_protocol_prompt"] += 1
        return "effect_unknown"

    def _do_ask_dependency(self, proposal):
        self.s.dependency_asked.add(proposal["part_ref"])
        self.c["effect_unknown_ask_dependency"] += 1
        return "effect_unknown_no_ground_truth"

    def _do_leave_unresolved(self, proposal):
        self.s.questions[proposal["question_id"]].state = LEFT_UNRESOLVED
        self.c["left_unresolved"] += 1
        return "left_unresolved"

    def result(self):
        return {
            "episode": self.episode.id,
            "source": self.episode.source,
            "replication_seed": self.seed,
            "arm": self.policy.name,
            "counters": dict(self.c),
            "validator_rejections": dict(sorted(self.rejections.items())),
            "questions": [{"id": qs.question.id, "state": qs.state, "revision": qs.revision,
                           "attempts": qs.attempts} for qs in self.s.deposit_order()],
            "trace": self.trace,
        }
