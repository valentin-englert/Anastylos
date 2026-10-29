"""Propose removal -> deposit custody links from observed (or operator-declared) events.

Temporal exclusivity is a conditional correspondence *hypothesis*: exactly
one pending removal when a part appeared in an empty cell, with nothing
else observed moving in that window. It is never identity. Anything that
could hide another movement downgrades the proposal to ``ambiguous`` with
explicit reasons: overlapping deposits or removals, a source site that
was not observable during the window, sampling gaps, or a deposit into an
already occupied cell. Every proposal requires operator confirmation; this
module never writes Assembly Memory.

Linker 1.1 (custody v1.2): ambiguity stays local. All deposits are judged
together by enumerating every *valid assignment*: deposits in time order,
each takes one observed or declared removal that started before it and is
not taken by an earlier deposit; when no such removal is left it comes from
outside the watched sites. A removal never serves two deposits. A deposit
whose only open question is "which of several pending removals" is linked
when every valid assignment gives it the same removal: an earlier ambiguous
deposit then no longer poisons it, because the alternatives were used up
by other deposits (status ``candidate_by_elimination``). If valid
assignments differ, it stays ``ambiguous``. Hidden-movement reasons
(overlaps, unobservable sources, sampling gaps, occupied cells) still make
a deposit ``ambiguous`` whatever the assignments say; they are never
resolved by elimination.
"""

from __future__ import annotations

LINKS_SCHEMA = "custody-links@1"
DECLARED_SCHEMA = "custody-declared-removals@1"
DEPOSIT_KINDS = ("appeared", "changed_while_occupied")
SCOPE_NOTE = "exclusive only among inventoried sites and watched tray cells; parts taken from anywhere else are invisible to this check"


def link(events_doc: dict, declared: dict | None = None) -> dict:
    events = events_doc["events"]
    removals = [_observed_removal(event) for event in events if event["kind"] == "site_changed"]
    if declared is not None:
        removals += _declared_removals(declared)
    removals.sort(key=lambda item: item["t_after_s"])
    deposits = sorted((event for event in events if event["zone_kind"] == "tray_cell" and event["kind"] in DEPOSIT_KINDS), key=lambda item: item["t_after_s"])
    blind_sites = _source_blind_spots(events_doc)
    gaps = events_doc["sampling"]["gaps_over_max"]

    options = _assignment_options(deposits, removals)
    proposals, linked = [], set()
    for deposit in deposits:
        pending = [removal for removal in removals if removal["t_before_s"] < deposit["t_after_s"] and removal["event"] not in linked]
        proposal = _judge(deposit, pending, deposits, removals, blind_sites, gaps)
        if proposal["status"] == "ambiguous":
            proposal = _by_elimination(proposal, options.get(deposit["id"]), deposit, deposits, removals, blind_sites, gaps)
        if proposal["status"] in ("candidate_temporal_exclusive", "candidate_by_elimination"):
            linked.add(proposal["removal"]["event"])
        proposals.append(proposal)
    orphans = [removal for removal in removals if removal["event"] not in linked]
    return {
        "schema": LINKS_SCHEMA,
        "scope_note": SCOPE_NOTE,
        "identity": "unverified: every proposal needs operator confirmation",
        "proposals": [{"id": f"L-{index:04d}", **proposal} for index, proposal in enumerate(proposals, 1)],
        "removals_without_linked_deposit": orphans,
        "source_blind_spots": blind_sites,
    }


def _judge(deposit: dict, pending: list[dict], deposits: list[dict], removals: list[dict], blind_sites: list[dict], gaps: list) -> dict:
    base = {"deposit": _brief(deposit), "requires_operator_confirmation": True}
    if not pending:
        reasons = ["no observed or declared removal precedes this deposit"]
        if blind_sites:
            reasons.append("source sites were unobservable at some point: " + ", ".join(sorted({item["zone"] for item in blind_sites})))
        return {**base, "status": "unattributed", "reasons": reasons, "candidates": []}
    window_start = min(item["t_before_s"] for item in pending)
    window = (window_start, deposit["t_after_s"])
    reasons = []
    if len(pending) > 1:
        reasons.append(f"{len(pending)} removals pending when the deposit was observed")
    others = [other["id"] for other in deposits if other is not deposit and _overlaps(other, window)]
    if others:
        reasons.append("other deposits overlap the custody window: " + ", ".join(others))
    late = [item["event"] for item in removals if item not in pending and _overlaps(item, window)]
    if late:
        reasons.append("other removals overlap the custody window: " + ", ".join(late))
    blind = sorted({item["zone"] for item in blind_sites if item["from_s"] <= window[1]})
    if blind:
        reasons.append("source sites unobservable during or before the window: " + ", ".join(blind))
    gap_hits = [gap for gap in gaps if gap[0] < window[1] and gap[1] > window[0]]
    if gap_hits:
        reasons.append(f"sampling gaps inside the window: {gap_hits}")
    if deposit["kind"] != "appeared":
        reasons.append("deposit into an already occupied cell (one part per cell violated)")
    if reasons:
        return {**base, "status": "ambiguous", "reasons": reasons, "candidates": pending, "custody_window_s": list(window)}
    return {**base, "status": "candidate_temporal_exclusive", "hypothesis": "temporal_exclusive", "removal": pending[0],
            "custody_window_s": list(window), "reasons": [SCOPE_NOTE]}


MAX_ASSIGNMENTS = 100_000


def _assignment_options(deposits: list[dict], removals: list[dict]) -> dict[str, set | None]:
    """deposit id -> removal events it takes across all valid assignments (None: too many to enumerate)."""
    options: dict[str, set] = {deposit["id"]: set() for deposit in deposits}
    count = 0

    def walk(index: int, used: frozenset) -> bool:
        nonlocal count
        if index == len(deposits):
            count += 1
            return count <= MAX_ASSIGNMENTS
        deposit = deposits[index]
        open_removals = [r["event"] for r in removals if r["t_before_s"] < deposit["t_after_s"] and r["event"] not in used]
        for event in open_removals or [None]:
            options[deposit["id"]].add(event)
            if not walk(index + 1, used | {event} if event else used):
                return False
        return True

    if not walk(0, frozenset()):
        return {deposit["id"]: None for deposit in deposits}
    return options


def _by_elimination(proposal: dict, choices: set | None, deposit: dict, deposits: list[dict], removals: list[dict],
                    blind_sites: list[dict], gaps: list) -> dict:
    """Link an ambiguous deposit only if all valid assignments agree AND the narrowed window passes every hidden-movement check."""
    if not choices or len(choices) != 1 or None in choices:
        return proposal
    event = next(iter(choices))
    removal = next(r for r in removals if r["event"] == event)
    narrowed = _judge(deposit, [removal], deposits, removals, blind_sites, gaps)
    if narrowed["status"] != "candidate_temporal_exclusive":
        return proposal
    eliminated = [c["event"] for c in proposal.get("candidates", []) if c["event"] != event]
    return {**narrowed, "status": "candidate_by_elimination", "hypothesis": "temporal_exclusive_by_elimination",
            "eliminated": eliminated, "ambiguous_before_elimination": proposal["reasons"],
            "reasons": [SCOPE_NOTE, "every valid assignment gives this deposit the same removal; the other pending removals "
                                    "are taken by other deposits in all of them: " + ", ".join(eliminated)]}


def _source_blind_spots(events_doc: dict) -> list[dict]:
    """Sites whose state is unknown from some time on (pose epoch ended, or never observed)."""
    spots = [{"zone": event["zone"], "from_s": event["t_before_s"], "why": "pose_epoch_break"} for event in events_doc["events"] if event["kind"] == "pose_epoch_break"]
    for zone in events_doc["zones"]:
        if zone["kind"] == "site" and zone["status"] == "inventory_not_observable":
            spots.append({"zone": zone["zone"], "from_s": 0.0, "why": "inventory_not_observable"})
    return spots


def _observed_removal(event: dict) -> dict:
    return {"event": event["id"], "site": event["zone"], "t_before_s": event["t_before_s"], "t_after_s": event["t_after_s"], "basis": "observed"}


def _declared_removals(declared: dict) -> list[dict]:
    if declared.get("schema") != DECLARED_SCHEMA:
        raise SystemExit(f"declared removals are not {DECLARED_SCHEMA}")
    result = []
    for item in declared["removals"]:
        if not str(item.get("declared_by", "")).strip():
            raise SystemExit(f"declared removal {item.get('id')} has no 'declared_by'")
        result.append({"event": item["id"], "site": item["site"], "t_before_s": float(item["t_before_s"]), "t_after_s": float(item["t_after_s"]),
                       "basis": "operator_declared", "declared_by": item["declared_by"]})
    return result


def _overlaps(item: dict, window: tuple[float, float]) -> bool:
    return item["t_before_s"] < window[1] and item["t_after_s"] > window[0]


def _brief(event: dict) -> dict:
    return {key: event[key] for key in ("id", "zone", "kind", "t_before_s", "t_after_s") if key in event}
