"""Build the session package a guide (Muse) answers from: Assembly Memory as an evidence graph.

Offline and read-only. Every fact in the package carries the id it comes
from (obs-, link-, L-, E-, R-). Nothing device-specific is assumed: source
names come only from the session's own inventory (an operator label) or
stay anonymous coordinates, and install order is derived only from the
observed removal order unless a dependency was confirmed. What was not
observed is listed explicitly under ``unknowns`` so the guide can say so.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

SCHEMA = "anastylos-session-package@1"
RULES_FILE = Path(__file__).with_name("GUIDE_RULES.md")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _source_site(centroid_mm: list[float], sites: list[dict]) -> dict:
    x, y = centroid_mm
    for site in sites:
        sx, sy, w, h = site["rect_mm"]
        if sx <= x <= sx + w and sy <= y <= sy + h:
            return {"site": site["id"], "label_basis": "operator inventory label", "rect_mm": site["rect_mm"]}
    return {"site": None, "label_basis": "anonymous: no inventory site contains the centroid"}


def _custody_history(custody: dict | None, cell: str | None) -> list[dict]:
    if not custody or not cell:
        return []
    history = []
    for proposal in custody["proposals"]:
        if proposal["deposit"]["zone"] == cell:
            history.append({"proposal": proposal["id"], "status": proposal["status"],
                            "deposit_event": proposal["deposit"]["id"],
                            "removal_site": (proposal.get("removal") or {}).get("site")})
    return history


def _storage_by_target(events: list[dict]) -> dict[str, dict]:
    storage = {}
    for event in events:
        if event["type"] == "tray_link":
            storage[event["observation_id"]] = {"location": f"cell-{event['tray']['number']}", "record": event["id"],
                                                "basis": event["basis"]["kind"], "confirmed_by": event["basis"].get("confirmed_by"),
                                                "note": event["basis"].get("note")}
        elif event["type"] == "storage_link":
            storage[event["target_id"]] = {"location": event["location"], "record": event["id"], "basis": "human",
                                           "confirmed_by": event["basis"]["confirmed_by"], "note": event["basis"].get("note")}
    return storage


def _part(event: dict, index: int, phase: str | None, stored: dict | None, sites: list[dict], custody: dict | None) -> dict:
    if event["type"] == "observation":
        source = {**_source_site(event["centroid_mm"], sites), "centroid_mm": event["centroid_mm"], "bbox_mm": event["bbox_mm"],
                  "evidence_pair": event["evidence"]["pair_id"], "evidence_result_sha256": event["evidence"].get("result_sha256"),
                  "note": event.get("note")}
        basis, label = "automatic", None
    else:
        source = {"described_as": event["source"], "evidence_note": event.get("evidence_note")}
        basis, label = "operator_declared", event["operator_label"]
    cell = (stored or {}).get("location") if str((stored or {}).get("location", "")).startswith("cell-") else None
    return {
        "id": event["id"],
        "basis": basis,
        "operator_label": label,
        "removal_index": index,
        "phase": phase,
        "identity": "unknown: evidence of a removal, not a verified part" if basis == "automatic"
                    else "operator's label, not verified by the system",
        "source": source,
        "stored_in": stored,
        "custody_history": _custody_history(custody, cell),
    }


def _install_steps(parts: list[dict]) -> list[dict]:
    """Reverse of the given removal order; consecutive members of one interchangeable group become one step."""
    steps = []
    for part in reversed(parts):
        target = part["source"].get("site") or part["source"].get("described_as") or part["source"].get("centroid_mm")
        group = part.get("group")
        if group and steps and steps[-1].get("group") == group["id"]:
            steps[-1]["parts"].append(part["id"])
            steps[-1]["to"].append(target)
            continue
        steps.append({"parts": [part["id"]], "label": part["operator_label"], "basis": part["basis"],
                      "from": (part["stored_in"] or {}).get("location"), "to": [target], "phase": part["phase"],
                      **({"group": group["id"], "rule": "any member into any listed source"} if group else {})})
    for number, step in enumerate(steps, start=1):
        step["step"] = number
    return steps


def build(memory_path: Path, inventory_path: Path | None, custody_path: Path | None,
          returns_path: Path | None) -> dict:
    events = _read_jsonl(memory_path)
    superseded = {event["supersedes"] for event in events if event.get("supersedes")}
    live = [e for e in events if e["id"] not in superseded]
    sites = json.loads(inventory_path.read_text(encoding="utf-8"))["sites"] if inventory_path else []
    custody = json.loads(custody_path.read_text(encoding="utf-8")) if custody_path else None
    storage = _storage_by_target(live)
    groups = {member: group for group in live if group["type"] == "compatible_group" for member in group["members"]}
    phases = {e["id"]: e["label"] for e in live if e["type"] == "phase"}

    parts, phase = [], None
    for event in live:
        if event["type"] == "phase":
            phase = event["id"]
        elif event["type"] in ("observation", "declared_removal"):
            part = _part(event, len(parts) + 1, event.get("phase", phase), storage.get(event["id"]), sites, custody)
            if event["id"] in groups:
                group = groups[event["id"]]
                part["group"] = {"id": group["id"], "label": group.get("label"), "members": group["members"],
                                 "basis": group["basis"]["kind"], "note": group["basis"].get("note")}
            part["phase_label"] = phases.get(part["phase"])
            parts.append(part)

    # Removal order: phase by phase (phase events in log order), then by removal time where known, then log order.
    phase_rank = {e["id"]: n for n, e in enumerate(e for e in live if e["type"] == "phase")}
    times = {e["id"]: e.get("t_s") for e in live}
    parts.sort(key=lambda p: (phase_rank.get(p["phase"], -1), times.get(p["id"]) is None,
                              times.get(p["id"]) or 0.0, p["removal_index"]))
    for n, part in enumerate(parts, start=1):
        part["removal_index"], part["t_s"] = n, times.get(part["id"])
    install = _install_steps(parts)
    unknowns = [
        "No mechanical dependency between parts was observed or confirmed; install order is only the reverse "
        "of the recorded removal order, phase by phase.",
        "Part identity was never verified; automatic parts are followed by temporal custody, declared parts are the "
        "operator's statement.",
        "Anything removed outside the watched sites and cells and not declared is not in this session.",
        "Reassembly progress is known only from return checks below or from what the operator says in the chat.",
    ]
    unknowns += [f"{p['id']} has no recorded storage place." for p in parts if not p["stored_in"]]
    unknowns += [f"{p['id']} ({p['operator_label']}): the operator did not state where it came from; its source is unknown."
                 for p in parts if p["basis"] == "operator_declared" and p["source"]["described_as"] is None]
    unknowns += [f"{p['id']} has no removal time; its order within its phase is only the log order." for p in parts if p["t_s"] is None]
    unknowns += [f"{p['id']} (automatic) source lies in no inventory site (anonymous location)."
                 for p in parts if p["basis"] == "automatic" and not p["source"].get("site")]
    unknowns += [f"group {g['id']} ({g.get('label')}): interchangeability is the operator's statement, not measured."
                 for g in {id(v): v for v in groups.values()}.values() if g["basis"]["kind"] == "operator_statement"]

    package = {
        "schema": SCHEMA,
        "phases": [{"id": e["id"], "label": e["label"], "starts_after": e.get("starts_after")} for e in live if e["type"] == "phase"],
        "parts": parts,
        "install_order": {"basis": "reverse removal order, phase by phase, within a phase by removal time t_s "
                                   "(log order where t_s is missing; no confirmed dependencies); "
                                   "interchangeable group members form one step", "steps": install},
        "dependencies": {"confirmed": [], "note": "none recorded in this session"},
        "unknowns": unknowns,
        "inputs": {name: None if path is None else {"path": str(path), "sha256": _sha256(path)}
                   for name, path in (("memory", memory_path), ("inventory", inventory_path),
                                      ("custody", custody_path), ("returns", returns_path))},
    }
    if returns_path:
        returns = json.loads(returns_path.read_text(encoding="utf-8"))
        package["reassembly_checks"] = [{k: r[k] for k in ("id", "status") if k in r} |
                                        {"to": r["arrival"]["zone"], "memory_check": r.get("memory_check")}
                                        for r in returns["returns"]]
    return package


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--memory", required=True, type=Path)
    parser.add_argument("--inventory", type=Path, help="site inventory (operator labels); omit for anonymous sources")
    parser.add_argument("--custody", type=Path, help="custody-links@1 of the disassembly")
    parser.add_argument("--returns", type=Path, help="anastylos-reassembly-returns@1 of a reassembly take")
    parser.add_argument("--out", required=True, type=Path, help="folder the guide is started in")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    package = build(args.memory, args.inventory, args.custody, args.returns)
    data = (json.dumps(package, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    (args.out / "session.json").write_bytes(data)
    (args.out / "GUIDE_RULES.md").write_bytes(RULES_FILE.read_bytes())
    print(f"session.json sha256 {hashlib.sha256(data).hexdigest()}, {len(package['parts'])} parts")


if __name__ == "__main__":
    main()
