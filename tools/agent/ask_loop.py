"""The question loop of a session: P0 decides which question comes next, the live coordinator asks it.

P0 is the product's action selector (the preregistered L2 arm was rejected, tools/live/integration/README.md):
questions in flight after a restart are resumed first, then open questions in live order (phase, then
deposit / removal time), then one more round for deferred questions. A deferred question with no candidates
that the operator did not know is left unresolved, as is one already asked ``MAX_ASKS`` times. The loop never
calls a model: wording comes from L0 or from wording prefetched earlier (``prefetch``).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

LIVE = Path(__file__).resolve().parents[1] / "live"
if str(LIVE) not in sys.path:
    sys.path.insert(0, str(LIVE))

import states as st  # noqa: E402  (tools/live)
from coordinator import Coordinator, Stopped  # noqa: E402
from journal import utc_now  # noqa: E402

from state import SessionError, SessionState  # noqa: E402

MAX_ASKS = 2          # per question revision, as P0 in l2sim/PREREG.md §5
MAX_UNCLEAR = 3       # replies to one ask the deterministic interpreter cannot use before the question is deferred
LANGUAGE = "en"       # product language
IN_FLIGHT = (st.ASKED, st.ANSWERED, st.READ_BACK)


class Operator(Protocol):
    def show(self, text: str) -> None: ...
    def reply(self, stage: str, question_id: str, text: str, prompt: dict) -> dict:
        """``stage``: ``answer`` or ``confirm``. Returns ``{"text": ...}`` or ``{"choice": ...}``."""


@dataclass
class LoopReport:
    committed: list[str] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)
    unresolved: dict[str, str] = field(default_factory=dict)  # question id -> reason
    excluded_phases: list[str] = field(default_factory=list)


def open_coordinator(state: SessionState, proposer=None, provider_call_cap: int = 0, **kwargs) -> Coordinator:
    runs = state.done_runs()
    if not runs:
        raise SessionError("no processed phase yet; run `phase` first")
    phase_words = json.loads(state.phase_words_path.read_text(encoding="utf-8"))["phases"]
    return Coordinator(state.root / "live", state.memory_dir, runs, phase_words, declared_by=state.doc["operator"],
                       proposer=proposer, provider_call_cap=provider_call_cap, language=LANGUAGE, **kwargs).open()


def operator_input(op_key: str, reply: dict) -> dict:
    if "choice" in reply:
        return {"schema": "anastylos-live-operator-input@1", "op_key": op_key, "source": "screen_choice", "text": None,
                "choice": reply["choice"], "received_at": utc_now(), "transcript": None}
    return {"schema": "anastylos-live-operator-input@1", "op_key": op_key, "source": "typed", "text": reply["text"],
            "choice": None, "received_at": utc_now(), "transcript": None}


def next_question(c: Coordinator, report: LoopReport) -> str | None:
    """P0: in-flight first, then open in live order, then deferred (leaving the hopeless ones unresolved)."""
    order = c.book.live_order()
    for qid in order:
        if c.state_of(qid).state in IN_FLIGHT:
            return qid
    for qid in order:
        if c.state_of(qid).state in (st.OPEN, st.FAILED):
            return qid
    for qid in order:
        if c.state_of(qid).state != st.DEFERRED:
            continue
        if not c.book.questions[qid].get("candidates") and c.last_outcome(qid) == "unsure":
            c.defer(qid, leave_unresolved=True, reason="no_candidates_and_operator_unsure")
            report.unresolved[qid] = "no candidates and the operator did not know"
        elif c.asks(qid) >= MAX_ASKS:
            c.defer(qid, leave_unresolved=True, reason="budget")
            report.unresolved[qid] = f"asked {MAX_ASKS} times without an answer"
        else:
            return qid
    return None


def ask_one(c: Coordinator, qid: str, operator: Operator, report: LoopReport) -> None:
    """One question until it is committed, deferred, superseded or the read-back is left open by a stop."""
    if c.resume(qid) == st.READ_BACK:
        state = c.state_of(qid)
        operator.show(state.read_back)
        if _confirm(c, qid, state.op_key, state.read_back, c.prompts.get(state.op_key, {}), operator, report):
            return
    prompt = c.ask(qid)
    op_key, text, unclear = prompt["op_key"], prompt["text"], 0
    operator.show(text)
    for option in prompt["screen_options"]:
        operator.show(f"  [{option['token']}] {option['description'] or 'marked on the screen'}"
                      + (f" (already named for “{option['claimed_by']}”)" if option["claimed_by"] else ""))
    while True:
        result = c.receive(operator_input(op_key, operator.reply("answer", qid, text, prompt)))
        status = result["status"]
        if status == "deferred":
            operator.show(result["message"])
            report.deferred.append(qid)
            return
        if status == "unclear":
            unclear += 1
            if unclear >= MAX_UNCLEAR:
                c.defer(qid, reason="no usable answer")
                operator.show("I will come back to this question later.")
                report.deferred.append(qid)
                return
            operator.show(result["message"])
            continue
        if status == "read_back":
            operator.show(result["read_back"])
            if _confirm(c, qid, op_key, result["read_back"], prompt, operator, report):
                return
            operator.show(c.lang.messages["asked_again"] + text)
            continue
        operator.show(result["message"])  # stale, duplicate, ignored or foreign input: the loop picks up again
        return


def _confirm(c: Coordinator, qid: str, op_key: str, read_back: str, prompt: dict, operator: Operator,
             report: LoopReport) -> bool:
    """True when the question left the read-back (committed, deferred or failed); False when it was rejected."""
    unclear = 0
    while True:
        result = c.receive(operator_input(op_key, operator.reply("confirm", qid, read_back, prompt)))
        if result["status"] == "committed":
            operator.show("Recorded.")
            report.committed.append(qid)
            return True
        if result["status"] == "asked_again":
            return False
        if result["status"] == "failed":
            raise Stopped(result["message"])
        unclear += 1
        if unclear >= MAX_UNCLEAR:
            c.defer(qid, reason="read-back not confirmed")
            operator.show("Nothing was recorded. I will come back to this question later.")
            report.deferred.append(qid)
            return True
        operator.show(result.get("read_back") or result.get("message", ""))


def run(state: SessionState, operator: Operator, **coordinator_kwargs) -> LoopReport:
    report = LoopReport(excluded_phases=[p["tag"] for p in state.doc["phases"] if p["status"] != "done"])
    for tag in report.excluded_phases:
        operator.show(f"Warning: phase {tag} did not finish processing; its questions are not included.")
    c = open_coordinator(state, **coordinator_kwargs)
    try:
        if c.stopped:
            raise Stopped(c.stopped)
        while (qid := next_question(c, report)) is not None:
            ask_one(c, qid, operator, report)
            if c.stopped:
                raise Stopped(c.stopped)
        return report
    finally:
        c.close()


def prefetch(state: SessionState, proposer, max_calls: int) -> dict[str, str]:
    """L1 wording for every open question, computed now (between phases), never while the operator waits."""
    c = open_coordinator(state, provider_call_cap=max_calls)
    try:
        if c.stopped:
            raise Stopped(c.stopped)
        verdicts = {}
        for qid in c.book.live_order():
            if c.state_of(qid).state in (st.OPEN, st.DEFERRED) and c.provider_calls < max_calls:
                verdicts[qid] = c.prefetch_wording(qid, proposer)
        return verdicts
    finally:
        c.close()


def question_states(state: SessionState) -> dict[str, str]:
    """Latest state per question id, read from the coordinator journal (no lock, read-only)."""
    path = state.root / "live" / "journal.jsonl"
    if not path.is_file():
        return {}
    latest: dict[str, tuple[int, str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("kind") == "question_state" and record.get("state"):
            qid, revision = record["question_id"], record["revision"]
            if qid not in latest or revision >= latest[qid][0]:
                latest[qid] = (revision, record["state"])
        if record.get("kind") == "done":
            qid = record["question_id"]
            latest[qid] = (latest.get(qid, (0, ""))[0], st.COMMITTED)
    return {qid: s for qid, (_, s) in sorted(latest.items())}


class ScriptedOperator:
    """Replies from a script: ``{question_id: {"answer": [...], "confirm": [...]}}``, consumed in order."""

    def __init__(self, script: dict[str, dict[str, list]]):
        self.script = {qid: {stage: list(replies) for stage, replies in stages.items()} for qid, stages in script.items()}
        self.transcript: list[str] = []

    def show(self, text: str) -> None:
        self.transcript.append(text)

    def reply(self, stage: str, question_id: str, text: str, prompt: dict) -> dict:
        replies = self.script.get(question_id, {}).get(stage) or []
        reply = replies.pop(0) if replies else ("I don't know" if stage == "answer" else "no")
        self.transcript.append(f"> {reply}")
        return reply if isinstance(reply, dict) else {"text": reply}


class ConsoleOperator:
    """The operator at the terminal. ``A``-``E`` alone selects a marked spot on the screen."""

    def show(self, text: str) -> None:
        print(text, flush=True)

    def reply(self, stage: str, question_id: str, text: str, prompt: dict) -> dict:
        line = input("Your answer> " if stage == "answer" else "Yes or no> ").strip()
        tokens = {o["token"] for o in prompt.get("screen_options", [])}
        return {"choice": line.upper()} if stage == "answer" and line.upper() in tokens else {"text": line}
