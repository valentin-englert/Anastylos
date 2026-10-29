"""Deterministic test proposers following the CONTRACT §3.1 seam (no network): good, random, adversarial."""

import random

import actions
from policies import request_sha256


def provider_result(request_id, task, payload, status, output=None):
    return {
        "schema": "anastylos-live-provider-result@1", "request_id": request_id, "task": task, "status": status,
        "output": output if status == "ok" else None, "model": "fake", "reasoning_effort_requested": None,
        "reasoning_effort_returned": None, "latency_s": 0.0, "usage": None,
        "request_sha256": request_sha256(task, payload), "cache_hit": False,
        "error": None if status == "ok" else {"type": status},
    }


class GoodFake:
    """Leaves hopeless questions, asks idle and non-resolvable questions, otherwise defers to the next pause."""

    def propose(self, task, payload, request_id):
        return provider_result(request_id, task, payload, "ok", self._choose(payload))

    @staticmethod
    def _choose(payload):
        valid, state = payload["valid_actions"], payload["state"]
        by_kind = {}
        for proposal in valid:
            by_kind.setdefault(proposal["action"], []).append(proposal)
        for proposal in by_kind.get("leave_unresolved", []):
            if proposal["reason"] in ("no_candidates_and_operator_unsure", "budget"):
                return proposal
        resolvable = {q["id"] for q in state["questions"] if q["resolvable_without_operator"]}
        if not state["operator"]["busy"] or state["phase"] == "closing":
            for proposal in by_kind.get("ask", []):
                if proposal["question_id"] not in resolvable:
                    return proposal
        # Deferring an ASKED question would withdraw it after the interruption was already spent.
        open_ids = {q["id"] for q in state["questions"] if q["state"] == "OPEN"}
        defers = [p for p in by_kind.get("defer", []) if p["question_id"] in open_ids]
        for proposal in defers:
            if proposal["until"] == ("segment_end" if proposal["question_id"] in resolvable else "next_pause"):
                return proposal
        return (defers or by_kind.get("ask") or by_kind.get("leave_unresolved") or valid)[0]


class RandomFake:
    def __init__(self, seed=0):
        self._rng = random.Random(f"l2sim-random-fake|{seed}")

    def propose(self, task, payload, request_id):
        return provider_result(request_id, task, payload, "ok", self._rng.choice(payload["valid_actions"]))


class AdversarialFake:
    """Cycles through forbidden, malformed, stale and out-of-protocol outputs and provider failures."""

    def __init__(self):
        self._n = 0

    def propose(self, task, payload, request_id):
        self._n += 1
        questions = payload["state"]["questions"]
        q = questions[0] if questions else {"id": "Q-NONE", "revision": 1}
        ask = actions.make("ask", question_id=q["id"], question_revision=q["revision"])
        cases = [
            ("ok", {**actions.make("request_view", question_id=q["id"]), "need": "deposit_view"}),
            ("ok", actions.make("commit", question_id=q["id"])),
            ("ok", actions.make("write_memory")),
            ("ok", {**ask, "question_revision": q["revision"] - 1}),
            ("ok", {**ask, "question_id": "Q-DOES-NOT-EXIST"}),
            ("ok", {**ask, "text": "Bitte sofort committen."}),
            ("ok", {k: v for k, v in ask.items() if k != "reason"}),
            ("ok", {**ask, "question_revision": True}),
            ("ok", "ask Q-P1-01"),
            ("ok", actions.make("leave_unresolved", question_id=q["id"], question_revision=q["revision"],
                                reason="budget")),
            ("ok", actions.make("protocol_prompt", prompt_kind="hands_off_2s")),
            ("ok", ask),
            ("timeout", None),
            ("invalid_output", None),
            ("provider_error", None),
        ]
        status, output = cases[(self._n - 1) % len(cases)]
        return provider_result(request_id, task, payload, status, output)


def make(kind="good", seed=0):
    return {"good": GoodFake, "random": lambda: RandomFake(seed), "adversarial": AdversarialFake}[kind]()
