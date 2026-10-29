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

Linker 1.2 (custody v1.3): declared external deposits
(``custody-declared-deposits@1``) record parts the operator put somewhere
the camera does not watch. Such a declaration closes a removal only when it
names that removal's source (an event id or a site) with written evidence;
the named removal must exist before the declared time and must not be
closed twice or contradict an automatic link. A declaration without a named
source closes nothing. A closed removal is no longer pending for deposits
observed after the declaration. Every proposal states its ``autonomy``:
``automatic``, or ``operator_assisted`` when a declared removal or any
applied declaration could have shaped its candidate set; counts keep the
two apart.

Linker 1.3: with site geometry (the inventory used by the observer), observed
removals are grouped into one removal *event* when they changed in the same
observed interval (start and end within ``same_interval_s``) and their site
rectangles are close: gap at most max(``min_gap_mm``, ``size_ratio`` x
sqrt(area of the larger site)), chained transitively. A large part that
uncovers many sites at once is then one removal, not many. Same time alone
never groups distant sites, a site that changes twice gives two removals
(stacked parts), and operator-declared removals are never grouped. Without
geometry nothing is grouped.

Linker 1.4: fragments join when they *start* in the same observed interval
(start within ``same_interval_s``) and their windows overlap; the end may
differ because one site can stay covered longer. A grouped removal event
whose total changed area is below ``min_removal_area_mm2`` (the same
minimum a tray cell needs to count a part) is reported under
``minor_site_changes`` and not used as a removal: a part that small could
never be seen deposited. Both rules apply only with site geometry.

Linker 1.6 (hand order): when the events carry colour-free ``hand_visits``,
a pending removal can only be the source of a deposit if the hand visited
one of its sites inside the removal's window *before* the visit to the
deposit's cell began (a part cannot be put down before it was picked up).
If exactly one pending removal passes, the deposit is linked
``candidate_by_hand_order``; overlapping deposits or removals are then
resolved by that order, but sampling gaps, unobservable sources and an
occupied cell still block. Assumption: one part is carried at a time.
"""

from __future__ import annotations

LINKS_SCHEMA = "custody-links@1"
DECLARED_SCHEMA = "custody-declared-removals@1"
DECLARED_DEPOSITS_SCHEMA = "custody-declared-deposits@1"
DEPOSIT_KINDS = ("appeared", "changed_while_occupied")
LINK_STATUSES = ("candidate_temporal_exclusive", "candidate_by_elimination", "candidate_by_hand_order")
GROUPING = {"same_interval_s": 0.25, "min_gap_mm": 2.0, "size_ratio": 0.5, "min_removal_area_mm2": 25.0}
SCOPE_NOTE = "exclusive only among inventoried sites and watched tray cells; parts taken from anywhere else are invisible to this check"


def link(events_doc: dict, declared: dict | None = None, declared_deposits: dict | None = None,
         site_rects: dict | None = None) -> dict:
    events = events_doc["events"]
    ppm = (events_doc.get("rectified") or {}).get("px_per_mm")
    removals = [_observed_removal(event, ppm) for event in events if event["kind"] == "site_changed"]
    minor = []
    if site_rects is not None:
        removals = group_removals(removals, site_rects)
        minor = [r for r in removals if r.get("changed_area_mm2") is not None and r["changed_area_mm2"] < GROUPING["min_removal_area_mm2"]]
        removals = [r for r in removals if r not in minor]
    if declared is not None:
        removals += _declared_removals(declared)
    removals.sort(key=lambda item: item["t_after_s"])
    deposits = sorted((event for event in events if event["zone_kind"] == "tray_cell" and event["kind"] in DEPOSIT_KINDS), key=lambda item: item["t_after_s"])
    blind_sites = _source_blind_spots(events_doc)
    gaps = events_doc["sampling"]["gaps_over_max"]

    externals = _resolve_declared_deposits(declared_deposits, removals) if declared_deposits is not None else []
    closed = {item["removal"]["event"]: item["t_s"] for item in externals if item["status"] == "closes_removal"}
    options = _assignment_options(deposits, removals, closed)
    proposals, linked = [], set()
    for deposit in deposits:
        pending = [removal for removal in removals if removal["t_before_s"] < deposit["t_after_s"] and removal["event"] not in linked
                   and not _closed_before(removal, closed, deposit["t_after_s"])]
        proposal = _judge(deposit, pending, deposits, removals, blind_sites, gaps)
        if proposal["status"] == "ambiguous":
            proposal = _by_elimination(proposal, options.get(deposit["id"]), deposit, deposits, removals, blind_sites, gaps)
        if proposal["status"] == "ambiguous" and events_doc.get("hand_visits"):
            proposal = _by_hand_order(proposal, deposit, pending, events_doc["hand_visits"], blind_sites, gaps)
        if proposal["status"] in LINK_STATUSES:
            linked.add(proposal["removal"]["event"])
        proposal["autonomy"] = _autonomy(proposal, deposit, externals)
        proposals.append(proposal)
    _flag_conflicts(proposals, externals)
    linked = {p["removal"]["event"] for p in proposals if p["status"] in LINK_STATUSES}
    closed_events = {item["removal"]["event"] for item in externals if item["status"] == "closes_removal"}
    orphans = [removal for removal in removals if removal["event"] not in linked and removal["event"] not in closed_events]
    doc = {
        "schema": LINKS_SCHEMA,
        "scope_note": SCOPE_NOTE,
        "identity": "unverified: every proposal needs operator confirmation",
        "proposals": [{"id": f"L-{index:04d}", **proposal} for index, proposal in enumerate(proposals, 1)],
        "removals_without_linked_deposit": orphans,
        "source_blind_spots": blind_sites,
        "counts": _counts(proposals, externals),
    }
    if declared_deposits is not None:
        doc["declared_external_deposits"] = externals
    if site_rects is not None:
        doc["removal_grouping"] = {**GROUPING, "groups": sum(1 for r in removals if len(r.get("events", [])) > 1)}
        doc["minor_site_changes"] = minor
    return doc


def _rect_gap(a: list[float], b: list[float]) -> float:
    dx = max(0.0, max(a[0], b[0]) - min(a[0] + a[2], b[0] + b[2]))
    dy = max(0.0, max(a[1], b[1]) - min(a[1] + a[3], b[1] + b[3]))
    return (dx * dx + dy * dy) ** 0.5


def group_removals(removals: list[dict], site_rects: dict) -> list[dict]:
    """One removal event for observed site changes in the same interval whose sites are close (see module docstring)."""
    observed = [r for r in removals if r["basis"] == "observed" and r["site"] in site_rects]
    others = [r for r in removals if r not in observed]
    parent = list(range(len(observed)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    joins = []
    for i, a in enumerate(observed):
        for j in range(i + 1, len(observed)):
            b = observed[j]
            same_start = abs(a["t_before_s"] - b["t_before_s"]) <= GROUPING["same_interval_s"]
            overlap = a["t_before_s"] < b["t_after_s"] and b["t_before_s"] < a["t_after_s"]
            if not (same_start and overlap):
                continue
            ra, rb = site_rects[a["site"]], site_rects[b["site"]]
            larger = max(ra[2] * ra[3], rb[2] * rb[3])
            limit = max(GROUPING["min_gap_mm"], GROUPING["size_ratio"] * larger ** 0.5)
            gap = _rect_gap(ra, rb)
            if gap <= limit:
                parent[find(i)] = find(j)
                joins.append((i, j, round(gap, 2), round(limit, 2)))
    groups: dict[int, list[int]] = {}
    for i in range(len(observed)):
        groups.setdefault(find(i), []).append(i)
    grouped = []
    for members in groups.values():
        items = [observed[i] for i in members]
        if len(items) == 1:
            grouped.append(items[0])
            continue
        first = min(items, key=lambda r: (r["t_before_s"], r["event"]))
        areas = [r.get("changed_area_mm2") for r in items]
        grouped.append({**first, "events": sorted(r["event"] for r in items), "sites": sorted(r["site"] for r in items),
                        "t_before_s": min(r["t_before_s"] for r in items), "t_after_s": max(r["t_after_s"] for r in items),
                        "changed_area_mm2": None if None in areas else round(sum(areas), 1),
                        "grouping": [{"a": observed[i]["event"], "b": observed[j]["event"], "gap_mm": g, "limit_mm": lim}
                                     for i, j, g, lim in joins if i in members]})
    return sorted(grouped + others, key=lambda r: r["t_after_s"])


def _cell_visit_start(deposit: dict, visits: dict) -> float | None:
    """Start of the last hand visit to the deposit's cell that overlaps the deposit window."""
    spans = [s for s in visits.get(deposit["zone"], []) if s[0] <= deposit["t_after_s"] and s[1] >= deposit["t_before_s"]]
    return max(s[0] for s in spans) if spans else None


def _source_visited_before(removal: dict, visits: dict, t_s: float) -> list[list[float]]:
    sites = removal.get("sites") or [removal["site"]]
    return [s for site in sites for s in visits.get(site, [])
            if s[0] < t_s and s[1] >= removal["t_before_s"] and s[0] <= removal["t_after_s"]]


def _by_hand_order(proposal: dict, deposit: dict, pending: list[dict], visits: dict, blind_sites: list[dict], gaps: list) -> dict:
    start = _cell_visit_start(deposit, visits)
    if start is None or not pending:
        return proposal
    possible = [r for r in pending if _source_visited_before(r, visits, start)]
    if len(possible) != 1:
        return proposal
    removal = possible[0]
    window = (removal["t_before_s"], deposit["t_after_s"])
    blockers = []
    if any(gap[0] < window[1] and gap[1] > window[0] for gap in gaps):
        blockers.append("sampling gap")
    if any(b["from_s"] <= window[1] and (b.get("until_s") is None or b["until_s"] > window[0]) for b in blind_sites):
        blockers.append("unobservable source")
    if deposit["kind"] != "appeared":
        blockers.append("occupied cell")
    if blockers:
        return proposal
    excluded = [r["event"] for r in pending if r is not removal]
    return {**{k: v for k, v in proposal.items() if k != "candidates"}, "status": "candidate_by_hand_order",
            "hypothesis": "hand_order", "removal": removal, "custody_window_s": list(window), "eliminated": excluded,
            "ambiguous_before_hand_order": proposal["reasons"],
            "hand_evidence": {"cell_visit_start_s": start,
                              "source_visits_before": _source_visited_before(removal, visits, start)},
            "reasons": [SCOPE_NOTE, f"the hand visited only this removal's sites before reaching {deposit['zone']} "
                                    f"at {start} s; the other pending removals were first touched later: " + ", ".join(excluded)]}


def _closed_before(removal: dict, closed: dict, t_s: float) -> bool:
    return removal["event"] in closed and closed[removal["event"]] < t_s


def _resolve_declared_deposits(declared: dict, removals: list[dict]) -> list[dict]:
    """Validate operator-declared external deposits; only an explicitly named source closes a removal."""
    if declared.get("schema") != DECLARED_DEPOSITS_SCHEMA:
        raise SystemExit(f"declared deposits are not {DECLARED_DEPOSITS_SCHEMA}")
    results, taken = [], {}
    for item in sorted(declared["deposits"], key=lambda d: float(d["t_s"])):
        for key in ("id", "location", "declared_by"):
            if not str(item.get(key, "")).strip():
                raise SystemExit(f"declared deposit {item.get('id')} has no {key!r}")
        record = {"id": item["id"], "location": item["location"], "t_s": float(item["t_s"]), "basis": "operator_declared",
                  "declared_by": item["declared_by"], "evidence": item.get("evidence")}
        source = item.get("source") or {}
        if not source:
            results.append({**record, "status": "no_named_source", "note": "closes nothing; open removals stay open"})
            continue
        if not str(item.get("evidence", "")).strip():
            results.append({**record, "status": "rejected", "note": "a named source needs written evidence"})
            continue
        before = [r for r in removals if r["t_before_s"] < record["t_s"]]
        if "removal_event" in source:
            match = [r for r in before if source["removal_event"] in r.get("events", [r["event"]])]
        else:
            match = sorted((r for r in before if source.get("site") in r.get("sites", [r["site"]])), key=lambda r: r["t_after_s"])[-1:]
        if not match:
            results.append({**record, "status": "rejected", "note": f"no removal matching {source} before {record['t_s']} s"})
            continue
        removal = match[0]
        if removal["event"] in taken:
            results.append({**record, "status": "rejected", "removal": removal, "note": f"removal already closed by {taken[removal['event']]}"})
            continue
        taken[removal["event"]] = item["id"]
        results.append({**record, "status": "closes_removal", "removal": removal})
    return results


def _autonomy(proposal: dict, deposit: dict, externals: list[dict]) -> str:
    if proposal.get("removal", {}).get("basis") == "operator_declared":
        return "operator_assisted"
    shaped = any(item["status"] == "closes_removal" and item["t_s"] < deposit["t_after_s"] for item in externals)
    return "operator_assisted" if shaped and proposal["status"].startswith("candidate") else "automatic"


def _flag_conflicts(proposals: list[dict], externals: list[dict]) -> None:
    """An operator closure of a removal that an automatic link already used is a contradiction, not a correction."""
    for item in externals:
        if item["status"] != "closes_removal":
            continue
        for proposal in proposals:
            if proposal["status"].startswith("candidate") and proposal["removal"]["event"] == item["removal"]["event"]:
                item["status"] = "conflict"
                item["note"] = f"an automatic link already used this removal for {proposal['deposit']['zone']}"
                proposal["status"] = "ambiguous"
                proposal["reasons"] = [f"operator declared this removal deposited at {item['location']} ({item['id']})"]
                proposal["candidates"] = [proposal.pop("removal")]


def _counts(proposals: list[dict], externals: list[dict]) -> dict:
    candidates = [p for p in proposals if p["status"].startswith("candidate")]
    return {
        "deposits": len(proposals),
        "candidate_links": len(candidates),
        "automatic_links": sum(p["autonomy"] == "automatic" for p in candidates),
        "operator_assisted_links": sum(p["autonomy"] == "operator_assisted" for p in candidates),
        "ambiguous": sum(p["status"] == "ambiguous" for p in proposals),
        "unattributed": sum(p["status"] == "unattributed" for p in proposals),
        "declared_closures": sum(item["status"] == "closes_removal" for item in externals),
        "declarations_rejected_or_conflicting": sum(item["status"] in ("rejected", "conflict") for item in externals),
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
    blind = sorted({item["zone"] for item in blind_sites
                    if item["from_s"] <= window[1] and (item.get("until_s") is None or item["until_s"] > window[0])})
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


def _assignment_options(deposits: list[dict], removals: list[dict], closed: dict | None = None) -> dict[str, set | None]:
    """deposit id -> removal events it takes across all valid assignments (None: too many to enumerate)."""
    options: dict[str, set] = {deposit["id"]: set() for deposit in deposits}
    count = 0

    def walk(index: int, used: frozenset) -> bool:
        nonlocal count
        if index == len(deposits):
            count += 1
            return count <= MAX_ASSIGNMENTS
        deposit = deposits[index]
        open_removals = [r["event"] for r in removals if r["t_before_s"] < deposit["t_after_s"] and r["event"] not in used
                         and not _closed_before(r, closed or {}, deposit["t_after_s"])]
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
    # Linker 1.5: when the device was re-acquired later (a newer device pose epoch in the events), a blind
    # spot of an older epoch ends there: the new epoch watches the device again, and a later removal at the
    # old place appears as a new source. Without re-acquisition a blind spot never ends.
    starts = sorted(d["valid_from_s"] for d in events_doc.get("devices", []))

    def until(t_s: float) -> float | None:
        later = [s for s in starts if s > t_s]
        return later[0] if later else None

    spots = [{"zone": event["zone"], "from_s": event["t_before_s"], "until_s": until(event["t_before_s"]), "why": "pose_epoch_break"}
             for event in events_doc["events"] if event["kind"] == "pose_epoch_break"]
    for zone in events_doc["zones"]:
        if zone["kind"] == "site" and zone["status"] == "inventory_not_observable":
            spots.append({"zone": zone["zone"], "from_s": 0.0, "until_s": None, "why": "inventory_not_observable"})
    return spots


def _observed_removal(event: dict, ppm: float | None = None) -> dict:
    removal = {"event": event["id"], "site": event["zone"], "t_before_s": event["t_before_s"], "t_after_s": event["t_after_s"], "basis": "observed"}
    if ppm and "changed_area_px" in event:
        removal["changed_area_mm2"] = round(event["changed_area_px"] / ppm ** 2, 1)
    return removal


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
