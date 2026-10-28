"""Roll and seal the PROTO-01-NOUV E2R (reassembly) action script (owner tool).

The return order and targets are public: they come from the memory plan
(tools/reassembly/returns.py plan). Only the controls are rolled and
sealed: C5 (a part lifted and put back into its own cell) and C6 (a part
deliberately put into a wrong empty well, then moved to its home well).
Writes nouv-E2R.script.json to --out (USB stick), prints the German paper
list and the SHA-256 for the Technical Lead. See docs/PROTO-01-E2R.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
from pathlib import Path

from roll_e2_script import paper_line


def roll(plan_steps: list[dict]) -> dict:
    returns = [{"from": step["cell"], "to": step["home_well"]} for step in plan_steps]
    wells = [item["to"] for item in returns]
    c6_index = secrets.randbelow(len(returns) - 1)       # not the last: it needs another empty well
    wrong = secrets.choice(wells[c6_index + 1:])          # a well that is still empty at that moment
    c5_before = secrets.randbelow(len(returns))           # C5 before return k, on a cell still full then
    steps = []
    for k, item in enumerate(returns):
        if k == c5_before:
            steps.append({"control": "C5", "cell": secrets.choice([r["from"] for r in returns[k:]])})
        if k == c6_index:
            steps.append({"control": "C6", "from": item["from"], "wrong_well": wrong, "to": item["to"]})
        else:
            steps.append(dict(item))
    for n, step in enumerate(steps, start=1):
        step["n"] = n
    return {"schema": "proto01-nouv-script@1", "experiment": "E2R", "nonce": secrets.token_hex(24), "steps": steps}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plan", required=True, help="plan.json from tools/reassembly/returns.py plan")
    parser.add_argument("--out", required=True, help="target folder, e.g. a USB stick (not this PC)")
    args = parser.parse_args()
    plan_bytes = Path(args.plan).read_bytes()
    script = roll(json.loads(plan_bytes)["steps"])
    script["plan_sha256"] = hashlib.sha256(plan_bytes).hexdigest()
    path = Path(args.out) / "nouv-E2R.script.json"
    if path.exists():
        raise SystemExit(f"{path} exists already; a script is rolled once")
    data = json.dumps(script, indent=2, ensure_ascii=False).encode("utf-8")
    path.write_bytes(data)
    print("Zettel für den Take (Hände nach jedem Schritt ganz aus dem Bild):")
    for step in script["steps"]:
        print("  " + paper_line(step))
    print(f"\nDatei: {path}")
    print(f"SHA-256 (nur diesen an den Technical Lead): {hashlib.sha256(data).hexdigest()}")


if __name__ == "__main__":
    main()
