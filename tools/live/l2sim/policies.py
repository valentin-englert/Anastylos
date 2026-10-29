"""Arms P0 (deterministic), PR (random-valid) and L2 (injected Proposer, CONTRACT §3.1). PREREG §2.

A policy only proposes. The simulator validates every proposal and executes P0's action instead of an invalid one.
"""

import hashlib
import json
import random
from dataclasses import dataclass

import actions
from session import MAX_ATTEMPTS, PENDING, TERMINAL
from validator import validate

HISTORY_K = 5
RETRYABLE = ("timeout", "provider_error")
STOPPING = ("disabled", "budget_exhausted")


@dataclass
class Decision:
    proposal: object = None       # None when the policy produced nothing (non-ok provider status)
    status: str = "ok"            # provider status for L2; "ok" for P0/PR
    calls: int = 0


def p0_action(session):
    """Defer to segment end while the segment runs; at segment end ask in deposit order, leaving when allowed."""
    for qs in session.needs_decision():
        ref = {"question_id": qs.question.id, "question_revision": qs.revision}
        if not session.closing:
            preferences = [actions.make("defer", until="segment_end", **ref)]
        elif not qs.candidates and qs.last_outcome == "unsure":
            preferences = [actions.make("leave_unresolved", reason="no_candidates_and_operator_unsure", **ref)]
        elif qs.attempts >= MAX_ATTEMPTS or session.question_budget <= 0:
            preferences = [actions.make("leave_unresolved", reason="budget", **ref)]
        else:
            preferences = [actions.make("ask", **ref)]
        for proposal in preferences:
            if not validate(proposal, session):
                return proposal
    return None


class P0Policy:
    name = "P0"

    def start_episode(self, episode_id, replication_seed):
        pass

    def decide(self, session, valid):
        return Decision(p0_action(session))


class RandomValidPolicy:
    name = "PR"

    def __init__(self):
        self._rng = None

    def start_episode(self, episode_id, replication_seed):
        self._rng = random.Random(f"l2sim-pr|{replication_seed}|{episode_id}")

    def decide(self, session, valid):
        return Decision(self._rng.choice(valid) if valid else None)


def state_for_model(session):
    """Structured state only: ids, counts, phase words, opaque label tokens. No operator text, no truth."""
    visible = [qs for qs in session.deposit_order() if qs.state != PENDING and qs.state not in TERMINAL]
    in_flight = session.in_flight()
    return {
        "schema": "anastylos-l2sim-state@1",
        "time_s": round(session.now, 3),
        "phase": "closing" if session.closing else "running",
        "segment": session.episode.segment,
        "operator": {"busy": session.busy},
        "questions": [{
            "id": qs.question.id, "revision": qs.revision, "kind": qs.question.kind, "state": qs.state,
            "candidate_count": len(qs.candidates), "deposit_age_s": round(session.now - qs.question.open_t, 3),
            "attempts": qs.attempts, "last_outcome": qs.last_outcome, "deferred_until": qs.defer_until,
            "resolvable_without_operator": qs.resolvable,
        } for qs in visible],
        "committed_parts": [{"part_ref": ref, **info} for ref, info in session.committed_parts.items()],
        "in_flight_question": in_flight.question.id if in_flight else None,
        "readback_open": session.readback_open(),
        "budgets": {"questions": session.question_budget, "prompts": session.prompts_left,
                    "turns": session.turn_limit - session.turns, "attempts_per_question": MAX_ATTEMPTS},
        "last_actions": session.history[-HISTORY_K:],
    }


def request_sha256(task, payload):
    body = json.dumps({"task": task, "payload": payload}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class ProposerPolicy:
    """L2 arm: asks an injected Proposer; retries timeout/provider_error within the per-episode retry limit."""

    name = "L2"

    def __init__(self, proposer, run_id, retry_limit=3):
        self._proposer = proposer
        self._run_id = run_id
        self._retry_limit = retry_limit
        self._retries_left = retry_limit
        self._episode = None
        self._call_index = 0
        self.stopped_status = None    # set once the provider is disabled or out of budget
        self.calls = []               # per call: request id, request hash, provider result

    def start_episode(self, episode_id, replication_seed):
        self._episode = f"{episode_id}:r{replication_seed}"
        self._retries_left = self._retry_limit

    def decide(self, session, valid):
        if self.stopped_status:
            return Decision(None, self.stopped_status, 0)
        payload = {"state": state_for_model(session), "valid_actions": valid}
        calls = 0
        while True:
            self._call_index += 1
            request_id = f"{self._run_id}:{self._episode}:{self._call_index}"
            result = self._proposer.propose("next_action", payload, request_id)
            calls += 1
            status = result.get("status") if isinstance(result, dict) else "invalid_output"
            self.calls.append({"request_id": request_id, "request_sha256": request_sha256("next_action", payload),
                               "result": result if isinstance(result, dict) else repr(result)[:300]})
            if status == "ok":
                return Decision(result.get("output"), "ok", calls)
            if status in RETRYABLE and self._retries_left > 0:
                self._retries_left -= 1
                continue
            if status in STOPPING:
                self.stopped_status = status
            return Decision(None, status if isinstance(status, str) else "invalid_output", calls)
