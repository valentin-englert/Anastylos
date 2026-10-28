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


def build(memory_path: Path, inventory_path: Path | None, custody_path: Path | None,
          returns_path: Path | None) -> dict:
    events = _read_jsonl(memory_path)
    superseded = {event["supersedes"] for event in events if event.get("supersedes")}
    observations = [e for e in events if e["type"] == "observation" and e["id"] not in superseded]
    links = {e["observation_id"]: e for e in events if e["type"] == "tray_link" and e["id"] not in superseded}
    sites = json.loads(inventory_path.read_text(encoding="utf-8"))["sites"] if inventory_path else []
    custody = json.loads(custody_path.read_text(encoding="utf-8")) if custody_path else None

    parts = []
    for index, obs in enumerate(observations, start=1):
        link = links.get(obs["id"])
        cell = f"cell-{link['tray']['number']}" if link else None
        parts.append({
            "observation": obs["id"],
            "removal_index": index,
            "identity": "unknown: evidence of a removal, not a verified part",
            "source": {**_source_site(obs["centroid_mm"], sites), "centroid_mm": obs["centroid_mm"],
                       "bbox_mm": obs["bbox_mm"], "evidence_pair": obs["evidence"]["pair_id"],
                       "evidence_result_sha256": obs["evidence"].get("result_sha256")},
            "stored_in": None if not link else {
                "cell": cell, "tray_link": link["id"], "basis": link["basis"]["kind"],
                "confirmed_by": link["basis"].get("confirmed_by"), "note": link["basis"].get("note")},
            "custody_history": _custody_history(custody, cell),
        })

    install = [{"step": n, "observation": part["observation"], "from": (part["stored_in"] or {}).get("cell"),
                "to": part["source"]["site"] or part["source"]["centroid_mm"]}
               for n, part in enumerate(reversed(parts), start=1)]
    unknowns = [
        "No mechanical dependency between parts was observed or confirmed; install order is only the reverse "
        "of the observed removal order.",
        "Part identity was never verified; a part is followed by temporal custody and operator confirmation.",
        "Anything removed outside the watched sites and cells is not in this session.",
        "Reassembly progress is known only from return checks below or from what the operator says in the chat.",
    ]
    unknowns += [f"{p['observation']} has no confirmed storage cell." for p in parts if not p["stored_in"]]
    unknowns += [f"{p['observation']} source lies in no inventory site (anonymous location)."
                 for p in parts if not p["source"]["site"]]

    package = {
        "schema": SCHEMA,
        "parts": parts,
        "install_order": {"basis": "reverse observed removal order (no confirmed dependencies)", "steps": install},
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
