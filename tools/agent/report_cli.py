"""Export one agent session to a standalone HTML report (read-only).

    python tools/agent/report_cli.py --session <dir> --out <report.html>

Prints the stable result document (``REPORT.md``) as JSON on success.
Refusals print ``STOPPED: <English reason>`` on stderr and write nothing.
Exit codes: 0 exported, 1 stopped, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import report
from state import SessionError


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Export a session report (standalone HTML, read-only).")
    parser.add_argument("--session", required=True, type=Path, help="agent session folder")
    parser.add_argument("--out", required=True, type=Path, help="HTML report to write")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):  # operator labels may carry any characters
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        result = report.export(args.session, args.out)
    except (report.ReportError, SessionError) as exc:
        print(f"STOPPED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
