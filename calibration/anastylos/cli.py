"""Command line for the calibration kit."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from anastylos.layout import build_config
from anastylos.render import generate

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate ANASTYLOS calibration sheets.")
    parser.add_argument("command", choices=["generate"])
    parser.add_argument("--out", type=Path, default=ROOT, help="directory for config, print/, and preview/")
    args = parser.parse_args(argv)
    if args.command == "generate":
        generate(build_config(), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
