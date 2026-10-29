"""Oracle operator with preregistered abstention and error rates (PREREG §4).

The oracle is constructed with the ground truth only and is called with the asked question's id, revision,
candidate tokens and the busy flag. It never sees model output, the policy, or the session state.
"""

import random
from dataclasses import dataclass

ABSTAIN_IDLE = 0.10
ABSTAIN_BUSY = 0.20
ERROR_RATE = 0.05
READBACK_CATCH = 0.5


@dataclass(frozen=True)
class Reply:
    unsure: bool              # operator said "weiß nicht"
    correct: bool
    caught_at_readback: bool  # meaningful only when not correct


class Oracle:
    def __init__(self, truths, replication_seed, episode_id):
        self._truths = dict(truths)
        self._seed = replication_seed
        self._episode = episode_id

    def reply(self, question_id, revision, candidates, busy, attempt):
        # One stream per (seed, episode, question, attempt): every arm faces the same draws (common random numbers).
        rng = random.Random(f"l2sim-oracle|{self._seed}|{self._episode}|{question_id}|{attempt}")
        abstain, error, catch = rng.random(), rng.random(), rng.random()
        truth = self._truths[question_id]
        if truth["kind"] == "unanswerable" or abstain < (ABSTAIN_BUSY if busy else ABSTAIN_IDLE):
            return Reply(unsure=True, correct=False, caught_at_readback=False)
        if error < ERROR_RATE:
            return Reply(unsure=False, correct=False, caught_at_readback=catch < READBACK_CATCH)
        return Reply(unsure=False, correct=True, caught_at_readback=False)
