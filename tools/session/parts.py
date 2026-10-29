"""Number the parts of one session phase and list the questions the evidence leaves open.

Pure functions over the custody linker output (custody-links@1) and, when
available, the adaptive source registry. A part is a deposit into a tray
cell: it gets a stable number from its phase and cell (``P2-C08``); further
deposits into the same cell get a letter (``P1-C01b``) and stay a same-cell
group without per-part identity. A part is *linked* when the linker found a
unique source (exclusive or by elimination); everything else becomes a
question for the operator, never a fact.
"""

from __future__ import annotations

LINKED = ("candidate_temporal_exclusive", "candidate_by_elimination", "candidate_by_hand_order")


def _sources(removal: dict, registry: dict[str, dict]) -> list[dict]:
    sites = removal.get("sites") or [removal["site"]]
    return [{"site": site, **({"centroid_mm": registry[site]["geometry"]["centroid_mm"],
                               "area_mm2": registry[site]["geometry"]["area_mm2"]} if site in registry else {})}
            for site in sites]


def number_parts(custody: dict, phase: str, registry: dict[str, dict] | None = None) -> tuple[list[dict], list[dict]]:
    """Return (parts, questions) for one phase."""
    registry = registry or {}
    parts, questions, per_cell = [], [], {}
    for proposal in custody["proposals"]:
        deposit = proposal["deposit"]
        cell = deposit["zone"]
        count = per_cell.get(cell, 0)
        per_cell[cell] = count + 1
        number = f"{phase}-C{int(cell.split('-')[1]):02d}{'' if count == 0 else chr(ord('a') + count)}"
        part = {"part": number, "cell": cell, "deposit_event": deposit["id"],
                "deposit_window_s": [deposit["t_before_s"], deposit["t_after_s"]],
                "same_cell_group": count > 0 or None, "link": proposal["id"], "status": proposal["status"],
                "autonomy": proposal.get("autonomy", "automatic")}
        if proposal["status"] in LINKED:
            removal = proposal["removal"]
            part.update({"removal_event": removal["event"], "removal_events": removal.get("events", [removal["event"]]),
                         "removal_window_s": [removal["t_before_s"], removal["t_after_s"]],
                         "sources": _sources(removal, registry)})
        else:
            candidates = [{"removal_event": c["event"], "window_s": [c["t_before_s"], c["t_after_s"]],
                           "sources": _sources(c, registry)} for c in proposal.get("candidates", [])]
            questions.append({"id": f"Q-{phase}-{len(questions) + 1:02d}", "kind": "source_of_part", "part": number,
                              "cell": cell, "deposit_window_s": part["deposit_window_s"],
                              "why": proposal.get("reasons", []), "candidates": candidates,
                              "ask": f"Woher kam das Teil in {cell} (abgelegt {deposit['t_before_s']:.1f}-{deposit['t_after_s']:.1f} s)?"})
        parts.append(part)
    offered = {c["removal_event"] for q in questions for c in q.get("candidates", [])}
    for removal in custody.get("removals_without_linked_deposit", []):
        if removal["event"] in offered:
            continue  # already a candidate in a question about a part
        questions.append({"id": f"Q-{phase}-{len(questions) + 1:02d}", "kind": "destination_of_removal",
                          "removal_event": removal["event"], "window_s": [removal["t_before_s"], removal["t_after_s"]],
                          "sources": _sources(removal, registry),
                          "ask": f"Bei {removal['t_before_s']:.1f}-{removal['t_after_s']:.1f} s wurde etwas entnommen, "
                                 "aber in keiner Zelle abgelegt. Wohin kam es?"})
    return parts, questions
