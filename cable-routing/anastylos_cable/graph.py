"""Evidence-backed assembly dependency graph. Read-only, stdlib only.

Consumes assembly-memory JSONL events plus the change-detection
result.json files they cite. Nodes are observations; edges are
directed ``before -> after`` (removal order constraints).

Edge kinds:
- temporal: A was removed before B (timeline only, never a constraint).
- spatial: A visibly overlaps or covers B (observation, not a constraint).
- candidate: removing A might be necessary before B (needs confirmation).
- confirmed: an operator confirmation states A must precede B.

Temporal order alone never becomes confirmed. A reversed disassembly
timeline is a reassembly PROPOSAL, never a verified procedure.
"""

from __future__ import annotations

import json
from pathlib import Path

def _load_store():
    """Import the memory reader lazily; cable.py puts it on sys.path."""
    from anastylos_memory.store import CorruptLogError, load_events
    return CorruptLogError, load_events


def _live_observations(events: list[dict]) -> tuple[list[dict], list[str]]:
    """Observations not superseded, in log order; plus superseded ids."""
    replaced = {event["supersedes"] for event in events
                if event.get("supersedes")}
    live = [event for event in events
            if event.get("type") == "observation"
            and event.get("id") not in replaced]
    return live, sorted(replaced)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _region_lookup(result: dict | None, label: str) -> dict | None:
    if not isinstance(result, dict):
        return None
    for region in result.get("regions") or []:
        if region.get("label") == label:
            return region
    return None


def _attach_regions(observations: list[dict], results_dir: str | None,
                    memory_path: Path) -> tuple[dict, list[dict]]:
    """Resolve each observation's cited region. Missing refs are reported."""
    regions, missing = {}, []
    for event in observations:
        evidence = event.get("evidence") or {}
        cited = evidence.get("result_json") or ""
        candidates = [Path(cited)]
        if results_dir:
            candidates.append(Path(results_dir) / Path(cited).name)
        candidates.append(memory_path.parent / cited)
        result, used = None, None
        for candidate in candidates:
            result = _read_json(candidate)
            if result is not None:
                used = candidate
                break
        region = _region_lookup(result, evidence.get("region", ""))
        if region is None:
            missing.append({"observation_id": event.get("id"),
                            "cited": cited or None,
                            "region": evidence.get("region") or None,
                            "reason": "result file unreadable"
                            if result is None else "region label not found"})
            continue
        regions[event["id"]] = {
            "bbox_mm": region.get("bbox_mm"),
            "centroid_mm": region.get("centroid_mm"),
            "view": (result.get("rectified") or {}).get("view"),
            "result_json": str(used),
            "pair_id": evidence.get("pair_id"),
            "region": evidence.get("region"),
            "result_sha256": evidence.get("result_sha256"),
        }
    return regions, missing


def _contains(outer, point) -> bool:
    x, y, w, h = outer
    return x <= point[0] <= x + w and y <= point[1] <= y + h


def _overlap_fraction(first, second) -> float:
    x0 = max(first[0], second[0])
    y0 = max(first[1], second[1])
    x1 = min(first[0] + first[2], second[0] + second[2])
    y1 = min(first[1] + first[3], second[1] + second[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    areas = [first[2] * first[3], second[2] * second[3]]
    if min(areas) <= 0:
        return 0.0
    return (x1 - x0) * (y1 - y0) / min(areas)


def default_reasoner(nodes: list[dict], regions: dict) -> list[dict]:
    """Spatial + temporal candidate prerequisites. Replaceable adapter.

    A candidate needs BOTH visible overlap/cover in one shared view AND
    earlier removal in the timeline. Pure temporal order yields nothing.
    """
    order = {node["id"]: index for index, node in enumerate(nodes)}
    found = []
    for index, first in enumerate(nodes):
        for second in nodes[index + 1:]:
            earlier, later = first, second
            first_region = regions.get(first["id"])
            second_region = regions.get(second["id"])
            if first_region is None or second_region is None:
                continue
            if first_region.get("view") != second_region.get("view"):
                continue
            first_box = first_region.get("bbox_mm")
            second_box = second_region.get("bbox_mm")
            if not first_box or not second_box:
                continue
            covers = _contains(first_box, second_region["centroid_mm"])
            covered_by = _contains(second_box, first_region["centroid_mm"])
            overlap = _overlap_fraction(first_box, second_box)
            if not (covers or covered_by or overlap > 0.30):
                continue
            before, after = earlier, later
            if covers and not covered_by:
                basis = "earlier region covers later centroid"
            elif covered_by and not covers:
                basis = "later region covers earlier centroid"
            else:
                basis = "regions overlap; timeline order only suggests"
            found.append({
                "before": before["id"], "after": after["id"],
                "kind": "candidate",
                "evidence": {
                    "type": "spatial+temporal",
                    "view": first_region.get("view"),
                    "overlap_fraction": round(overlap, 3),
                    "covers": covers, "covered_by": covered_by,
                    "timeline": [order[before["id"]], order[after["id"]]],
                    "regions": [first_region["region"],
                                second_region["region"]],
                },
                "basis": basis,
                "confirmed_by": None,
            })
    found.sort(key=lambda edge: (edge["before"], edge["after"]))
    return found


def _load_confirmations(path: str | None,
                        known: set[str]) -> tuple[list[dict], list[dict]]:
    """Operator-confirmed edges. Unknown ids are reported, never added."""
    if not path:
        return [], []
    data = _read_json(Path(path))
    entries = data if isinstance(data, list) else (data or {}).get("edges", [])
    confirmed, missing = [], []
    for entry in entries:
        before, after = entry.get("before"), entry.get("after")
        by = entry.get("confirmed_by")
        if before not in known or after not in known:
            missing.append({"before": before, "after": after,
                            "reason": "unknown observation id"})
            continue
        if not isinstance(by, str) or not by.strip():
            missing.append({"before": before, "after": after,
                            "reason": "confirmation without confirmed_by"})
            continue
        confirmed.append({"before": before, "after": after,
                          "kind": "confirmed",
                          "evidence": {"type": "human",
                                       "note": entry.get("note")},
                          "basis": "operator confirmation",
                          "confirmed_by": by})
    confirmed.sort(key=lambda edge: (edge["before"], edge["after"]))
    return confirmed, missing


def _find_cycle(nodes: list[str], edges: list[dict]) -> list[str] | None:
    """One directed cycle, or None. Deterministic depth-first search."""
    outgoing: dict[str, list[str]] = {node: [] for node in nodes}
    for edge in edges:
        outgoing[edge["before"]].append(edge["after"])
    for node in outgoing:
        outgoing[node].sort()
    visited: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str):
        visited[node] = 1
        stack.append(node)
        for target in outgoing[node]:
            if target not in visited:
                hit = visit(target)
                if hit is not None:
                    return hit
            elif visited[target] == 1:
                return stack[stack.index(target):] + [target]
        stack.pop()
        visited[node] = 2
        return None

    for node in sorted(nodes):
        if node not in visited:
            hit = visit(node)
            if hit is not None:
                return hit
    return None


def _topo_sort(nodes: list[str], edges: list[dict]) -> list[str]:
    """Lexicographically smallest topological order (Kahn)."""
    incoming: dict[str, int] = {node: 0 for node in nodes}
    outgoing: dict[str, list[str]] = {node: [] for node in nodes}
    for edge in edges:
        outgoing[edge["before"]].append(edge["after"])
        incoming[edge["after"]] += 1
    ready = sorted(node for node in nodes if incoming[node] == 0)
    order = []
    while ready:
        node = ready.pop(0)
        order.append(node)
        for target in sorted(outgoing[node]):
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
        ready.sort()
    return order


def _count_orders(nodes: list[str], edges: list[dict]) -> int | None:
    """Exact count of valid orders; None when too large to enumerate."""
    if len(nodes) > 20:
        return None
    index = {node: i for i, node in enumerate(nodes)}
    before = [0] * len(nodes)
    for edge in edges:
        before[index[edge["after"]]] |= 1 << index[edge["before"]]
    total = 1 << len(nodes)
    ways = [0] * total
    ways[0] = 1
    for mask in range(total):
        for i in range(len(nodes)):
            if not mask & (1 << i) and (before[i] & mask) == before[i]:
                ways[mask | (1 << i)] += ways[mask]
    return ways[-1]


def _levels(nodes: list[str], order: list[str],
            edges: list[dict]) -> list[list[str]]:
    """Parallel groups: each node's longest confirmed-chain depth."""
    depth = {node: 0 for node in nodes}
    for node in order:
        for edge in edges:
            if edge["before"] == node:
                depth[edge["after"]] = max(depth[edge["after"]],
                                           depth[node] + 1)
    groups: dict[int, list[str]] = {}
    for node in order:
        groups.setdefault(depth[node], []).append(node)
    return [groups[key] for key in sorted(groups)]


def build_graph(memory_path: str, results_dir: str | None = None,
                confirmations_path: str | None = None,
                reasoner=None) -> dict:
    """Build the dependency graph. Never writes to the memory store."""
    path = Path(memory_path)
    try:
        CorruptLogError, load_events = _load_store()
    except ImportError:
        return {"status": "unavailable",
                "reason": "assembly-memory store module not importable",
                "nodes": [], "timeline": [], "confirmed": [], "candidates": [],
                "cycles": [], "orderings": None, "reassembly": None}
    try:
        events = load_events(path)
    except (CorruptLogError, OSError) as exc:
        return {"status": "unavailable", "reason": f"memory log: {exc}",
                "nodes": [], "timeline": [], "confirmed": [], "candidates": [],
                "cycles": [], "orderings": None, "reassembly": None}
    if not events:
        return {"status": "unavailable", "reason": "memory log has no events",
                "nodes": [], "timeline": [], "confirmed": [], "candidates": [],
                "cycles": [], "orderings": None, "reassembly": None}
    live, superseded = _live_observations(events)
    nodes = [{"id": event["id"], "session": event.get("session"),
              "source_view": event.get("source_view"),
              "timestamp": event.get("timestamp"),
              "evidence": event.get("evidence")}
             for event in live]
    regions, missing_regions = _attach_regions(live, results_dir, path)
    known = {node["id"] for node in nodes}
    confirmed, missing_confirm = _load_confirmations(confirmations_path, known)
    reason = reasoner or default_reasoner
    candidates = [edge for edge in reason(nodes, regions)
                  if {edge["before"], edge["after"]} <= known]
    confirmed_pairs = {(edge["before"], edge["after"]) for edge in confirmed}
    candidates = [edge for edge in candidates
                  if (edge["before"], edge["after"]) not in confirmed_pairs]
    contradictions = [edge for edge in candidates
                      if (edge["after"], edge["before"]) in confirmed_pairs]
    ids = [node["id"] for node in nodes]
    confirmed_cycle = _find_cycle(ids, confirmed)
    candidate_cycle = _find_cycle(ids, confirmed + candidates)
    cycles = []
    if confirmed_cycle:
        cycles.append({"kind": "confirmed", "nodes": confirmed_cycle})
    if candidate_cycle and candidate_cycle != confirmed_cycle:
        cycles.append({"kind": "candidate", "nodes": candidate_cycle})
    orderings, reassembly = None, None
    if not confirmed_cycle:
        order = _topo_sort(ids, confirmed)
        orderings = {"canonical_disassembly": order,
                     "count_valid": _count_orders(ids, confirmed),
                     "parallel_groups": _levels(ids, order, confirmed)}
        constrained = {edge["before"] for edge in confirmed} | \
            {edge["after"] for edge in confirmed}
        unresolved = [node for node in ids if node not in constrained]
        reassembly = {
            "proposal": list(reversed(order)),
            "unresolved": unresolved,
            "warning": "proposal only: reversed disassembly order is NOT "
                       "a verified reassembly procedure; every step needs "
                       "operator confirmation",
        }
    if not nodes:
        status, reason_text = "unknown", "no live observations"
    elif confirmed_cycle:
        status = "ambiguous"
        reason_text = "confirmed dependencies contain a cycle"
    elif contradictions or missing_confirm:
        status = "ambiguous"
        reason_text = "candidate contradicts confirmed edge" \
            if contradictions else "confirmations reference unknown ids"
    elif not confirmed and not candidates:
        status, reason_text = "unknown", \
            "observations exist but no dependency evidence resolved"
    else:
        status, reason_text = "ok", None
    return {
        "status": status, "reason": reason_text,
        "memory": str(path),
        "nodes": nodes,
        "timeline": [node["id"] for node in nodes],
        "superseded": superseded,
        "regions_resolved": len(regions),
        "missing_regions": missing_regions,
        "confirmed": confirmed,
        "candidates": candidates,
        "contradictions": contradictions,
        "missing_confirmations": missing_confirm,
        "cycles": cycles,
        "orderings": orderings,
        "reassembly": reassembly,
    }


def to_dot(graph: dict) -> str:
    """Graphviz DOT: solid confirmed edges, dashed candidate edges."""
    lines = ["digraph assembly {", '  rankdir="LR";',
             '  node [shape=box, fontname="Helvetica"];']
    cyclic = {node for cycle in graph.get("cycles", [])
              for node in cycle.get("nodes", [])}
    for node in graph.get("nodes", []):
        ident = node["id"]
        label = f"{ident}\\n{node.get('source_view', '')}"
        if ident in cyclic:
            lines.append(f'  "{ident}" [label="{label}", color=red];')
        else:
            lines.append(f'  "{ident}" [label="{label}"];')
    for edge in graph.get("confirmed", []):
        lines.append(f'  "{edge["before"]}" -> "{edge["after"]}" '
                     f'[label="confirmed"];')
    for edge in graph.get("candidates", []):
        lines.append(f'  "{edge["before"]}" -> "{edge["after"]}" '
                     f'[style=dashed, label="candidate"];')
    lines.append("}")
    return "\n".join(lines) + "\n"
