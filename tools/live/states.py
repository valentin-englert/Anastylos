"""Question state machine of a live session (CONTRACT §3.3).

One ``QuestionState`` per (question id, revision). ``op_key`` names it:
``<live_session_id>:<question_id>:<revision>``. Only ``CONFIRMED`` (the operator said
yes to the read-back) may lead to a memory write; a superseded revision never commits.
"""

from __future__ import annotations

OPEN, ASKED, ANSWERED, READ_BACK, CONFIRMED, COMMITTED = "OPEN", "ASKED", "ANSWERED", "READ_BACK", "CONFIRMED", "COMMITTED"
DEFERRED, LEFT_UNRESOLVED, SUPERSEDED, FAILED = "DEFERRED", "LEFT_UNRESOLVED", "SUPERSEDED", "FAILED"

TERMINAL = frozenset({COMMITTED, LEFT_UNRESOLVED, SUPERSEDED})
_SIDE_EXITS = {DEFERRED, LEFT_UNRESOLVED, SUPERSEDED}
TRANSITIONS = {
    OPEN: {ASKED} | _SIDE_EXITS,
    ASKED: {ANSWERED} | _SIDE_EXITS,
    ANSWERED: {READ_BACK, ASKED} | _SIDE_EXITS,
    # "no" or a correction sends the question back to the operator; nothing was written
    READ_BACK: {CONFIRMED, ASKED} | _SIDE_EXITS,
    # a confirmed answer is already in the write-ahead journal: it can only commit or fail
    CONFIRMED: {COMMITTED, FAILED},
    DEFERRED: {ASKED, LEFT_UNRESOLVED, SUPERSEDED},
    FAILED: {ASKED, LEFT_UNRESOLVED, SUPERSEDED},
    COMMITTED: set(),
    LEFT_UNRESOLVED: set(),
    SUPERSEDED: set(),
}


class TransitionError(ValueError):
    """A state change the contract does not allow; nothing changed."""


def make_op_key(live_session_id: str, question_id: str, revision: int) -> str:
    if ":" in live_session_id or ":" in question_id:
        raise ValueError("live session id and question id must not contain ':'")
    return f"{live_session_id}:{question_id}:{revision}"


def parse_op_key(op_key: str) -> tuple[str, str, int]:
    session, question, revision = op_key.split(":")
    return session, question, int(revision)


class QuestionState:
    def __init__(self, op_key: str, state: str = OPEN):
        self.op_key = op_key
        self.state = state
        self.answer: dict | None = None  # answers.py-compatible answer awaiting read-back / write
        self.read_back: str | None = None

    @property
    def revision(self) -> int:
        return parse_op_key(self.op_key)[2]

    def can(self, target: str) -> bool:
        return target in TRANSITIONS[self.state]

    def move(self, target: str) -> str:
        if not self.can(target):
            raise TransitionError(f"{self.op_key}: {self.state} -> {target} is not allowed")
        previous, self.state = self.state, target
        return previous
