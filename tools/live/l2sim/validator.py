"""Deterministic, fail-closed precondition check of one proposed action against the session (seed spec table).

Returns reason codes; an empty list means the action may be executed now. Shape errors short-circuit, so a
malformed proposal never reaches the precondition checks.
"""

import actions
from session import (ASKED, DEFERRED, MAX_ATTEMPTS, OPEN, PENDING, PROMPT_INTERVAL_S, SESSION_TTL_S)

# Reasons that mean the proposal aimed at the wrong question (unknown, stale, or not in a usable state).
WRONG_TARGET = ("unknown_question", "stale_revision", "question_state")


def validate(proposal, session):
    errors = actions.shape_errors(proposal)
    if errors:
        return errors
    check = {"ask": _ask, "defer": _defer, "protocol_prompt": _prompt,
             "ask_dependency": _dependency, "leave_unresolved": _leave}[proposal["action"]]
    return check(proposal, session)


def _target(proposal, session):
    qs = session.questions.get(proposal["question_id"])
    if qs is None or qs.state == PENDING:
        return None, ["unknown_question"]
    if proposal["question_revision"] != qs.revision:
        return qs, ["stale_revision"]
    return qs, []


def _ask(proposal, session):
    qs, errors = _target(proposal, session)
    if errors:
        return errors
    if qs.state not in (OPEN, DEFERRED):
        errors.append("question_state")
    if session.in_flight() is not None:
        errors.append("another_question_asked")
    if qs.attempts >= MAX_ATTEMPTS:
        errors.append("attempt_limit")
    if session.question_budget <= 0:
        errors.append("question_budget")
    return errors


def _defer(proposal, session):
    qs, errors = _target(proposal, session)
    if errors:
        return errors
    if qs.state not in (OPEN, ASKED):
        errors.append("question_state")
    if session.now - qs.question.open_t >= SESSION_TTL_S:
        errors.append("ttl_expired")
    if session.closing:
        errors.append("after_segment_end")
    return errors


def _prompt(proposal, session):
    errors = []
    if session.last_prompt_t is not None and session.now - session.last_prompt_t < PROMPT_INTERVAL_S:
        errors.append("prompt_rate_limit")
    if session.readback_open():
        errors.append("readback_open")
    if session.prompts_left <= 0:
        errors.append("prompt_budget")
    if session.closing:
        errors.append("after_segment_end")
    return errors


def _dependency(proposal, session):
    if proposal["part_ref"] not in session.committed_parts:
        return ["part_not_committed"]
    if proposal["part_ref"] in session.dependency_asked:
        return ["dependency_already_asked"]
    return []


def _leave(proposal, session):
    qs, errors = _target(proposal, session)
    if errors:
        return errors
    if qs.state not in (OPEN, DEFERRED):
        return ["question_state"]
    unsure = qs.last_outcome == "unsure"
    reason = proposal["reason"]
    if reason == "no_candidates_and_operator_unsure":
        ok = not qs.candidates and unsure
    elif reason == "operator_declined":
        ok = qs.attempts >= 1 and unsure
    else:  # budget
        ok = qs.attempts >= 1 and (qs.attempts >= MAX_ATTEMPTS or session.question_budget <= 0)
    if qs.attempts < 1 and not (not qs.candidates and unsure):
        return ["no_attempt"]
    return [] if ok else ["reason_mismatch"]


def valid_actions(session):
    """Every concrete action that passes `validate` right now; the list L2 and PR choose from."""
    proposals = []
    for qs in session.deposit_order():
        if qs.state == PENDING:
            continue
        ref = {"question_id": qs.question.id, "question_revision": qs.revision}
        proposals.append(actions.make("ask", **ref))
        proposals.extend(actions.make("defer", until=u, **ref) for u in actions.UNTIL)
        proposals.extend(actions.make("leave_unresolved", reason=r, **ref) for r in actions.REASONS)
    proposals.extend(actions.make("protocol_prompt", prompt_kind=k) for k in actions.PROMPT_KINDS)
    proposals.extend(actions.make("ask_dependency", part_ref=p) for p in session.committed_parts)
    return [p for p in proposals if not validate(p, session)]
