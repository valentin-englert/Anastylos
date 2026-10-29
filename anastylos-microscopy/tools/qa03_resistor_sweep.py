"""QA-03 evaluation sweep: resistor-reader verdicts vs the v0.1.1 Core assessment.

Usage:  python tools/qa03_resistor_sweep.py [--anastylos-root PATH] [--json OUT]

Renders SYNTHETIC resistors with the reader module's own deterministic
renderer (anastylos_resistor.synthetic) over a fixed grid of sequences, body
colours, rotations, exposure (brightness), glare and noise. For every render
the unchanged reader runs, then the Core assessment. Native ``ok`` results are
classified as:

  accepted_correct   reader ok, value correct, Core keeps it as candidate
  false_rejection    reader ok, value correct, Core holds it as ambiguous
  false_acceptance   reader ok, value WRONG, Core keeps it as candidate
  caught_wrong       reader ok, value WRONG, Core holds it as ambiguous

The grid and the assessment thresholds were fixed before the sweep was run;
nothing here is fitted. Synthetic renders say nothing about real microscope
accuracy.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# "dev" grid: used while designing the checks. "heldout" grid: different sequences,
# rotations, exposures, noise and seed; run once after the thresholds were frozen.
DEV_SEQUENCES = [
    ["brown", "black", "red", "gold"],          # 1 k
    ["yellow", "violet", "red", "gold"],        # 4.7 k
    ["red", "red", "red", "gold"],              # 2.2 k
    ["brown", "red", "red", "gold"],            # 1.2 k
    ["orange", "white", "orange", "gold"],      # 39 k (white digit)
    ["green", "blue", "brown", "gold"],         # 560
    ["blue", "gray", "yellow", "silver"],       # 680 k
    ["red", "violet", "gold", "gold"],          # 2.7 (gold multiplier)
    ["yellow", "violet", "black", "red", "brown"],      # 5-band 47 k
    ["brown", "black", "black", "red", "brown", "red"],  # 6-band 10 k
]
HELDOUT_SEQUENCES = [
    ["gray", "red", "orange", "gold"],          # 82 k
    ["violet", "green", "black", "silver"],     # 75
    ["white", "brown", "brown", "gold"],        # 910
    ["orange", "orange", "brown", "gold"],      # 330
    ["brown", "green", "yellow", "gold"],       # 150 k
    ["red", "black", "brown", "brown", "brown"],  # 5-band 2.01 k
    ["yellow", "violet", "orange", "gold"],     # 47 k
    ["blue", "gray", "red", "gold"],            # 6.8 k
]
GRIDS = {
    "dev": {"sequences": DEV_SEQUENCES, "bodies": ["tan", "blue"], "rotations": [0.0, 8.0, -15.0],
            "brightness": [0.75, 1.0, 1.3, 1.6, 1.9], "glare": [False, True], "noise": [0.0, 6.0],
            "seed": 7},
    "heldout": {"sequences": HELDOUT_SEQUENCES, "bodies": ["tan", "blue"],
                "rotations": [3.0, -6.0, 12.0, 20.0], "brightness": [0.85, 1.15, 1.45, 1.75],
                "glare": [False, True], "noise": [3.0], "seed": 11},
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--anastylos-root", default=os.environ.get("ANASTYLOS_ROOT"))
    parser.add_argument("--json", default=None)
    parser.add_argument("--grid", default="dev", choices=sorted(GRIDS))
    args = parser.parse_args(argv)
    from anastylos_microscopy import env
    from anastylos_microscopy.assessment import assess_resistor
    env.activate(env.resolve_root(args.anastylos_root))
    from anastylos_resistor.decode import decode
    from anastylos_resistor.synthetic import render
    from anastylos_resistor.vision import analyze

    rows, totals = [], {}
    by_condition: dict[str, dict] = {}
    grid = GRIDS[args.grid]
    for seq, body, rot, bright, glare, noise in product(grid["sequences"], grid["bodies"],
                                                        grid["rotations"], grid["brightness"],
                                                        grid["glare"], grid["noise"]):
        truth = decode(seq, len(seq))[0]["resistance_ohms"]
        image = render(seq, body=body, rotation_deg=rot, brightness=bright, glare=glare,
                       noise_sigma=noise, seed=grid["seed"])
        native = analyze(image)
        verdict = native["status"]
        if verdict == "ok":
            got = native["candidates"][0]["resistance_ohms"]
            correct = abs(got - truth) <= 1e-9 * max(1.0, truth)
            kept = assess_resistor(native, image)["supports_ok"]
            klass = {(True, True): "accepted_correct", (True, False): "false_rejection",
                     (False, True): "false_acceptance", (False, False): "caught_wrong"}[(correct, kept)]
        else:
            klass = f"native_{verdict}"
        totals[klass] = totals.get(klass, 0) + 1
        cond = f"brightness={bright} glare={glare}"
        by_condition.setdefault(cond, {})
        by_condition[cond][klass] = by_condition[cond].get(klass, 0) + 1
        rows.append({"sequence": seq, "body": body, "rotation": rot, "brightness": bright,
                     "glare": glare, "noise": noise, "native_status": verdict, "class": klass})
    ok_total = sum(totals.get(k, 0) for k in ("accepted_correct", "false_rejection",
                                              "false_acceptance", "caught_wrong"))
    wrong = totals.get("false_acceptance", 0) + totals.get("caught_wrong", 0)
    right = totals.get("accepted_correct", 0) + totals.get("false_rejection", 0)
    summary = {
        "grid": args.grid, "renders": len(rows), "native_ok": ok_total, "classes": dict(sorted(totals.items())),
        "false_acceptance_rate_among_wrong_ok": round(totals.get("false_acceptance", 0) / wrong, 4)
        if wrong else None,
        "false_rejection_rate_among_correct_ok": round(totals.get("false_rejection", 0) / right, 4)
        if right else None,
        "baseline_v010_false_confident": wrong,
        "by_condition": dict(sorted(by_condition.items())),
        "validation_class": "synthetic (reader's own renderer); not real-image validation",
    }
    print(json.dumps({k: v for k, v in summary.items() if k != "by_condition"}, indent=2))
    for cond, counts in summary["by_condition"].items():
        print(f"  {cond:28s} {json.dumps(dict(sorted(counts.items())))}")
    if args.json:
        Path(args.json).write_text(json.dumps({"summary": summary, "rows": rows}, indent=1),
                                   encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
