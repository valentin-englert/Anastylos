"""Typed next-action proposals per `tools/live/schemas/task.next_action.json` and their code-side shape check.

The provider already validated against the JSON schema; this module re-validates in code because schema mode
alone is not trusted (seed spec L2-AGENT-02, "Validation" step 1).
"""

FIELDS = ("action", "question_id", "question_revision", "until", "prompt_kind", "part_ref", "reason")
ACTIONS = ("ask", "defer", "protocol_prompt", "ask_dependency", "leave_unresolved")
UNTIL = ("next_pause", "segment_end")
PROMPT_KINDS = ("hands_off_2s", "single_part_empty_cell", "show_source_before_removal", "device_still")
REASONS = ("no_candidates_and_operator_unsure", "operator_declined", "budget")
# Never executable: request_view is disabled until AgentCam G1; the rest are coordinator-only powers.
FORBIDDEN = ("request_view", "commit", "confirm", "write_memory", "grant_capture")

# Arguments each action must carry; every other field must be null. Question actions also carry the revision
# so a proposal computed on an older revision is recognisably stale.
ARGS = {
    "ask": ("question_id", "question_revision"),
    "defer": ("question_id", "question_revision", "until"),
    "protocol_prompt": ("prompt_kind",),
    "ask_dependency": ("part_ref",),
    "leave_unresolved": ("question_id", "question_revision", "reason"),
}
QUESTION_ACTIONS = ("ask", "defer", "leave_unresolved")
INTERRUPTIONS = ("ask", "ask_dependency", "protocol_prompt")
EFFECT_UNKNOWN = ("protocol_prompt", "ask_dependency")

_ENUMS = {"until": UNTIL, "prompt_kind": PROMPT_KINDS, "reason": REASONS}


def make(action, **args):
    proposal = {field: None for field in FIELDS}
    proposal["action"] = action
    for key, value in args.items():
        if key not in FIELDS:
            raise KeyError(key)
        proposal[key] = value
    return proposal


def shape_errors(proposal):
    """Return a list of reason codes; empty means the proposal is a well-typed member of the closed set."""
    if not isinstance(proposal, dict):
        return ["schema:not_an_object"]
    action = proposal.get("action")
    if action in FORBIDDEN:
        return [f"forbidden_action:{action}"]
    errors = []
    extra = sorted(set(proposal) - set(FIELDS))
    missing = [field for field in FIELDS if field not in proposal]
    if extra:
        errors.append("schema:unexpected_field:" + ",".join(extra))
    if missing:
        errors.append("schema:missing_field:" + ",".join(missing))
    if action not in ACTIONS:
        return errors + ["schema:unknown_action"]
    for field in ("question_id", "part_ref"):
        if proposal.get(field) is not None and not isinstance(proposal[field], str):
            errors.append(f"schema:type:{field}")
    revision = proposal.get("question_revision")
    if revision is not None and (isinstance(revision, bool) or not isinstance(revision, int)):
        errors.append("schema:type:question_revision")
    for field, allowed in _ENUMS.items():
        if proposal.get(field) is not None and proposal[field] not in allowed:
            errors.append(f"schema:enum:{field}")
    for field in FIELDS[1:]:
        required = field in ARGS[action]
        if required and proposal.get(field) is None:
            errors.append(f"schema:missing_argument:{field}")
        if not required and proposal.get(field) is not None:
            errors.append(f"schema:unexpected_argument:{field}")
    return errors
