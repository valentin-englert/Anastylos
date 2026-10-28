"""Record and seal what was actually performed in a PROTO-01-NOUV take (E2, E2R) (owner tool).

Run by the owner after reviewing the witness video. Reads the sealed
nouv-E2.script.json from --dir (USB stick), asks step by step whether it
was performed as written, writes nouv-E2.actual.json next to it with its
own nonce and prints its SHA-256. See docs/PROTO-01-NOUV.md section 4.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
from pathlib import Path

from roll_e2_script import paper_line


def ask(prompt: str) -> str:
    return input(prompt).strip()


def record_step(step: dict) -> dict:
    print("\n" + paper_line(step))
    if ask("  So ausgeführt, laut Zeugen-Video? [j/n] ").lower().startswith("j"):
        return {**step, "performed": "as_scripted"}
    what = ask("  Was ist tatsächlich passiert? (z. B. 'W-RED -> cell-7' oder 'ausgelassen'): ")
    return {**step, "performed": "deviated", "actual": what}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", required=True, help="folder holding nouv-<experiment>.script.json (USB stick)")
    parser.add_argument("--experiment", default="E2", help="E2 or E2R")
    args = parser.parse_args()
    folder = Path(args.dir)
    script_path = folder / f"nouv-{args.experiment}.script.json"
    out_path = folder / f"nouv-{args.experiment}.actual.json"
    if out_path.exists():
        raise SystemExit(f"{out_path} exists already")
    script_bytes = script_path.read_bytes()
    script = json.loads(script_bytes)
    steps = [record_step(step) for step in script["steps"]]
    extra = ask("\nZusätzliche Ereignisse (Teil fallen gelassen, zweimal gegriffen ...), sonst leer: ")
    cells = ask("Endzustand laut Foto (Zellen bzw. Mulden), z. B. 'cell-6=YELLOW' oder 'W-RED=RED': ")
    actual = {
        "schema": "proto01-nouv-actual@1",
        "experiment": args.experiment,
        "nonce": secrets.token_hex(24),
        "script_sha256": hashlib.sha256(script_bytes).hexdigest(),
        "basis": "owner review of the witness video and photos of the cells",
        "steps": steps,
        "other_events": extra or None,
        "final_cells_from_photos": cells,
    }
    data = json.dumps(actual, indent=2, ensure_ascii=False).encode("utf-8")
    out_path.write_bytes(data)
    print(f"\nDatei: {out_path}")
    print(f"SHA-256 (nur diesen an den Technical Lead): {hashlib.sha256(data).hexdigest()}")


if __name__ == "__main__":
    main()
