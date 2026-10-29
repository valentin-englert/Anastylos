"""Memory-guided reassembly: plan the returns and check a reassembly take.

Offline and read-only. ``plan`` reads an Assembly Memory log and the site
inventory of the disassembly and lists, in reverse removal order, which tray
cell goes back to which well. ``verify`` reads the custody-events@1 file of a
reassembly take produced by custody observer v1.1 with declared start
occupancy (cells occupied, wells empty), proposes return links (source zone
-> destination zone) by the same temporal-exclusivity rule as the custody
linker, and compares every well placement with the home well from memory.

Return links are hypotheses that need operator confirmation; nothing here
writes memory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

SCHEMA = "anastylos-reassembly-returns@1"
REQUIRED_OBSERVER = "1.1.0"
DEPARTURES = {("tray_cell", "vacated"): "part left the cell", ("site", "site_emptied"): "part left the well"}
ARRIVALS = {("site", "site_filled"): "part placed in the well", ("tray_cell", "appeared"): "part placed in a cell"}


def read_memory(path: Path) -> list[dict]:
    """Observations with their latest tray link, in removal (log) order."""
    observations, links, superseded = {}, {}, set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("supersedes"):
            superseded.add(event["supersedes"])
        if event["type"] == "observation":
            observations[event["id"]] = event
        elif event["type"] == "tray_link":
            links[event["id"]] = event
    by_observation = {link["observation_id"]: link for link_id, link in links.items() if link_id not in superseded}
    return [{"observation": obs, "link": by_observation.get(obs_id)}
            for obs_id, obs in observations.items() if obs_id not in superseded]


def home_well(centroid_mm: list[float], sites: list[dict]) -> str | None:
    x, y = centroid_mm
    hits = [site["id"] for site in sites
            if site["rect_mm"][0] <= x <= site["rect_mm"][0] + site["rect_mm"][2]
            and site["rect_mm"][1] <= y <= site["rect_mm"][1] + site["rect_mm"][3]]
    return hits[0] if len(hits) == 1 else None


def plan(memory: list[dict], sites: list[dict]) -> list[dict]:
    steps = []
    for entry in reversed(memory):
        link = entry["link"]
        steps.append({
            "observation": entry["observation"]["id"],
            "cell": f"cell-{link['tray']['number']}" if link else None,
            "home_well": home_well(entry["observation"]["centroid_mm"], sites),
            "tray_link": link["id"] if link else None,
        })
    return steps


def check_events_doc(events_doc: dict, plan_cells: set[str]) -> None:
    """Refuse event logs whose names do not carry v1.1 declared-occupancy meaning.

    Sites and every cell in the plan must declare their start occupancy;
    other tray cells may stay undeclared, which means empty (the v1.0
    convention, correct for a cell that starts empty).
    """
    version = events_doc.get("provenance", {}).get("observer_version")
    if version != REQUIRED_OBSERVER:
        raise SystemExit(f"events come from observer {version!r}; reassembly needs {REQUIRED_OBSERVER}")
    undeclared = [zone["zone"] for zone in events_doc["zones"]
                  if "initially" not in zone and (zone["kind"] == "site" or zone["zone"] in plan_cells)]
    if undeclared:
        raise SystemExit(f"zones without declared start occupancy: {undeclared}")


def _movements(events: list[dict]) -> tuple[list[dict], list[dict]]:
    departures, arrivals = [], []
    for event in events:
        key = (event["zone_kind"], event["kind"])
        if key not in DEPARTURES and key not in ARRIVALS:
            continue  # reference_set, device_realigned, pose_epoch_break, ... carry no movement
        brief = {name: event[name] for name in ("id", "zone", "kind", "t_before_s", "t_after_s")}
        if key in DEPARTURES:
            departures.append({**brief, "meaning": DEPARTURES[key]})
        elif key in ARRIVALS:
            arrivals.append({**brief, "meaning": ARRIVALS[key]})
    by_time = lambda item: item["t_after_s"]  # noqa: E731
    return sorted(departures, key=by_time), sorted(arrivals, key=by_time)


def _overlaps(event: dict, window: tuple[float, float]) -> bool:
    return event["t_before_s"] < window[1] and event["t_after_s"] > window[0]


def verify(events_doc: dict, steps: list[dict]) -> dict:
    departures, arrivals = _movements(events_doc["events"])
    gaps = events_doc["sampling"]["gaps_over_max"]
    carried = {step["cell"]: step["home_well"] for step in steps if step["cell"]}  # zone -> home well of the part in it
    used, returns = set(), []
    for arrival in arrivals:
        pending = [d for d in departures if d["t_before_s"] < arrival["t_after_s"] and d["id"] not in used]
        result = {"arrival": arrival, "requires_operator_confirmation": True}
        if not pending:
            returns.append({**result, "status": "unattributed", "reasons": ["no departure precedes this arrival"]})
            continue
        window = (min(d["t_before_s"] for d in pending), arrival["t_after_s"])
        reasons = []
        if len(pending) > 1:
            reasons.append(f"{len(pending)} departures pending")
        others = [a["id"] for a in arrivals if a is not arrival and _overlaps(a, window)]
        if others:
            reasons.append("other arrivals overlap the window: " + ", ".join(others))
        late = [d["id"] for d in departures if d not in pending and _overlaps(d, window)]
        if late:
            reasons.append("other departures overlap the window: " + ", ".join(late))
        if any(gap[0] < window[1] and gap[1] > window[0] for gap in gaps):
            reasons.append("sampling gap inside the window")
        if reasons:
            returns.append({**result, "status": "ambiguous", "reasons": reasons, "candidates": pending})
            continue
        source = pending[0]
        used.add(source["id"])
        home = carried.pop(source["zone"], None)
        carried[arrival["zone"]] = home
        check = ("unknown_home" if home is None
                 else "back_in_a_cell" if arrival["zone"].startswith("cell-")
                 else "consistent_with_memory" if home == arrival["zone"]
                 else "MISMATCH_with_memory")
        returns.append({**result, "status": "candidate_temporal_exclusive", "departure": source,
                        "window_s": list(window), "memory_home_well": home, "memory_check": check})
    final = {zone: home for zone, home in carried.items() if not zone.startswith("cell-")}
    return {
        "schema": SCHEMA,
        "identity": "unverified: a return link is a temporal hypothesis, not part identity",
        "returns": [{"id": f"R-{index:04d}", **item} for index, item in enumerate(returns, 1)],
        "final_wells": final,
        "wells_without_return": sorted({s["home_well"] for s in steps if s["home_well"]} - set(final)),
        "departures_without_return": [d for d in departures if d["id"] not in used],
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "verify"):
        command = sub.add_parser(name)
        command.add_argument("--memory", required=True, type=Path)
        command.add_argument("--inventory", required=True, type=Path, help="site inventory of the disassembly")
        command.add_argument("--out", required=True, type=Path)
        if name == "verify":
            command.add_argument("--events", required=True, type=Path, help="custody-events@1 of the reassembly take")
    args = parser.parse_args()
    sites = json.loads(args.inventory.read_text(encoding="utf-8"))["sites"]
    steps = plan(read_memory(args.memory), sites)
    inputs = {"memory": {"path": str(args.memory), "sha256": _sha256(args.memory)},
              "inventory": {"path": str(args.inventory), "sha256": _sha256(args.inventory)}}
    if args.command == "plan":
        document = {"schema": "anastylos-reassembly-plan@1", "order": "reverse removal order from memory",
                    "steps": steps, "inputs": inputs}
    else:
        events_doc = json.loads(args.events.read_text(encoding="utf-8"))
        check_events_doc(events_doc, {step['cell'] for step in steps if step['cell']})
        document = {**verify(events_doc, steps), "plan": steps,
                    "inputs": {**inputs, "events": {"path": str(args.events), "sha256": _sha256(args.events)}}}
    data = (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(data)
    print(f"{args.out} sha256 {hashlib.sha256(data).hexdigest()}")
    for item in document["steps"] if args.command == "plan" else document["returns"]:
        print(json.dumps(item, ensure_ascii=False)[:220])


if __name__ == "__main__":
    main()
