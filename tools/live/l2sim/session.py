"""Coordinator-side session state of one simulated episode (question states per CONTRACT §3.3)."""

from dataclasses import dataclass, field

MAX_ATTEMPTS = 2
SESSION_TTL_S = 600.0
PROMPT_INTERVAL_S = 60.0
PROMPT_BUDGET = 2

PENDING = "PENDING"           # deposit not yet observed; invisible to policies
OPEN, DEFERRED, ASKED, READ_BACK, CONFIRMED = "OPEN", "DEFERRED", "ASKED", "READ_BACK", "CONFIRMED"
COMMITTED, LEFT_UNRESOLVED, RESOLVED_BY_LINKER = "COMMITTED", "LEFT_UNRESOLVED", "RESOLVED_BY_LINKER"
TERMINAL = (COMMITTED, LEFT_UNRESOLVED, RESOLVED_BY_LINKER)
IN_FLIGHT = (ASKED, READ_BACK)


@dataclass
class QuestionState:
    question: object          # episodes.Question
    revision: int = 1
    candidates: tuple = ()
    state: str = PENDING
    attempts: int = 0
    last_outcome: str | None = None   # unsure | readback_rejected | superseded | withdrawn
    resolvable: bool = False
    defer_until: str | None = None
    deferred_at: float | None = None
    ask_token: int | None = None      # identifies the pending answer; cleared when it goes stale
    asked_revision: int | None = None
    asked_busy: bool = False
    reply: object = None              # oracle.Reply for the pending answer


@dataclass
class Session:
    episode: object
    questions: dict
    question_budget: int
    turn_limit: int
    now: float = 0.0
    busy: bool = False
    closing: bool = False
    prompts_left: int = PROMPT_BUDGET
    last_prompt_t: float | None = None
    committed_parts: dict = field(default_factory=dict)   # part_ref -> {"basis", "cell", "t_s", "label"}
    dependency_asked: set = field(default_factory=set)
    history: list = field(default_factory=list)           # executed actions with outcomes, newest last
    turns: int = 0

    @classmethod
    def start(cls, episode):
        questions = {q.id: QuestionState(q, candidates=q.candidates) for q in episode.questions}
        session = cls(episode, questions, question_budget=2 * len(questions),
                      turn_limit=3 * len(questions) + 6)
        for part in episode.committed_at_start:
            session.add_committed_part(part["part_ref"], part["basis"], part["cell"], part["t_s"])
        return session

    def add_committed_part(self, part_ref, basis, cell, t_s):
        # Labels reach a model only as opaque tokens, never as operator text.
        label = f"label#{len(self.committed_parts) + 1}"
        self.committed_parts[part_ref] = {"basis": basis, "cell": cell, "t_s": t_s, "label": label}

    def in_flight(self):
        return next((qs for qs in self.questions.values() if qs.state in IN_FLIGHT), None)

    def readback_open(self):
        return any(qs.state == READ_BACK for qs in self.questions.values())

    def deposit_order(self):
        return sorted(self.questions.values(), key=lambda qs: (qs.question.open_t, qs.question.id))

    def needs_decision(self):
        return [qs for qs in self.deposit_order() if qs.state == OPEN]
