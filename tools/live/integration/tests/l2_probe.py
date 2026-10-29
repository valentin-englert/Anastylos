"""Helper process for test_adversarial: lane C's FakeProposer failure modes through lane D's L2 arbitration.

Prints one JSON object per mode: rejected proposals, actions the policy executed, integrity counters.
"""

import json
import sys
from pathlib import Path

LIVE = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(LIVE / "l2sim"), str(LIVE / "providers")]

import episodes  # noqa: E402
from fake import FakeProposer  # noqa: E402
from policies import ProposerPolicy  # noqa: E402
from simulator import INTEGRITY, simulate  # noqa: E402


class FromValid:
    """Takes the first valid action, then lets lane C's fake corrupt it (stale revision or forbidden action)."""

    def __init__(self, mode):
        self.mode = mode

    def propose(self, task, payload, request_id):
        first = dict(payload["valid_actions"][0])
        output = {k: first.get(k) for k in ("action", "question_id", "question_revision", "until", "prompt_kind",
                                            "part_ref", "reason")}
        if self.mode == "stale_revision" and not isinstance(output["question_revision"], int):
            output = {**output, "action": "ask", "question_id": payload["state"]["questions"][0]["id"],
                      "question_revision": payload["state"]["questions"][0]["revision"]}
        return FakeProposer(script={task: {"mode": self.mode, "output": output}}).propose(task, payload, request_id)


def probe(mode):
    episode = episodes.synthetic("stale_revisions", 1)
    result = simulate(episode, ProposerPolicy(FromValid(mode), f"probe-{mode}"), 0)
    entries = [e for e in result["trace"] if "executed_by" in e]
    integrity = {name: result["counters"][name] for name in INTEGRITY}
    rejected = [e for e in entries if e["validator_verdict"] != "valid"]
    return {"rejected": len(rejected), "executed_by_policy": sum(e["executed_by"] == "policy" for e in entries),
            "reasons": sorted({str(e["validator_verdict"]) for e in rejected}), "integrity": integrity,
            "integrity_ok": all(v == 0 for v in integrity.values()),
            "stale_actions_executed": result["counters"]["stale_actions_executed"]}


if __name__ == "__main__":
    print(json.dumps({mode: probe(mode) for mode in ("stale_revision", "wrong_enum")}))
