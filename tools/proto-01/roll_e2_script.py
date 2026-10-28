"""Roll and seal the PROTO-01-NOUV E2 action script (owner tool).

Run by the owner only. Writes nouv-E2.script.json to --out (put it on a
USB stick or phone, not on this PC), prints a German paper list for the
take and the SHA-256 to send to the Technical Lead. Randomness comes
from the OS (``secrets``). See docs/PROTO-01-NOUV.md sections 2 and 4.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
from pathlib import Path

WELLS = ["W-GREEN", "W-YELLOW", "W-BLUE", "W-RED"]
CELLS = list(range(1, 13))  # tray: cells 1-6 in one row, 7-12 below, n above n+6


def adjacent(a: int, b: int) -> bool:
    same_row = (a - 1) // 6 == (b - 1) // 6
    return (same_row and abs(a - b) == 1) or abs(a - b) == 6


def pick_cell(used: set[int], previous: int | None) -> int:
    free = [c for c in CELLS if c not in used and (previous is None or not adjacent(c, previous))]
    return secrets.choice(free)


def shuffled(items: list) -> list:
    rest, out = list(items), []
    while rest:
        out.append(rest.pop(secrets.randbelow(len(rest))))
    return out


def roll() -> dict:
    order = shuffled(WELLS)
    c3_before = 1 + secrets.randbelow(4)            # C3 before removal step 1..4
    c4_after = 2 + secrets.randbelow(3)             # C4 after removal step 2..4
    used: set[int] = set()
    occupied: dict[str, int] = {}
    previous = None
    steps = []
    for k, well in enumerate(order, start=1):
        if k == c3_before:
            steps.append({"control": "C3", "well": secrets.choice(order[k - 1:])})
        cell = pick_cell(used, previous)
        used.add(cell)
        occupied[well] = cell
        previous = cell
        steps.append({"from": well, "to": f"cell-{cell}"})
        if k == c4_after:
            part = secrets.choice(sorted(occupied))
            source = occupied[part]
            target = pick_cell(used, previous)
            used.add(target)
            occupied[part] = target
            previous = target
            steps.append({"control": "C4", "part": part, "from_cell": f"cell-{source}", "to": f"cell-{target}"})
    for n, step in enumerate(steps, start=1):
        step["n"] = n
    return {"schema": "proto01-nouv-script@1", "experiment": "E2", "nonce": secrets.token_hex(24), "steps": steps}


def paper_line(step: dict) -> str:
    if step.get("control") == "C3":
        return f"{step['n']}. KONTROLLE C3: {step['well']} anheben und in DIESELBE Mulde zurücklegen"
    if step.get("control") == "C4":
        return f"{step['n']}. KONTROLLE C4: Teil aus {step['from_cell']} ({step['part']}) nach {step['to']} umlegen"
    if step.get("control") == "C5":
        return f"{step['n']}. KONTROLLE C5: Teil in {step['cell']} anheben und in DIESELBE Zelle zurücklegen"
    if step.get("control") == "C6":
        return (f"{step['n']}. KONTROLLE C6: Teil aus {step['from']} ABSICHTLICH in die FALSCHE Mulde {step['wrong_well']} "
                f"legen, Hände weg, 3 s warten, dann in {step['to']} umsetzen")
    return f"{step['n']}. {step['from']} -> {step['to']}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, help="target folder, e.g. a USB stick (not this PC)")
    args = parser.parse_args()
    path = Path(args.out) / "nouv-E2.script.json"
    if path.exists():
        raise SystemExit(f"{path} exists already; a script is rolled once")
    data = json.dumps(roll(), indent=2, ensure_ascii=False).encode("utf-8")
    path.write_bytes(data)
    print("Zettel für den Take (Hände nach jedem Schritt ganz aus dem Bild):")
    for step in json.loads(data)["steps"]:
        print("  " + paper_line(step))
    print(f"\nDatei: {path}")
    print(f"SHA-256 (nur diesen an den Technical Lead): {hashlib.sha256(data).hexdigest()}")


if __name__ == "__main__":
    main()
