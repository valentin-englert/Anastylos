"""Run the unchanged unit tests of every wrapped ANASTYLOS reader module.

Usage (any OS):  python tools/run_reader_regression.py [--anastylos-root PATH] [--json OUT]

Each module's own suite runs in its own interpreter with its own directory as
working directory, exactly as the module documents it
(``python -m unittest discover -s tests -v``). Nothing is modified.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

MODULES = ["resistor-reader", "smd-reader", "pcb-ocr", "component-markings", "polarity-reader",
           "screw-reader", "screw-measurement", "washer-spacer", "component-fingerprints",
           "connector-matching"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--anastylos-root", default=os.environ.get("ANASTYLOS_ROOT"))
    parser.add_argument("--json", default=None)
    args = parser.parse_args(argv)
    if not args.anastylos_root:
        parser.error("set ANASTYLOS_ROOT or pass --anastylos-root")
    root = Path(args.anastylos_root)
    rows, failed = [], False
    for module in MODULES:
        proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                              cwd=root / module, capture_output=True, text=True)
        tail = proc.stderr.strip().splitlines()[-3:]
        ran = re.search(r"Ran (\d+) test", proc.stderr)
        skipped = re.search(r"skipped=(\d+)", proc.stderr)
        row = {"module": module, "exit_code": proc.returncode,
               "tests": int(ran.group(1)) if ran else None,
               "skipped": int(skipped.group(1)) if skipped else 0, "result": tail[-1] if tail else ""}
        failed |= proc.returncode != 0
        rows.append(row)
        print(f"{module:24s} {row['tests']!s:>5} tests  skipped={row['skipped']}  {row['result']}")
    total = sum(r["tests"] or 0 for r in rows)
    print(f"TOTAL {total} tests, {'FAILURES' if failed else 'all OK'}")
    if args.json:
        Path(args.json).write_text(json.dumps({"python": sys.version.split()[0],
                                               "platform": sys.platform, "modules": rows,
                                               "total": total, "ok": not failed}, indent=2),
                                   encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
